from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import web_to_tts_script as app


class FakeHeaders:
    def __init__(self, content_type: str = "text/html; charset=utf-8") -> None:
        self._content_type = content_type

    def get(self, key: str, default=None):
        if key.lower() == "content-length":
            return None
        return default

    def get_content_charset(self) -> str | None:
        if "charset=" in self._content_type:
            return self._content_type.split("charset=", 1)[1]
        return None


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "text/html; charset=utf-8") -> None:
        self._body = body
        self._offset = 0
        self.headers = FakeHeaders(content_type)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size == -1:
            size = len(self._body) - self._offset
        start = self._offset
        end = min(len(self._body), start + size)
        self._offset = end
        return self._body[start:end]


def fake_urlopen(request, timeout=0):
    parsed = request.full_url
    fake_urlopen.calls.append(parsed)
    if parsed.endswith("/shiftjis"):
        body = "こんにちは".encode("shift_jis")
        return FakeResponse(body, "text/html; charset=shift_jis")
    if parsed.endswith("/article"):
        html = """
        <html>
          <body>
            <header>Site header</header>
            <nav>Menu</nav>
            <main>
              <article>
                <h1>Title</h1>
                <p>First paragraph.</p>
                <div class="related">Related</div>
                <p>Second line.</p>
              </article>
            </main>
            <footer>Footer</footer>
          </body>
        </html>
        """
        return FakeResponse(html.encode("utf-8"))
    if parsed.endswith("/body"):
        html = """
        <html>
          <body>
            <div class="nav">Ignore me</div>
            <p>Only body text</p>
          </body>
        </html>
        """
        return FakeResponse(html.encode("utf-8"))
    raise AssertionError(f"unexpected URL: {parsed}")


fake_urlopen.calls = []


class WebToTtsScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        fake_urlopen.calls = []

    def test_validate_url_rejects_invalid(self) -> None:
        with self.assertRaises(SystemExit):
            app.validate_url("ftp://example.com")

    def test_fetch_html_decodes_charset(self) -> None:
        with patch.object(app, "urlopen", side_effect=fake_urlopen):
            text = app.fetch_html("https://example.com/shiftjis", timeout=1.0)
        self.assertEqual(text, "こんにちは")

    def test_extract_article_prefers_article(self) -> None:
        html = """
        <html>
          <body>
            <main>
              <article>
                <h1>Title</h1>
                <p>First paragraph.</p>
                <p>Second line.</p>
              </article>
            </main>
          </body>
        </html>
        """

        with patch.object(app, "_readability_document", return_value=SimpleNamespace(summary=lambda: html)):
            text = app.extract_article_text(html)
        self.assertIn("First paragraph.", text)
        self.assertIn("Second line.", text)
        self.assertNotIn("Menu", text)
        self.assertNotIn("Footer", text)

    def test_extract_article_falls_back_to_body(self) -> None:
        html = """
        <html>
          <body>
            <div class="nav">Ignore me</div>
            <p>Only body text</p>
          </body>
        </html>
        """

        with patch.object(app, "_readability_document", return_value=SimpleNamespace(summary=lambda: "")):
            text = app.extract_article_text(html)
        self.assertEqual(text, "Only body text")

    def test_extract_article_concatenates_multiple_structured_blocks(self) -> None:
        html = """
        <html>
          <body>
            <main>
              <div class="post__content wysiwyg p theme__anchors--underline">
                <p>Opening paragraph.</p>
                <p>Second opening paragraph.</p>
              </div>
              <div class="post__content wysiwyg p theme__anchors--underline">
                <p>Later excerpt paragraph.</p>
              </div>
            </main>
          </body>
        </html>
        """

        with patch.object(app, "_readability_document", return_value=SimpleNamespace(summary=lambda: "")):
            text = app.extract_article_text(html)
        self.assertIn("Opening paragraph.", text)
        self.assertIn("Second opening paragraph.", text)
        self.assertIn("Later excerpt paragraph.", text)
        self.assertLess(text.index("Opening paragraph."), text.index("Later excerpt paragraph."))

    def test_generate_title_sanitizes_output(self) -> None:
        def fake_run(prompt, model, timeout):
            return "  タイトル: すごい 記事!  \n説明"

        with patch.object(app, "_run_codex", side_effect=fake_run):
            title = app.generate_title("本文", "gpt-5.4-mini", 1.0)
        self.assertEqual(title, "すごい記事")

    def test_run_codex_builds_command_and_reads_output(self) -> None:
        captured = {}

        def fake_run(cmd, input, text, capture_output, timeout, check):
            captured["cmd"] = cmd
            captured["input"] = input
            output_path = Path(cmd[cmd.index("--output-last-message") + 1])
            output_path.write_text("result text", encoding="utf-8")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with patch.object(app.subprocess, "run", side_effect=fake_run):
            result = app._run_codex("PROMPT", "gpt-5.4-mini", 1.0)

        self.assertEqual(result, "result text")
        self.assertEqual(captured["cmd"][:3], ["codex", "exec", "--model"])
        self.assertIn("gpt-5.4-mini", captured["cmd"])
        self.assertEqual(captured["input"], "PROMPT")

    def test_synthesize_mp3_invokes_voicevox_script(self) -> None:
        captured = {}

        def fake_run(cmd, input, text, capture_output, timeout, check):
            captured["cmd"] = cmd
            captured["input"] = input
            Path(cmd[3]).write_bytes(b"mp3")
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = Path(tmpdir) / "out.mp3"
            with patch.object(app.subprocess, "run", side_effect=fake_run):
                app.synthesize_mp3("原稿", out_path, 1.0)

        self.assertEqual(captured["cmd"][1].endswith("voicevox_tts.py"), True)
        self.assertEqual(captured["input"], "原稿")

    def test_main_auto_names_and_tts(self) -> None:
        calls = []

        def fake_fetch_html(url, timeout, max_bytes=app.MAX_HTML_BYTES):
            self.assertEqual(url, "https://example.com/article")
            return "<html><body><article><p>本文</p></article></body></html>"

        def fake_generate_title(article_text, model, timeout):
            self.assertEqual(article_text, "本文")
            return "短い記事"

        def fake_convert(article_text, model, timeout):
            self.assertEqual(article_text, "本文")
            return "読み上げ原稿"

        def fake_synthesize(script_text, output_path, timeout):
            calls.append((script_text, output_path))
            output_path.write_bytes(b"mp3")

        class FixedDatetime:
            @classmethod
            def now(cls):
                from datetime import datetime

                return datetime(2026, 6, 20, 14, 30, 12)

        with tempfile.TemporaryDirectory() as tmpdir:
            args = SimpleNamespace(
                url="https://example.com/article",
                output_dir=Path(tmpdir),
                model="gpt-5.4-mini",
                timeout=30.0,
                script_only=False,
                raw_output=None,
                script_output=None,
                tts_output=None,
            )
            buffer = io.StringIO()
            with patch.object(app, "parse_args", return_value=args), patch.object(
                app, "fetch_html", side_effect=fake_fetch_html
            ), patch.object(app, "extract_article_text", return_value="本文"), patch.object(
                app, "generate_title", side_effect=fake_generate_title
            ), patch.object(app, "convert_to_script", side_effect=fake_convert), patch.object(
                app, "synthesize_mp3", side_effect=fake_synthesize
            ), patch.object(app, "datetime", FixedDatetime), redirect_stdout(buffer):
                rc = app.main()

            self.assertEqual(rc, 0)
            self.assertEqual(len(calls), 1)
            script_text, output_path = calls[0]
            self.assertEqual(script_text, "読み上げ原稿")
            self.assertTrue(output_path.name.endswith(".mp3"))
            self.assertEqual(output_path.read_bytes(), b"mp3")
            self.assertEqual(buffer.getvalue().strip(), str(output_path))

            raw_path = Path(tmpdir) / "20260620-143012-短い記事.raw.txt"
            script_path = Path(tmpdir) / "20260620-143012-短い記事.script.txt"
            self.assertEqual(raw_path.read_text(encoding="utf-8"), "本文")
            self.assertEqual(script_path.read_text(encoding="utf-8"), "読み上げ原稿")

    def test_main_script_only_skips_tts(self) -> None:
        calls = []

        def fake_synthesize(script_text, output_path, timeout):
            calls.append((script_text, output_path))

        with tempfile.TemporaryDirectory() as tmpdir:
            args = SimpleNamespace(
                url="https://example.com/article",
                output_dir=Path(tmpdir),
                model="gpt-5.4-mini",
                timeout=30.0,
                script_only=True,
                raw_output=None,
                script_output=None,
                tts_output=None,
            )
            with patch.object(app, "parse_args", return_value=args), patch.object(
                app, "fetch_html", return_value="<html><body><article><p>本文</p></article></body></html>"
            ), patch.object(app, "extract_article_text", return_value="本文"), patch.object(
                app, "generate_title", return_value="短い記事"
            ), patch.object(app, "convert_to_script", return_value="読み上げ原稿"), patch.object(
                app, "synthesize_mp3", side_effect=fake_synthesize
            ):
                rc = app.main()

            self.assertEqual(rc, 0)
            self.assertEqual(calls, [])
            self.assertFalse(list(Path(tmpdir).glob("*.mp3")))


if __name__ == "__main__":
    unittest.main()
