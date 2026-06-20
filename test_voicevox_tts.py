from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
import base64
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from types import SimpleNamespace
from io import StringIO
from unittest.mock import patch

import voicevox_tts


class FakeHeaders:
    def __init__(self, content_type: str) -> None:
        self._content_type = content_type

    def get_content_type(self) -> str:
        return self._content_type


class FakeResponse:
    def __init__(self, body: bytes, content_type: str = "application/json") -> None:
        self._body = body
        self.headers = FakeHeaders(content_type)

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def fake_urlopen(request, timeout=0):
    parsed = urlparse(request.full_url)
    fake_urlopen.calls.append((parsed.path, request.data, request.full_url))
    if parsed.path.endswith("/audio_query"):
        params = parse_qs(parsed.query)
        body = {
            "accent_phrases": [],
            "speedScale": 1.0,
            "pitchScale": 0.0,
            "intonationScale": 1.0,
            "volumeScale": 1.0,
            "prePhonemeLength": 0.1,
            "postPhonemeLength": 0.1,
            "speaker": int(params["speaker"][0]),
            "text": params["text"][0],
        }
        return FakeResponse(json.dumps(body).encode("utf-8"))

    if parsed.path.endswith("/synthesis"):
        payload = json.loads(request.data.decode("utf-8"))
        fake_urlopen.last_query = payload
        marker = payload["accent_phrases"][0]["moras"][0]["text"] if payload["accent_phrases"] else "X"
        return FakeResponse(f"RIFF{marker}WAVE".encode("ascii"), content_type="audio/wav")

    if parsed.path.endswith("/speakers"):
        return FakeResponse(
            json.dumps(
                [
                    {
                        "name": "Test Speaker",
                        "styles": [{"id": 1, "name": "Normal"}],
                    }
                ]
            ).encode("utf-8")
        )

    if parsed.path.endswith("/connect_waves"):
        waves = json.loads(request.data.decode("utf-8"))
        fake_urlopen.last_connected_waves = waves
        combined = b"".join(base64.b64decode(wave) for wave in waves)
        return FakeResponse(combined, content_type="audio/wav")

    raise AssertionError(f"unexpected URL: {request.full_url}")


fake_urlopen.last_query = None
fake_urlopen.last_connected_waves = None
fake_urlopen.calls = []


def fake_convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    mp3_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path.write_bytes(b"ID3FAKE")


class VoiceVoxCliTest(unittest.TestCase):
    def setUp(self) -> None:
        fake_urlopen.last_query = None
        fake_urlopen.last_connected_waves = None
        fake_urlopen.calls = []

    def test_apply_query_overrides(self) -> None:
        query = {"speedScale": 1.0, "pitchScale": 0.0}

        class Args:
            speed_scale = 1.25
            pitch_scale = None
            intonation_scale = 1.5
            volume_scale = None
            pre_phoneme_length = 0.2
            post_phoneme_length = None

        updated = voicevox_tts.apply_query_overrides(query, Args())
        self.assertEqual(updated["speedScale"], 1.25)
        self.assertEqual(updated["pitchScale"], 0.0)
        self.assertEqual(updated["intonationScale"], 1.5)
        self.assertEqual(updated["prePhonemeLength"], 0.2)

    def test_default_speaker_is_no7(self) -> None:
        with patch("sys.argv", ["voicevox_tts.py", "hello"]):
            args = voicevox_tts.parse_args()
        self.assertEqual(args.speaker, 29)

    def test_resolve_voicevox_launcher_prefers_path_binary(self) -> None:
        with patch.object(voicevox_tts.shutil, "which", return_value="/usr/bin/voicevox"):
            self.assertEqual(voicevox_tts.resolve_voicevox_launcher(), ["/usr/bin/voicevox"])

    def test_resolve_voicevox_launcher_falls_back_to_appimage(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            home = Path(tmpdir)
            appimage = home / ".voicevox" / "VOICEVOX.AppImage"
            appimage.parent.mkdir(parents=True)
            appimage.write_text("")

            with patch.object(voicevox_tts.shutil, "which", return_value=None), patch.object(
                voicevox_tts.Path, "home", return_value=home
            ):
                self.assertEqual(voicevox_tts.resolve_voicevox_launcher(), [str(appimage)])

    def test_ensure_voicevox_running_launches_when_not_running(self) -> None:
        with patch.object(voicevox_tts, "probe_voicevox_engine", side_effect=[False, True]), patch.object(
            voicevox_tts, "launch_voicevox_engine"
        ) as launch_mock, patch.object(voicevox_tts.time, "sleep", return_value=None):
            voicevox_tts.ensure_voicevox_running("http://127.0.0.1:50021", timeout=1.0)

        self.assertEqual(launch_mock.call_count, 1)

    def test_split_text_prefers_punctuation(self) -> None:
        text = "第一文。第二文。第三文。"
        chunks = voicevox_tts.split_text_for_voicevox(text, max_len=6)
        self.assertTrue(all(len(chunk) <= 6 for chunk in chunks))
        self.assertEqual(chunks, ["第一文。", "第二文。", "第三文。"])

    def test_split_text_prefers_hiragana_to_kanji_boundary(self) -> None:
        text = "お知らせ更新しました"
        chunks = voicevox_tts.split_text_for_voicevox(text, max_len=6)
        self.assertTrue(all(len(chunk) <= 6 for chunk in chunks))
        self.assertEqual(chunks, ["お知らせ", "更新しました"])

    def test_split_text_hard_cuts_when_no_boundary_exists(self) -> None:
        text = "あ" * 4500
        chunks = voicevox_tts.split_text_for_voicevox(text, max_len=2000)
        self.assertTrue(all(len(chunk) <= 2000 for chunk in chunks))
        self.assertEqual([len(chunk) for chunk in chunks], [2000, 2000, 500])

    def test_convert_wav_to_mp3_duplicates_mono_to_stereo(self) -> None:
        calls = []

        def fake_run(cmd, check, capture_output, text):
            calls.append(cmd)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with tempfile.TemporaryDirectory() as tmpdir:
            wav_path = Path(tmpdir) / "in.wav"
            mp3_path = Path(tmpdir) / "out.mp3"
            wav_path.write_bytes(b"RIFFFAKE")

            with patch.object(voicevox_tts.shutil, "which", return_value="/usr/bin/ffmpeg"), patch.object(
                voicevox_tts.subprocess, "run", side_effect=fake_run
            ):
                voicevox_tts.convert_wav_to_mp3(wav_path, mp3_path)

        self.assertEqual(len(calls), 1)
        self.assertIn("-af", calls[0])
        self.assertIn("pan=stereo|c0=c0|c1=c0", calls[0])

    def test_end_to_end_synthesis(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "out.mp3"
            args = SimpleNamespace(
                base_url="http://127.0.0.1:50021",
                speaker=2,
                output=output_path,
                list_speakers=False,
                speed_scale=1.4,
                pitch_scale=None,
                intonation_scale=None,
                volume_scale=None,
                pre_phoneme_length=None,
                post_phoneme_length=None,
                timeout=30.0,
                text=["こんにちは"],
            )

            with patch.object(voicevox_tts, "parse_args", return_value=args), patch.object(
                voicevox_tts, "urlopen", side_effect=fake_urlopen
            ), patch.object(
                voicevox_tts, "convert_wav_to_mp3", side_effect=fake_convert_wav_to_mp3
            ):
                self.assertEqual(voicevox_tts.main(), 0)

            self.assertEqual(output_path.read_bytes(), b"ID3FAKE")
            self.assertEqual(fake_urlopen.last_query["speedScale"], 1.4)
            self.assertEqual(fake_urlopen.last_query["speaker"], 2)
            self.assertEqual(fake_urlopen.last_query["text"], "こんにちは")

    def test_main_auto_starts_voicevox_when_not_running(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "out.mp3"
            args = SimpleNamespace(
                base_url="http://127.0.0.1:50021",
                speaker=2,
                output=output_path,
                list_speakers=False,
                speed_scale=None,
                pitch_scale=None,
                intonation_scale=None,
                volume_scale=None,
                pre_phoneme_length=None,
                post_phoneme_length=None,
                timeout=30.0,
                text=["こんにちは"],
            )

            with patch.object(voicevox_tts, "parse_args", return_value=args), patch.object(
                voicevox_tts, "probe_voicevox_engine", side_effect=[False, True]
            ), patch.object(
                voicevox_tts, "launch_voicevox_engine"
            ) as launch_mock, patch.object(
                voicevox_tts, "urlopen", side_effect=fake_urlopen
            ), patch.object(
                voicevox_tts, "convert_wav_to_mp3", side_effect=fake_convert_wav_to_mp3
            ):
                self.assertEqual(voicevox_tts.main(), 0)

            self.assertEqual(launch_mock.call_count, 1)
            self.assertEqual(output_path.read_bytes(), b"ID3FAKE")

    def test_no_play_without_output_keeps_temp_file(self) -> None:
        args = SimpleNamespace(
            base_url="http://127.0.0.1:50021",
            speaker=1,
            output=None,
            list_speakers=False,
            speed_scale=None,
            pitch_scale=None,
            intonation_scale=None,
            volume_scale=None,
            pre_phoneme_length=None,
            post_phoneme_length=None,
            timeout=30.0,
            text=["こんにちは"],
        )

        buffer = StringIO()
        with patch.object(voicevox_tts, "parse_args", return_value=args), patch.object(
            voicevox_tts, "urlopen", side_effect=fake_urlopen
        ), patch.object(
            voicevox_tts, "convert_wav_to_mp3", side_effect=fake_convert_wav_to_mp3
        ), redirect_stdout(buffer):
            self.assertEqual(voicevox_tts.main(), 0)

        printed_path = Path(buffer.getvalue().strip())
        self.assertTrue(printed_path.exists())
        self.assertEqual(printed_path.read_bytes(), b"ID3FAKE")
        printed_path.unlink()

    def test_long_text_is_batched_and_connected(self) -> None:
        long_sentence = "あ" * 1800 + "。"
        text = long_sentence + ("い" * 1800) + "、" + ("う" * 1800) + "。"
        with tempfile.TemporaryDirectory() as tmpdir:
            args = SimpleNamespace(
                base_url="http://127.0.0.1:50021",
                speaker=1,
                output=Path(tmpdir) / "voicevox_test_out.mp3",
                list_speakers=False,
                speed_scale=None,
                pitch_scale=None,
                intonation_scale=None,
                volume_scale=None,
                pre_phoneme_length=None,
                post_phoneme_length=None,
                timeout=30.0,
                text=[text],
            )

            with patch.object(voicevox_tts, "parse_args", return_value=args), patch.object(
                voicevox_tts, "urlopen", side_effect=fake_urlopen
            ), patch.object(
                voicevox_tts, "convert_wav_to_mp3", side_effect=fake_convert_wav_to_mp3
            ):
                self.assertEqual(voicevox_tts.main(), 0)

            self.assertGreaterEqual(len(fake_urlopen.calls), 4)
            audio_query_texts = [
                parse_qs(urlparse(url).query)["text"][0]
                for path, _, url in fake_urlopen.calls
                if path.endswith("/audio_query")
            ]
            self.assertGreater(len(audio_query_texts), 1)
            self.assertTrue(all(len(chunk) <= 2000 for chunk in audio_query_texts))
            self.assertIsNotNone(fake_urlopen.last_connected_waves)
            combined_input = b"".join(base64.b64decode(w) for w in fake_urlopen.last_connected_waves)
            self.assertTrue(combined_input.startswith(b"RIFF"))


if __name__ == "__main__":
    unittest.main()
