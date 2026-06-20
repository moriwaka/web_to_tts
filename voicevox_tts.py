#!/usr/bin/env python3
"""Speak text through a local VOICEVOX engine.

Usage examples:
  python3 voicevox_tts.py "こんにちは"
  echo "こんにちは" | python3 voicevox_tts.py
  python3 voicevox_tts.py --speaker 3 --output out.mp3 "今日は晴れです"
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen


DEFAULT_BASE_URL = "http://127.0.0.1:50021"
DEFAULT_TIMEOUT = 300.0
MAX_REQUEST_TEXT_LEN = 2000
HIRAGANA_START = 0x3040
HIRAGANA_END = 0x309F
KANJI_START = 0x4E00
KANJI_END = 0x9FFF


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read text aloud using a local VOICEVOX engine."
    )
    parser.add_argument(
        "text",
        nargs="*",
        help="Text to read aloud. If omitted, read from stdin.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"VOICEVOX engine base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--speaker",
        type=int,
        default=29,
        help="Speaker/style id passed to VOICEVOX (default: 29, No.7 normal).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the synthesized MP3 to this file.",
    )
    parser.add_argument(
        "--list-speakers",
        action="store_true",
        help="List speakers/styles exposed by the engine and exit.",
    )
    parser.add_argument(
        "--speed-scale",
        type=float,
        help="Override query.speedScale.",
    )
    parser.add_argument(
        "--pitch-scale",
        type=float,
        help="Override query.pitchScale.",
    )
    parser.add_argument(
        "--intonation-scale",
        type=float,
        help="Override query.intonationScale.",
    )
    parser.add_argument(
        "--volume-scale",
        type=float,
        help="Override query.volumeScale.",
    )
    parser.add_argument(
        "--pre-phoneme-length",
        type=float,
        help="Override query.prePhonemeLength.",
    )
    parser.add_argument(
        "--post-phoneme-length",
        type=float,
        help="Override query.postPhonemeLength.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_TIMEOUT,
        help=f"HTTP timeout in seconds for each VOICEVOX request (default: {DEFAULT_TIMEOUT}).",
    )
    return parser.parse_args()


def read_text(args: argparse.Namespace) -> str:
    if args.text:
        return " ".join(args.text).strip()
    if sys.stdin.isatty():
        raise SystemExit("text is required when stdin is a TTY")
    return sys.stdin.read().strip()


def split_text_for_voicevox(text: str, max_len: int = MAX_REQUEST_TEXT_LEN) -> list[str]:
    """Split text into request-sized chunks with best-effort natural boundaries."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[str] = []
    start = 0
    text_len = len(normalized)

    while start < text_len:
        end = min(start + max_len, text_len)
        if end == text_len:
            chunks.append(normalized[start:end].strip())
            break

        cut = find_best_split_point(normalized, start, end)
        if cut <= start:
            cut = end

        chunk = normalized[start:cut].strip()
        if chunk:
            chunks.append(chunk)
        start = cut

    return chunks


def find_best_split_point(text: str, start: int, end: int) -> int:
    """Return the best cut position in [start, end]."""
    sentence_cut = -1
    script_cut = -1
    last_non_space = -1

    for idx in range(start, end):
        ch = text[idx]
        if not ch.isspace():
            last_non_space = idx + 1

        if ch in "。．.!?！？、,":
            sentence_cut = idx + 1
            continue

        if idx > start and is_hiragana(text[idx - 1]) and is_kanji(ch):
            script_cut = idx

    if sentence_cut != -1:
        return sentence_cut
    if script_cut != -1:
        return script_cut
    if last_non_space != -1:
        return last_non_space
    return end


def is_hiragana(ch: str) -> bool:
    code = ord(ch)
    return HIRAGANA_START <= code <= HIRAGANA_END


def is_kanji(ch: str) -> bool:
    code = ord(ch)
    return KANJI_START <= code <= KANJI_END


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def request_json(
    url: str,
    *,
    method: str = "GET",
    params: dict[str, Any] | None = None,
    body: Any | None = None,
    timeout: float,
) -> Any:
    full_url = url
    if params:
        query = urlencode(params, doseq=True)
        separator = "&" if "?" in full_url else "?"
        full_url = f"{full_url}{separator}{query}"

    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method.upper() == "POST":
        data = b""

    req = Request(full_url, data=data, headers=headers, method=method)
    try:
        with urlopen(req, timeout=timeout) as response:
            payload = response.read()
            content_type = response.headers.get_content_type()
            if content_type == "application/json":
                return json.loads(payload.decode("utf-8"))
            return payload
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"VOICEVOX request failed: HTTP {exc.code} {exc.reason}: {detail}") from exc
    except URLError as exc:
        raise SystemExit(f"VOICEVOX request failed: {exc.reason}") from exc


def apply_query_overrides(query: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    overrides = {
        "speedScale": args.speed_scale,
        "pitchScale": args.pitch_scale,
        "intonationScale": args.intonation_scale,
        "volumeScale": args.volume_scale,
        "prePhonemeLength": args.pre_phoneme_length,
        "postPhonemeLength": args.post_phoneme_length,
    }
    for key, value in overrides.items():
        if value is not None:
            query[key] = value
    return query


def fetch_audio_query(base_url: str, text: str, speaker: int, timeout: float) -> dict[str, Any]:
    url = urljoin(f"{base_url}/", "audio_query")
    return request_json(
        url,
        method="POST",
        params={"text": text, "speaker": speaker},
        timeout=timeout,
    )


def synthesize_audio(
    base_url: str, speaker: int, query: dict[str, Any], timeout: float
) -> bytes:
    url = urljoin(f"{base_url}/", "synthesis")
    return request_json(
        url,
        method="POST",
        params={"speaker": speaker},
        body=query,
        timeout=timeout,
    )


def connect_waves(base_url: str, waves: list[bytes], timeout: float) -> bytes:
    url = urljoin(f"{base_url}/", "connect_waves")
    payload = [base64.b64encode(wave).decode("ascii") for wave in waves]
    return request_json(url, method="POST", body=payload, timeout=timeout)


def list_speakers(base_url: str, timeout: float) -> list[dict[str, Any]]:
    url = urljoin(f"{base_url}/", "speakers")
    speakers = request_json(url, timeout=timeout)
    if not isinstance(speakers, list):
        raise SystemExit("unexpected /speakers response")
    return speakers


def write_output(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg is required to generate mp3 output")

    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            ffmpeg,
            "-y",
            "-i",
            str(wav_path),
            "-af",
            "pan=stereo|c0=c0|c1=c0",
            "-codec:a",
            "libmp3lame",
            "-q:a",
            "4",
            str(mp3_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


def print_speakers(speakers: list[dict[str, Any]]) -> None:
    for speaker in speakers:
        name = speaker.get("name", "<unknown>")
        print(name)
        for style in speaker.get("styles", []):
            print(f"  {style.get('id')}: {style.get('name', '<unknown>')}")


def main() -> int:
    args = parse_args()
    base_url = normalize_base_url(args.base_url)

    if args.list_speakers:
        print_speakers(list_speakers(base_url, args.timeout))
        return 0

    text = read_text(args)
    if not text:
        raise SystemExit("text is empty")

    chunks = split_text_for_voicevox(text)
    if not chunks:
        raise SystemExit("text is empty after normalization")

    audio_chunks: list[bytes] = []

    for chunk in chunks:
        query = fetch_audio_query(base_url, chunk, args.speaker, args.timeout)
        query = apply_query_overrides(query, args)
        audio_chunks.append(synthesize_audio(base_url, args.speaker, query, args.timeout))

    audio = audio_chunks[0] if len(audio_chunks) == 1 else connect_waves(
        base_url, audio_chunks, args.timeout
    )

    output_path = args.output
    temp_output = None
    if output_path is None:
        fd, temp_name = tempfile.mkstemp(suffix=".mp3", prefix="voicevox_")
        os.close(fd)
        temp_output = Path(temp_name)
        output_path = temp_output

    fd, temp_name = tempfile.mkstemp(suffix=".wav", prefix="voicevox_")
    tempfile_path = Path(temp_name)
    os.close(fd)

    try:
        write_output(tempfile_path, audio)
        convert_wav_to_mp3(tempfile_path, output_path)
        if temp_output is not None:
            print(output_path)
    finally:
        if tempfile_path.exists():
            tempfile_path.unlink(missing_ok=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
