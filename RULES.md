# 決まりの一覧 (RULES.md)

この作品が守る、お金と数についての約束。番号は以後変えない (消した決まりは「廃止」と書いて残す)。各決まりは自動テスト (`tests/test_r<番号>_*.py`) と 1 対 1 で結ぶ (R20 だけは製品の hook `plugin/hooks/guard_core.py` が守り、hook のテスト `tests/test_plugin_guard_core.py` が確かめる)。

原則は現物優先。実際に動いた数をそのまま記録し、実数の入力を決まりで止めない。注文の数との差は「残」として見せる (マイナスも出す)。止めるのは、あり得ない入力と、記録どうしの矛盾だけ。間違いに気づかせる物 (ポカよけ) は、会社ごとに止めるか知らせるかを選ぶ。

## 中心の決まり (止める。全社共通。通す道は無い)

| 番号 | 決まり | 守り方 | テスト |
|---|---|---|---|
| R02 | 数量と単価は 0 より大きい (受注の明細の数量と単価、出荷・指示書の数。実績は良品の数と不良の数がどちらも 0 以上で、合わせて 0 より大きい) | `services` と DB の制約 | `tests/test_r02_positive_qty_price.py` |
| R03 | 受注の明細の金額は数量 × 単価 (単価の単位が「式」の明細は単価のまま) で、端数は会社の設定どおりに丸める。DB の側で計算しない | `services`。書き方は決まりの検査 | `tests/test_r03_amount.py` |
| R05 | 取消した受注には、出荷も指示書も作れない | `services` | `tests/test_r05_cancelled_order.py` |
| R08 | 納品書の数量は出荷の数量と一致する。納品書を出した後の出荷は、納品書を取り消してから直す | `services` | `tests/test_r08_delivery_note_qty.py` |
| R09 | 請求書の明細は、出荷済みの物だけ | `services` | `tests/test_r09_invoice_shipped_only.py` |
| R10 | 受信した注文は、人が受け入れるまで受注にならない | `services` | `tests/test_r10_inbox_accept.py` |
| R11 | 同じ番号の注文のデータは 1 回だけ登録する (2 回届いても 1 件) | DB の一意の制約と `services` | `tests/test_r11_dedupe.py` |
| R12 | 受け口は、登録した取引先からの、署名の合う物だけを受ける | `services` (署名の検査) | `tests/test_r12_signature.py` |
| R21 | 取消した請求書には、入金を記録できない | `services` | `tests/test_r21_payment_on_cancelled_invoice.py` |
| R23 | 同じ得意先の同じ締め期間の請求書は 1 枚だけ (請求の仕方が「都度」の得意先は除く) | `services` | `tests/test_r23_one_invoice_per_period.py` |
| R25 | 明細 (受注の明細と発注の明細) が終わっているのは、人が付けた完了の印が真か、出荷 (発注は受入) の合計が数量以上のとき。完了の明細は、足りない分を残に数えない (多く出した分と多く受けた分はマイナスのまま見せる)。完了の印は人だけが付け外しし、出荷・受入・数量の直しでは書き換えない。出荷済みと受入の数と金額は変えない | `services` | `tests/test_r25_completed_remaining.py` |

## 標準のポカよけ (会社の設定で、止める / 知らせる / 通す を選ぶ。既定は知らせる)

知らせるのときは黄色の知らせと「このまま保存」のボタンが出て、もう一度押すと保存される。止めるのときは赤の知らせが出て保存されない。検査の層 (`seizou.core.checks`) が、画面から入った値を業務の処理に渡す前に見る。会社は自分のポカよけを会社のプロジェクトの `checks.py` に足せる (core の道具だけで書く)。

| 番号 | ポカよけ | 設定の鍵 | テスト |
|---|---|---|---|
| R01 | 出荷の数の合計が、受注の明細の数を超えたら | `over_shipment` | `tests/test_r01_over_shipment.py` |
| R04 | 出荷済みの数より少ない数量に、受注の明細を変えたら | `over_shipment` | `tests/test_r04_order_below_shipped.py` |
| R06 | 指示書の数量の合計が、受注の明細の数を超えたら | `over_workorder` | `tests/test_r06_over_workorder.py` |
| R07 | 進捗の実績の数が、その工程の予定の数を超えたら | `over_workorder` | `tests/test_r07_over_progress.py` |
| R24 | 入金の金額が、請求書の残を超えたら | `over_payment` | `tests/test_r24_over_payment.py` |
| R26 | 社員の一覧に使用中の社員が居る会社で、作業した人 (社員番号か、社員の一覧に居ない人の名前) の無い実績を入れたら | `progress_staff` | `tests/test_r26_progress_staff.py` |
| R27 | 請求済みの出荷 (請求書に出荷の行で入った出荷・一式の行で請求した明細の出荷・請求書の外で入金を受け取った出荷) を直すか消そうとしたら | `billed_shipment` | `tests/test_r27_billed_shipment.py` |

## 書き方の決まり (決まりの検査が止める。自社の画面のコードを、起動のときと CI で検査する)

| 番号 | 決まり | 守り方 | テスト |
|---|---|---|---|
| R13 | 数と金額を変える処理は、部品の `services` の関数を通す。画面から表に直接書かない | 決まりの検査 (見るのは views.py・forms.py と、views/・templatetags/ の下と management/commands の .py。services.py・models.py・migrations は見ない) | `tests/test_r13_services_only.py` |
| R14 | SQL を直接書かない (`raw`・`cursor` を使わない) | 決まりの検査 | `tests/test_r14_no_raw_sql.py` |
| R15 | 画面は `core` の土台の画面 (`seizou.core.views` の `Base*View`) を継承する。上書きしてよいのは `model`・`ordering`・`columns`・`search_fields`・`filters`・`presets`・`tabs`・`form_class`・`service`・`template_name`・`get_queryset`・`screen_name`・`screen_help` の 13 だけ | 決まりの検査 (見るのは、親の名前が View で終わる class だけ。関数の画面は見ない) | `tests/test_r15_inherit_base_view.py` |
| R16 | 部品の表を変えない。会社ごとの項目は拡張の表に足す | 決まりの検査 | `tests/test_r16_no_core_model_change.py` |
| R17 | 相手の会社への HTTP は `transaction.atomic` の外で送る | 決まりの検査 | `tests/test_r17_http_outside_atomic.py` |
| R18 | CSRF の見出しは土台のテンプレートの 1 か所にだけ書く | 決まりの検査 | `tests/test_r18_csrf_single_place.py` |
| R19 | class 名を途中で組み立てない。古い版の class 名を使わない | 決まりの検査 | `tests/test_r19_no_dynamic_classnames.py` |
| R20 | AI は部品の中身を書き換えない。会社の違いは拡張の表と設定とテンプレートと会社の検査で出す | 製品の hook (`plugin/hooks/guard_core.py`。Write・Edit・NotebookEdit の書き込みを見る。Bash の道具での書き込みは見ない) | hook のテスト |
| R22 | 決まりを実装した関数 (断る処理・端数の丸め・ポカよけの検査) は、docstring に「訳:」の行を持つ。訳は、何のためか・出所 (法令・商慣習・作者の決め・AI の案・未確認のどれか)・変えてよい条件を書く。出所が分からない物は「未確認」と書く (推測で埋めない)。断る処理は、今は中心の決まりで断る関数 (`RuleViolation`) と断りの文を返す関数 (`cancel_refusal`) だけを見る | 決まりの検査 | `tests/test_r22_reason_docstring.py` |

番号の無い約束 (決まりの検査は見ない。`tests/test_core_screens.py` が見る): 画面の class の docstring に、ユースケースを「使う人: … / いつ: … / 何のために: … / 次にすること: …」の形で 1〜2 行書く。画面の目録 (`all_screens()`) が `usecase` として拾い、MCP と AI チャットの `list_screens` が返す (AI が画面を選ぶ材料)。

## 黙らせ方 (書き方の決まり)

どうしても守れない行には、その行の末尾か直上のコメントに `seizou: ignore[R14] 理由` の形で、番号と理由を書く (番号は必須。番号の無い印は効かない)。黙らせた数は検査の結果に必ず出る。

## 提案と反例

決まりの提案と、決まりの反例 (この操作が通ってしまう) は Issue で受ける (CONTRIBUTING.md)。会期中 (2026-11-08 まで) は受けられない。
