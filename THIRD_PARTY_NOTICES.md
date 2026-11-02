# THIRD_PARTY_NOTICES

seizou 自身のライセンスは Apache-2.0 (本文は LICENSE)。この文書には、seizou の配布物に同梱する外の物と、Python の依存のライセンスを並べる。

これは初稿 (2026-11-02)。同梱する物は、まだリポジトリに無い。

## 同梱する物 (予定)

| 名前 | 版 | ライセンス | 出所の URL |
|---|---|---|---|
| Tailwind CSS | [空欄: 版・同梱するときに作者が埋める] | MIT | https://github.com/tailwindlabs/tailwindcss |
| daisyUI | [空欄: 版・同梱するときに作者が埋める] | MIT | https://github.com/saadeghi/daisyui |
| htmx | [空欄: 版・同梱するときに作者が埋める] | 0BSD | https://github.com/bigskysoftware/htmx |
| Tom Select | [空欄: 版・同梱するときに作者が埋める] | Apache-2.0 | https://github.com/orchidjs/tom-select |
| Noto Sans JP | [空欄: 版・同梱するときに作者が埋める] | OFL-1.1 | [空欄: 出所の URL・同梱するときに作者が埋める] |

- 版は、同梱物の版の一覧のファイル (`scripts/vendor.toml`) と同じにする。
- 出所の URL は、同梱するときに実際に取った所と照らし合わせる。
- ライセンスの全文は、同梱物の横に置くファイルを指す。JS と CSS の LICENSE は公式の配布物から写し、フォントは OFL のファイルを置く。置き場: [空欄: 全文のファイルのパス・同梱したときに作者が埋める]
- Apache-2.0 の全文は LICENSE と同じなので、ここには写さない。
- Tom Select の NOTICE: 未確認 (同梱するときに写す)。
- `pyproject.toml` の license の式は、`Apache-2.0 AND MIT AND 0BSD AND OFL-1.1` と一致させる。

## Python の依存 (同梱しない)

pip で入れるときに別に取ってくる物。同梱しないので全文は置かず、名前とライセンスだけを並べる。ライセンスの名前は手元で確かめていない (未確認)。`pyproject.toml` が入った後に、各 package の情報で確かめる。

| 名前 | 使う所 | ライセンス | 確かめ |
|---|---|---|---|
| Django | 製品の依存 | BSD-3-Clause | 未確認 |
| Pillow | 製品の依存 | MIT-CMU | 未確認 |
| requests | 製品の依存 | Apache-2.0 | 未確認 |
| PyYAML | 製品の依存 | MIT | 未確認 |
| WeasyPrint | extras `[pdf]` | BSD-3-Clause | 未確認 |
| pytest | 開発用 | MIT | 未確認 |
| pytest-django | 開発用 | BSD-3-Clause | 未確認 |
| ruff | 開発用 | MIT | 未確認 |
| playwright | 開発用 | Apache-2.0 | 未確認 |
| psycopg | 開発用 | LGPL-3.0 | 未確認 |

ほかの依存 (pypdf・qrcode など) は予定で、入れるときにこの表に足す。
