# Claude Code から使う (プラグインと MCP)

読み手: Claude Code を使える方。プラグインの命令と、手元の Claude Code から会社のシステムを操作する口を書く。入口は [README](../README.md)。

## Claude Code のプラグイン

Claude Code が使える方は、プラグインの命令で会社のシステムを立てる。`/seizou:build` は問いを 3 つ聞く (会社の名前と業種・部品マスターに足したい項目・生産計画を立てているか)。答えると、上の Python の命令 `seizou new` と同じ会社の置き場ができる。答えの YAML を渡せば、問いは飛ばせる。
`seizou new` (と `/seizou:build`) は会社の置き場に、Claude Code が置き場を開いたときに読む `CLAUDE.md` (AI が守る決まりと、変えた後に `seizou_check` と `/simplify` を回す流れ) と、その短い版の `AGENTS.md` も書く。それより前に作った置き場には、`seizou ai-docs <置き場>` で後から足せる (書き直すときは `--force`)。`seizou new` と `seizou ai-docs` は、置き場に `.gitignore` が無ければ、鍵 (`.env`) とデータベースと上げたファイルと控えを git に入れない `.gitignore` も書く。点検 (`seizou_doctor`) は、CLAUDE.md と AGENTS.md の無い置き場に `seizou ai-docs` を勧める 1 行を出す。

プラグインの入れ方: `git clone --branch v0.1.0 https://github.com/kishii-py/seizou.git` と `claude --plugin-dir seizou/plugin` を 1 行ずつ打つと (Windows の PowerShell でも同じ 2 行)、プラグインを読み込んだ Claude Code の会話が開く (読み込まれるのは、その会話の間だけ。skill 4 つ・hook 1 つ・MCP サーバー 1 つが入る)。開いた会話で `/seizou:build` を打つ。`/seizou:build` は、uv が無ければ公式の版つきの入れ方 (`curl -LsSf https://astral.sh/uv/0.13.5/install.sh | sh`。Windows は `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/0.13.5/install.ps1 | iex"`) で入れ、uv が 0.11.0 より古ければ `uv self update` を打ち、clone した `seizou` の中で `uv sync --locked` を打って部品を入れる (PATH を変える段は要らない)。11-05 に作者の Mac で、`claude --plugin-dir seizou/plugin plugin details seizou` の出力にこの 3 種類が並ぶことを確かめた。

| 命令 | 何をするか | 確認のテスト |
|---|---|---|
| `/seizou:build` | 質問に答えると、その会社専用の業務システムが手元の PC にできて動き出す | `tests/test_plugin_build.py` |
| `/seizou:connect` | 取引先と注文のデータを送り合えるように、取引先とのつながり (相手の窓口の URL・鍵の置き場の名前・向き) を登録する | `tests/test_plugin_skills.py` |
| `/seizou:fix` | 決まりの検査の違反を、会社の置き場の側だけで直し、違反が 0 件になるまで検査を回し直す | `tests/test_plugin_fix.py` |
| `/seizou:manual` | 画面ごとの使い方を、日本語の説明書として会社の置き場の `manual/` に書き起こす。PDF の冊子も作る | `tests/test_plugin_skills.py` |
| hook (`plugin/hooks/guard_core.py`) | 会社の置き場から開いた会話で、AI が部品 (seizou) の中身を Write・Edit・NotebookEdit で書き換えるのを止める (R20)。Bash の道具での書き込みは見ない。働くのは `claude --plugin-dir` で開いた会話だけで、置き場の `.claude/settings.json` には hook を書かない (置き場で普通に `claude` を開いたときの守りは CLAUDE.md の文だけ) | `tests/test_plugin_guard_core.py` |

## 手元の Claude Code から操作する (MCP)

会社の置き場で `manage.py seizou_mcp` を打つと、MCP サーバー (Claude Code が seizou の機能を道具として呼び出すための窓口。標準入力と標準出力でやり取りする) が動き出す。手元の Claude Code に「今週納期の注文残は」「この明細を 5 個出荷して」「めっきの前処理の手順書は」と頼むと、Claude Code が会社のシステムの道具を呼んで読み書きする。業務マニュアルも探して読める。道具は部品の services と読むだけの関数を通るので、画面から入れたときと同じ決まりで書かれる。

起動のしかた: プラグインの `plugin/.mcp.json` が起動用の script `plugin/mcp/start.py` を `uv run --no-project python` で呼ぶ。この script は、`seizou new` と `seizou ai-docs` が `~/.seizou/sites.json` に登録した置き場 (Claude Code を開いたフォルダがその 1 つのとき) だけで、登録した python で `manage.py seizou_mcp` を打つ。登録の無いフォルダでは起動せず、登録の仕方を 1 行で知らせる (開いただけのフォルダの中の物を動かさないため)。プラグインを使わないときは、次の 1 行で登録する (`<python>` は置き場の `README.md` の起動の 1 行の python。Windows では `<seizou を clone した場所>\.venv\Scripts\python.exe` の形)。

```
claude mcp add seizou -- <python> <置き場>/manage.py seizou_mcp --user <利用者名>
```

利用者と約束は次のとおり。

- 読む道具は利用者を決めなくても使える。
- 書く道具 (データを書き込む 3 つ = `create_order`・`ship`・`plan_step`) は `--user <利用者名>` か、置き場の `.env` の `SEIZOU_MCP_USER=<利用者名>` で利用者を決めたときだけ書く。決めていなければ、断りの文を返す。`--read-only` で起動すると、書く道具を出さない。
- 書く道具は画面と同じ決まりとポカよけを通る。お金と数の決まり (RULES.md) と、止める設定のポカよけに当たると、番号と文を返して書かない。知らせる設定に当たると、`confirm=true` が無ければ知らせの文を返して書かない。人が知らせを確かめてから `confirm=true` で呼び直すと、保存する。
- 数と金額は数値型でなく文字列 (例: "4800.00") で返す (小数の丸めの誤差を避けるため。受け取る側がそのまま Decimal に戻せる)。URL はホストとポートを付けないパス (`/orders/1/` の形) で返す。
- 受入と入金の金額 (`list_receipts`・`list_payments`) は、決めた利用者の金額の閲覧の判定に従う。判定を有効にした会社で、閲覧の無い利用者のときと、利用者を決めずに起動したときは「—」で返す。

| 道具 | 何をするか | 読む / 書く |
|---|---|---|
| `company_info` | 会社の名前・選べる設定の今の値と出所・入っている部品を返す | 読む |
| `list_screens` | 画面の目録 (名前・1 文・URL) を返す | 読む |
| `search_items` | 部品を品番か品名の語で探す | 読む |
| `get_item` | 部品の 1 件を、部品の取引先と標準の工程つきで返す | 読む |
| `list_orders` | 受注の一覧を、明細ごとの出荷済みと残 (注文残) つきで返す | 読む |
| `get_order` | 受注の 1 件を、明細ごとの出荷と指示書つきで返す | 読む |
| `list_workorders` | 指示書の一覧を、工程ごとの予定の日と実績の数つきで返す | 読む |
| `list_shipments` | 出荷の一覧を、出荷日の新しい順に返す | 読む |
| `get_manual` | 使い方 (画面ごとの説明書) の目次か、1 画面の本文を返す | 読む |
| `plan_board` | 工程の 1 週の生産計画の盤 (日を置いた予定と未計画の予定) を返す | 読む |
| `list_rules` | ポカよけの今の設定 (止める・知らせる・通す) と、書き方の決まりの一覧を返す | 読む |
| `search_manuals` | 業務マニュアル (会社の人が書いた作業の手順書と社内の文書) を題と本文の言葉で探す。画面ごとの使い方 (`get_manual`) とは別 | 読む |
| `get_manual_body` | 業務マニュアルの 1 件の本文を文字だけ (画像なし) で返す (8000 字まで。`search_manuals` の番号で引く) | 読む |
| `list_receipts` | 受入 (協力企業から受け入れた物の記録。発注側) の一覧を、検収を送った日時つきで返す。金額は金額の閲覧の判定に従う | 読む |
| `list_acceptances` | 検収 (得意先の seizou からの受け入れた数の知らせ。受注側) の一覧を返す。受入 (`list_receipts`) とは別 | 読む |
| `list_payments` | 入金の一覧と、請求書番号を渡したときはその請求書の残 (合計・入金済み・残・入金の状態) を返す。金額は金額の閲覧の判定に従う | 読む |
| `list_feedback` | フィードバック (困りごと・要望・不具合の下書き) の一覧を返す | 読む |
| `get_feedback` | フィードバックの 1 件の本文と、Issue に写す文 (伏せ字つき) を返す | 読む |
| `create_order` | 受注を 1 件作る | 書く |
| `ship` | 受注の明細を出荷する | 書く |
| `plan_step` | 指示書の工程に、生産計画の開始日と終了日を置く | 書く |

API の鍵は要らない。道具は手元のデータベースを読み書きするだけで、外の API を呼ばない。ただし、道具の結果 (業務のデータと社員のログイン ID を含む) は Claude Code の会話に入る (SECURITY.md の「AI に送るデータ」)。

Claude Desktop から使う: 端末 (shell) を開かずに使う方法。主に使うのは Claude Desktop の Code タブで、Claude Code と同じようにプラグイン (`/seizou:build`・hook・`plugin/.mcp.json` の MCP) がそのまま使える (Code タブは有料のプランが要る)。Chat から使うときは、`claude_desktop_config.json` (Developer > Edit Config) の `mcpServers` に、上の 1 行と同じ `<python>` と `<置き場>/manage.py seizou_mcp --user <利用者名>` を絶対パスで登録する。Chat は開いたフォルダを持たないので、置き場を今のフォルダから探す起動用の script `plugin/mcp/start.py` は使えない。Chat には Claude Code の hook が無く、道具を呼ぶたびに承認のダイアログ (Allow once / Always allow) が出る。守りは MCP サーバーの側にあり、書く道具は画面と同じ決まりとポカよけを通り、知らせる設定のポカよけに当たったときは `confirm=true` が要る (上の「利用者と約束」)。この段落は公式の文書で確かめた形で、実機ではまだ通していない。
