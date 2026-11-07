# VPS 1 台に 2 社分を立てる手順 (https・取引先とつなぐ)

読み手は、会社のシステムを預かる人です。プログラムは書けなくてかまいません。

## この文書は何か

この文書は、VPS (借りるサーバー) を 1 台借りて、2 社分の置き場を別のポートで立て、nginx と Let's Encrypt の https の下に置き、2 社の間で注文を送り合うまでの手順です。上の段から順に、手で打ちます。建てる命令は会期後です。

トンネル (外から手元の PC へ通す道具) は使いません。注文を受ける側には、外から届く https の名前を持たせます。外から届く名前を持たない会社どうしをつなぐ中継は、会期後です。

手元の 2 社のデモ (README の「触り方」) との違いは 3 つです。

- 起動は、runserver でなく gunicorn と systemd です。
- 開く URL は、`http://127.0.0.1:<ポート>` でなく `https://<名前>` です。
- つながりの受け口の URL は、https の名前です。

OS は AlmaLinux 9 (RHEL 系) です。仕事で使う前提なので、SELinux (OS がプログラムごとに読み書きできる物を絞る仕組み) は Enforcing (断る形) のまま使います。動かないときも、`setenforce 0` で SELinux を止めません。

データベースは、`seizou new` の既定の SQLite のままにします。README の「本番の決まり」は PostgreSQL を勧めていますが、この手順は小さな 2 社を SQLite で立てます。PostgreSQL へ移す手順は会期後です。

立てた後の毎日の運用 (控え・戻し・点検・版上げ) は [operations.md](operations.md) にあります。

この文書には、どの会社でも同じ手順と作り物の値だけを書きます。会社ごとの名前・IP・鍵・答えの YAML は、公開のリポジトリにも issue にも書きません。それらは VPS の中と、各社の控えの置き場にだけ置きます。

## 作り物の値

この文書の値は、次の作り物です。自分の会社の値に置き換えて打ってください。

| 物 | 値 |
|---|---|
| VPS の OS | AlmaLinux 9 (SELinux は Enforcing) |
| VPS の IP | `203.0.113.10` (文書の例のための範囲。実在しない) |
| 名前 (DNS) | `a.example.jp` (A 社)・`b.example.jp` (B 社) |
| 会社 | 若葉精機 (A 社・`a_machining`・発注する側・ポート 8001)・白樺めっき工業 (B 社・`b_plating`・注文を受ける側・ポート 8002)。seizou の `examples/companies/` にある作り物の 2 社 |
| OS の利用者 | `seizou` (サーバーを動かす利用者。人はこの利用者でログインしない)・`nginx` (nginx が動く利用者。nginx を入れると作られる) |
| seizou の置き場 | `/srv/seizou/src` (git の clone)・答えの YAML の置き場 `/srv/seizou/answers/`・uv の置き場 `/srv/seizou/tools/` |
| 会社の置き場 | `/srv/seizou-sites/<会社の id>/` |
| systemd の unit | `seizou-a_machining.service`・`seizou-b_plating.service` |
| nginx の設定 | `/etc/nginx/conf.d/seizou-a_machining.conf`・`/etc/nginx/conf.d/seizou-b_plating.conf` |

命令の中の `a_machining`・`8001`・`a.example.jp` は A 社の値です。B 社の分は `b_plating`・`8002`・`b.example.jp` に置き換えて、同じ命令をもう 1 度打ちます。

各段の最後の「確かめ方」の行は、打つ命令と、見えるはずの答えです。「(VPS で確かめる)」の付いた答えは、作者が VPS で見ていない見込みの字です。付いていない答えは、作者が手元の一時の置き場で同じ手順を通して見た字です (道は VPS の物に置き換えた)。

各段の一番最後の行は、SELinux の確かめ方です。`getenforce` が `Enforcing` を出し、`sudo ausearch -m AVC,USER_AVC -ts recent` (ここ 10 分の、SELinux が断った記録) が `<no matches>` を出せば、その段で SELinux は何も断っていません。記録が出たときは、`sudo ausearch -m AVC,USER_AVC -ts recent -i` で読みやすい形にし、`comm=` (断られたプログラム) と `scontext` (そのプログラムの印) と `tcontext` (触ろうとした物の印) を見て、その段の context (ファイルの印) か boolean (SELinux の切り替え) を見直します。

## 1. VPS と名前を用意する

AlmaLinux 9 の VPS を 1 台借ります。メモリは 2GB を目安にします (2 社 × gunicorn 1 つ)。借りる所の画面で、ssh の公開鍵を登録しておきます (2 で、パスワードのログインを止めるため)。

名前の管理の画面 (DNS) で、`a.example.jp` と `b.example.jp` の A レコードを足し、値を VPS の IP にします。この後の命令は、VPS に ssh でログインして打ちます。

```
ssh <自分の利用者>@203.0.113.10
```

確かめ方: `getent hosts a.example.jp` → `203.0.113.10    a.example.jp` (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 2. どの会社でも同じ守り

最初に、SELinux が Enforcing で、再起動の後も Enforcing のままかを見ます。

```
getenforce
grep '^SELINUX=' /etc/selinux/config
```

借りる所の像が Permissive (断らずに記録だけする形) なら、`sudo setenforce 1` を打ち、`/etc/selinux/config` の `SELINUX=` の行を `enforcing` に直します。Disabled なら、`/etc/selinux/config` を直してから `sudo touch /.autorelabel` と `sudo reboot` を打ちます (再起動のときに、全部のファイルの context を付け直します)。

ssh のログインを鍵だけにします。先に、別の窓で鍵でログインできることを確かめてから打ちます (鍵で入れないままパスワードを止めると、入れなくなります)。

```
echo 'PasswordAuthentication no' | sudo tee /etc/ssh/sshd_config.d/00-seizou.conf
sudo systemctl restart sshd
```

設定は、`/etc/ssh/sshd_config.d/` の頭の名前のファイルに書きます。同じフォルダの別のファイル (例 `50-cloud-init.conf`) がパスワードのログインを許していることがあり、ssh は先に読んだ値を使うためです。

防火壁 (firewalld) で、http と https を開けます。ssh は初めから開いています。ブラウザの管理画面 (cockpit) を使わないなら、その口を閉じます。

```
sudo firewall-cmd --permanent --add-service=http --add-service=https
sudo firewall-cmd --permanent --remove-service=cockpit
sudo firewall-cmd --reload
```

自動のセキュリティ更新を入れます。`/etc/dnf/automatic.conf` の `upgrade_type` をセキュリティの更新だけにし、更新を入れる timer を動かします。

```
sudo dnf install dnf-automatic
sudo sed -i 's/^upgrade_type = .*/upgrade_type = security/' /etc/dnf/automatic.conf
sudo systemctl enable --now dnf-automatic-install.timer
```

時計が合っているかを見ます。受ける側は注文の時刻を前後 5 分の幅で確かめるので、時計がずれると注文を断ります (README の「取引先とつなぐ」)。

```
timedatectl
```

確かめ方: `grep '^SELINUX=' /etc/selinux/config` → `SELINUX=enforcing`・`sudo sshd -T | grep -i '^passwordauthentication'` → `passwordauthentication no`・`sudo firewall-cmd --list-services` → `dhcpv6-client http https ssh` (dhcpv6-client は IPv6 の住所を受け取る口で、初めからある)・`systemctl is-active dnf-automatic-install.timer` → `active`・`timedatectl` → `System clock synchronized: yes` (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 3. 道具を入れる

certbot は EPEL (RHEL 系の追加の置き場) から入れます。EPEL の案内のとおり、先に CRB の置き場を開けます。nginx と python3.12 は、AlmaLinux の AppStream から入ります。

```
sudo dnf config-manager --set-enabled crb
sudo dnf install epel-release
sudo dnf install git nginx python3.12 certbot python3-certbot-nginx pango policycoreutils-python-utils
```

`pango` は PDF の帳票を作るための部品です (README の「前提」)。`policycoreutils-python-utils` は、10 で context を足す命令 `semanage` を入れます。

確かめ方: `nginx -v` → `nginx version:` で始まる 1 行・`certbot --version` → `certbot` で始まる 1 行・`python3.12 --version` → `Python 3.12.` で始まる 1 行 (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 4. OS の利用者と置き場

サーバーを動かす利用者 `seizou` を作り、置き場のフォルダを用意します。

```
sudo useradd --system --user-group --create-home --home-dir /srv/seizou --shell /sbin/nologin seizou
sudo install -d -o seizou -g seizou /srv/seizou/answers /srv/seizou-sites
```

利用者 `seizou` は、ログインの shell を持ちません。この後の seizou の命令は、`sudo -u seizou -H <命令>` の形で、利用者 `seizou` として打ちます。

確かめ方: `id seizou` → `uid=` で始まり `(seizou)` を含む 1 行 (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 5. seizou を入れる

```
sudo -u seizou -H python3.12 -m venv /srv/seizou/tools
sudo -u seizou -H /srv/seizou/tools/bin/pip install uv
sudo -u seizou -H git clone https://github.com/kishii-py/seizou.git /srv/seizou/src
sudo -u seizou -H /srv/seizou/tools/bin/uv sync --directory /srv/seizou/src --locked --no-dev --extra pdf --extra server --python /usr/bin/python3.12
```

`--python /usr/bin/python3.12` は、3 で入れた OS の Python を使う印です。uv が自分で取る Python は `/srv/seizou` の下に置かれ、SELinux の印が実行の物にならないので使いません。`--no-dev` は、開発と試験の道具 (pytest など) を入れない印です。`--extra server` は、本番の Web サーバーの gunicorn を入れる印です (seizou の extra `server`)。この版より前に gunicorn を別に入れた置き場は、`--extra server` を付けて打ち直せば、そのまま同じ venv に入ります。版を上げるときも、同じ形で打ちます (18)。

確かめ方: `sudo -u seizou -H /srv/seizou/src/.venv/bin/seizou --help` → `new       答えの YAML から会社のプロジェクトを作る` の行・`sudo -u seizou -H /srv/seizou/src/.venv/bin/gunicorn --version` → `gunicorn (version` で始まる 1 行 (gunicorn は VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 6. 答えの YAML を置く

2 社の答えの YAML は、手元の PC で書きます。形は seizou の `examples/companies/a_machining.yaml` と同じで、ファイルの名前は会社の id にそろえます。書いたら、手元の PC から VPS へ送ります。

```
scp a_machining.yaml b_plating.yaml <自分の利用者>@203.0.113.10:
```

VPS で、利用者 `seizou` だけが読める形で置き、送った写しを消します。実在の会社の答えの YAML は、公開のリポジトリに入れません。

```
sudo install -o seizou -g seizou -m 600 a_machining.yaml b_plating.yaml /srv/seizou/answers/
rm a_machining.yaml b_plating.yaml
```

確かめ方: `sudo ls /srv/seizou/answers` → `a_machining.yaml  b_plating.yaml` (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 7. 会社の置き場を 2 つ作る

```
sudo -u seizou -H env SEIZOU_SITES_DIR=/srv/seizou-sites /srv/seizou/src/.venv/bin/seizou new /srv/seizou/answers/a_machining.yaml
sudo -u seizou -H env SEIZOU_SITES_DIR=/srv/seizou-sites /srv/seizou/src/.venv/bin/seizou new /srv/seizou/answers/b_plating.yaml
```

`--demo` は付けません。`--demo` は作り物のデータを入れる印で、作り物の 2 社で試すときだけ付けます。実在の会社は、自分のデータを入れます。

`--demo` を付けなくても、`seizou new` は画面ごとの説明書の断片の標準の組を置き場の `manual/` に写すので、本番の置き場でも上の帯の「使い方」と画面の「?」の説明が空になりません (この版より前に作った置き場は、`docs/operations.md` の「版を上げる手順」のとおりに写します)。

命令は、管理者のパスワードを出力に 1 度だけ出します。14 で変えるまで控え、画面の写しや記録やチャットに貼りません。出力の「次にすること」の runserver の 1 行と、置き場の `README.md` の「起動」の 1 行は、手元で試すための物です。VPS では使わず、11 の gunicorn で動かします。

確かめ方: `seizou new` の出力 → `できた物: /srv/seizou-sites/a_machining の会社のプロジェクト。` で始まる行・`sudo ls /srv/seizou-sites` → `a_machining  b_plating`
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 8. .env を本番の形にする

2 社とも、置き場の `.env` (鍵を置くファイル) を開きます。

```
sudo -u seizou -H nano /srv/seizou-sites/a_machining/.env
```

`DEBUG` と `SEIZOU_DEV_AUTO_LOGIN` の行を 0 に直し、`ALLOWED_HOSTS`・`SEIZOU_FILE_DELIVERY`・`SEIZOU_CLIENT_IP_HEADER` の 3 行を足して、次の 5 行がある形にします。B 社は `ALLOWED_HOSTS=b.example.jp` です。

```
DEBUG=0
SEIZOU_DEV_AUTO_LOGIN=0
ALLOWED_HOSTS=a.example.jp
SEIZOU_FILE_DELIVERY=nginx
SEIZOU_CLIENT_IP_HEADER=X-Real-IP
```

`SECRET_KEY` の行は、`seizou new` が作った値のまま触りません (2 社で違う値です)。注文書の読み取りと AI チャットを使う会社だけ、API の鍵 (`ANTHROPIC_API_KEY`) の行を足します。`SEIZOU_CLIENT_IP_HEADER` は、ログインの締め出しが接続元を 12 の nginx が付ける見出し `X-Real-IP` から読む印です (無いと、nginx の後ろでは接続元がいつも 127.0.0.1 になり、ID ごとの締め出しになります)。`.env` の中身は、画面に出しません (`cat` で開かない)。`nano` が無ければ `sudo dnf install nano` で入れます。

確かめ方: `sudo stat -c %a /srv/seizou-sites/a_machining/.env` → `600`・`sudo grep -c -x -F -e DEBUG=0 -e SEIZOU_DEV_AUTO_LOGIN=0 -e ALLOWED_HOSTS=a.example.jp -e SEIZOU_FILE_DELIVERY=nginx -e SEIZOU_CLIENT_IP_HEADER=X-Real-IP /srv/seizou-sites/a_machining/.env` → `5`
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 9. 静的ファイルを集めて点検する

2 社とも、次の 3 つを打ちます。

```
sudo -u seizou -H /srv/seizou/src/.venv/bin/python /srv/seizou-sites/a_machining/manage.py collectstatic --no-input
sudo -u seizou -H /srv/seizou/src/.venv/bin/python /srv/seizou-sites/a_machining/manage.py seizou_doctor --production
sudo -u seizou -H /srv/seizou/src/.venv/bin/python /srv/seizou-sites/a_machining/manage.py check --deploy
```

`DEBUG=0` では Django が静的ファイル (画面の CSS と JS) を送らないので、`collectstatic` で `staticfiles/` に集め、12 で nginx に配らせます。点検の読み方は、[operations.md](operations.md) の「立ち上げの確かめ (点検)」にあります。点検は SELinux を見ないので、SELinux は各段の最後の行で見ます。

`check --deploy` は、2 件の注意を出します。`security.W008` (http を https へ移す設定) は、13 で certbot が足す nginx の移しが受け持ちます。`security.W004` (HSTS = ブラウザに https だけで開かせる見出し) を足すかは、会社が決めます。この手順では足しません。

確かめ方: `… manage.py seizou_doctor --production` → 最後の方に `点検の結果: 良い 20 件・直す 0 件`・`… manage.py check --deploy` → `security.W004` と `security.W008` の 2 件だけ (`System check identified 2 issues (0 silenced).`)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 10. 置き場の権限と SELinux の context

nginx (利用者 `nginx`) が置き場の中で読むのは、静的ファイル (`staticfiles/`) と上げたファイル (`media/`) と、`include` で読む断片 (`deploy/nginx-files.conf`) です。データベースと `.env` と控えは、nginx から読めないようにします。2 社とも次を打ちます。

```
sudo chgrp nginx /srv/seizou-sites/a_machining
sudo chmod 750 /srv/seizou-sites/a_machining
sudo -u seizou chmod 600 /srv/seizou-sites/a_machining/db.sqlite3
sudo -u seizou install -d -m 700 /srv/seizou-sites/a_machining/backups /srv/seizou-sites/a_machining/practice /srv/seizou-sites/a_machining/upload-tmp
sudo -u seizou install -d -m 755 /srv/seizou-sites/a_machining/media
```

控え (`backups/`) と練習モードの写し (`practice/`) は、命令が後で作ると、ほかの利用者も読める権限 (フォルダは 755、データベースの写しは 644) になります。そこで、先に持ち主だけが入れるフォルダを作っておきます。データベースの作業のファイル (`db.sqlite3-wal`) は、`db.sqlite3` と同じ 600 で作られます。`.env` は 600 のままです。`media/` と `upload-tmp/` (上げたファイルの一時の置き場。11 で使う) は、下の context を付けるために先に作ります。

次に、SELinux の context を足します。`/srv` の下のファイルは、そのままでは `var_t` (データの印) で、nginx は読めません。この印の決まりは 2 社分を 1 度に足すので、2 回目は打ちません。

```
sudo semanage fcontext -a -t httpd_sys_content_t '/srv/seizou-sites/[^/]+/(staticfiles|media|upload-tmp)(/.*)?'
sudo semanage fcontext -a -t httpd_config_t '/srv/seizou-sites/[^/]+/deploy(/.*)?'
sudo semanage fcontext -a -t bin_t '/srv/seizou/src/\.venv/bin(/.*)?'
sudo restorecon -R /srv/seizou-sites /srv/seizou/src/.venv/bin
```

- `httpd_sys_content_t` は、nginx が読める中身の印です。上げたファイルは、Django がログインを見てから `X-Accel-Redirect` で断片の内部の location (`/seizou-files/`) に渡し、nginx がその alias の `media/` から読みます。このため、`media/` の印が要ります。後で Django が `media/` の中に作るフォルダとファイルは、`media/` と同じ印になります。
- `httpd_config_t` は、nginx の設定の印です。nginx は 12 の `include` で、置き場の断片を読みます。
- `bin_t` は、実行の印です。systemd は 11 で、置き場の外の `/srv/seizou/src/.venv/bin/gunicorn` を動かします。
- データベースと `.env` と控えは `var_t` のままなので、権限に加えて SELinux でも nginx から読めません。

確かめ方: `ls -Zd /srv/seizou-sites/a_machining/staticfiles /srv/seizou-sites/a_machining/media | grep -c httpd_sys_content_t` → `2`・`ls -Z /srv/seizou-sites/a_machining/deploy/nginx-files.conf` → `httpd_config_t` を含む 1 行・`sudo -u nginx test -r /srv/seizou-sites/a_machining/staticfiles/css/app.css && echo 読める` → `読める`・`sudo -u nginx test -r /srv/seizou-sites/a_machining/db.sqlite3 || echo 読めない` → `読めない` (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 11. gunicorn を systemd で動かす

`sudo nano /etc/systemd/system/seizou-a_machining.service` で、次の中身のファイルを作ります。B 社は、名前と道の `a_machining` を `b_plating` に、ポートの `8001` を `8002` に置き換えた `seizou-b_plating.service` を作ります。

```
[Unit]
Description=seizou a_machining
After=network.target

[Service]
User=seizou
Group=seizou
WorkingDirectory=/srv/seizou-sites/a_machining
Environment=DJANGO_SETTINGS_MODULE=settings
Environment=TMPDIR=/srv/seizou-sites/a_machining/upload-tmp
ExecStart=/srv/seizou/src/.venv/bin/gunicorn --chdir /srv/seizou-sites/a_machining --pythonpath /srv/seizou-sites/a_machining --bind 127.0.0.1:8001 --workers 1 --threads 4 --timeout 90 'django.core.wsgi:get_wsgi_application()'
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```
sudo systemctl daemon-reload
sudo systemctl enable --now seizou-a_machining seizou-b_plating
```

各行の理由は次の 6 つです。

- `--bind 127.0.0.1:8001` は、同じ VPS の中 (nginx) からだけ届く待ち方です。外から Django に直に届くと、nginx が付ける https の印 (`X-Forwarded-Proto`) と nginx を通った印 (`X-Seizou-Proxy`) を偽れるためです。
- `--workers 1 --threads 4` は、2GB の VPS で 2 社を動かすための小さな形です。worker を増やしてもよく、ログインの締め出しの数は置き場の `cache/login/` のファイルで 1 か所に数えるので、worker の数で変わりません ([operations.md](operations.md) の「ログインの締め出し」)。
- `--timeout 90` は、注文書の読み取りと AI チャットが API の答えを 60 秒まで待つためです。gunicorn の既定の 30 秒では、途中で切れます。
- `'django.core.wsgi:get_wsgi_application()'` と `--chdir`・`--pythonpath`・`DJANGO_SETTINGS_MODULE=settings` は、置き場の `settings.py` を `settings` の名前で読んで、Django の app を作る書き方です。
- `TMPDIR` は、上げたファイルの一時の置き場を置き場の中にする行です。Django は 2.5MB より大きいファイルを一時のファイルに受け、受け終わったら `media/` へ名前を変えて移します。名前を変えて移したファイルは、SELinux の印を移す前のまま持つので、`/tmp` で受けると `tmp_t` のままになり、nginx が読めない見込みです。`upload-tmp/` は 10 で `media/` と同じ印にしてあります。
- `.env` は `settings.py` が自分で読むので、unit に `EnvironmentFile` は書きません。
- gunicorn 26 以降は、既定で制御の socket (`/run/user/<uid>/gunicorn.ctl`) を作ろうとします。unit に `ProtectHome=yes` のような守りを足すと `Permission denied` の記録が出るので、そのときは `ExecStart` に `--no-control-socket` を足します (この版の unit は守りを足していないので、そのままで動きます)。

確かめ方: `systemctl is-active seizou-a_machining` → `active`・`curl -fsS -H 'Host: a.example.jp' http://127.0.0.1:8001/health/` → `{"ok": true}` (作者の手元では、gunicorn と同じ形で Django の app を作り、`/health/` が `200 OK` と `{"ok": true}` を返すのを見た。gunicorn の上の答えは VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる。gunicorn が動かず `comm="gunicorn"` か `comm="(gunicorn)"` の記録が出たら、10 の `bin_t` の行を見る)

## 12. nginx の server を 2 つ書く

`sudo nano /etc/nginx/conf.d/seizou-a_machining.conf` で、次の中身のファイルを作ります。B 社は、名前を `b.example.jp` に、道の `a_machining` を `b_plating` に、ポートを `8002` に置き換えた `seizou-b_plating.conf` を作ります。AlmaLinux の nginx は `conf.d/` の `.conf` のファイルを読みます。

```
server {
    listen 80;
    server_name a.example.jp;
    client_max_body_size 25m;

    location /static/ {
        alias /srv/seizou-sites/a_machining/staticfiles/;
        add_header Cache-Control "public, no-cache";
    }

    include /srv/seizou-sites/a_machining/deploy/nginx-files.conf;

    location / {
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Seizou-Proxy nginx;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 90s;
    }
}
```

SELinux の既定では、nginx はほかのプログラムのポートへつなげません。gunicorn (8001・8002) へつなげるように boolean を入れてから、nginx を立てます。

```
sudo setsebool -P httpd_can_network_connect 1
sudo nginx -t
sudo systemctl enable --now nginx
sudo systemctl reload nginx
```

各行の理由は次のとおりです。

- `client_max_body_size 25m` は、図面と添付と注文書を 1 つ 20MB まで上げられるためです。nginx の既定の 1MB では上げられません。
- `location /static/` は、9 で集めた静的ファイルを nginx が配る所です。`Cache-Control "public, no-cache"` は、ブラウザに毎回確かめさせる印です。seizou の静的ファイルは名前にハッシュが付かないので、長く持たせると版を上げた後に古い CSS が残ります。
- `include` の行は、`seizou new` が置き場に書いた断片 (`deploy/nginx-files.conf`) を読みます。上げたファイルは、Django がログインを見てから、断片の内部の location に渡します。上げたファイルの置き場を nginx がそのまま配る location (`location /media/`) は書きません。書くと、ログインを通らずにファイルが出ます。
- `proxy_set_header` の 4 行が無いと、次のように止まります。`Host` が無いと `ALLOWED_HOSTS` で 400 になります。`X-Forwarded-Proto` が無いと、ログインの保存が 403 (CSRF) になります。`X-Seizou-Proxy` が無いと、上げたファイルの画面が 503 になります。`X-Real-IP` が無いと、ログインの締め出しが接続元を見分けられず、ID ごとの締め出しになります (8 の `SEIZOU_CLIENT_IP_HEADER` と対)。nginx は、外から来た同じ名前の見出しを、この値で上書きします。CDN (Cloudflare など) を通す置き場では、nginx の realip で CDN から来た要求の接続元 (`CF-Connecting-IP` など) を `$remote_addr` に取り直してから、この行で渡します。
- `proxy_read_timeout 90s` は、11 の `--timeout 90` と合わせた待ち時間です。
- `setsebool -P` の `-P` は、再起動の後も boolean を残す印です。
- 443 (https) の server は、13 で certbot が書きます。
- 図面は 1 つ 20MB まで、形は PDF・TIFF・PNG・JPEG だけを受けます (CAD のファイル .dxf・.dwg は部品の添付ファイルに付けます)。表示用の絵を作るときは、絵を読む前に記憶の使う量を見積もり、600MB を超える絵 (カラーの A0 の走査など) は表示用の絵を作らずに元のファイルだけを保存します。2GB の VPS で、1 枚の図面が gunicorn を記憶の不足で止めないための目安です (白黒の A0 は約 0.5GB で通ります)。

確かめ方: `sudo nginx -t` → `syntax is ok` と `test is successful`・`getsebool httpd_can_network_connect` → `httpd_can_network_connect --> on`・`curl -fsS http://a.example.jp/health/` → `{"ok": true}` (VPS で確かめる。作者の手元では、Django の試しの client で、https の印つきの `/health/` が 200、`ALLOWED_HOSTS` に無い名前が 400、https の印つきのログインの保存が 200、印の無いログインの保存が 403 になるのを見た)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる。`/health/` が 502 で `comm="nginx"` と `name_connect` の記録が出たら boolean を、`include` の所で nginx が止まったら 10 の `httpd_config_t` の行を見る)

## 13. https にする (Let's Encrypt)

```
sudo certbot --nginx -d a.example.jp
sudo certbot --nginx -d b.example.jp
sudo systemctl enable --now certbot-renew.timer
```

certbot は証明書を取り、12 の server に 443 の待ち受けと、http から https への移し (301) を足します。初めての certbot は、知らせを受けるメールの宛先と、利用の条件への同意を聞きます。証明書の更新は、最後の 1 行で動かす timer がします。

確かめ方: `curl -fsS https://a.example.jp/health/` → `{"ok": true}`・`curl -sI http://a.example.jp/login/` → `301` と `Location: https://a.example.jp/login/`・`sudo certbot renew --dry-run` → `Congratulations, all simulated renewals succeeded` (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 14. ログインして管理者のパスワードを変える

ブラウザで `https://a.example.jp/login/` を開き、ID `admin` と、7 の出力のパスワードでログインします。続けて、自分の端末で次の命令を打ち、パスワードを変えます。

```
sudo -u seizou -H /srv/seizou/src/.venv/bin/python /srv/seizou-sites/a_machining/manage.py changepassword admin
```

この命令は、8 字以上で、よくある語・数字だけ・ID に似た字でないパスワードだけを受けます。変えた後は、アカウント管理で管理する人ごとに名前の付いたアカウント (フルコントロール) を発行し、ふだんはそれでログインします。`admin` は非常用にします (誰が操作したかが記録に名前で残るため)。

nginx の後ろでは、Django から見た接続元が、いつも同じ VPS の nginx になります。このため、ログインの締め出しは ID ごとになり、ほかの人がある ID を 15 分止めることもできます。2 社は別の process なので、片方の締め出しはもう片方に及びません。締め出しの決まりは、[operations.md](operations.md) の「ログインの締め出し」にあります。

ログインの後に、図面か添付を 1 つ上げて開きます。開けなければ、11 の `TMPDIR` と 10 の `media/` の context を見ます。

確かめ方: `https://a.example.jp/login/` でログインする → ダッシュボードが出る (「CSRF」の 403 の画面が出たら、12 の `X-Forwarded-Proto` の行を見る)・上げたファイルを開く → ファイルが出る (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる。上げたファイルが開けず `comm="nginx"` と `tmp_t` の記録が出たら、11 の `TMPDIR` の行を見る)

## 15. もう 1 度点検する

2 社とも、もう 1 度点検します。

```
sudo -u seizou -H /srv/seizou/src/.venv/bin/python /srv/seizou-sites/a_machining/manage.py seizou_doctor --production
```

確かめ方: `… manage.py seizou_doctor --production` → `点検の結果: 良い 20 件・直す 0 件`
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 16. 2 社をつなぐ

手順は `/seizou:connect` (Claude Code のプラグインの、取引先とつなぐ命令) と同じで、問 1〜4 → 画面で登録 → 鍵の値 → 起動し直し → 鍵の設定の確かめ、の順です。VPS では、2 社の画面 (`https://a.example.jp/` と `https://b.example.jp/`) で進めます。

先に、取引先を登録します。A 社の画面では B 社を、種類「協力企業」の取引先として「新規登録」します。B 社の画面では A 社を、種類「得意先」の取引先として「新規登録」します。

次に、「取引先との接続」(右上の利用者のメニューの「設定」から開く) の「新規登録」で、2 社とも両向きの 2 件を登録します。鍵の置き場の名前は、相手ごとに 1 つで、両向きに使います。

| 会社 | 向き | 受け口の URL | 鍵の置き場の名前 |
|---|---|---|---|
| A 社 | 送る | `https://b.example.jp/connect/receive/order/` | `SEIZOU_PARTNER_B_PLATING_SECRET` |
| A 社 | 受ける | (空) | `SEIZOU_PARTNER_B_PLATING_SECRET` |
| B 社 | 受ける | (空) | `SEIZOU_PARTNER_A_MACHINING_SECRET` |
| B 社 | 送る | `https://a.example.jp/connect/receive/order/` | `SEIZOU_PARTNER_A_MACHINING_SECRET` |

`/seizou:connect` の問 3 と、相手に渡す文 (7) は、手元の 2 社の `http://127.0.0.1:<ポート>` の形を案内します。VPS では、上の表の https の名前に読み替えます。同じ VPS の中でも、A 社は B 社へ https の名前で送ります。点検は https でない受け口の URL を「直す」にし、片方を別の VPS に移しても直す所が無いためです。

鍵の値は、片方の担当者が自分の端末で作ります。Claude Code の会話では打ちません。

```
sudo -u seizou -H /srv/seizou/src/.venv/bin/python /srv/seizou-sites/a_machining/manage.py seizou_secret SEIZOU_PARTNER_B_PLATING_SECRET
```

命令が出した 1 行を、A 社の `.env` に足します (8 と同じく nano で開く)。= の後ろの同じ値を、B 社の `.env` に次の形の 1 行で足します。値の形は SECURITY.md の「取引先の鍵の作り方」にあります。

```
SEIZOU_PARTNER_A_MACHINING_SECRET=<値>
```

足したら、2 社を起動し直します。

```
sudo systemctl restart seizou-a_machining seizou-b_plating
```

確かめ方: 2 社の「取引先との接続」の詳細 → 「鍵の設定」が `済み` (VPS で確かめる)・A 社の `… manage.py seizou_doctor --production` → `良い  つながり 白樺めっき工業 (送る) の受け口の URL: https` と `良い  つながり 白樺めっき工業 (送る) の鍵: ある (SEIZOU_PARTNER_B_PLATING_SECRET)` の行と、`点検の結果: 良い 22 件・直す 0 件`
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 17. A 社から発注して、B 社の受信した注文に出るまで

A 社の「発注」の「新規登録」で、B 社への発注を保存します。保存すると、注文のデータが B 社へ送られます。B 社の「受信した注文」に新しい行が出たら、行の「承認」を押し、「注文を受け入れる」の画面で保存します。

届かないときは、次の 5 つを見ます。

- A 社の発注の詳細の、送った結果の字
- B 社のサーバーの記録 (`sudo journalctl -u seizou-b_plating -n 50`)
- 2 社の時計 (2 の `timedatectl`)
- `.env` の鍵の置き場の名前と、足した後の起動し直し (16)
- SELinux の記録 (`sudo ausearch -m AVC,USER_AVC -ts recent -i`)

確かめ方: B 社の「受信した注文」を開いておく → 新しい行が出る (開いたまま 2 秒ごとに足される)・A 社の発注の一覧 → 状態が「送った」から「相手が受け入れた」に変わる (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 18. 毎日の控えと、版を上げるとき

毎日の控えは、[operations.md](operations.md) の「毎日の控え」の cron の 1 行を、利用者 `seizou` の cron に 2 社分足します。

```
sudo crontab -u seizou -e
```

足す 2 行は、道を VPS の物にした次の形です。

```
0 2 * * * cd /srv/seizou-sites/a_machining && /srv/seizou/src/.venv/bin/python manage.py seizou_backup --keep 30 >> backups/backup.log 2>&1
10 2 * * * cd /srv/seizou-sites/b_plating && /srv/seizou/src/.venv/bin/python manage.py seizou_backup --keep 30 >> backups/backup.log 2>&1
```

毎朝の点検をメールで届けるには、[operations.md](operations.md) の「毎日の控え」の点検の 1 行を、同じ cron に 2 社分足します。

```
0 6 * * * cd /srv/seizou-sites/a_machining && /srv/seizou/src/.venv/bin/python manage.py seizou_doctor --mail >> backups/doctor.log 2>&1
10 6 * * * cd /srv/seizou-sites/b_plating && /srv/seizou/src/.venv/bin/python manage.py seizou_doctor --mail >> backups/doctor.log 2>&1
```

`.env` は控えに入らないので、2 社の `.env` を VPS の外の安全な所に写します。控えのフォルダも、VPS の外に写します。

VPS の外へ暗号で写す例は、[operations.md](operations.md) の「外への写し」の節にあります。取引の記録を残す期間・止まったとき (初動)・定期の仕事と見張り・土台の寿命も、同じ文書の同じ名前の節にあります。

版を上げるときは、[operations.md](operations.md) の「版を上げる手順」の 8 段のとおりに進めます。VPS では、次の段を読み替えます。

- 2 段目 (サーバーを止める): `sudo systemctl stop seizou-a_machining seizou-b_plating`
- 3 段目 (入れ直す): `sudo -u seizou -H git -C /srv/seizou/src pull` と、5 の `uv sync … --extra server` の 1 行
- 4〜6 段目 (`migrate`・点検・`collectstatic --no-input`): 2 社とも打つ
- 6 段目の後: `sudo restorecon -R /srv/seizou-sites /srv/seizou/src/.venv/bin` (入れ直しで増えたファイルの context をそろえる)
- 7 段目 (サーバーを立てる): `sudo systemctl restart seizou-a_machining seizou-b_plating`

確かめ方: `sudo -u seizou -H /srv/seizou/src/.venv/bin/python /srv/seizou-sites/a_machining/manage.py seizou_restore --list` → 控えの名前 (`20` で始まる日時) の行が並ぶ (作者の手元では、`seizou_backup` の後に `20261104-060125  2026-11-04T06:01:25  sqlite  seizou 0.1.0  0.7 MB` の形の 1 行を見た)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)

## 19. 2 社を OS の利用者で分け、受け口に上限を置く (後から)

この段は、1 から 18 で立てた後に、2 社の守りを固めるときに打ちます。本番の 2 社をこの形にするのは、会社の担当が決めてからです。

1 から 18 の形では、2 社が同じ OS の利用者 `seizou` で動きます。片方の会社の process でコードが動くと、もう片方の会社の DB・`.env`・図面も読めます。この段では、会社ごとに OS の利用者を分けます。あわせて、誰でも送れる受け口 (`/connect/receive/order/`) に、nginx で回数の上限を置きます。

### 会社ごとの OS の利用者

会社の利用者を作り、置き場の持ち主をその利用者にします。

```
sudo useradd --system --user-group --no-create-home --home-dir /srv/seizou-sites/a_machining --shell /sbin/nologin seizou-a_machining
sudo chown -R seizou-a_machining:seizou-a_machining /srv/seizou-sites/a_machining
sudo chgrp nginx /srv/seizou-sites/a_machining
sudo chmod 750 /srv/seizou-sites/a_machining
sudo chmod 600 /srv/seizou-sites/a_machining/db.sqlite3 /srv/seizou-sites/a_machining/.env
sudo chmod 700 /srv/seizou-sites/a_machining/backups /srv/seizou-sites/a_machining/practice
```

B 社は、`seizou-b_plating` と `/srv/seizou-sites/b_plating` で同じ命令を打ちます。

- `chown -R` は置き場のフォルダの組も会社の利用者の組に替えるので、10 と同じ `chgrp nginx` と `chmod 750` を打ち直します。
- `db.sqlite3` と `.env` は持ち主だけが読み書きする 600、控え (`backups/`) と練習モードの写し (`practice/`) は持ち主だけが入れる 700 にします。
- seizou の置き場 `/srv/seizou/src` は、利用者 `seizou` の物のままにし、会社の利用者からは読めるだけにします。4 の `useradd --create-home` が作った `/srv/seizou` が `ls -ld /srv/seizou` で `drwx------` なら、会社の利用者が中の python と gunicorn に届かないので、`sudo chmod 711 /srv/seizou` で通るだけを許します (答えの YAML は 6 で 600 にしてあるので、読めません)。

毎日の控えの cron は、会社の利用者の cron に移します。18 で利用者 `seizou` の cron に足した行のうち、その会社の行を写し、`seizou` の cron からは消します。

```
sudo crontab -u seizou-a_machining -e
sudo crontab -u seizou -e
```

この後の seizou の命令 (点検・`migrate`・控え・戻しなど) は、`sudo -u seizou-a_machining -H <命令>` の形で、会社の利用者として打ちます。18 の版を上げる手順の 4〜6 段目も同じです (3 段目の `git pull` と `uv sync` は、今までどおり利用者 `seizou` で打ちます)。

### unit の利用者と守りの行

11 の unit (`/etc/systemd/system/seizou-a_machining.service`) の `User=` と `Group=` を会社の利用者にし、守りの 4 行を足します。守りを足すと gunicorn の制御の socket が作れないので、`ExecStart` に `--no-control-socket` を足します (11 の最後の箇条と同じ)。`[Service]` は次の形になります。

```
[Service]
User=seizou-a_machining
Group=seizou-a_machining
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ReadWritePaths=/srv/seizou-sites/a_machining
WorkingDirectory=/srv/seizou-sites/a_machining
Environment=DJANGO_SETTINGS_MODULE=settings
Environment=TMPDIR=/srv/seizou-sites/a_machining/upload-tmp
ExecStart=/srv/seizou/src/.venv/bin/gunicorn --no-control-socket --chdir /srv/seizou-sites/a_machining --pythonpath /srv/seizou-sites/a_machining --bind 127.0.0.1:8001 --workers 1 --threads 4 --timeout 90 'django.core.wsgi:get_wsgi_application()'
Restart=on-failure
```

```
sudo systemctl daemon-reload
sudo systemctl restart seizou-a_machining seizou-b_plating
```

- `NoNewPrivileges=yes` は、gunicorn とその子の process が、権限を上げるプログラムで権限を上げられないようにする行です。
- `PrivateTmp=yes` は、`/tmp` をこの service だけの物にする行です。
- `ProtectSystem=strict` は、OS のファイルの全体を読むだけにする行で、`ReadWritePaths=` で書けるのを会社の置き場の中だけにします。

### 受け口の nginx の上限

12 の conf (`/etc/nginx/conf.d/seizou-a_machining.conf`) の頭 (server の外) に `limit_req_zone` の 1 行を足します。server の中 (13 で certbot が `listen 443 ssl` を足した server の、`location /` の前) には、受け口の location を足します。上限の名前 (`seizou_receive_a`) は会社ごとに分けます (B 社は `seizou_receive_b` で、ポートは `8002`)。

```
limit_req_zone $binary_remote_addr zone=seizou_receive_a:10m rate=30r/m;

server {
    (今の行はそのまま)

    location = /connect/receive/order/ {
        limit_req zone=seizou_receive_a burst=20 nodelay;
        client_max_body_size 3m;
        proxy_pass http://127.0.0.1:8001;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_set_header X-Seizou-Proxy nginx;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 90s;
    }

    location / {
        (今の行はそのまま)
    }
}
```

```
sudo nginx -t
sudo systemctl reload nginx
```

- `limit_req_zone` は、送り元の IP ごとに回数を数える場所 (10MB) と、速さ (1 分に 30 回) を決める行です。server の外にしか書けません。
- `burst=20 nodelay` は、続けて来た 20 回までは待たせずに通し、それを超えた分を断る印です。nginx は断った要求に 503 を返します。
- 数 (1 分に 30 回・burst 20) は、2 社の送り合いに十分な目安で、会社が変えてかまいません。相手が会話の添付を取りに来る要求も同じ受け口を通るので、同じ数に入ります。
- `client_max_body_size 3m` は、受け口の本文が JSON だけ (会話の添付の中身は後で取りに行く) なので、Django の本文の既定の上限 (2.5MB) を少し超える値にしました。
- `proxy_set_header` の 4 行と `proxy_read_timeout 90s` は、12 の `location /` の物を写します。`location /` に書いた行は、ほかの location には効かないためです。

確かめ方: `sudo -u seizou-a_machining test -r /srv/seizou-sites/b_plating/.env || echo 読めない` → `読めない`・`systemctl show seizou-a_machining -p User` → `User=seizou-a_machining`・`sudo nginx -t` → `syntax is ok` と `test is successful`・`for i in $(seq 30); do curl -s -o /dev/null -w '%{http_code} ' -X POST https://a.example.jp/connect/receive/order/; done` → 署名の無い要求を断る `400` が並び、途中から `503` になる (VPS で確かめる)
確かめ方: `getenforce` → `Enforcing`・`sudo ausearch -m AVC,USER_AVC -ts recent` → `<no matches>` (VPS で確かめる)
