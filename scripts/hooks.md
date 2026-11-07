# 作り手の hook の設定の書き方

この文書は、夜と昼の無人の仕事で使う Claude Code の hook 2 つの設定の書き方を書く。hook は作り手の運用の物で、公開のプラグインには入れない。

設定は、レーンの作業場ごとに `.claude/settings.local.json` に置く。このファイルは git に入れない。公開の `.claude/settings.json` には置かない。外の人がこのリポジトリを clone して Claude Code を開いたときに、作り手の hook が動かないようにするためである。

禁止語の pre-push (`.githooks/pre-push`) と鍵の検査の pre-commit (`.githooks/pre-commit`。gitleaks でステージした差分を見る) は git の hook なので、この文書の対象に入らない。どちらも `git config core.hooksPath .githooks` を打った手元だけで動く (pre-commit は gitleaks が無い端末では知らせだけ出して通す)。

## 置く順

作業場で Claude Code を 1 度起こし、「このフォルダを信頼するか」の問いを通す。次に、許しの一覧 `docs/nights/<日付>.allow` と `.claude/settings.local.json` を作業場に置く。最後に無人で起こす。許しの一覧の形は `scripts/night_guard.py` の頭のコメントに書いてある。

## 登録する hook

| event | matcher | command (1 行) | timeout (秒) |
|---|---|---|---|
| `PreToolUse` | `Write\|Edit` (JSON の中では縦棒をそのまま書く) | `uv run --no-project python <作業場の絶対パス>/scripts/night_guard.py` | 書かない (既定の 600 で足りる) |
| `Stop` | 書かない (Stop は matcher を使わない) | `uv run --no-project python <作業場の絶対パス>/scripts/night_log.py` | `900` |

設定の鍵は、外から順に `hooks`、event の名前、matcher の組の配列、hook の配列と並ぶ。matcher の組は `matcher` と `hooks` の鍵を持つ。hook の配列の 1 つは `type` に `command` を、`command` に上の表の 1 行を、要るときだけ `timeout` に秒の数を持つ。hook 以外の鍵 (permissions など) はこの文書では扱わない。

`command` には shell の構文 (パイプ・`&&`・変数) を使わない。script の道は作業場の絶対パスで書く。hook は Claude Code の今の作業のフォルダで起動するので、相対の道は会話の途中の `cd` で外れる。`${CLAUDE_PROJECT_DIR}` で書くこともできるが、その場合は shell の変数になるので二重引用符で囲む必要がある。

`--no-project` を付けると、uv は作業場の venv を作らずに python を起こす。2 つの script は標準の library だけで書いてあるので venv は要らない。新しい作業場の最初の Write で venv を作り始めて時間切れになることも防げる。

## event を選んだ理由

夜の記録の hook には `Stop` を選んだ。`Stop` は Claude が返事を終えるたびに起きる。無人のレーンが最後の返事を終えたときにも 1 回起きる。

`SessionEnd` は選ばなかった。`SessionEnd` の hook は、`timeout` を書いても全体の持ち時間が 60 秒までしか伸びず、pytest を回すと間に合わないおそれがある。`SessionEnd` は会話を閉じたときに起きるので、開いたままの会話では閉じるまで起きない。

`Stop` は返事のたびに起きるので、無人のレーンが何度か返事を終えると記録の行が何本か増える。行には hook の印 `[hook night_log]` が付くので、レーンが自分で書いた行と見分けられる。`night_log.py` は、入力の `stop_hook_active` が true のときは何もしない。いつも 0 で終わるので、会話を止めることもない。`night_log.py` の中の pytest は 840 秒で打ち切り、その場合は「回せなかった」と書く。hook の `timeout` の 900 秒より短くしてある。

## 止め方と、hook が動かなかったとき

`night_guard.py` は、止めるときに終わりの番号 2 で終わり、理由を標準エラーに日本語で出す。Claude Code は終わりの番号 2 を `permissionDecision` の `deny` と同じに扱い、標準エラーの文を Claude に渡す。

hook が起動できないとき (uv が無い・道を書き違えた) や時間切れのときは、既定では Write と Edit はそのまま通る。そのときも止めたいなら、hook に `onFailure` の鍵を足して `block` を入れる (Claude Code v2.1.295 から)。足すと、uv が無い作業場では全部の Write と Edit が止まる。足すかどうかは置く人が決める。

この hook が見るのは Write と Edit だけである。Bash の道具でファイルを書く道は止めない。

## Claude Code の文書で確かめた事 (2026-11-02 に読んだ)

出典は Claude Code の Hooks reference (https://code.claude.com/docs/en/hooks) である。

| 確かめた事 | 文書に書いてあった事 |
|---|---|
| 設定の置き場 | `.claude/settings.local.json` は 1 つのプロジェクトの設定で、共有しない |
| 標準入力の JSON | どの event にも `session_id`・`transcript_path`・`cwd`・`hook_event_name` が入る。PreToolUse には `tool_name`・`tool_input`・`tool_use_id` が入り、Write と Edit の `tool_input.file_path` はいつも絶対の道 |
| Stop の入力 | `stop_hook_active` (Stop の hook のせいで続いているときに true)・`last_assistant_message` などが入る |
| SessionEnd | 会話の終わりに起き、止められない。持ち時間は既定 1.5 秒で、hook ごとの `timeout` で 60 秒まで伸びる。環境変数 `CLAUDE_CODE_SESSIONEND_HOOKS_TIMEOUT_MS` でも変えられる |
| 止める終わりの番号 | 2 で止まり、標準エラーの文が理由になる。PreToolUse では `deny` と同じ扱い |
| timeout の鍵 | 鍵の名前は `timeout`、単位は秒。command の hook の既定は 600 秒 |
| command の起こし方 | `args` が無いと `command` を shell (macOS と Linux は `sh -c`) に渡す。`args` があると shell を通さずに `command` を直に起こす |
| matcher | `Write\|Edit` で Write と Edit の両方に当たる。matcher を使わない event に書いた matcher は黙って無視される |

この文書の設定は `args` を使わず、shell の構文を含まない `command` の 1 行で書く。
