# Web to TTS Script

Web page URL を受け取り、本文を抽出して Codex CLI で読み上げ用日本語原稿に変換し、既定では MP3 まで生成する CLI です。

`voicevox_tts.py` は VOICEVOX エンジンにテキストを渡して、単体で音声合成するための CLI です。`web_to_tts_script.py` からも MP3 生成に使われますが、直接実行して読み上げ確認や個別ファイル生成にも使えます。

## 使い方

```bash
python3 web_to_tts_script.py https://example.com/article
```

既定では、次の3つを `日時-短い記事タイトル` ベースで出力します。

- `.raw.txt`: 抽出した本文
- `.script.txt`: Codex 変換後の読み上げ原稿
- `.mp3`: VOICEVOX で生成した音声

`voicevox_tts.py` 単体の例:

```bash
python3 voicevox_tts.py "こんにちは"
echo "こんにちは" | python3 voicevox_tts.py
python3 voicevox_tts.py --speaker 3 --output out.mp3 "今日は晴れです"
```

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
- `voicevox_tts.py` の MP3 生成を使う場合は `ffmpeg` が必要で、VOICEVOX は未起動なら自動起動する
- `voicevox` コマンドが PATH にあればそれを起動し、なければ `~/.voicevox/VOICEVOX.AppImage` を起動する

## 例

```bash
python3 web_to_tts_script.py --script-only https://example.com/article
python3 web_to_tts_script.py --output-dir out https://example.com/article
python3 web_to_tts_script.py --tts-output out/news.mp3 https://example.com/article
```
