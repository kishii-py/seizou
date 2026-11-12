# 答えの YAML の書き方

読み手: 自分の会社の置き場を立てる人で、Claude Code を使わない方。答えの YAML の鍵ごとの書き方と例と、作った後に直すファイルを書く。入口は [README](../README.md)。

## 答えの YAML とは

答えの YAML は、会社の id・名前・ポートと、会社ごとの項目と設定を書いた 1 つのファイル。Python の命令 `seizou new` がこのファイルを読み、会社の置き場 (会社ごとのプロジェクトのフォルダ。既定は `~/seizou-sites/<id>/`) を作る。Claude Code の立ち上げの命令 `/seizou:build` の 3 問に答えたときも、同じ形の答えの YAML が置き場の `answers.yaml` に書かれる ([「Claude Code から使う」](plugin.md))。

いちばん小さい答えは次の 3 行 (値は作り物の例)。

```yaml
id: mihon_seisakusho
name: 見本製作所
port: 8003
```

このファイルを、例えば `~/mihon_seisakusho.yaml` に保存して `seizou new ~/mihon_seisakusho.yaml` を打つ (clone した seizou のフォルダでは `uv run seizou new ~/mihon_seisakusho.yaml`)。打つ場所と、作った後の起動の 1 行は[「入れて触る」](getting-started.md)の「自分の会社で使い始める」にある。

`seizou answers-template <道>` を打つと、この 3 行に任意の鍵の例と説明の注を付けた雛形を `<道>` に書き出せる (既にあるファイルは上書きしない。v0.1.0 の次の版から)。

## 鍵ごとの書き方

| 鍵 | 要るか | 書き方 |
|---|---|---|
| `id` | 要る | 会社の id。英小文字で始め、英小文字と数字と `_` だけで書く (ハイフンと大文字は使えない)。置き場のフォルダ `~/seizou-sites/<id>/` の名前になる |
| `name` | 要る | 会社の名前 |
| `port` | 要る | 起動の 1 行の runserver のポート。整数で書く (引用符で囲むと字になり、止まる)。ほかの会社の置き場と同じ番号にしない |
| `industry` | 任意 | 業種。業種の見本 (機械加工・表面処理・鋳造・板金) の名前か言い換えに当たると、部品マスターの項目・工程・向く設定を見本から最初に入れる (答えに書いた項目と設定が勝つ)。当たるかを比べるときは、字の幅と空白と英字の大小をそろえる。業種は、画面の左上の会社の名前の下にも出る |
| `recipe` | 任意 | 書けるのは `none` だけ。業種の見本を入れない |
| `company` | 任意 | 会社の情報。下に `address` (住所)・`phone` (電話)・`bank_account` (振込先)・`registration_no` (登録番号。T と 13 桁の数字) を書く。値は字で書き、数に読める値は引用符で囲む。納品書と請求書の会社の欄に出る (振込先と登録番号は請求書だけ) |
| `logo` | 任意 | 会社のロゴ。答えの YAML のあるフォルダから見た、PNG か JPEG の絵のファイルのパス。ファイルが無いと止まる。`seizou new` はロゴから印 (favicon) も作る |
| `item_fields` | 任意 | 部品マスターに足す項目の並び。書き方は下の「項目の書き方」 |
| `settings` | 任意 | 選べる設定。鍵と既定は[「選べる設定」](settings.md)にある。書かない鍵は既定になる |
| `partner_base_urls` | 任意 | `--demo` のときだけ使う。作り物のつながりの種の受け口の頭 (相手の会社の id → 置き場の URL の頭)。`--demo` を付けないときも、形が違えば止まる |

### 項目の書き方

`item_fields` の 1 つの項目に、次の鍵を書く。`seizou new` は項目ごとに、部品マスターの拡張の表の列 (`ext01`・`ext02` …) を作る。

| 鍵 | 書き方 | 書かないとき |
|---|---|---|
| `label` | 見出し (画面に出る項目の名前) | 書く (`seizou new` は書いたかを見ない) |
| `type` | 型。`text` (字。100 字まで)・`number` (数。小数は 2 桁まで)・`date` (日付)・`choice` (選び肢から 1 つ)・`bool` (はい・いいえ) のどれか | 止まる |
| `required` | 必須か。`true` なら、空で保存できない | `false` |
| `in_list` | 一覧に出すか。`true` なら、部品の一覧に列が出る | `false` |
| `searchable` | 検索に使うか。`true` なら、一覧の検索の欄でこの値を探す | `false` |
| `choices` | `type` が `choice` のときの選び肢の並び | 空の並び |

`required`・`in_list`・`searchable` を書かなかった項目があると、`seizou new` は「答えに無い項目の属性は既定を使いました: …」の行を出す。

## 項目と設定と会社の情報を書いた例

値は作り物の例。

```yaml
id: mihon_seisakusho
name: 見本製作所
port: 8003
industry: 板金
company:
  address: 〒000-0003 見本県架空市見本町 7-8-9
  phone: 00-0000-0003
  bank_account: 作り物銀行 見本支店 普通 0000003 名義 ミホンセイサクショ
  registration_no: T0000000000003
item_fields:
  - label: 材質
    type: choice
    choices:
      - SPCC
      - SUS304
      - A5052
    required: true
    in_list: true
    searchable: true
  - label: 板厚
    type: number
    required: false
    in_list: true
    searchable: false
  - label: 図面の版
    type: text
    required: false
    in_list: true
    searchable: false
settings:
  over_shipment: warn
  receiving_required: "off"
  order_number: ym_seq
  weekly_holidays: [6]
```

この例では、`seizou new` は次のように動く。

- 業種の「板金」が業種の見本に当たるので、答えに無い項目と工程を見本から足し、「業種の見本 (板金) から入れました: …」の行を出す。見出しが同じ項目 (「材質」「板厚」) と、鍵が同じ設定 (`over_shipment`) は、答えに書いた物が勝つ。
- `receiving_required` の `"off"` は引用符で囲む。引用符の無い `off` を、YAML は偽 (`false`) として読む。`access_control` に書く `"on"` も、引用符が無いと真 (`true`) として読まれるので囲む。
- `settings` に書かなかった鍵は既定になり、`seizou new` は「答えに無い設定は既定を使いました: …」の行を出す。

## 誤りのとき

答えの形が違うと、`seizou new` は「止まりました: …」の 1 行を出して止まり、置き場のファイルを何も作らない。YAML を直して、もう 1 度打つ。よく出る文は次のとおり。

| 書いた物 | 出る文 |
|---|---|
| `id: Mihon-Seisakusho` | `止まりました: 会社の id の形が違います: 'Mihon-Seisakusho'。英小文字で始め、英小文字と数字と _ だけで書きます (ハイフンと大文字は使えません)。` |
| `item_fields` の 1 番目に `type: string` | `止まりました: item_fields の 1 番目の型を知りません: 'string'。text / number / date / choice / bool のどれかで書きます。` |
| `company` の下に `phone: 0123` (引用符なし) | `止まりました: company の phone は字で書きます (数に読める値は引用符で囲む): 83` |

3 つ目は、0 で始まる数字だけの値を、YAML が 8 進の数として読んだ例。`phone: "0123"` のように引用符で囲む。

## 作った後に直すファイル

`seizou new` は、答えの YAML を置き場の `answers.yaml` に写す。会社のシステムは、動いている間は置き場の `settings.py` の辞書 `SEIZOU` を読み、`answers.yaml` を読み直さない。

- 設定を変えるときは、置き場の `settings.py` の辞書 `SEIZOU` の行を直して、起動し直す。`answers.yaml` だけを直しても値は変わらない。点検 (`seizou_doctor`) の直し方の文のとおり、`answers.yaml` の `settings` も同じ値にしておく。画面から変えられる鍵は、設定の画面でも変えられる。設定の画面で保存した値は `settings.py` より先に読まれるので、画面で変えた鍵は設定の画面で直す ([「選べる設定」](settings.md))。
- 会社の情報とロゴは、設定の画面の「会社の情報」で直す。
- 会社ごとの項目を後から足すときは、置き場の `fields/` のファイルを直す。手順は[「運用の手引き」](operations.md)の「会社ごとの項目を立てた後に足す」にある。`answers.yaml` の `item_fields` を直しても、画面は変わらない。
- 同じ id の置き場を作り直すときは、置き場を消してから `seizou new` をもう 1 度打つ。置き場に `manage.py` か `settings.py` が既にあると、`seizou new` は上書きせずに止まる。
