# Web to TTS Script

Web page URL を受け取り、本文を抽出して Codex CLI で読み上げ用日本語原稿に変換し、既定では MP3 まで生成する CLI です。

## 使い方

```bash
python3 web_to_tts_script.py https://example.com/article
```

既定では、次の3つを `日時-短い記事タイトル` ベースで出力します。

- `.raw.txt`: 抽出した本文
- `.script.txt`: Codex 変換後の読み上げ原稿
- `.mp3`: VOICEVOX で生成した音声

## オプション

- `--script-only`: 原稿生成までで止める
- `--output-dir DIR`: 自動生成ファイルの出力先
- `--raw-output PATH`: 抽出本文の出力先を明示する
- `--script-output PATH`: 原稿の出力先を明示する
- `--tts-output PATH`: MP3 の出力先を明示する
- `--model MODEL`: Codex のモデル指定
- `--timeout SEC`: fetch と Codex 呼び出しのタイムアウト

## 前提

- `codex` CLI が利用可能であること
- `readability-lxml` が利用可能であること
- `voicevox_tts.py` の MP3 生成を使う場合は `ffmpeg` と VOICEVOX エンジンが利用可能であること

## 例

```bash
python3 web_to_tts_script.py --script-only https://example.com/article
python3 web_to_tts_script.py --output-dir out https://example.com/article
python3 web_to_tts_script.py --tts-output out/news.mp3 https://example.com/article
```
