# 運用の手引き (控え・戻し・点検・版上げ)

読み手は、会社のシステムを預かる人です。プログラムは書けなくてかまいません。

VPS を 1 台借りて、2 社分の置き場を https で立て、取引先とつなぐまでの手順は [vps.md](vps.md) にあります。この文書は、立てた後の毎日の運用です。

## この文書は何か

この文書は、`seizou new` で立ち上げた会社のシステムを、毎日安心して使い続けるための手順です。毎日の控え (バックアップ) の取り方、壊したときの戻し方、立ち上げの確かめ (点検) の読み方、seizou の版を上げる手順を書きます。

命令は、置き場 (`seizou new` が作ったフォルダ。既定は `~/seizou-sites/<会社の id>/`) に移ってから、`<python> manage.py …` の形で打ちます。`<python>` は、置き場の README の「起動」の 1 行に出てくる Python と同じ物です (例 `<seizou を clone した場所>/.venv/bin/python`)。

## 毎日の控え (バックアップ)

次の命令で、控えを 1 つ取ります。

```
<python> manage.py seizou_backup
```

この命令は、置き場の `backups/` の下に、日時の名前のフォルダ (例 `backups/20261103-020000/`) を 1 つ作ります。中身は次の 4 つです。

- データベースの写し (SQLite は `db.sqlite3`、PostgreSQL は `db.pgdump`)
- 上げたファイル (図面・添付・マニュアルの画像) の写し `media/`
- 会社の答え `answers.yaml` の写し (置き場にあるときだけ)
- 中身の一覧 `backup.json` (取った日時・データベースの種類・会社の id・seizou の版・上げたファイルの数)

`.env` (鍵) は控えに入りません。`.env` だけは、控えとは別の安全な場所に人の手で写してください。中身を人に見せたり、メールやチャットに貼ったりしません。

控えのフォルダは、同じ PC が壊れたときに備えて、別の PC か外の記録装置にも写してください。遠くの置き場へ自動で控える仕組みはありません。置き場を変えるときは `--to <フォルダ>` を付けます。

毎晩 2 時に自動で控えるには、cron に次の 1 行を足します (置き場と Python の道は例です。自分の物に置き換えてください)。

```
0 2 * * * cd /path/to/site && /path/to/.venv/bin/python manage.py seizou_backup >> backups/backup.log 2>&1
```

古い控えは自動では消えません。30 日より前の控えを消すには、先に `<python> manage.py seizou_restore --list` で一覧を見てから、次の 1 行を打ちます。

```
find /path/to/site/backups -maxdepth 1 -type d -name '20??????-??????*' -mtime +30 -exec rm -r {} +
```

PostgreSQL の置き場では、PostgreSQL の命令 `pg_dump` が要ります。`pg_dump` が見つからないとき、命令は何も書かずに止まり、手で取るための 1 行を出します。

`settings.py` の `MEDIA_ROOT` が置き場そのものかその親のフォルダを指しているときは、控えに置き場の全部 (`.env` を含む) が入り、戻しが置き場ごと消すのを防ぐため、控えと戻しは何も書かず何も消さずに止まります (`seizou new` が書く `MEDIA_ROOT = BASE_DIR / "media"` では起きません)。

## 戻す (復元)

戻すと、今のデータベースと上げたファイルが、控えを取った時点の物に置き換わります。次の順に進めます。

1. サーバーを止めます (runserver の窓を閉じるか、サーバーの service を止める)。
2. `<python> manage.py seizou_restore --list` で控えの一覧を出し、戻す控えの名前を選びます。
3. `<python> manage.py seizou_restore <控えの名前>` を打ちます。
4. 「本当に戻しますか?」の問いに `yes` と打ちます。ほかの答えでは何も変えずに止まります。
5. 控えの seizou の版が今より古いときは、`<python> manage.py migrate` を打ちます。
6. `<python> manage.py seizou_doctor` で点検し、直す物があれば直します。
7. サーバーを立てます。

命令は、戻す前の今の物を `backups/<日時>-before-restore/` に控えます。戻したことを取り消すには、同じ命令にその名前を渡します (`<python> manage.py seizou_restore <日時>-before-restore`)。

上げたファイルは、控えの時点の物にそろいます。控えの後に上げたファイルは消えますが、`<日時>-before-restore` の中に残っています。`answers.yaml` は戻しません (置き場の `settings.py` と対の物で、データではないため)。

命令は、別の会社の控えと、データベースの種類が違う控えを戻しません。

PostgreSQL の置き場では、命令は戻さずに、`pg_restore` で戻す 1 行を出して止まります。その 1 行で戻し、上げたファイルは控えの `media/` を `MEDIA_ROOT` (置き場の `media/`) に写してください。

## 立ち上げの確かめ (点検)

次の命令で、置き場を点検します。点検は読むだけで、何も書き換えません。

```
<python> manage.py seizou_doctor
```

打つのは、立ち上げた直後、本番にする前 (`--production` を付ける)、版を上げた後、戻した後です。
`seizou new` も、終わりにこの命令の 1 行を出します。

命令は 1 行に 1 つ、`良い  <見た物>: <様子>` か `直す  <見た物>: <様子> → 直し方: <すること>` を出します。最後に「点検の結果: 良い N 件・直す M 件」を出します。直す物が 1 つでもあれば、終わりの番号 1 で終わります (cron や監視の道具が見分けられます)。

見る物は次のとおりです。鍵は、値を出さずに、有るか無いかだけを見ます。

- `SECRET_KEY` (置いてあるか。短すぎないか)
- `.env` の権限 (持ち主だけが読めるか)
- 開発の設定 (`DEBUG`)
- 開発機の自動ログイン (本番の条件だけ)
- `ALLOWED_HOSTS` (ほかの PC から開く名前があるか。本番の条件だけ)
- migrate の漏れ
- Django の起動の検査 (`check`)
- 上げたファイルの置き場 (`MEDIA_ROOT` に書けるか)
- 上げたファイルの置き場の場所 (`MEDIA_ROOT` が置き場そのものか、その親を指していないか。指していれば、控えと戻しは止まる)
- 控えの置き場 (`backups/` に書けるか)
- CSS (seizou の画面の見た目のファイルがあるか)
- `collectstatic` (本番の条件だけ)
- 上げたファイルの配り方 (nginx に渡すときは、`deploy/nginx-files.conf` の `internal;` と alias の道)
- 会社の選べる設定 (`answers.yaml` の `settings` の値が選べる値の中か)
- つながりの鍵 (取引先ごとの鍵の置き場の名前が `.env` にあるか)
- つながりの受け口の URL (`https://` か。本番の条件だけ)
- Cookie が HTTPS だけ (セッションと CSRF の Cookie。本番の条件だけ)

「本番の条件」は、`.env` の `DEBUG` が 0 のときか、`--production` を付けたときです。開発の設定 (`DEBUG` が 1) のままでは、本番の条件の行を出さずに、その案内を 1 行出します。HTTPS の細かい設定は、`<python> manage.py check --deploy` で見ます。LAN の中だけで使う置き場では、当たらない項目もあります。

## 本番の守り (HTTPS・ログイン・稼働の確認)

本番 (`.env` の `DEBUG=0`) で、seizou が自分で守る物と、置き場で確かめる物です。

### 稼働の確認の口 `/health/`

`/health/` は、ログインを見ずに `{"ok": true}` を返します。データベースは読みません (サーバーが要求に答えるかだけを見ます)。監視の道具や nginx の確かめは、この URL を開きます (例 `curl -fsS https://<このシステムの名前>/health/`)。名前は `.env` の `ALLOWED_HOSTS` にある物を使います (無い名前では 400 になります)。データベースと置き場の確かめは `seizou_doctor` で見ます。

### HTTPS と Cookie

本番では、ログインの Cookie (セッションと CSRF) を HTTPS の道でだけ送ります (`seizou new` が書く `settings.py` の `if not DEBUG:` の行)。nginx で https を受け、Django へ渡す location (`proxy_pass` を書いた所) に次の 3 行を書きます。3 行目は、下の「ログインの締め出し」が接続元を見分けるための行です。

```
proxy_set_header Host $host;
proxy_set_header X-Forwarded-Proto $scheme;
proxy_set_header X-Real-IP $remote_addr;
```

LAN の中だけで http で開く置き場では、この形のままではブラウザが Cookie を送らず、ログインできません。その置き場は、`settings.py` の `SESSION_COOKIE_SECURE` と `CSRF_COOKIE_SECURE` の 2 行を消します (点検の「Cookie が HTTPS だけ」は「直す」のまま残ります)。

上の帯の「現品票を読む」(QR のボタン) でカメラを使うのは、https の住所で開いたときだけです (ブラウザの決まり)。http で開く LAN の置き場では、小窓の「指示書」の欄で探します。今までどおり、スマホのカメラのアプリで現品票の QR を読んで開くこともできます。現品票の QR には、刷った時に開いていた住所が入ります。「現品票を読む」は、今開いている住所と同じ住所の現品票だけを開きます。

### ログインの締め出し

ログインの画面で、同じ ID と同じ接続元 (IP) から 5 回続けて失敗すると、15 分の間はその組のログインを受け付けません (正しいパスワードでも)。画面には「ログインに続けて失敗したので、15 分の間は受け付けません。」と出ます。ログインに成功すると数え直します。締め出したときは、サーバーの記録 (`seizou.login`) に 1 行出ます (パスワードは書きません)。

数は、置き場の `cache/login/` のファイルに持ちます (同じ置き場で動く gunicorn の worker と process の全部で 1 か所に数えるので、worker を複数にしても 5 回目で締め出します)。15 分より早く解くには、`cache/login/` の中のファイルを消します (サーバーを立て直しても数は残ります)。置く場所は `settings.py` の `SEIZOU_LOGIN_CACHE_DIR` で変えられます。

nginx の後ろでは、Django から見た接続元 (`REMOTE_ADDR`) がいつも nginx (127.0.0.1) になり、ID ごとの締め出しになります (ほかの人が、ある ID を 15 分止めることもできます)。その置き場は、`.env` に `SEIZOU_CLIENT_IP_HEADER=X-Real-IP` の 1 行を足し、nginx の `proxy_set_header X-Real-IP $remote_addr;` (上の「HTTPS と Cookie」の 3 行目) の値を接続元にします。nginx は、外から来た同じ名前の見出しをこの値で上書きします。手前に nginx のような proxy が無い置き場では、この行を書きません (書くと、利用者が見出しで接続元を偽れます)。CDN (Cloudflare など) を通す置き場では、nginx の realip で CDN から来た要求の接続元を取り直してから渡します。

### ログインが切れたときと、2 回押し

一覧の検索や小窓の途中でログインが切れても、画面の一部にログインの画面がはまり込まず、画面の全体がログインの画面に移ります。ログインの後は、元の画面へ戻ります (`settings.py` の `MIDDLEWARE` の `HtmxLoginMiddleware`)。

保存のボタンは、押すと「処理中…」になり、同じ画面から 2 回送りません。htmx で送る欄も、送っている間は送るボタンを押せません。

### 会社どうしの送る先を外の名前に絞る

`settings.py` に `SEIZOU_CONNECT_PUBLIC_ONLY = True` の 1 行を書くと、会社どうしのつながりの送る先を外の名前に絞ります。既定 (書かないとき) は今のままで、名前を引かず、http の URL も通します。会社の設定の画面からは変えられません。

真にすると、次の 2 つが変わります。

- 取引先との接続の「送る」の URL は、`https://` で始まる物だけを保存できます。`http://127.0.0.1` や `http://localhost` も保存できません。
- 送る前に URL の名前を引き、引いた番地のどれか 1 つでも社内か手元の番地 (`10.0.0.5`・`127.0.0.1`・`169.254.169.254` など) なら送りません。返事の欄には「送っていません: 受け口の URL の名前が社内か手元の番地 (…) を指しています。」と出ます。名前が引けないときも送りません。

VPS の置き場で勧めます。入れる前に、VPS の中で相手の名前を引き、外の番地を指すかを確かめます (例 `getent hosts <相手の名前>`)。真にする前に保存した「送る」の URL は、接続の一覧で https かを確かめます (保存し直すまで http のまま残ります)。LAN の中だけで http で開く置き場と、手元で 2 社を立てて送り合う置き場では、この行を書きません (社内の PC の名前と `http://127.0.0.1` へ送れなくなります)。

限り: 名前を確かめた後に、送る道具がもう 1 度名前を引きます。その間に名前の向きを変える攻撃 (DNS rebinding) は止めません。

## 版を上げる手順

seizou の新しい版を入れるときは、次の順に進めます。

1. `<python> manage.py seizou_backup` で控えを取ります。
2. サーバーを止めます。
3. seizou を入れ直します。git から入れた人は `cd <seizou を clone した場所> && git pull && uv sync --locked`、PyPI の版を使う人は `pip install -U seizou` か `uv pip install -U seizou` です。本番の VPS ([docs/vps.md](vps.md) の 5 の形) では `uv sync --locked --no-dev --extra pdf --extra server` です。`--extra pdf` が無いと PDF の部品 (weasyprint) が入らず、`--extra server` が無いと本番の Web サーバーの gunicorn が入りません (gunicorn は extra `server` に入っています。この版より前に別に入れた gunicorn を残す置き場は、`--inexact` も付けます)。
4. `<python> manage.py migrate` を打ちます。
5. `<python> manage.py seizou_doctor` で点検し、直す物があれば直します。
6. 本番で nginx が静的ファイルを配る形なら、`<python> manage.py collectstatic --no-input` を打ちます。
7. サーバーを立てます (runserver の 1 行か、サーバーの service の再起動。手元の 2 社のデモは `uv run python scripts/sites.py restart`)。
8. 画面を 1 つ開いて、ログインと一覧が出ることを確かめます。

版を上げた後に、置き場ごとに `seizou ai-docs <置き場>` を打ちます (git から入れた人は clone した場所で `uv run seizou ai-docs <置き場>`)。この命令は置き場を、プラグインの MCP が起こしてよい置き場の一覧 (`~/.seizou/sites.json`) に、打った python で登録します (登録の無い置き場では、プラグインの MCP は立ちません)。Claude Code に `.env`・DB・控えを読ませない設定 (`.claude/settings.json`) が無い置き場 (この版より前に作った物) には、それも書きます (既にあれば触りません)。`CLAUDE.md` と `AGENTS.md` が既にある置き場では「止まりました: 既にあります」と出ますが、登録と settings.json はその前に済んでいます。

うまく動かないときは、前の版に戻します。PyPI の版は `pip install seizou==<前の版>`、git から入れた人は `git checkout <前の tag>` と `uv sync --locked` です。続けて、サーバーを止めてから、`<python> manage.py seizou_restore <1 で取った控えの名前>` で戻します。

`settings.py` に次の行が無い置き場 (この版より前に `seizou new` で作った物) は、版を上げるときに足します。`ALLOWED_HOSTS` の行の後に、次の塊を足します。Cookie の 2 行は、HTTPS で開く置き場だけです (上の「HTTPS と Cookie」)。

```
SESSION_COOKIE_HTTPONLY = True
if not DEBUG:
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
```

`MIDDLEWARE` の `"seizou.core.middleware.DevAutoLoginMiddleware",` の次の行に、`"seizou.core.middleware.HtmxLoginMiddleware",` を足します。

画面ごとの説明書の断片が無い置き場 (この版より前に、`--demo` を付けずに `seizou new` で作った物) は、版を上げた後に、部品の中の標準の組を置き場の `manual/` に写します: `cp -n <seizou を clone した場所>/src/seizou/core/manual_default/*.html <置き場>/manual/`。`-n` は、同じ名前のファイル (自社で直した断片) を上書きしない印です。

## 困ったとき

`manage.py` が `KeyError: 'SECRET_KEY'` で止まるときは、置き場に `.env` が無いか、`.env` に `SECRET_KEY=` の行がありません。`.env` は控えに入らないので、別に写しておいた `.env` を置き場に戻してください。このときは `settings.py` が先に止まるので、点検も動きません。

上げたファイルの画面が 503 で止まるときは、nginx の設定が足りません。画面の案内のとおりに直し、点検の「上げたファイルの配り方」の行を見てください。

どの画面も「決まりに沿っていません」になるときは、開発の設定 (`DEBUG` が 1) で、会社の側のコードが決まりを破っています。画面の文を写して、Claude Code の `/seizou:fix` で直してください。

## 練習モード (本番に残らない練習用のサーバー)

練習モードは、新しく入った人の練習や、手順の確かめに使います。今のデータベースと上げたファイルの写しを使うサーバーを、本番のサーバーを止めずに、別のポートで立てます。練習の画面での登録・変更・削除は写しにだけ入り、本番には残りません。練習のサーバーでは、全部の画面の上に黄色の帯「練習モード」が出て、ブラウザの題の頭に「練習:」が付きます。

### 立て方

置き場で次の命令を打ち、出た URL をブラウザで開きます。

```
<python> manage.py seizou_practice start
```

ポートの既定は、会社のポートに 100 を足した番号です (例 8001 の会社は 8101)。`--port <番号>` で変えられます。開けるのは同じ PC のブラウザだけです (127.0.0.1)。開発の設定 (`.env` の `DEBUG` と `SEIZOU_DEV_AUTO_LOGIN` が 1) の置き場では、本番と同じく自動ログインで開きます。

命令は、置き場の `practice/` の下に日時の名前のフォルダを 1 つ作り、写しと記録を置きます。写しの取り方は控え (`seizou_backup`) と同じで、本番のサーバーが動いている間でも取れます。

### 止め方

練習が終わったら、次の命令で止めます。

```
<python> manage.py seizou_practice stop
```

命令は、自分が立てた練習のサーバーを止めて、写し (`practice/` の下のフォルダ) を消します。写しは自動では消えないので、練習が終わったら必ず止めてください。立っているかは `<python> manage.py seizou_practice status` で見ます。

### 注意

- 練習のサーバーには、取引先とのつながりの鍵 (`SEIZOU_PARTNER_<相手の id>_SECRET`) を渡しません (空の値にして立てる)。練習の画面で発注を保存する・受信した注文を受け入れる・出荷しても、相手の会社には送られず、「鍵が無い」の知らせになります。相手に送る練習はできません。
- 注文書の読み取りと AI チャットも、本番と同じ鍵で API を呼びます (費用がかかります)。
- 練習モードは SQLite の置き場だけで使えます。PostgreSQL の置き場では、控え (`seizou_backup`) を別のデータベースに戻し、別のサーバーとして立ててください。
- 練習のサーバーは同じ PC の 127.0.0.1 だけで待つので、VPS など nginx の後ろの置き場では、ブラウザから練習の画面に届きません。練習は手元の PC の置き場で行うか、ssh のポートの転送が許されている置き場で、転送した先を開いてください。

### この版より前に作った置き場

この版より前の `seizou new` が作った置き場では、`settings.py` の `MEDIA_ROOT = BASE_DIR / "media"` の行の後に、次の塊を足してから使います。

```python
# 練習モード: manage.py seizou_practice start が立てるサーバーだけが、写した DB と上げたファイルの道を渡す。
# cookie の名前も分ける (同じ PC で開いた本番の画面と、ログインと CSRF の cookie が混ざらないように)。
if os.environ.get("SEIZOU_PRACTICE_DB"):
    DATABASES["default"]["NAME"] = os.environ["SEIZOU_PRACTICE_DB"]
    MEDIA_ROOT = os.environ["SEIZOU_PRACTICE_MEDIA"]
    SESSION_COOKIE_NAME += "_practice"
    CSRF_COOKIE_NAME += "_practice"
```

塊の無い置き場では、命令は練習のサーバーを立てずに止まり、取った写しを消します。塊が無いまま立てると、練習の帯を出したまま本番のデータに書いてしまうためです。
