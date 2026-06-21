# Web to TTS Script

Web page URL を受け取り、本文を抽出して Codex CLI で読み上げ用日本語原稿に変換し、必要なら VOICEVOX で MP3 まで生成する CLI です。

## `web_to_tts_script.py`

Web ページを 1 本ずつ処理して、抽出本文、読み上げ原稿、MP3 を自動生成します。`voicevox_tts.py` は内部で呼ばれます。

### 使い方

```bash
python3 web_to_tts_script.py https://example.com/article
python3 web_to_tts_script.py https://example.com/article --voicevox-args --speaker 3
```

既定では、次の 3 つを `日時-短い記事タイトル` ベースで出力します。

- `.raw.txt`: 抽出した本文
- `.script.txt`: Codex 変換後の読み上げ原稿
- `.mp3`: VOICEVOX で生成した音声

### オプション

- `--script-only`: 原稿生成までで止める
- `--output-dir DIR`: 自動生成ファイルの出力先
- `--voicevox-args ...`: `voicevox_tts.py` に渡す追加オプション
- `--model MODEL`: Codex のモデル指定
- `--timeout SEC`: fetch と Codex 呼び出しのタイムアウト。既定は 300 秒

### 例

```bash
python3 web_to_tts_script.py --script-only https://example.com/article
python3 web_to_tts_script.py --output-dir out https://example.com/article
python3 web_to_tts_script.py https://example.com/article --voicevox-args --speaker 3
```

### 前提

- `codex` CLI が利用可能であること
- `readability-lxml` が利用可能であること
- `beautifulsoup4` と `lxml` が利用可能であること
- `voicevox_tts.py` の MP3 生成には `ffmpeg` が必要であること

## `voicevox_tts.py`

VOICEVOX エンジンにテキストを渡して、単体で音声合成するための CLI です。`web_to_tts_script.py` からも MP3 生成に使われますが、直接実行して読み上げ確認や個別ファイル生成にも使えます。

### 使い方

```bash
python3 voicevox_tts.py "こんにちは"
echo "こんにちは" | python3 voicevox_tts.py
python3 voicevox_tts.py --speaker 3 --output out.mp3 "今日は晴れです"
```

`voicevox_tts.py` は、引数か stdin のどちらかでテキストが必要です。TTY から引数なしで起動すると、入力方法を案内して終了します。
`--output` を省略した場合は、一時 MP3 を作成してそのパスを表示します。

### 入力

- 引数で `text` を渡すか、stdin から流し込む必要がある
- 何も渡さず TTY から起動すると、入力方法を案内して終了する
- 空白だけの入力は、正規化後に空として扱われる

### オプション

- `--base-url URL`: VOICEVOX エンジンの URL を変更する
- `--speaker ID`: 話者/style を指定する
- `--output PATH`: MP3 の出力先を指定する
- `--list-speakers`: 利用可能な話者/style を一覧表示する

### VOICEVOX

`voicevox_tts.py` は起動時に VOICEVOX エンジンを確認します。見つからない場合は、次の順で起動を試みます。

1. `voicevox` コマンドが PATH にあればそれを起動する
2. なければ `~/.voicevox/VOICEVOX.AppImage` を起動する

どちらも見つからない場合は、起動元が見つからない旨を表示して終了します。
