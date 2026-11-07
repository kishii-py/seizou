# 安全の方針

## 報告の仕方

安全上の問題を見つけたら、公開の Issue や PR には書かず、次のアドレスへメールで知らせてほしい。直した版を出す前に問題が広く知られると、使っている会社が守る手を打つ前に狙われるため。

- 報告先: [空欄: 会社の OSS 用のメールアドレス・本人が埋める]
- 返事の目安: [空欄: 最初の返事までの日数・本人が埋める]

## 対応の方針

- 問題を直した版を出す。
- 直した事を [CHANGELOG.md](CHANGELOG.md) に書く。
- 立ち上げた会社のシステムへは、Claude Code のプラグインが、公開リポジトリの最新の版と手元の版を比べて知らせる (予定)。

## 作りの要点

下は、今のリポジトリにある物。2 段階の確かめのような、まだ無い物は下の「会期後の予定」に並べた。

- 注文を受け取る窓口は、署名の合う注文のデータだけを受ける。署名は Standard Webhooks の形で、鍵は取引先ごとに別にする。送った時刻が決めた幅 (前後 5 分) を外れた物は受けない。同じ番号の注文のデータは 1 回だけ登録する。
- 鍵は会社のプロジェクトの `.env` にだけ置き、設定ファイルとリポジトリに書かない。
- 取引先の鍵の値の形は、Standard Webhooks の公開の仕様の形 (接頭辞の付いた base64) にする。値の置き場は会社のプロジェクトの `.env` だけで、環境変数の名前は `SEIZOU_PARTNER_<相手の会社の id を大文字にした物>_SECRET` にする。つながりの表には環境変数の名前だけを持ち、値は表・画面・ログに出さない。注文をやり取りする両方の会社が、同じ値を持つ。
- 取引先の鍵の値は、会社のプロジェクトで `manage.py seizou_secret <鍵の置き場の名前>` を打って作る。命令は値を 1 つ作り、`<名前>=<値>` の 1 行を 1 度だけ画面に出す。ファイルには書かない。値を会話・チャット・commit に貼らず、AI の道具にこの命令を打たせない。
- 注文書の読み取りの API の鍵は、環境変数 `ANTHROPIC_API_KEY` に置く。会社のプロジェクトの `.env` に書いてよく、会社の `settings.py` が起動のときに読む (既にある環境変数が先)。リポジトリと設定ファイルには書かない。鍵の値は、画面・ログ・誤りの文に出さない (誤りの文は、鍵が無い・鍵が合わない・混んでいる・残高が足りないなどの理由だけを日本語で書く)。読み取りを使うと、選んだ注文書のファイルの中身が Anthropic の API へ送られる。鍵を置かない会社では送られない。読めるファイルは 20MB までで、PDF・PNG・JPEG だけ。
- AI チャット (`seizou.assistant`。左のメニューの「AI に質問」で開く窓) は、同じ鍵 (`ANTHROPIC_API_KEY`) があるときだけ入力の欄を出す。質問と、画面の説明書と、読む道具 (MCP サーバーと同じ物。部品・受注と注文残・指示書・出荷・業務マニュアルの中身。単価と金額を含む) の結果を Anthropic の API へ送る。鍵を置かない会社では送らない。書く道具 (受注の登録・出荷・工程の予定の置き直し) は Claude が直に保存せず、窓に出た提案のカードを人が「承認」したときに、承認の口 (`/assistant/approve/`。ログインした人の POST だけ。CSRF の守りを通る) が、その人の権限の判定・画面と同じ検査の層・部品の services を通して保存する。提案は利用者の session にだけ残り、60 分を過ぎた物は承認できない。会話も session にだけ残る。答えは誤りうるので、数は答えの link の先の画面で確かめる。
- フィードバックの AI の聞き取り (`seizou.feedback`) は、同じ鍵 (`ANTHROPIC_API_KEY`) があるときだけ、フィードバックの入力の画面に AI の段を出す。利用者が書いた字と、会社の名前・今日の日付・開いていた画面の名前と、読む道具 3 つ (画面の目録・画面ごとの説明書・会社の設定 = 選べる設定の今の値と入っている部品) の結果を Anthropic の API へ送る。受注・部品・出荷のデータを読む道具と、書く道具は渡さない。鍵を置かない会社では送らない。会話は利用者の session にだけ残り、下書きを作ると下書きの行の会話の記録に写る。下書きは会社の DB に残る。GitHub へ送るのは、世話役 (アカウント管理の担当者) が「GitHub とつなぐ」の画面で GitHub の口座のトークンを置いた会社だけで、送るのは世話役が「確認済み」にした下書きの「Issue の文」(取引先マスターの名前と金額らしい数は「〇〇」に伏せ、会話の記録は入れない) だけ。トークンは会社の置き場の `.env` にだけ置き (持ち主だけが読める形)、画面・ログ・誤りの文に出さない。トークンを置かない会社は送らず、世話役が Issue の文を手で写す。GitHub の Issue を探す・取り込む読みは、トークンが無くてもできる (公開のリポジトリを読むだけ)。
- フィードバックの GitHub の口 (`seizou.feedback`) のトークンは、世話役が画面で貼るか、GitHub のデバイスの認証 (OAuth App。Client ID は公開の値で、client secret は持たない) で取り、会社の置き場の `.env` にだけ置く。途中の device_code は利用者の session にだけ置き、表に書かない。トークンの値は画面・記録・AI・誤りの文に出さない。
- 手元の Claude Code から操作する MCP サーバー (`manage.py seizou_mcp`) の書く道具 3 つ (受注を作る・出荷する・計画を置く) は、`--user` か `.env` の `SEIZOU_MCP_USER` で決めた利用者で書き、画面と同じ決まりとポカよけを通る。知らせる段のポカよけは `confirm=true` の呼び直しが要る。人の承認は、Claude Code の道具ごとの許可と、この呼び直しに乗る。書いた物が MCP からか画面からかの区別と、誰が書いたかを残す列は、まだ無い (会期後の予定)。`--read-only` で起こすと、書く道具を出さない。プラグインの起こし役 (`plugin/mcp/start.py`) は、置き場の一覧 (`~/.seizou/sites.json`。`seizou new` と `seizou ai-docs` が登録する) にある置き場だけを一覧の python で起こし、開いたフォルダの `README.md` から python を読まない (人から受け取ったフォルダを Claude Code で開いても、起こし役がその中の実行ファイルと `manage.py` を動かさないため)。
- 管理者 (`admin`) のパスワードは、`seizou new` が乱数で作り、出力に 1 度だけ出る。`--password-file <道>` を付けると、出力に出さず、持ち主だけが読めるファイルに `admin:<値>` の 1 行で書く。`/seizou:build` は `--password-file ~/seizou-<id>-admin.txt` を付けて打ち、パスワードを Claude Code の会話に出さない。担当者が自分でそのファイルを開き、ログインしたら消す。Claude はこのファイルを読まず、cat もしない (skill の文の約束で、仕組みで止める物ではない)。
- `/seizou:build` は、uv が無ければ公式の版つきの URL (`https://astral.sh/uv/<版>/install.sh`。Windows は `install.ps1`) で入れ、0.11.0 より古い uv は `uv self update` で上げる。seizou の部品は、clone した seizou のフォルダで `uv sync --locked` を打ち、`uv.lock` のとおりの版で入れる。
- 会社の置き場で開いた Claude Code に `.env`・`db.sqlite3`・`backups/` を読ませない設定を、置き場の `.claude/settings.json` (permissions.deny。`Read(/.env)`・`Read(/.env.*)`・`Edit(/.env)`・`Read(/db.sqlite3)`・`Read(/backups/**)`・`Bash(*seizou_secret*)`) に置く。`seizou new` と `seizou ai-docs` が書き、既にあれば触らない。プラグインの hook (`plugin/hooks/guard_core.py`) は、Write・Edit・NotebookEdit で部品 (seizou) の中身に書くのを止める。どちらも AI のうっかりの読みと書きを止める物で、全部の道を塞ぐ物ではない。shell (Bash の道具で打つ `cat` や `python`)・`manage.py diffsettings`・`grep -r`・子の process (起動した `manage.py` は `.env` を読む) での読みは止まらず、Bash の道具での書き込みも hook は見ない。
- Django の `SECRET_KEY` は、`seizou new` が会社のプロジェクトを作るときに乱数で作り、`.env` に書く。`.env` は持ち主だけが読める権限で書く (Windows では、この権限は働かない)。
- ログインは Django 標準の認証を使う。開発機の自動ログイン (`seizou.core.middleware.DevAutoLoginMiddleware`) は、`DEBUG` が真で、起動が `runserver` で、`.env` の `SEIZOU_DEV_AUTO_LOGIN=1` のときだけ動き、ログインしていない要求を有効な superuser の要求として扱う。本番の形 (gunicorn・`DEBUG=0`) では動かない。会社の置き場を外から届く所で `runserver` で立てない。
- ログインの画面は、同じ ID と同じ接続元 (`REMOTE_ADDR`) で 5 回続けて失敗すると、15 分の間はその組を受け付けない (正しいパスワードでも)。数はサーバーの cache に ID と接続元の組の要約 (sha256) で持ち、成功で消す。締め出したときはサーバーの記録 (`seizou.login`) に 1 行出す (パスワードは書かない)。`seizou new` が書く `settings.py` は、本番 (`DEBUG=0`) でセッションと CSRF の Cookie を HTTPS の道でだけ送り、セッションの Cookie を JavaScript から読ませない。htmx の部分の要求でログインが切れていたら、部分を差し替えずに画面の全体をログインの画面へ移す (`seizou.core.middleware.HtmxLoginMiddleware`)。保存のボタンは押すと止まり、同じ form を 2 回送らない (画面の JavaScript。サーバーの側の確かめの代わりではない)。
- 画面からの書き込みは、Django の CSRF の守りを通す。
- PDF を作るときに読むファイルは、static と media の中だけに限る。
- 上げたファイル (`/media/` の図面・添付・マニュアルの画像) は、開発機でも本番でも同じ口 (`seizou.core.media`) がログインを見てから返す。`MEDIA_ROOT` の外は読まず、断るときは全部 404 にする (「無い」と「見せない」を外から見分けさせない)。HTML・SVG・XML・JS は必ずダウンロードにし (上げたファイルのスクリプトを同じ origin で動かさない)、nosniff と保存させないヘッダーを付ける。nginx に送らせる配り方 (`SEIZOU_FILE_DELIVERY=nginx`。置き場の `deploy/nginx-files.conf`) では、nginx を通った印の無い要求を 503 で止める。
- 上げたファイルの口がブラウザの中で開くのは、PNG・JPEG・WebP・GIF・PDF・テキストの型だけにし、ほかの型はダウンロードにして `Content-Security-Policy: sandbox` を付ける (nginx に送らせる配り方では、置き場の `deploy/nginx-files.conf` が同じ見出しを付ける)。注文書の読み取りの元のファイル (単価が入る) は、権限の判定の金額の閲覧が有効 (on) のとき、金額を見られない人には返さない (404)。
- PDF を作る部品 (WeasyPrint) か OS の pango が無いときは、PDF を作らず、印刷用の画面に落ちる (`seizou.core.reports.PdfUnavailable`)。
- SQL を直接書かない (決まりの検査が止める)。
- 依存の下限は、知られた問題を直した版に置く。`Django>=5.2.18,<6.2`・`Pillow>=13.0.0`・`weasyprint>=70.0`。

## 取引先の鍵の作り方

1. 片方の会社の担当者が、自分の端末で `manage.py seizou_secret <鍵の置き場の名前>` を打つ。命令は値を 1 つ作り、`<鍵の置き場の名前>=<値>` の 1 行を画面に 1 度だけ出す。ファイルには書かない。値が会話に残るので、AI の道具には打たせない。
2. 出た 1 行を自社の `.env` に足し、= の後ろの値を相手の会社の担当者へ人の手で渡す。相手は自社の `.env` に、相手の側の鍵の置き場の名前 (`SEIZOU_PARTNER_<こちらの会社の id を大文字にした物>_SECRET`) で、同じ値の 1 行を足す。
3. 両方の会社で起動し直し、つながりの画面の「鍵の設定」が「済み」になったことを見る。値は会話・チャット・commit・画面に貼らない。

## 会期後の予定

- 2 段階の確かめ
- 依存を上げて試験を回し、再起動するまでを 1 つにした命令 (版を上げる手順は、今は [docs/operations.md](docs/operations.md) に手で進める形である)
- MCP と AI チャットから書いた記録 (誰が、どの道から) を残す列と、権限に合わせて道具を絞ること
- nginx の設定 (内部の location の `internal` など、Django から見えない物) を外から確かめる命令
