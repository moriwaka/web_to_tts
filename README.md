# Web to TTS Script

Web page URL を受け取り、本文を抽出して Codex CLI で読み上げ用日本語原稿に変換し、既定では MP3 まで生成する CLI です。

`voicevox_tts.py` は VOICEVOX エンジンにテキストを渡して、単体で音声合成するための CLI です。`web_to_tts_script.py` からも MP3 生成に使われますが、直接実行して読み上げ確認や個別ファイル生成にも使えます。

## 使い方

```bash
python3 web_to_tts_script.py https://example.com/article
python3 web_to_tts_script.py https://example.com/article --voicevox-args --speaker 3 --pause-mora-scale 0.5
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
python3 voicevox_tts.py --pause-mora-scale 0.5 "クロード・コード"
```

`voicevox_tts.py` は、引数か stdin のどちらかでテキストが必要です。TTY から引数なしで起動すると、入力方法を案内して終了します。

## オプション

- `--script-only`: 原稿生成までで止める
- `--output-dir DIR`: 自動生成ファイルの出力先
- `--raw-output PATH`: 抽出本文の出力先を明示する
- `--script-output PATH`: 原稿の出力先を明示する
- `--tts-output PATH`: MP3 の出力先を明示する
- `--voicevox-args ...`: `voicevox_tts.py` に渡す追加オプション
- `--model MODEL`: Codex のモデル指定
- `--timeout SEC`: fetch と Codex 呼び出しのタイムアウト

## 前提

- `codex` CLI が利用可能であること
- `readability-lxml` が利用可能であること
- `voicevox_tts.py` の MP3 生成には `ffmpeg` が必要であること
- VOICEVOX が未起動でも自動起動を試みること
- `voicevox` コマンドが PATH にあるか、なければ `~/.voicevox/VOICEVOX.AppImage` があること

## VOICEVOX

`voicevox_tts.py` は起動時に VOICEVOX エンジンを確認します。見つからない場合は、次の順で起動を試みます。

1. `voicevox` コマンドが PATH にあればそれを起動する
2. なければ `~/.voicevox/VOICEVOX.AppImage` を起動する

`--pause-mora-scale` を指定すると、`audio_query` の `accent_phrases[*].pause_mora.vowel_length` をまとめて縮めたり伸ばしたりできます。`1.0` が既定値で、`0.5` なら半分、`0` なら間を消す方向になります。

どちらも見つからない場合は、起動元が見つからない旨を表示して終了します。

## 入力

- 引数で `text` を渡すか、stdin から流し込む必要がある
- 何も渡さず TTY から起動すると、入力方法を案内して終了する
- 空白だけの入力は、正規化後に空として扱われる

## 例

```bash
python3 web_to_tts_script.py --script-only https://example.com/article
python3 web_to_tts_script.py --output-dir out https://example.com/article
python3 web_to_tts_script.py --tts-output out/news.mp3 https://example.com/article
python3 web_to_tts_script.py https://example.com/article --voicevox-args --speaker 3
```
