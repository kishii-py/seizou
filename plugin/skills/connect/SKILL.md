---
name: connect
description: 取引先と注文のデータを送り合えるように、会社のシステムに取引先とのつながり (相手の窓口の URL・鍵の置き場の名前・向き) を登録する。
---

# 取引先とつなぐ

この命令は、工場で PC を任された担当者と話しながら、会社のシステムに取引先とのつながりを登録します。担当者はプログラムを書かない人です。担当者に見せる文は短く平易な日本語にし、専門の言葉を初めて使うときは短い説明を添えます。

つながりは、取引先ごとに 3 つを持つ記録です。1 つ目は相手の受け口 (注文を受け取る窓口。相手のシステムが注文のデータを受ける URL) です。2 つ目は鍵の置き場の名前 (鍵の値を入れる環境変数の名前) で、3 つ目は向き (送る か 受ける) です。鍵の値そのものは、つながりに持ちません。

## 1. 最初に言うこと

動き出す前に、次の趣旨を 1〜3 行で担当者に伝えます。

- 取引先と注文のデータ (取引先との間でやり取りする注文 1 件) を送り合えるように、つながりを登録します。
- この会社と相手の会社の両方で、この命令を 1 回ずつ打ちます。
- 鍵の値 (署名に使う秘密の値) は、この会話で聞かず、画面にも出しません。担当者が自分で置きます。

## 2. 前提を確かめる

1. 会社のシステムの置き場は `~/seizou-sites/<id>/` です (`/seizou:build` が作った所。`<id>` は会社の id = 会社を見分ける短い名前)。今の作業のフォルダがそこでなければ、どの会社かを聞きます。候補は `~/seizou-sites/` の下のフォルダの名前です。
2. 置き場に `manage.py` と `answers.yaml` が無ければ、「先に `/seizou:build` で会社のシステムを立ち上げてください」の趣旨を伝えて止まります。
3. 置き場の `README.md` の「起動」にある 1 行 (`seizou new` が書いた `cd <置き場> && <python> manage.py runserver <port>`) を読み、`<python>` の所を覚えます。この後の `manage.py` は、いつもこの python で、置き場の中で打ちます。
4. 命令が見つからないときや失敗したときは、出力をそのまま担当者に見せて止まります。手で作業を補いません。

## 3. 聞くこと

次の 4 問を 1 問ずつ聞きます。1 問を聞いたら答えを待ち、答えを読んでから次の問いを出します。答えを勝手に補いません。

問 1: つなぐ相手の取引先 (取引先コード)

- 候補として、取引先マスター (取引先の表) の取引先コードと名前を読んで見せてよいです。読む命令は次のとおりで、コードと名前だけを出します。

```
<python> manage.py shell --no-imports -c '
from django.apps import apps
for partner in apps.get_model("masters", "Partner").objects.order_by("code"):
    print(partner.code, partner.name, sep="\t")
'
```

- 相手が取引先マスターに無いときは、「取引先の画面で、先に取引先を登録してください」の趣旨を伝えて止まります。この命令は取引先を作りません。

問 2: 向き

- 送る (`send`): この会社が発注を送る相手です。
- 受ける (`receive`): 相手から注文を受け取ります。

問 3: 相手の受け口の URL (送るときだけ聞く)

- 形は `http://<相手の PC>:<相手のポート>/connect/receive/order/` です。相手の担当者に聞いてもらいます。
- 聞いた URL だけを使います。探したり、推し量って作ったりしません。
- `<相手の PC>` は IP アドレス (例 `127.0.0.1`) か、点を含む名前にします。点の無い名前は、登録のときに URL の形の誤りになります。
- 受けるときは聞きません。URL は空で登録します。

問 4: 相手の会社の id

- 相手が `/seizou:build` で決めた、会社を見分ける短い名前です。英小文字と数字と `_` だけでできています。相手の担当者に聞いてもらいます。
- 答えから、鍵の置き場の名前を `SEIZOU_PARTNER_<相手の会社の id を大文字にした物>_SECRET` の形で作り、担当者に見せて確かめてもらいます。例: 相手の id が `b_plating` なら、`SEIZOU_PARTNER_B_PLATING_SECRET` です。
- id に英小文字と数字と `_` のほかの字があれば、聞き直します。

## 4. 登録する

1. 同じ取引先と向きのつながりが既にあるかを、次の命令で確かめます。この命令は読むだけです。

```
SEIZOU_CONNECT_PARTNER=<取引先コード> SEIZOU_CONNECT_DIRECTION=<send か receive> <python> manage.py shell --no-imports -c '
import os
from django.apps import apps
partner = apps.get_model("masters", "Partner").objects.get(code=os.environ["SEIZOU_CONNECT_PARTNER"])
print("既にある" if partner.connections.filter(direction=os.environ["SEIZOU_CONNECT_DIRECTION"]).exists() else "まだ無い")
'
```

2. 「既にある」と出たら、登録せずに「この取引先とのこの向きのつながりは、既に登録してあります」の趣旨を伝えて止まります。
3. 「まだ無い」と出たら、次の命令でつながりを登録します。この命令は、部品の関数 `seizou.connect.services.register_connection(partner, url, secret_env, direction, user)` を呼びます。

```
SEIZOU_CONNECT_PARTNER=<取引先コード> SEIZOU_CONNECT_URL=<URL> SEIZOU_CONNECT_ENV_NAME=<鍵の置き場の名前> SEIZOU_CONNECT_DIRECTION=<send か receive> <python> manage.py shell --no-imports -c '
import os
from django.apps import apps
from django.contrib.auth import get_user_model
from seizou.connect.services import register_connection
partner = apps.get_model("masters", "Partner").objects.get(code=os.environ["SEIZOU_CONNECT_PARTNER"])
user = get_user_model().objects.get(username="admin")
connection = register_connection(partner, os.environ["SEIZOU_CONNECT_URL"], os.environ["SEIZOU_CONNECT_ENV_NAME"], os.environ["SEIZOU_CONNECT_DIRECTION"], user)
print("登録しました:", connection)
'
```

- 答えの値は環境変数で渡し、Python の文の中に書きません。shell の引用符と Python の文字列の引用符が重ならないようにするためです。
- `<URL>` は問 3 の URL です。受けるときは `SEIZOU_CONNECT_URL=` の後に何も書かず、空にします。
- 取引先は取引先コードで引きます。`user` は、`seizou new` が作った管理者の ID `admin` です。
- 誤りが出たら、その文をそのまま担当者に見せて止まります。`register_connection` の誤りは日本語の文 (`ValueError`) で出ます。
- 表に直接書きません。この命令がつながりを書くときに呼ぶのは、`register_connection` だけです。

## 5. 鍵を置いてもらう

鍵の値は、注文をやり取りする両方の会社の `.env` に、同じ値を置きます。`.env` は、会社のシステムの置き場にある、鍵を置くファイルです。置くのは担当者です。

1. 担当者に、置き場の `.env` に `<鍵の置き場の名前>=` で始まる行を 1 行足してもらいます。例は `SEIZOU_PARTNER_B_PLATING_SECRET=` で始まる行です。`=` の後には、相手の会社と同じ値を置いてもらいます。
2. 値の形と作り方は、公開リポジトリ (seizou) の `SECURITY.md` を見てもらいます。値の形は、この会話で書きません。
3. 値は片方の会社が作り、もう片方の会社へ人の手で渡します。
4. 値をこの会話に貼らないよう伝えます。貼られても、その値を書き写したり使ったりしません。
5. Claude は `.env` を読まず、書かず、中身を画面に出しません。

## 6. 起動し直してもらう

会社のシステムは、起動するときに `.env` を読みます。担当者に、動いている runserver を止めて起動し直すよう伝えます。起動の 1 行は、置き場の `README.md` の「起動」にあります。

## 7. 相手に渡す文を出す (受けるときだけ)

受ける向きのときは、次の 2 つを、相手に渡す文として 1〜2 行で出します。

- この会社の受け口の URL: `http://127.0.0.1:<port>/connect/receive/order/` です。`<port>` は `answers.yaml` の `port` です。この URL は、2 社が同じ PC で動くときの物です。離れた PC からは、その PC から届く名前の URL が要ります。その建て方は、この版の命令には入っていません。
- この会社の id (`answers.yaml` の `id`): 相手は、これを大文字にして鍵の置き場の名前に使います。

例: B 社 (`b_plating`) が受けるときは、「注文を受け取る窓口は http://127.0.0.1:8002/connect/receive/order/ です。会社の id は b_plating です。」のように出します。

## 8. 確かめてもらう

担当者に、つながりの画面を開いてもらいます。URL は `http://127.0.0.1:<port>/connect/connections/` で、左のメニューの「つながり」からも開けます。登録した行の「鍵の設定」が「済み」になっていれば、鍵が置けています。「まだ」のときは、`.env` の行の名前と、起動し直したかを確かめてもらいます。

## 9. 終わりに言うこと

「できた物」と「次にすること」を 3 行以内で伝えます。

- できた物は、登録したつながり (取引先・向き・鍵の置き場の名前) です。
- 次にすることは、相手の会社でも `/seizou:connect` を打つことです。向きは、この会社と逆にします。2 社の例 (`examples/companies/*/connections.yaml`) では、A 社 (`a_machining`) が送り、B 社 (`b_plating`) が受けます。

## してはいけないこと

- 鍵の値を聞きません。書きません。画面に出しません。値の形も書きません。
- `.env` を読みません。書きません。
- 相手の受け口を探しません。聞いた URL だけを使います。
- 部品の中身 (`seizou` の package) を書き換えません。
- 表に直接書きません。つながりを書くときに呼ぶのは `register_connection` だけです。
- 取引先を作りません。取引先は、取引先の画面で登録してもらいます。
- 試しの注文を送りません。注文を送るのは、発注の画面 (「発注を入れる」) です。
- 手元のほかのリポジトリを読みません。読むのは公開リポジトリと、会社のシステムの置き場だけです。
