# 本番で使う (本番の決まりと毎日の運用)

読み手: 会社のシステムを預かる人。本番で使うときの決まりと、毎日の運用の命令を書く。入口は [README](../README.md)。

## 本番の決まり

本番で使うときの決まり (予定)。本番の前に残る物 (2026-11-08 の時点。これから作る) は 6 つ = 日本語の Windows の実機で手で通した確認 (Linux は済み = VPS の AlmaLinux 9 に [docs/vps.md](vps.md) の手順で 2 社を手で立て、発注が相手の受注になるまでを画面で通した。Windows は今は CI だけ。2026-11-11 から、入れる所から画面が開くまでを CI が ubuntu・windows・macos で通している)・PostgreSQL の実機の置き場 (VPS の 2 社は SQLite で立てている。控えから入れ直す手順もこれから)・Claude Security の未走査の部品 (出荷・発注・フィードバック・プラグイン) の走査・部品の版を上げた実例 (今は v0.1.0 の 1 版だけ。出した migration を消さない決まりと試験はある)・置き場を立てる命令と公開の URL を持たない会社のための中継・2 段階の確認。それぞれの所在は下と[「これからの道筋」](roadmap.md)にある。

- DB は PostgreSQL を使う。開発と試しは SQLite で動く。PostgreSQL は 15 以上 (今の `uv.lock` の Django 6.1 の下限。CI は PostgreSQL 16 で試験を回す)。PostgreSQL にするには、`seizou new` を打つ前に、環境変数 `SEIZOU_DB=postgres` と、接続の環境変数 (PGHOST・PGUSER・PGPASSWORD・PGDATABASE) を入れる (置き場の README の「本番で使うとき」と同じ)。
- 11-07 に Claude Security (Claude Code のプラグイン) で `src/seizou` と `plugin` を走査し、出た候補 45 件を人が選り分けて直した。出荷・発注・フィードバック・プラグインなどは、まだ走査していない。走査は定期に回す物だ。
- 注文書の読み取りに使う API の鍵は、環境変数 (`ANTHROPIC_API_KEY`) に入れる。製品は Claude の契約の枠を使わない。
- 本番の形は 2 つ。取引先から注文のデータを受ける会社は VPS (月額固定) に置く ([docs/vps.md](vps.md))。社内だけで使う会社は、社内の PC 1 台に置き、LAN の中だけで開く形でもよい (`manage.py seizou_serve` で起こす。nginx は要らない。手順と気をつける事は [docs/operations.md](operations.md) の「社内の PC で動かす」)。社内の PC で動かす会社は、注文のデータを送るだけになる (受けるには外から届く URL が要るため)。
- PDF の帳票は Linux のサーバーか、GTK を入れた PC で作る。Windows の手元の PC では GTK の実行環境を別に入れる必要があり、要注意。
- 適格請求書の登録番号 (T と 13 桁の数字) は、答えの YAML の `company` の `registration_no` か設定の画面の「会社の情報」で入れ、請求書の会社の名前の下に出る (空なら出さない。形の違う番号は止める)。消費税は請求書の税率ごとの合計に 1 回だけ計算し (今の率は 10% の 1 つ)、1 円に丸める向きは取引先の「消費税の端数の向き」(空なら会社の既定の切捨) で選ぶ。会社の住所・電話・振込先・ロゴは、答えの YAML の `company` か設定の画面の「会社の情報」で入れ、納品書と請求書の会社の欄に出る (振込先は請求書だけ。ロゴは答えの YAML の `logo:` か画面から)。
- 上げたファイル (`/media/` の図面・添付・マニュアルの画像) は、開発機でも本番でも、ログインした人にだけ返す (`seizou.core.media`)。HTML や SVG のような、ブラウザがスクリプトとして動かしうる型は、必ずダウンロードにする。既定は Django がファイルを送る。大きなファイルを nginx に送らせるには、置き場の `deploy/nginx-files.conf` を nginx の設定に読み込み、`.env` に `SEIZOU_FILE_DELIVERY=nginx` を足す。nginx を通った印の無い要求は、空のファイルを返さずに 503 の画面で止まる (設定の漏れに、その場で気づくため)。`internal` の付け忘れのような、Django から見えない漏れを外から確かめる命令は、これから作る。
- 本番 (`DEBUG=0`) では、セッションと CSRF の Cookie を HTTPS のときだけ送る (nginx で https を受ける)。ログインに 5 回続けて失敗した ID は 15 分止まる (接続元ごとは 15 分に 30 回の失敗で止まる)。nginx の後ろでは、`.env` に `SEIZOU_CLIENT_IP_HEADER=X-Real-IP` の 1 行を足し、nginx が上書きする見出し (`proxy_set_header X-Real-IP $remote_addr;`) の値を接続元にする (無いと接続元がいつも nginx になり、ほかの人がある ID を 15 分止められる)。監視の道具はログインの要らない稼働を確かめる URL `/health/` を見る。手順は [docs/operations.md](operations.md) の「本番の守り」。
- VPS 1 台に 2 社分の置き場を立て、https で取引先とつなぐ手順は [docs/vps.md](vps.md) にある (手で進める。立てる命令は、これから作る)。文書にはどの会社でも同じ手順と作り物の値だけを書く。会社の名前・ドメイン・鍵・答えの YAML は、公開のリポジトリに入れない。

## 毎日の運用 (控え・戻し・点検・版を上げる)

毎日使う命令は 3 つで、会社の置き場で `<python> manage.py <命令>` の形で打つ (`<python>` は置き場の README の起動の 1 行の python)。練習モード (`seizou_practice`)・取引先の鍵を作る `seizou_secret`・`collectstatic`・`clearsessions`・`changepassword` のような、ほかの命令と手順の全部は [docs/operations.md](operations.md) にある。

- `seizou_backup`: データベースと上げたファイルと答えの YAML の写しと、会社の作り込みの写し `site/` (`settings.py`・`fields/`・`templates/` など) を、置き場の `backups/<日時>/` に控える。`.env` (鍵) は控えに入らないので、別の安全な場所に人の手で写す。
- `seizou_restore <控えの名前>`: 控えの時点に戻す (`--list` で控えの一覧)。戻す前の今の物も `backups/<日時>-before-restore/` に控える。
- `seizou_doctor`: 置き場を点検し、直す物を直し方つきで 1 行ずつ出す。何も書き換えない。本番にする前は `--production` を付ける。
- 版を上げるときは、控え → サーバーを止める → seizou を入れ直す (PyPI の版は `uv pip install --python <python> -U seizou`) → `migrate` → 点検 → 起動する、の順に進める (docs/operations.md の「版を上げる手順」の 8 段のうち主な物)。
