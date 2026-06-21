#!/usr/bin/python3
from __future__ import annotations

import argparse
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from bs4 import BeautifulSoup


DEFAULT_MODEL = "gpt-5.4-mini"
DEFAULT_TIMEOUT = 300.0
MAX_HTML_BYTES = 8 * 1024 * 1024
TITLE_CONTEXT_LIMIT = 12000
USER_AGENT = "Mozilla/5.0 (compatible; CodexWebToTTS/1.0)"

UNWANTED_TAGS = {
    "script",
    "style",
    "noscript",
    "svg",
    "nav",
    "footer",
    "header",
    "form",
    "aside",
    "iframe",
    "canvas",
    "input",
    "button",
    "select",
    "option",
    "textarea",
    "dialog",
}

BOILERPLATE_HINTS = re.compile(
    r"(nav|menu|footer|header|breadcrumb|cookie|subscribe|share|social|"
    r"related|recommend|comment|promo|advert|ad-|ads|sidebar|modal|popup|"
    r"overlay|widget|toc|table-of-contents)",
    re.IGNORECASE,
)

INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
WHITESPACE_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class OutputLayout:
    raw_path: Path
    script_path: Path
    mp3_path: Path | None
    needs_title: bool


@dataclass(frozen=True)
class ArticleCandidate:
    source: str
    text: str
    node: Any | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch a web page, extract the article body, and turn it into a Japanese TTS script."
    )
    parser.add_argument("url", help="Web page URL to fetch.")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path.cwd(),
        help="Directory used for auto-generated outputs when explicit paths are omitted.",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Codex model name used for title/script conversion (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"Timeout in seconds for each fetch and Codex invocation (default: {DEFAULT_TIMEOUT}).",
    )
    parser.add_argument(
        "--script-only",
        action="store_true",
        help="Stop after writing the Japanese script; do not synthesize MP3.",
    )
    parser.add_argument(
        "--voicevox-args",
        nargs=argparse.REMAINDER,
        default=[],
        metavar="ARG",
        help="Extra arguments forwarded to voicevox_tts.py. Place this option last.",
    )
    return parser.parse_args()


def validate_url(raw_url: str) -> str:
    url = raw_url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise SystemExit(f"invalid URL: {raw_url!r}")
    return url


def fetch_html(url: str, timeout: float, max_bytes: int = MAX_HTML_BYTES) -> str:
    request = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=timeout) as response:
            declared_size = response.headers.get("Content-Length")
            if declared_size:
                try:
                    if int(declared_size) > max_bytes:
                        raise SystemExit(
                            f"page is larger than the configured limit ({max_bytes} bytes)"
                        )
                except ValueError:
                    pass

            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = response.read(min(65536, max_bytes - total + 1))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise SystemExit(
                        f"page is larger than the configured limit ({max_bytes} bytes)"
                    )
            data = b"".join(chunks)
            charset = response.headers.get_content_charset() or "utf-8"
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"fetch failed: HTTP {exc.code} {exc.reason}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"fetch failed: {exc.reason}") from exc

    try:
        return data.decode(charset, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def _attr_text(tag: Any, name: str) -> str:
    if tag is None or getattr(tag, "attrs", None) is None:
        return ""
    value = tag.get(name)
    if isinstance(value, list):
        return " ".join(str(item) for item in value)
    if value is None:
        return ""
    return str(value)


def _looks_like_boilerplate(tag: Any) -> bool:
    if tag is None or getattr(tag, "attrs", None) is None:
        return False
    if tag.name in {"html", "body", "main", "article"}:
        return False
    text = f"{_attr_text(tag, 'id')} {_attr_text(tag, 'class')}"
    return bool(BOILERPLATE_HINTS.search(text))


def _prune_boilerplate(soup: BeautifulSoup) -> None:
    for tag in list(soup.find_all(True)):
        if tag.name in UNWANTED_TAGS or _looks_like_boilerplate(tag):
            tag.decompose()
            continue
        hidden = _attr_text(tag, "aria-hidden").lower() == "true"
        style = _attr_text(tag, "style").lower()
        if hidden or "display:none" in style or "visibility:hidden" in style:
            tag.decompose()


def _normalize_text(text: str) -> str:
    lines: list[str] = []
    last_was_blank = False
    for raw_line in text.replace("\r", "").split("\n"):
        line = WHITESPACE_RE.sub(" ", raw_line).strip()
        if not line:
            last_was_blank = True
            continue
        if lines and last_was_blank:
            lines.append("")
        lines.append(line)
        last_was_blank = False
    return "\n".join(lines).strip()


def _node_text(node: Any) -> str:
    return _normalize_text(node.get_text("\n", strip=True))


def _candidate_text(candidate: ArticleCandidate) -> str:
    return candidate.text


def _paragraph_count(text: str) -> int:
    return sum(1 for chunk in re.split(r"\n{2,}", text) if chunk.strip())


def _link_text_length(node: Any | None) -> int:
    if node is None or not hasattr(node, "find_all"):
        return 0
    total = 0
    for link in node.find_all("a"):
        total += len(_normalize_text(link.get_text(" ", strip=True)))
    return total


def _candidate_score(candidate: ArticleCandidate) -> float:
    text = _candidate_text(candidate)
    length = len(text)
    if not length:
        return float("-inf")

    source_bonus = {
        "article-body": 700.0,
        "structured": 280.0,
        "readability": 80.0,
        "fallback": 0.0,
    }.get(candidate.source, 0.0)

    paragraph_bonus = min(_paragraph_count(text) * 120.0, 600.0)
    link_length = _link_text_length(candidate.node)
    link_penalty = (link_length / length) * length * 0.9 if length else 0.0
    short_penalty = max(0, 300 - length) * 2.5
    boilerplate_penalty = 250.0 if BOILERPLATE_HINTS.search(text) else 0.0

    return float(length) + paragraph_bonus + source_bonus - link_penalty - short_penalty - boilerplate_penalty


def _best_candidate(candidates: list[ArticleCandidate]) -> ArticleCandidate:
    viable = [candidate for candidate in candidates if candidate.text.strip()]
    if not viable:
        return ArticleCandidate(source="fallback", text="")
    return max(viable, key=_candidate_score)


def _readability_document(html: str) -> Any:
    try:
        from readability import Document  # type: ignore
    except Exception as exc:
        raise SystemExit("readability-lxml is required for article extraction") from exc
    return Document(html)


def _fallback_article_text(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    _prune_boilerplate(soup)

    candidates: list[Any] = []
    candidates.extend(soup.find_all("article"))
    candidates.extend(soup.find_all("main"))

    best_text = ""
    for candidate in candidates:
        candidate_text = _node_text(candidate)
        if len(candidate_text) > len(best_text):
            best_text = candidate_text

    if best_text.strip():
        return best_text

    body = soup.body or soup
    best_text = _node_text(body)
    if not best_text.strip():
        best_text = _normalize_text(soup.get_text("\n", strip=True))
    return best_text


def _collect_text_blocks(soup: BeautifulSoup) -> list[ArticleCandidate]:
    blocks: list[ArticleCandidate] = []
    seen: set[str] = set()
    for node in soup.select("main .post__content.wysiwyg, main article, main section"):
        text = _node_text(node)
        if len(text) < 120:
            continue
        if "The Quanta Podcast" in text:
            continue
        if text in seen:
            continue
        seen.add(text)
        blocks.append(ArticleCandidate(source="structured", text=text, node=node))
    return blocks


def _join_candidate_texts(candidates: list[ArticleCandidate]) -> str:
    parts: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = candidate.text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        parts.append(text)
    return "\n\n".join(parts)


def _collect_article_body_candidates(soup: BeautifulSoup) -> list[ArticleCandidate]:
    blocks: list[ArticleCandidate] = []
    seen: set[str] = set()
    selectors = (
        "div[itemprop='articleBody']",
        "article [itemprop='articleBody']",
        "article .sf-article-body",
        "main article .sf-article-body",
        "article",
    )
    for selector in selectors:
        for node in soup.select(selector):
            text = _node_text(node)
            minimum = 200 if selector != "article" else 250
            if len(text) < minimum:
                continue
            if text in seen:
                continue
            seen.add(text)
            source = "article-body" if "articleBody" in selector or "sf-article-body" in selector else "structured"
            blocks.append(ArticleCandidate(source=source, text=text, node=node))
    return blocks


def _extract_from_structure(html: str) -> str:
    soup = BeautifulSoup(html, "lxml")
    candidates = _collect_article_body_candidates(soup)
    if candidates:
        return _best_candidate(candidates).text
    _prune_boilerplate(soup)
    candidates = _collect_text_blocks(soup)
    if candidates:
        combined = _join_candidate_texts(candidates)
        if combined.strip():
            return combined
        return _best_candidate(candidates).text
    return ""


def extract_article_text(html: str) -> str:
    candidates: list[ArticleCandidate] = []

    doc = _readability_document(html)
    summary = doc.summary()
    summary_soup = BeautifulSoup(summary, "lxml")
    summary_text = _node_text(summary_soup)
    if summary_text.strip():
        candidates.append(ArticleCandidate(source="readability", text=summary_text, node=summary_soup))

    structured_text = _extract_from_structure(html)
    if structured_text.strip():
        candidates.append(ArticleCandidate(source="structured", text=structured_text))

    fallback_text = _fallback_article_text(html)
    if fallback_text.strip():
        candidates.append(ArticleCandidate(source="fallback", text=fallback_text))

    best = _best_candidate(candidates)
    return best.text


def _escape_prompt_text(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _truncate_for_prompt(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit]


def _run_codex(prompt: str, model: str, timeout: float) -> str:
    with tempfile.TemporaryDirectory(prefix="web_to_tts_") as tmpdir:
        output_path = Path(tmpdir) / "codex_last_message.txt"
        cmd = [
            "codex",
            "exec",
            "--model",
            model,
            "--ephemeral",
            "--skip-git-repo-check",
            "--ignore-rules",
            "--output-last-message",
            str(output_path),
            "-",
        ]
        try:
            completed = subprocess.run(
                cmd,
                input=prompt,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SystemExit("codex CLI is not installed") from exc

        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
            raise SystemExit(f"codex exec failed: {detail}")

        if output_path.exists():
            result = output_path.read_text(encoding="utf-8")
        else:
            result = completed.stdout

    result = result.strip()
    if not result:
        raise SystemExit("codex exec returned empty output")
    return result


def generate_title(article_text: str, model: str, timeout: float) -> str:
    prompt = _escape_prompt_text(
        f"""
次の本文から、ファイル名に使える短い日本語タイトルを1行で返してください。
- 20文字前後
- 主題名詞を先頭に置く
- 固有名詞は必要なら残す
- 記号はできるだけ避ける
- 余計な説明や引用符は不要
- 出力はタイトルのみ
- 英数字や数字は必要最小限にする
- 煽り語や比喩は避ける

本文:
<<<
{_truncate_for_prompt(article_text, TITLE_CONTEXT_LIMIT)}
>>>
"""
    )
    raw_title = _run_codex(prompt, model, timeout)
    title = raw_title.splitlines()[0].strip()
    title = re.sub(r"^(タイトル|見出し)[:：]\s*", "", title)
    title = INVALID_FILENAME_CHARS.sub("_", title)
    title = WHITESPACE_RE.sub("", title)
    title = re.sub(r"[^\wぁ-んァ-ヶ一-龯々〆〤ー_-]+", "", title)
    title = title.strip("._-")
    title = title[:20]
    return title or "記事"


def convert_to_script(article_text: str, model: str, timeout: float) -> str:
    prompt = _escape_prompt_text(
        f"""
次の本文を、できるだけ文意を維持したうえで、放送原稿化した日本語原稿に変換してください。
- 英語は読みカタカナにおきかえる
- 数値はASCII文字
- 日本語の普通の文章として出力する
- 出力は本文のみで、説明やMarkdownは不要
- ナビゲーションや広告の文言は含めない
- 箇条書きは必要なら読みやすい文に直す
- 句読点は自然な音読に向く形で調整する
- 固有名詞のカタカナ表記では「・」を使わず、連続したカタカナで表記する
- ただし、語と語を並置する一般的な用途での「・」は必要なら残してよい
- 本文中の用語にブレがある場合は、意味が変わらない範囲で整理する
- コード片、型名、メソッド名、数字は、読み上げで誤読しにくい形に整える

本文:
<<<
{article_text}
>>>
"""
    )
    return _run_codex(prompt, model, timeout)


def _strip_known_suffixes(name: str) -> str:
    for suffix in (".script.txt", ".raw.txt", ".txt", ".md", ".mp3"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return Path(name).stem if Path(name).suffix else name


def _initial_layout(args: argparse.Namespace) -> OutputLayout:
    stem = datetime.now().strftime("%Y%m%d-%H%M%S")
    parent = args.output_dir
    raw_path = parent / f"{stem}.raw.txt"
    script_path = parent / f"{stem}.script.txt"
    mp3_path = None if args.script_only else (parent / f"{stem}.mp3")
    needs_title = True
    return OutputLayout(raw_path=raw_path, script_path=script_path, mp3_path=mp3_path, needs_title=needs_title)


def _final_layout(title: str, base_layout: OutputLayout) -> OutputLayout:
    if not base_layout.needs_title:
        return base_layout
    stamp = base_layout.raw_path.stem
    if stamp.endswith(".raw"):
        stamp = stamp[: -len(".raw")]
    base_name = f"{stamp}-{title}"
    parent = base_layout.raw_path.parent
    return OutputLayout(
        raw_path=parent / f"{base_name}.raw.txt",
        script_path=parent / f"{base_name}.script.txt",
        mp3_path=None if base_layout.mp3_path is None else (parent / f"{base_name}.mp3"),
        needs_title=False,
    )


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_text(path: Path, text: str) -> None:
    _ensure_parent(path)
    path.write_text(text, encoding="utf-8")


def _move_path(src: Path, dst: Path) -> None:
    _ensure_parent(dst)
    if src == dst:
        return
    src.replace(dst)


def synthesize_mp3(
    script_text: str, output_path: Path, timeout: float, voicevox_args: list[str]
) -> None:
    voicevox = Path(__file__).with_name("voicevox_tts.py")
    cmd = [sys.executable, str(voicevox), *voicevox_args, "--output", str(output_path)]
    try:
        completed = subprocess.run(
            cmd,
            input=script_text,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SystemExit("voicevox_tts.py is not available") from exc

    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown error"
        raise SystemExit(f"voicevox synthesis failed: {detail}")


def main() -> int:
    args = parse_args()

    url = validate_url(args.url)
    html = fetch_html(url, args.timeout)
    article_text = extract_article_text(html)
    if not article_text.strip():
        raise SystemExit("no article text could be extracted")

    layout = _initial_layout(args)
    if layout.needs_title:
        _write_text(layout.raw_path, article_text)
        title = generate_title(article_text, args.model, args.timeout)
        final_layout = _final_layout(title, layout)
        _move_path(layout.raw_path, final_layout.raw_path)
    else:
        title = _strip_known_suffixes(layout.script_path.name)
        final_layout = layout
        _write_text(final_layout.raw_path, article_text)

    script_text = convert_to_script(article_text, args.model, args.timeout)
    _write_text(final_layout.script_path, script_text)

    if final_layout.mp3_path is not None:
        synthesize_mp3(script_text, final_layout.mp3_path, args.timeout, args.voicevox_args)

    if args.script_only:
        print(final_layout.script_path)
    else:
        print(final_layout.mp3_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
