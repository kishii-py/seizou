# 開発の手引き

読み手: Django の部品を直す開発者。受け入れの条件は [CONTRIBUTING.md](../CONTRIBUTING.md)。入口は [README](../README.md)。

## 開発する人へ

Django の部品を直すときの命令。

- 入れる: `uv sync --locked --group dev --extra pdf` (開発の道具と、PDF の部品も入れる)
- 試験: `uv run pytest`。PDF の試験 (印 `pdf`) は OS の pango が要る。macOS は `brew install pango`、Linux は apt で pango を入れる (CI は `libpango-1.0-0`・`libpangoft2-1.0-0`・`libharfbuzz0b`・`libharfbuzz-subset0`)。pango の無い所では `uv run pytest -m "not pdf"` で除く。
- 書き方の検査: `uv run ruff check .`
- 決まりの検査: `uv run python -m django seizou_check src/seizou --settings=tests.settings`
- CSS: テンプレートを変えたら、`uv run python scripts/build_css.py` で作り直す。最初の 1 回は、道具 (Tailwind CSS と daisyUI) を取りにネットに出る。
- 手元の 2 社の再起動: `uv run python scripts/sites.py restart` (置き場の親は `SEIZOU_SITES_DIR`、無ければ `~/seizou-sites`。`status`・`start`・`stop` もある。runserver は `--noreload` で起動するので、取り込みの後はこれで再起動する)

### 一覧の列 (columns) の印

一覧の画面 (`BaseListView`) の `columns` の 1 要素は、項目の名前の字 (`"part_no"`)、名前と見出しの組 (`("item", "部品")`)、辞書のどれかで書く。辞書の `kind` は欄の種類で、`number` は右寄せと負の数の赤、`money` は右寄せで、金額の閲覧が無い人には列ごと出ず CSV からも外れる。`date` は等幅の年月日になる。`sortable` を真にすると見出しが並び替えの link になり、表の項目でない名前 (部品の `get_queryset` が annotate した列) も並び替える。`total` を真にすると、表の数の項目と、`kind` が number / money の annotate の列を、絞り込みに追従する合計の行に足す。`sort_key` は、Python の値の列 (例 生産管理ボードの `board__days_left`) を、同じ順になる別の列 (表の項目か annotate の列。例 `due_date`) で並べるときに書く。並び替えたときは、同じ値の行を `pk` の順にそろえる。

### E2E (ブラウザで画面を通す試験)

2 社を立てて、ブラウザで画面を通す試験。`tests/e2e/` に、仕様書の番号ごとに 1 ファイルを置く。環境変数 `SEIZOU_E2E` が `1` のときだけ動き、無ければ全部 skip するので、普段の `uv run pytest` と CI には影響しない。

前提は次の 3 つ。

- `uv run playwright install chromium` で、試験が使うブラウザを入れてある。
- 2 社を `seizou new … --demo` で作ってある。
- ポート 8001 と 8002 が空いていれば、試験が 2 社を起動する。既に立っていればそれを使うが、その場合は 2 社の間の鍵を試験から渡せない。

試験が読む環境変数は次のとおり。値はここに書かない。パスワードは `seizou new` の出力に出た物を渡す。

| 名前 | 意味 |
|---|---|
| `SEIZOU_E2E` | `1` のときだけ試験が動く門 |
| `SEIZOU_E2E_SITES_DIR` | 会社の置き場の親。無ければ `~/seizou-sites` |
| `SEIZOU_E2E_USER` | 管理者の ID。無ければ `admin` |
| `SEIZOU_E2E_PASSWORD_A_MACHINING` | A 社の管理者のパスワード |
| `SEIZOU_E2E_PASSWORD_B_PLATING` | B 社の管理者のパスワード |
| `SEIZOU_E2E_PARTNER_SECRET` | 2 社の間の署名の鍵 |
| `SEIZOU_E2E_COMPANY_NAME_A_MACHINING`・`SEIZOU_E2E_COMPANY_NAME_B_PLATING` | 外の置き場の会社名が種 (examples) と違うときの差し替え (会社名で見る確認のため) |
| `SEIZOU_E2E_SEED_NAME_<会社の id>_<PARTNERS か ITEMS>_<コード>` | 外の置き場で種の行の名前を直してあるとき (相手の会社の行を実名にした、など) の差し替え (例 `SEIZOU_E2E_SEED_NAME_A_MACHINING_PARTNERS_S001`) |
| `SEIZOU_E2E_BASE_URL_A_MACHINING`・`SEIZOU_E2E_BASE_URL_B_PLATING` | 外の置き場 (VPS など) で回すときの 2 社の URL (例 `https://a.example.jp`)。2 社とも書くと、runserver を起動せずにその URL で回す。置き場のファイルを読む E-30 は skip |

回し方: `SEIZOU_E2E=1 uv run pytest tests/e2e -q -rs`。画面の絵は `docs/e2e/<日付>/` に番号つきで残る。外の置き場で回すと、試験が置いた `E2E-` の印の行 (受注・部品・発注など) がその置き場に残るので、人に見せる前に `--demo` で作り直す。

`docs/` には [docs/vps.md](vps.md) (VPS に 2 社を立てて https でつなぐ手順)・[docs/operations.md](operations.md) (運用。控え・鍵の入れ替え・版を上げる手順)・[docs/denchoho.md](denchoho.md) (電子取引のデータの保存と seizou)・[docs/edi-items.md](edi-items.md) (会社どうしで送り合う項目の表)・[docs/intake-watch.md](intake-watch.md) (注文書の見張り) と、`docs/images/` (README の画面の絵) と `docs/nights/` (夜の無人の仕事の記録。日付のまとめと仕事ごとのファイル) と `docs/e2e/` (E2E の絵) を置く。
