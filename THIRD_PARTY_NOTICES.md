# THIRD_PARTY_NOTICES

seizou 自身のライセンスは Apache-2.0 (本文は LICENSE)。この文書には、seizou の配布物に同梱する外の物と、Python の依存のライセンスを並べる。

同梱する物は、`src/seizou/core/static/vendor/` (JS と CSS) と `src/seizou/core/static/fonts/` (フォント) にある (2026-11-02 の時点)。

## 同梱する物

| 名前 | 版 | ライセンス | 出所の URL | 配布物の中の置き場 |
|---|---|---|---|---|
| Tailwind CSS | 4.3.3 | MIT | https://github.com/tailwindlabs/tailwindcss/releases/download/v4.3.3/ (OS ごとの実行ファイル 3 つ) | CSS を作る道具。道具そのものは同梱せず、作った CSS `src/seizou/core/static/css/app.css` が入る |
| daisyUI | 5.7.50 | MIT | https://github.com/saadeghi/daisyui/releases/download/v5.7.50/ (`daisyui.mjs` と `daisyui-theme.mjs`) | CSS を作る道具。道具そのものは同梱せず、作った CSS `src/seizou/core/static/css/app.css` が入る |
| htmx | 2.0.11 | 0BSD | https://cdn.jsdelivr.net/npm/htmx.org@2.0.11/dist/htmx.min.js | `src/seizou/core/static/vendor/htmx.min.js` |
| Tom Select | 2.4.3 | Apache-2.0 | https://cdn.jsdelivr.net/npm/tom-select@2.4.3/dist/ (`js/tom-select.complete.min.js` と `css/tom-select.default.css`) | `src/seizou/core/static/vendor/tom-select.complete.min.js`・`tom-select.css` |
| Noto Sans JP | 2.004 | OFL-1.1 | https://raw.githubusercontent.com/notofonts/noto-cjk/Sans2.004/Sans/Variable/TTF/Subset/NotoSansJP-VF.ttf | `src/seizou/core/static/fonts/NotoSansJP-VF.ttf` |

- 版と出所の URL は、同梱物の版の一覧のファイル (`scripts/vendor.toml`) と同じにする。版を上げるときは、`scripts/vendor.toml` とこの表を一緒に書き換える。
- Noto Sans JP は、可変のフォント 1 本 (`NotoSansJP-VF.ttf`) を同梱し、帳票の Regular と Bold はこの 1 本から作る。配布元に、日本語の Regular と Bold の static な TrueType が無いため (`scripts/vendor.toml` の `noto-sans-jp` の項目の説明)。
- ライセンスの全文は、同梱物の横に置くファイルを指す。フォントの OFL の全文は `src/seizou/core/static/fonts/OFL.txt` に置いた。JS と CSS の LICENSE は公式の配布物から写す。JS と CSS の全文の置き場: [空欄: 全文のファイルのパス・同梱物の横に置いたときに作者が埋める]
- Apache-2.0 の全文は LICENSE と同じなので、ここには写さない。
- Tom Select の NOTICE: 未確認 (同梱するときに写す)。
- `pyproject.toml` の license の式は、`Apache-2.0 AND MIT AND 0BSD AND OFL-1.1` と一致させる。

## Python の依存 (同梱しない)

pip で入れるときに別に取ってくる物。同梱しないので全文は置かず、名前とライセンスだけを並べる。ライセンスの名前は、`uv sync --locked --group dev --extra pdf` で入る版の package の情報 (License-Expression か License の欄。WeasyPrint は package に入った LICENSE のファイル) で確かめた (2026-11-02)。

| 名前 | 使う所 | ライセンス | 確かめ |
|---|---|---|---|
| Django | 製品の依存 | BSD-3-Clause | 確かめた (版 6.1.2) |
| Pillow | 製品の依存 | MIT-CMU | 確かめた (版 13.0.0) |
| requests | 製品の依存 | Apache-2.0 | 確かめた (版 2.34.2) |
| PyYAML | 製品の依存 | MIT | 確かめた (版 6.0.3) |
| anthropic | 製品の依存 (注文書の読み取り) | Apache-2.0 | 確かめた (版 2.3.0) |
| qrcode | 製品の依存 (現品票とラベルの QR) | BSD-3-Clause | 確かめた (版 8.2。package の情報の分類は BSD とだけあり、3 条項は package に入った LICENSE のファイルで確かめた) |
| mcp | 製品の依存 (手元の Claude Code から操作する MCP サーバー) | MIT | 確かめた (版 2.3.0) |
| WeasyPrint | extras `[pdf]` | BSD-3-Clause | 確かめた (版 70.0。package の情報の分類は BSD とだけあり、3 条項は LICENSE のファイルで確かめた) |
| pytest | 開発用 | MIT | 確かめた (版 9.2.0) |
| pytest-django | 開発用 | BSD-3-Clause | 確かめた (版 4.14.0) |
| ruff | 開発用 | MIT | 確かめた (版 0.17.3) |
| playwright | 開発用 | Apache-2.0 | 確かめた (版 1.64.1) |
| psycopg | 開発用 | LGPL-3.0-only | 確かめた (版 3.3.6。`psycopg-binary` も同じ) |

ほかの依存 (pypdf など) は予定で、入れるときにこの表に足す。
