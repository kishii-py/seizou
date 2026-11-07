# 会社どうしで送り合う項目の表

seizou どうしが送り合う JSON の項目ごとに、作品の欄・送る字数と桁・受け取れる字数と桁・中小企業共通 EDI の項目・国連 CEFACT 共通辞書の ID を並べます。

## この表を置く訳

中小企業共通 EDI 標準仕様書 (ver.4.3_r0、2026-03-01) は、文字列の長さを決めていません。その代わりに、送る側と受ける側が桁数をすり合わせる手間を減らすため、業務アプリが送り出す情報項目と取り込む情報項目の桁数を公開するよう求めています (本編 7.3.3「EDI データの桁数属性」)。また、中小企業共通 EDI ガイドブック (ver.4_r0、2022-09-01) は、認証を受けた業務アプリが国連 CEFACT 共通辞書の情報項目との対応表と、受け取れる桁数を公開していると書いています (3.1.1「取引企業の業務アプリの情報項目とデータ桁数」)。

seizou の受注と発注の項目は、中小企業共通 EDI の項目を元にした形です。seizou は中小企業共通 EDI の認証を受けた業務アプリではありません。会社どうしでは、EDI の電文 (XML) ではなく、下の JSON を署名して送り合います。この表は、相手の業務アプリと項目をすり合わせるときと、将来 EDI の電文に直すときに使います。

## 表の読み方

- JSON の鍵: `buyer.name` は buyer の辞書の中の name、`lines[].qty` は明細の並び (lines) の 1 行ずつの中の qty です。
- 送る側の元の欄・受ける側が入れる欄: `<部品>.<表>.<欄>` の形で書きます (例 `orders.Order.customer_order_no`)。「—」は表の欄が無い物です。
- 送る字数・桁: seizou が送り出す値の上限です。
- 受け取れる字数・桁: seizou が受けて表に入れる値の上限です。
- 字は 1 字を 1 と数えます (バイトの数ではありません)。数は「整数 n 桁・小数 m 桁」です。送る側と受ける側の両方に欄がある行では、送る字数・桁は受け取れる字数・桁を超えません。
- EDI の項目: 中小企業共通 EDI 標準仕様書の付表 2 (メッセージ辞書・BIE 表) の項目名と、( ) にシートの名前と行番号です。
- 共通辞書の ID: 国連 CEFACT 共通辞書の ID です。JPS で始まる ID は付表 2 の字のままで、UN で始まる共通辞書の ID ではありません。「—」は当てる項目が無い物です。

## 注文 (kind = order)

EDI の文書は注文です。発注を保存すると、発注した会社が受ける会社へ送ります。受けた側は「受信した注文」に入れ、人が受け入れると受注になります。

| JSON の鍵 | 意味 | 送る側の元の欄 | 送る字数・桁 | 受ける側が入れる欄 | 受け取れる字数・桁 | EDI の項目 (付表 2) | 共通辞書の ID |
|---|---|---|---|---|---|---|---|
| `id` | この知らせの番号 (見出しの webhook-id と同じ値) | `connect.Outbox.message_id` | uuid (36 字) | `connect.Inbox.message_id` (見出しの webhook-id から) | uuid (36 字) | 取引ID (注文 3) | UN01005480 |
| `kind` | 知らせの種類 | — | 下の「知らせの種類」 | — (種類を見分けるだけ) | 下の「知らせの種類」 | 取引プロセスID (注文 7) | UN01005472 |
| `sent_at` | 送った日時 | — | 日時 (下の「形」) | — (読まない) | 日時 (下の「形」) | 処理日時 (注文 4) | UN01012746 |
| `order_no` | 発注番号 | `purchasing.PurchaseOrder.po_no` | 35 字 | `orders.Order.customer_order_no` | 35 字 | 注文書ID (注文 30) | UN01006518 |
| `order_date` | 発注日 | `purchasing.PurchaseOrder.order_date` | 日付 | `orders.Order.order_date` | 日付 | 注文書発効日 (注文 33) | UN01006521 |
| `order_id` | 元の注文 (第 1 版) の知らせの番号 (変更と取消の版が元の注文を引く。第 1 版は `id` と同じ値) | `connect.Outbox.message_id` | uuid (36 字) | `connect.Inbox.message_id` (元の行を引くだけ) | uuid (36 字) | — (seizou どうしで元の注文を引く番号。EDI の項目に当てない) | — |
| `order_revision_no` | 注文履歴番号 (第 1 版は 1。変更と取消を送るたびに 1 ずつ増える) | `purchasing.PurchaseOrder.revision_no` | 正の整数 | `connect.Inbox.revision_no` | 正の整数 | 注文履歴ID (注文) | UN01006528 |
| `purpose_code` | 注文書目的コード (新規・変更・取消。下の「コードの値」) | `purchasing.PurchaseOrder.purpose_code` | 2 字 | `connect.Inbox.purpose_code` | 2 字 | 注文書目的コード (注文) | UN01006524 |
| `buyer.id` | 発注した会社の id (英小文字で始め、英小文字・数字・_ の字) | — (会社の置き場の設定) | 決まりなし | — (受けた中身に残すだけ) | 決まりなし (受けた中身に残すだけ) | 発注者ID (注文 85) | UN01005757 |
| `buyer.name` | 発注した会社の名前 | `core.Company.name` | 100 字 | — (受けた中身に残すだけ) | 決まりなし (受けた中身に残すだけ) | 発注者名 (注文 87) | UN01005759 |
| `supplier.code` | 発注した会社が、受ける会社に付けた取引先コード | `masters.Partner.code` | 10 字 | — (受けた中身に残すだけ) | 決まりなし (受けた中身に残すだけ) | 受注者ID (注文 61) | UN01005757 |
| `supplier.name` | 受ける会社の名前 (発注した会社の取引先マスターの名前) | `masters.Partner.name` | 100 字 | — (受けた中身に残すだけ) | 決まりなし (受けた中身に残すだけ) | 受注者名称 (注文 63) | UN01005759 |
| `note` | 発注の備考 | `purchasing.PurchaseOrder.note` | 500 字 | — (受けた中身に残すだけ) | 決まりなし (受けた中身に残すだけ) | 注文注釈内容 (注文 43。備考は注釈 ID 1) | UN01005560 |
| `lines[].line_no` | 明細の行番号 | `purchasing.PurchaseOrderLine.line_no` | 正の整数 | `orders.OrderLine.line_no` (送った順に 1 から振る) | 正の整数 | 注文明細行ID (注文 221) | UN01006603 |
| `lines[].part_no` | 発注した会社の自社品番 | `masters.Item.part_no` | 35 字 | `orders.OrderLine.customer_part_no`・`masters.PartnerItem.partner_part_no` | 35 字 | 発注者品目ID (注文 314) | UN01005813 |
| `lines[].part_name` | 品名 | `masters.Item.name` | 100 字 | `masters.PartnerItem.partner_item_name` (取引先の品番の対応を初めて足すときだけ) | 100 字 | 品目名 (注文 316) | UN01005815 |
| `lines[].qty` | 数量 | `purchasing.PurchaseOrderLine.qty` | 整数 10 桁・小数 2 桁 | `orders.OrderLine.qty` | 整数 10 桁・小数 2 桁 | 注文数量 (注文 253) | UN01006632 |
| `lines[].unit` | 単位の名前の字 (例「個」) | `purchasing.PurchaseOrderLine.unit` | 10 字 | `orders.OrderLine.unit` | 10 字 | — (単位の名前。コードは `lines[].unit_code`。下の「単位」) | — |
| `lines[].unit_code` | 数量単位コード (例 H87 = 個、KGM = kg。単位が数量単位の一覧に無い字なら空の字) | `purchasing.PurchaseOrderLine.unit_code` | 10 字 | `orders.OrderLine.unit_code` (一覧にあるコードだけ。無いコードは使わず、単位の名前から引く) | 10 字 | 注文数量単位コード (注文 254。注文数量の数量単位属性。値は UNECE Rec.20 のコード) | — |
| `lines[].unit_price` | 単価 (税抜き) | `purchasing.PurchaseOrderLine.unit_price` | 整数 12 桁・小数 2 桁 | `orders.OrderLine.unit_price` | 整数 12 桁・小数 2 桁 | 注文単価 (注文 245) | UN01005792 |
| `lines[].price_basis` | 単価の単位 (`unit` = 部品の単位 1 つあたり・`lump` = 一式で数量を掛けない。値はこの 2 つだけで、鍵が無ければ `unit`。ほかの値は受ける側が断る) | `purchasing.PurchaseOrderLine.price_basis` | 10 字 | `orders.OrderLine.price_basis` | 10 字 | — (seizou の項目。中小企業共通 EDI の数量単位コードに一式に当たる値は無く、部品の単位のときは単価基準数量が空か 1 のときと同じ意味) | — |
| `lines[].due_date` | 納期 (無ければ null) | `purchasing.PurchaseOrderLine.due_date` | 日付 | `orders.OrderLine.due_date` | 日付 | 要求納入日 (注文 288) | UN01005628 |
| `lines[].note` | 明細の備考 | `purchasing.PurchaseOrderLine.note` | 500 字 | `orders.OrderLine.note` | 500 字 | 注文明細注釈内容 (注文 229。備考は明細行注釈 ID 1) | UN01005560 |
| `lines[].line_status_code` | 注文明細状態コード (変更の版で、取り消した明細の印。下の「コードの値」) | `purchasing.PurchaseOrderLine.line_status_code` | 2 字 | — (受けた中身に残し、受け入れるときに読む) | 決まりなし (受けた中身に残すだけ) | 注文明細状態コード (注文) | UN01006605 |
| `lines[].line_status_reason_code` | 注文明細状態理由コード (取り消した訳。無ければ空) | `purchasing.PurchaseOrderLine.line_status_reason_code` | 4 字 | — (受けた中身に残すだけ) | 決まりなし (受けた中身に残すだけ) | 注文明細状態理由コード (注文) | UN01006606 |
| `lines[].process_name` | 協力企業に出す工程の名前 (工程コードでなく名前の字。無ければ空の字。受ける側は自社の工程マスターにつながず字のまま持つ) | `masters.Process.name` | 100 字 | `orders.OrderLine.process_name` | 100 字 | 工程名 (注文の明細。この品目の生産工程の名称。任意の項目) | UN01012678 |
| `lines[].quote_no` | 単価表から写した見積の番号 (無ければ空の字) | `purchasing.PurchaseOrderLine.quote_no` | 35 字 | `orders.OrderLine.quote_no` | 35 字 | (明細行参照) 見積回答書ID (注文の明細。この明細行が参照する見積回答書に記載の文書ID。任意の項目) | UN01005580 |

## 注文回答 (kind = order_reply)

EDI の文書は注文回答です。受けた会社が受信した注文を受け入れるか断ると、発注した会社へ返します。

| JSON の鍵 | 意味 | 送る側の元の欄 | 送る字数・桁 | 受ける側が入れる欄 | 受け取れる字数・桁 | EDI の項目 (付表 2) | 共通辞書の ID |
|---|---|---|---|---|---|---|---|
| `id` | この知らせの番号 (見出しの webhook-id と同じ値) | `connect.Inbox.reply_message_id` | uuid (36 字) | `purchasing.PurchaseOrder.reply_message_id` (見出しの webhook-id から) | uuid (36 字) | 取引ID (注文回答 3) | UN01005480 |
| `kind` | 知らせの種類 | — | 下の「知らせの種類」 | — (種類を見分けるだけ) | 下の「知らせの種類」 | 取引プロセスID (注文回答 7) | UN01005472 |
| `sent_at` | 送った日時 | — | 日時 (下の「形」) | — (読まない) | 日時 (下の「形」) | 処理日時 (注文回答 4) | UN01012746 |
| `order_id` | 元の注文の知らせの番号 | `connect.Inbox.message_id` | uuid (36 字) | `connect.Outbox.message_id` (元の注文を引くだけ) | uuid (36 字) | — (seizou どうしで元の注文を引く番号。EDI の項目に当てない) | — |
| `order_no` | 元の発注番号 | — (受信した注文の中身の order_no) | 受けた字のまま | `purchasing.PurchaseOrder.po_no` (元の発注を引くだけ) | 35 字 | （ヘッダ参照）注文書ID (注文回答 107) | UN01005580 |
| `order_revision_no` | 答えた注文の注文履歴番号 | `connect.Inbox.revision_no` | 正の整数 | — (送った注文の行を引くだけ) | 正の整数 | （ヘッダ参照）注文履歴ID (注文回答) | UN01005588 |
| `reply_type` | 答え (受諾・条件付受諾・辞退・保留。下の「注文回答の答え」) | `connect.Inbox.reply_type` | 2 字 | `purchasing.PurchaseOrder.reply_type` | 2 字 | 注文回答タイプコード (注文回答 37) | UN01015497 |
| `lines[].line_no` | 答えた明細の行番号 (条件付受諾のときだけ並ぶ。ほかは空の並び) | — (受信した注文の中身の line_no) | 正の整数 | — (発注の明細の行番号で引く) | 正の整数 | 注文書明細行ID (注文回答) | UN01005585 |
| `lines[].reply_type` | 明細ごとの答え (受諾・条件付受諾。下の「注文回答の答え」) | — (受け入れの画面で決めた値。JSON の並び) | 2 字 | `purchasing.PurchaseOrderLine.reply_type` | 2 字 | 注文回答明細回答タイプコード (注文回答) | UN01015601 |
| `lines[].reply_unit_price` | 相手が直した単価 (条件付受諾のとき) | — (受け入れの画面で入れた値) | 整数 12 桁・小数 2 桁 | `purchasing.PurchaseOrderLine.reply_unit_price` | 整数 12 桁・小数 2 桁 | 注文回答単価 (注文回答) | UN01007305 |
| `lines[].reply_qty` | 相手が直した数量 (条件付受諾のとき) | — (受け入れの画面で入れた値) | 整数 10 桁・小数 2 桁 | `purchasing.PurchaseOrderLine.reply_qty` | 整数 10 桁・小数 2 桁 | 注文回答数量 (注文回答) | UN01006633 |
| `lines[].reply_due_date` | 相手が直した納期 (条件付受諾のとき。無ければ null) | — (受け入れの画面で入れた値) | 日付 | `purchasing.PurchaseOrderLine.reply_due_date` | 日付 | 回答納入日 (注文回答) | UN01005628 |
| `supplier_order_no` | 受けた会社の受注番号 (受け入れた時だけ) | `orders.Order.order_no` | 35 字 | — (読まない) | 読まない | 注文回答書番号 (注文回答 30) | UN01006661 |
| `reason` | 断った理由 (断った時だけ) | `connect.Inbox.reason` | 500 字 | `purchasing.PurchaseOrder.reply_note` | 500 字 | 注文注釈内容 (注文回答 42) | UN01005560 |
| `supplier.id` | 受けた会社の id | — (会社の置き場の設定) | 決まりなし | — (読まない) | 読まない | 受注者ID (注文回答 60) | UN01005757 |
| `supplier.name` | 受けた会社の名前 | `core.Company.name` | 100 字 | — (読まない) | 読まない | 受注者名称 (注文回答 62) | UN01005759 |

## 出荷の知らせ (kind = shipping_notice)

EDI の文書は出荷案内です。受けた会社が受信した注文から作った受注を出荷すると、発注した会社へ送ります。

| JSON の鍵 | 意味 | 送る側の元の欄 | 送る字数・桁 | 受ける側が入れる欄 | 受け取れる字数・桁 | EDI の項目 (付表 2) | 共通辞書の ID |
|---|---|---|---|---|---|---|---|
| `id` | この知らせの番号 (見出しの webhook-id と同じ値) | `shipping.Shipment.notice_message_id` | uuid (36 字) | `connect.ShippingNotice.message_id` (見出しの webhook-id から) | uuid (36 字) | 取引ID (出荷案内 3) | UN01005480 |
| `kind` | 知らせの種類 | — | 下の「知らせの種類」 | — (種類を見分けるだけ) | 下の「知らせの種類」 | 取引プロセスID (出荷案内 7) | UN01005472 |
| `sent_at` | 送った日時 | — | 日時 (下の「形」) | — (読まない) | 日時 (下の「形」) | 処理日時 (出荷案内 4) | UN01012746 |
| `order_id` | 元の注文の知らせの番号 | `connect.Inbox.message_id` | uuid (36 字) | `connect.Outbox.message_id` (元の発注を引くだけ) | uuid (36 字) | — (seizou どうしで元の注文を引く番号。EDI の項目に当てない) | — |
| `order_no` | 元の発注番号 | — (受信した注文の中身の order_no) | 受けた字のまま | — (読まない) | 読まない | （ヘッダ参照）注文書ID (出荷案内 107) | UN01005580 |
| `order_revision_no` | 元の注文の注文履歴番号 (受信した注文の今の版) | — (受信した注文の今の版の番号) | 正の整数 | `connect.ShippingNotice.order_revision_no` | 正の整数 | （ヘッダ参照）注文履歴ID (出荷案内) | — |
| `line_no` | 元の注文の明細の行番号 | `orders.OrderLine.line_no` | 正の整数 | `purchasing.PurchaseOrderLine.line_no` (元の明細を引くだけ) | 正の整数 | （明細行参照）注文明細行ID (出荷案内 218) | UN01005585 |
| `supplier_order_no` | 受けた会社の受注番号 | `orders.Order.order_no` | 35 字 | `connect.ShippingNotice.supplier_order_no` | 35 字 | （ヘッダ参照）受注書ID (出荷案内 116) | UN01005580 |
| `shipment_no` | 出荷番号 | `shipping.Shipment.shipment_no` | 35 字 | `connect.ShippingNotice.shipment_no` | 35 字 | 出荷ID (出荷案内 169) | UN01005627 |
| `shipped_at` | 出荷日 | `shipping.Shipment.shipped_at` | 日付 | `connect.ShippingNotice.shipped_at` | 日付 | 出荷日 (出荷案内 170) | UN01005628 |
| `qty` | 出荷した数 | `shipping.Shipment.qty` | 整数 10 桁・小数 2 桁 | `connect.ShippingNotice.qty` | 整数 10 桁・小数 2 桁 | 出荷数量 (出荷案内 231) | UN01011509 |
| `supplier.id` | 受けた会社の id | — (会社の置き場の設定) | 決まりなし | — (読まない) | 読まない | 受注者ID (出荷案内 60) | UN01005757 |
| `supplier.name` | 受けた会社の名前 | `core.Company.name` | 100 字 | — (読まない) | 読まない | 受注者名称 (出荷案内 62) | UN01005759 |

## 検収 (kind = acceptance)

EDI の文書は出荷回答です。発注した会社が受け入れた日と数を、受けた会社へ返します。seizou の検収は金額を持たないので、金額と税を持つ EDI の仕入明細 (検収) ではなく、受領日と受領数量を持つ出荷回答に当てました。EDI の出荷回答で必須のヘッダ出荷回答タイプコード (UN01006810) は、今は送っていません (受け取れなかった物を返す形がまだ無いため)。

| JSON の鍵 | 意味 | 送る側の元の欄 | 送る字数・桁 | 受ける側が入れる欄 | 受け取れる字数・桁 | EDI の項目 (付表 2) | 共通辞書の ID |
|---|---|---|---|---|---|---|---|
| `id` | この知らせの番号 (見出しの webhook-id と同じ値) | `purchasing.Receipt.acceptance_message_id` | uuid (36 字) | `connect.Acceptance.message_id` (見出しの webhook-id から) | uuid (36 字) | 取引ID (出荷回答 3) | UN01005480 |
| `kind` | 知らせの種類 | — | 下の「知らせの種類」 | — (種類を見分けるだけ) | 下の「知らせの種類」 | 取引プロセスID (出荷回答 7) | UN01005472 |
| `sent_at` | 送った日時 | — | 日時 (下の「形」) | — (読まない) | 日時 (下の「形」) | 処理日時 (出荷回答 4) | UN01012746 |
| `order_id` | 元の注文の知らせの番号 | `connect.Outbox.message_id` | uuid (36 字) | `connect.Inbox.message_id` (元の受信した注文を引くだけ) | uuid (36 字) | — (seizou どうしで元の注文を引く番号。EDI の項目に当てない) | — |
| `order_no` | 発注番号 | `purchasing.PurchaseOrder.po_no` | 35 字 | `connect.Acceptance.buyer_order_no` | 35 字 | （ヘッダ参照）注文書ID (出荷回答 108) | UN01005580 |
| `order_revision_no` | 発注の注文履歴番号 | `purchasing.PurchaseOrder.revision_no` | 正の整数 | `connect.Acceptance.order_revision_no` | 正の整数 | （ヘッダ参照）注文履歴ID (出荷回答) | — |
| `line_no` | 発注の明細の行番号 | `purchasing.PurchaseOrderLine.line_no` | 正の整数 | `connect.Acceptance.buyer_line_no`・`orders.OrderLine.line_no` (受注の明細を引く) | 正の整数 | （明細行参照）注文明細行ID (出荷回答 235) | UN01005585 |
| `accepted_on` | 受け入れた日 | `purchasing.Receipt.received_on` | 日付 | `connect.Acceptance.accepted_on` | 日付 | 受領日 (出荷回答 182) | UN01005628 |
| `qty` | 受け入れた数 | `purchasing.Receipt.qty` | 整数 10 桁・小数 2 桁 | `connect.Acceptance.qty` | 整数 10 桁・小数 2 桁 | 受領数量 (出荷回答 254) | JPS2500009 |
| `buyer.id` | 発注した会社の id | — (会社の置き場の設定) | 決まりなし | — (読まない) | 読まない | 発注者ID (出荷回答 85) | UN01005757 |
| `buyer.name` | 発注した会社の名前 | `core.Company.name` | 100 字 | — (読まない) | 読まない | 発注者名称 (出荷回答 87) | UN01005759 |

## 会話の言葉 (kind = chat_message)

EDI に当たる文書はありません。受注と発注の 1 件ごとの会話の言葉で、seizou どうしだけで送り合います (EDI の電文に直す物に入れません)。発注した会社は発注の詳細から、受けた会社は受注か受信した注文の詳細から書き、相手の seizou にも同じ言葉が残ります。送り手の会社の辞書は、発注した会社なら `buyer`、受けた会社なら `supplier` の鍵に置き、もう片方の鍵は送りません。

| JSON の鍵 | 意味 | 送る側の元の欄 | 送る字数・桁 | 受ける側が入れる欄 | 受け取れる字数・桁 | EDI の項目 (付表 2) | 共通辞書の ID |
|---|---|---|---|---|---|---|---|
| `id` | この言葉の番号 (見出しの webhook-id と同じ値。送り直しも同じ値) | `connect.ChatMessage.message_id` | uuid (36 字) | `connect.ChatMessage.message_id` (見出しの webhook-id から) | uuid (36 字) | — (会話は EDI に無い) | — |
| `kind` | 知らせの種類 | — | 下の「知らせの種類」 | — (種類を見分けるだけ) | 下の「知らせの種類」 | — (会話は EDI に無い) | — |
| `sent_at` | 送った日時 | — | 日時 (下の「形」) | — (読まない) | 日時 (下の「形」) | — (会話は EDI に無い) | — |
| `order_id` | 元の注文 (第 1 版) の知らせの番号 | `connect.Outbox.message_id` (発注した会社)・`connect.Inbox.message_id` (受けた会社) | uuid (36 字) | `connect.Inbox.message_id`・`connect.Outbox.message_id` (元の注文を引くだけ) | uuid (36 字) | — (seizou どうしで元の注文を引く番号。EDI の項目に当てない) | — |
| `order_no` | 元の発注番号 (受けた会社は受信した注文の中身の order_no) | `purchasing.PurchaseOrder.po_no` | 35 字 | — (読まない) | 読まない | — (会話は EDI に無い) | — |
| `sender_role` | 送り手の立場 (`buyer` = 発注した会社・`supplier` = 受けた会社。ほかの値は受ける側が断る) | — | buyer か supplier の字 | — (引く表を決めるだけ) | buyer か supplier の字 | — (会話は EDI に無い) | — |
| `author` | 書いた人の氏名 (空の字でよい。ログインの名前は送らない。受ける側は 100 字で切って残す) | — (書いた人の氏名) | 決まりなし | `connect.ChatMessage.author_name` | 100 字 | — (会話は EDI に無い) | — |
| `written_at` | 書いた日時 (時差の付いた日時。送り直しも同じ値) | `connect.ChatMessage.written_at` | 日時 | `connect.ChatMessage.written_at` | 日時 | — (会話は EDI に無い) | — |
| `body` | 言葉 (1〜2000 字。空白だけの言葉は送らない) | `connect.ChatMessage.body` | 2000 字 | `connect.ChatMessage.body` | 2000 字 | — (会話は EDI に無い) | — |
| `buyer.id` | 発注した会社の id (発注した会社が送る時だけ) | — (会社の置き場の設定) | 決まりなし | — (読まない) | 読まない | — (会話は EDI に無い) | — |
| `buyer.name` | 発注した会社の名前 (発注した会社が送る時だけ) | `core.Company.name` | 100 字 | — (読まない) | 読まない | — (会話は EDI に無い) | — |
| `supplier.id` | 受けた会社の id (受けた会社が送る時だけ) | — (会社の置き場の設定) | 決まりなし | — (読まない) | 読まない | — (会話は EDI に無い) | — |
| `supplier.name` | 受けた会社の名前 (受けた会社が送る時だけ) | `core.Company.name` | 100 字 | — (読まない) | 読まない | — (会話は EDI に無い) | — |
| `attachments[].id` | 添付の識別 ID (添付があるときだけ鍵を送る。1 回に 1 つ) | `connect.ChatFile.file_id` | uuid (36 字) | `connect.ChatFile.file_id` | uuid (36 字) | 添付バイナリファイル識別ID (付表 2。行番号は未確認) | UN01006015 |
| `attachments[].name` | ファイル名 (拡張子は添付ファイルの形式の表にある物だけ。ほかは受ける側が断る) | `connect.ChatFile.name` | 100 字 | `connect.ChatFile.name` | 100 字 | 添付バイナリファイル名 (付表 2。行番号は未確認) | UN01006019 |
| `attachments[].mime` | 形式 (受ける側は読まず、自社の形式の表の値を入れる) | `connect.ChatFile.mime` | 100 字 | — (読まない) | 読まない | 添付バイナリファイルのMIMEコード (付表 2。行番号は未確認) | UN01006021 |
| `attachments[].size` | 大きさ (バイト。1 から 20MB まで。ほかは受ける側が断る) | — (会話の添付の大きさの欄) | 整数 (1〜20971520) | — (会話の添付の大きさの欄) | 整数 (1〜20971520) | 添付バイナリファイルサイズ (付表 2。行番号は未確認) | UN01006027 |
| `attachments[].sha256` | 中身の印 (SHA-256 の 16 進の 64 字。受けた側が取りに行った中身を確かめる) | `connect.ChatFile.sha256` | 64 字 | `connect.ChatFile.sha256` | 64 字 | — (EDI に無い) | — |

## 仕入明細 (kind = self_invoice)

EDI の文書は仕入明細です (文書タイプコード 389 仕入明細書。版 2 からは 384 修正仕入明細書、目的 1 は 384 取消仕入明細書)。発注した会社が、seizou どうしで送った発注の受入をまとめ、受け入れた数・金額・受領日を受けた会社へ送ります。受けた会社は受注の明細に当てて「受けた仕入明細」に入れます。金額は税抜きで、消費税は仕入明細 1 枚の合計に 1 回だけ計算します。

| JSON の鍵 | 意味 | 送る側の元の欄 | 送る字数・桁 | 受ける側が入れる欄 | 受け取れる字数・桁 | EDI の項目 (付表 2) | 共通辞書の ID |
|---|---|---|---|---|---|---|---|
| `id` | この知らせの番号 (見出しの webhook-id と同じ値。同じ版の送り直しも同じ値) | `purchasing.SelfInvoice.message_id` | uuid (36 字) | `connect.ReceivedSelfInvoice.message_id` (見出しの webhook-id から) | uuid (36 字) | — (当て先を確かめていない) | — |
| `kind` | 知らせの種類 | — | 下の「知らせの種類」 | — (種類を見分けるだけ) | 下の「知らせの種類」 | — (当て先を確かめていない) | — |
| `sent_at` | 送った日時 | — | 日時 (下の「形」) | — (読まない) | 日時 (下の「形」) | — (当て先を確かめていない) | — |
| `self_invoice_no` | 仕入明細番号 | `purchasing.SelfInvoice.self_invoice_no` | 35 字 | `connect.ReceivedSelfInvoice.self_invoice_no` | 35 字 | インボイス文書ID (仕入明細 SIID1) | UN01005862 |
| `revision_no` | 版 (第 1 版は 1。修正と取消を送るたびに 1 ずつ増える) | `purchasing.SelfInvoice.revision_no` | 正の整数 | `connect.ReceivedSelfInvoice.revision_no` | 正の整数 | インボイス文書履歴ID (仕入明細 SIID6) | UN01005874 |
| `document_code` | インボイス文書類型コード (版 1 は 1 = 仕入明細、版 2 からは 1C = 修正仕入明細) | — (版から決める) | 3 字 | `connect.ReceivedSelfInvoice.document_code` | 3 字 | インボイス文書類型コード (仕入明細 SIID7) | UN01005875 |
| `purpose_code` | インボイス文書目的コード (9 = 新規・4 = 変更・修正・1 = 取消) | `purchasing.SelfInvoice.purpose_code` | 2 字 | `connect.ReceivedSelfInvoice.purpose_code` | 2 字 | インボイス文書目的コード (仕入明細 SIID5) | UN01005869 |
| `issued_on` | 発行日 | `purchasing.SelfInvoice.issued_on` | 日付 | `connect.ReceivedSelfInvoice.issued_on` | 日付 | インボイス文書発効日 (仕入明細 SIID4) | UN01005865 |
| `period_from` | 締め期間の初め (取引先の締日で決める) | `purchasing.SelfInvoice.period_from` | 日付 | `connect.ReceivedSelfInvoice.period_from` | 日付 | 鑑ヘッダ取引開始日 (仕入明細 SIID161) | UN01005612 |
| `period_to` | 締め期間の終わり | `purchasing.SelfInvoice.period_to` | 日付 | `connect.ReceivedSelfInvoice.period_to` | 日付 | 鑑ヘッダ取引終了日 (仕入明細 SIID162) | UN01005613 |
| `due_on` | 支払期日 (取引先の支払の条件から決める。無ければ null) | `purchasing.SelfInvoice.due_on` | 日付 | `connect.ReceivedSelfInvoice.due_on` | 日付 | 支払期日 (仕入明細 SIID165) | UN01005784 |
| `subtotal` | 小計 (税抜きの明細の金額の合計) | `purchasing.SelfInvoice.subtotal` | 整数 12 桁・小数 2 桁 | `connect.ReceivedSelfInvoice.subtotal` | 整数 12 桁・小数 2 桁 | 明細文書ヘッダ課税分類課税対象合計金額 (仕入明細 SIID258) | UN01005839 |
| `tax` | 消費税 (小計に 1 回だけ率をかけて 1 円に丸めた額) | `purchasing.SelfInvoice.tax` | 整数 12 桁・小数 2 桁 | `connect.ReceivedSelfInvoice.tax` | 整数 12 桁・小数 2 桁 | 明細文書ヘッダ課税分類税額 (仕入明細 SIID256) | UN01005833 |
| `total` | 合計 (小計 + 消費税) | `purchasing.SelfInvoice.total` | 整数 12 桁・小数 2 桁 | `connect.ReceivedSelfInvoice.total` | 整数 12 桁・小数 2 桁 | — (当て先を確かめていない) | — |
| `tax_rate` | 税率 (百分率の字。例「10」) | `purchasing.SelfInvoice.tax_rate` | 整数 3 桁・小数 2 桁 | `connect.ReceivedSelfInvoice.tax_rate` | 整数 3 桁・小数 2 桁 | 明細文書ヘッダ税率 (仕入明細 SIID262) | UN01005836 |
| `tax_category_code` | 課税分類コード (S = 標準税率) | — (下の「送り合わない書類と欄」の課税分類コード) | 3 字 | `connect.ReceivedSelfInvoice.tax_category_code` | 3 字 | 明細文書ヘッダ課税分類コード (仕入明細 SIID259) | UN01005841 |
| `supplier_name` | 受けた会社の名前 (発注した会社の取引先マスターの名前) | `masters.Partner.name` | 100 字 | `connect.ReceivedSelfInvoice.supplier_name` | 100 字 | 受注者名称 (仕入明細 SIID26) | UN01005759 |
| `supplier_registration_no` | 受けた会社の適格請求書発行事業者の登録番号 (T と 13 桁の数字。無ければ空の字) | `purchasing.SelfInvoice.supplier_registration_no` | 14 字 | `connect.ReceivedSelfInvoice.supplier_registration_no` | 14 字 | 受注者適格請求書発行事業者登録番号 (仕入明細 SIID27) | UN01013039 |
| `buyer.id` | 発注した会社の id | — (会社の置き場の設定) | 決まりなし | — (読まない) | 読まない | — (当て先を確かめていない) | — |
| `buyer.name` | 発注した会社の名前 | `core.Company.name` | 100 字 | — (読まない) | 読まない | 発注者名称 (仕入明細 SIID45) | UN01005759 |
| `lines[].line_no` | 明細の行番号 (受入 1 件に 1 行) | `purchasing.SelfInvoiceLine.line_no` | 正の整数 | `connect.ReceivedSelfInvoiceLine.line_no` | 正の整数 | 明細行ID (仕入明細 SIID313) | UN01009648 |
| `lines[].order_id` | 元の注文 (第 1 版) の知らせの番号 | `connect.Outbox.message_id` | uuid (36 字) | `connect.Inbox.message_id` (元の受信した注文を引くだけ) | uuid (36 字) | — (seizou どうしで元の注文を引く番号。EDI の項目に当てない) | — |
| `lines[].order_no` | 発注番号 | `purchasing.PurchaseOrder.po_no` | 35 字 | `connect.ReceivedSelfInvoiceLine.buyer_order_no` | 35 字 | — (当て先を確かめていない) | — |
| `lines[].order_line_no` | 発注の明細の行番号 | `purchasing.PurchaseOrderLine.line_no` | 正の整数 | `connect.ReceivedSelfInvoiceLine.buyer_line_no`・`orders.OrderLine.line_no` (受注の明細を引く) | 正の整数 | — (当て先を確かめていない) | — |
| `lines[].order_revision_no` | 発注の注文履歴番号 (送った時の発注の版) | `purchasing.PurchaseOrder.revision_no` | 正の整数 | `connect.ReceivedSelfInvoiceLine.order_revision_no` | 正の整数 | — (当て先を確かめていない) | — |
| `lines[].part_no` | 発注した会社の自社品番 | `masters.Item.part_no` | 35 字 | — (読まない。受注の明細から引く) | 読まない | — (当て先を確かめていない) | — |
| `lines[].part_name` | 品名 | `masters.Item.name` | 100 字 | — (読まない。受注の明細から引く) | 読まない | 品目名 (仕入明細 SIID397) | UN01005815 |
| `lines[].received_on` | 受領日 (発注した会社が物を受け取った日) | `purchasing.SelfInvoiceLine.received_on` | 日付 | `connect.ReceivedSelfInvoiceLine.received_on` | 日付 | 明細行受領日 (仕入明細 SIID496) | UN01005628 |
| `lines[].qty` | 受領数量 | `purchasing.SelfInvoiceLine.qty` | 整数 10 桁・小数 2 桁 | `connect.ReceivedSelfInvoiceLine.qty` | 整数 10 桁・小数 2 桁 | 受領数量 (仕入明細 SIID355) | JPS2500012 |
| `lines[].unit` | 単位の名前の字 | `purchasing.SelfInvoiceLine.unit` | 10 字 | `connect.ReceivedSelfInvoiceLine.unit` | 10 字 | — (単位の名前。コードは `lines[].unit_code`) | — |
| `lines[].unit_code` | 数量単位コード (無ければ空の字) | `purchasing.SelfInvoiceLine.unit_code` | 10 字 | `connect.ReceivedSelfInvoiceLine.unit_code` | 10 字 | 受領数量単位コード (仕入明細 SIID356) | — |
| `lines[].price_basis` | 単価の単位 (`unit` = 部品の単位 1 つあたり・`lump` = 一式。鍵が無ければ `unit`) | `purchasing.SelfInvoiceLine.price_basis` | 10 字 | `connect.ReceivedSelfInvoiceLine.price_basis` | 10 字 | — (seizou の項目) | — |
| `lines[].unit_price` | 単価 (税抜き) | `purchasing.SelfInvoiceLine.unit_price` | 整数 12 桁・小数 2 桁 | `connect.ReceivedSelfInvoiceLine.unit_price` | 整数 12 桁・小数 2 桁 | — (当て先を確かめていない) | — |
| `lines[].amount` | 金額 (税抜き。受入の金額) | `purchasing.SelfInvoiceLine.amount` | 整数 12 桁・小数 2 桁 | `connect.ReceivedSelfInvoiceLine.amount` | 整数 12 桁・小数 2 桁 | — (当て先を確かめていない) | — |
| `lines[].revised_in` | この明細を最後に直した版 | `purchasing.SelfInvoiceLine.revised_in` | 正の整数 | `connect.ReceivedSelfInvoiceLine.revised_in` | 正の整数 | — (当て先を確かめていない) | — |

## 仕入明細回答 (kind = self_invoice_response)

EDI の文書は仕入明細回答です (文書タイプコード 389)。仕入明細を受けた会社が、明細ごとに承諾・提案 (違う数か単価)・確認できない (訳のコード) を発注した会社へ返します。その仕入明細番号への 1 回目の答えは仕入明細回答 (1R)、2 回目からの答えは修正仕入明細回答 (1CR) です。一度承諾した明細は答え直せません。

| JSON の鍵 | 意味 | 送る側の元の欄 | 送る字数・桁 | 受ける側が入れる欄 | 受け取れる字数・桁 | EDI の項目 (付表 2) | 共通辞書の ID |
|---|---|---|---|---|---|---|---|
| `id` | この知らせの番号 (見出しの webhook-id と同じ値。同じ答えの送り直しも同じ値) | `connect.ReceivedSelfInvoice.reply_message_id` | uuid (36 字) | `purchasing.SelfInvoice.reply_message_id` (見出しの webhook-id から) | uuid (36 字) | — (当て先を確かめていない) | — |
| `kind` | 知らせの種類 | — | 下の「知らせの種類」 | — (種類を見分けるだけ) | 下の「知らせの種類」 | — (当て先を確かめていない) | — |
| `sent_at` | 送った日時 | — | 日時 (下の「形」) | — (読まない) | 日時 (下の「形」) | — (当て先を確かめていない) | — |
| `self_invoice_no` | 元の仕入明細番号 | `connect.ReceivedSelfInvoice.self_invoice_no` | 35 字 | `purchasing.SelfInvoice.self_invoice_no` (元の仕入明細を引くだけ) | 35 字 | インボイス文書ID (仕入明細 SIID1) | UN01005862 |
| `revision_no` | 答えた仕入明細の版 | `connect.ReceivedSelfInvoice.revision_no` | 正の整数 | `purchasing.SelfInvoice.revision_no` (今の版と比べるだけ) | 正の整数 | インボイス文書履歴ID (仕入明細 SIID6) | UN01005874 |
| `document_code` | インボイス文書類型コード (1R = 仕入明細回答・1CR = 修正仕入明細回答) | `connect.ReceivedSelfInvoice.reply_document_code` | 3 字 | — (読まない) | 3 字 | インボイス文書類型コード (仕入明細 SIID7) | UN01005875 |
| `reply_type` | 鑑ヘッダの答え (AP = 一括承諾・CA = 部分承諾・RE = 一括確認不可・AC = 修正承諾。明細の答えから決める) | — (明細の答えから決める) | 2 字 | `purchasing.SelfInvoice.reply_type` | 2 字 | 鑑ヘッダ仕入明細回答タイプコード | UN01015491 |
| `supplier.id` | 受けた会社の id | — (会社の置き場の設定) | 決まりなし | — (読まない) | 読まない | — (当て先を確かめていない) | — |
| `supplier.name` | 受けた会社の名前 | `core.Company.name` | 100 字 | — (読まない) | 読まない | — (当て先を確かめていない) | — |
| `lines[].line_no` | 答えた明細の行番号 | `connect.ReceivedSelfInvoiceLine.line_no` | 正の整数 | `purchasing.SelfInvoiceLine.line_no` (明細を引くだけ) | 正の整数 | 明細行ID (仕入明細 SIID313) | UN01009648 |
| `lines[].reply_type` | 明細の答え (AP = 承諾・UV = 提案・RE = 確認できない・AC = 直した内容を承諾) | `connect.ReceivedSelfInvoiceLine.reply_type` | 2 字 | `purchasing.SelfInvoiceLine.reply_type` | 2 字 | 明細行回答タイプコード (仕入明細 SIID803) | UN01015599 |
| `lines[].reason_code` | 確認できない訳 (101〜105・191・199。確認できない以外は空の字) | `connect.ReceivedSelfInvoiceLine.reply_reason_code` | 3 字 | `purchasing.SelfInvoiceLine.reply_reason_code` | 3 字 | 明細行回答理由コード (仕入明細 SIID806) | JPS2500015 |
| `lines[].reply_qty` | 提案の数 (提案以外は null) | `connect.ReceivedSelfInvoiceLine.reply_qty` | 整数 10 桁・小数 2 桁 | `purchasing.SelfInvoiceLine.reply_qty` | 整数 10 桁・小数 2 桁 | — (当て先を確かめていない) | — |
| `lines[].reply_unit_price` | 提案の単価 (提案以外は null) | `connect.ReceivedSelfInvoiceLine.reply_unit_price` | 整数 12 桁・小数 2 桁 | `purchasing.SelfInvoiceLine.reply_unit_price` | 整数 12 桁・小数 2 桁 | — (当て先を確かめていない) | — |
| `lines[].note` | 書いておく字 | `connect.ReceivedSelfInvoiceLine.reply_note` | 500 字 | `purchasing.SelfInvoiceLine.reply_note` | 500 字 | 明細行回答理由 (仕入明細 SIID807) | JPS2500016 |

## コードの値

### 知らせの種類 (kind)

| kind | seizou の字 | EDI の文書 | 取引プロセスID の値 | 文書タイプコード (UNCL1001) | 文書サブタイプコード |
|---|---|---|---|---|---|
| `order` | 注文 | 注文 | SMEOrder | 220 | 22001 (注文書) |
| `order_reply` | 注文回答 | 注文回答 | SMEOrderResponse | 231 | 23101 (注文回答書) |
| `shipping_notice` | 出荷の知らせ | 出荷案内 | SMEDespatchAdvice | 351 | 35100 (出荷案内書) |
| `acceptance` | 検収 | 出荷回答 | SMEReceivingAdvice | 632 | 63201 (出荷回答書) |
| `chat_message` | 会話の言葉 | — (EDI に無い。seizou どうしだけの種類) | — | — | — |
| `self_invoice` | 仕入明細 | 仕入明細 | — (確かめていない) | 389 (版 2 からは 384 修正仕入明細書、目的 1 は 384 取消仕入明細書) | — |
| `self_invoice_response` | 仕入明細回答 | 仕入明細回答 | — (確かめていない) | 389 | — |

取引プロセスID の値は付表 2 と本編の字です (付表 4 の取引プロセスID の表は、出荷回答を SMERecievingAdvice と綴っています)。

### 注文回答の答え (reply_type)

値は中小企業共通 EDI の注文回答タイプコード (UNCL4343) の字そのもの (`core/edi_codes.py`)。明細ごとの答え (`lines[].reply_type`) は注文回答明細回答タイプコードで、同じ字の AP・CA・RE を使う。

| reply_type | 意味 | 注文回答タイプコード (UNCL4343) |
|---|---|---|
| `AP` | 受諾 (受け入れた) | AP |
| `CA` | 条件付受諾 (単価・数量・納期を直して受け入れた。直した明細は `lines[]`) | CA |
| `RE` | 辞退 (断った) | RE |
| `AJ` | 保留 (答えを待たせる) | AJ |

### 単位 (lines[].unit と lines[].unit_code)

単位の名前の字 (例「個」) と、EDI の数量単位コード (UNECE Rec.20。例 H87 = 個、KGM = kg) を一緒に送ります。コードの値は、付表 4 の数量単位コードの表 (国際サブセット定義表と＜参考＞の表の 40 行) の項目を元にした一覧 (`masters.QuantityUnit`) の物です。部品と明細の単位は、この一覧から選びます。
単位が一覧に無い字 (一覧から選ぶ前の部品の字など) の明細は、コードを空の字で送ります。EDI は数量単位コードの指定が無い数量を、数えられる品目 (定貫品目) の数と見なします (本編 7.3.2 の「数量単位コード」)。
受ける側は、一覧にあるコードが届けばそのコードを使い、無い・一覧に無いコードなら単位の名前から引きます。コードが一覧に無くても、受け入れは止めません。

## 形

- 本文: UTF-8 の JSON です。鍵を名前の順に並べ、空白を入れません。署名はこの本文の bytes のままにかけ、見出し webhook-id・webhook-timestamp・webhook-signature の 3 つで送ります。
- 日付: YYYY-MM-DD です (EDI の Date 型と同じ形)。
- 日時 (sent_at): 時差を付けた ISO 8601 の字です (例「2026-11-06T23:50:12.123456+09:00」。小数の秒は付かないことがあります)。EDI の DateTime 型 (YYYY-MM-DDThh:mm:ss) とは形が違います。
- 数: 数量・単価・出荷と検収の数は、区切りの無い 10 進の字 (JSON の文字列) です (例「10.00」)。小数は 2 桁までです。行番号 (line_no) は JSON の整数です。
- 通貨: 送りません。EDI は通貨を指定しない業務アプリを、日本円 (JPY) を指定したと見なします (本編 7.3.2 の「通貨コード」)。
- 単価: 税抜きです。EDI の注文単価も、税計算方式の指定が無ければ税抜きです (付表 2 の注文 245 の定義)。
- 字: 使える字の範囲は限っていません。EDI の電文は、JIS X 0201・JIS X 0208・JIS X 0212 (と対応の取れる JIS X 0213) の字に限ります (本編 7.3.2 の「文字コード」)。

## 受け取れる字数を超えたとき

- 受け口で断る物: 注文の発注番号・品番・品名・単位・明細の備考で、上の表の受け取れる字数を超える字 (品番は前後の空白を外して数えます)、注文の明細の数量・単価・金額 (数量 × 単価)、出荷の知らせの数と出荷番号、検収の数。会話の言葉 (2000 字。空白だけの言葉も断る)。仕入明細の仕入明細番号・受けた会社の名前・登録番号・課税分類コード・明細の発注番号・単位・数量単位コードで、上の表の受け取れる字数を超える字と、仕入明細の数と金額 (例「仕入明細の 2 行目の単位は 10 字までにしてください。」)。仕入明細回答の明細の字 (note。500 字) と提案の数と単価。断った注文は「受信した注文」に入りません。断った理由は日本語の文 (例「注文の 2 行目の品番は 35 字までにしてください。」) で送った側に返り、送った側の相手の返事の欄に「断られた: <理由>」の形で残ります。字や数を直して送り直してください。
- 欄の字数で切って残す物: 検収の発注番号 (35 字)、出荷の知らせの受注番号 (35 字)、注文回答の断った理由 (500 字)、会話の書いた人の氏名 (100 字)。
- 受けた中身に残すだけの物 (発注した会社の名前・取引先コード・発注の備考など): 字数を見ず、受注の表には入れません。

## 送り合わない書類と欄で、EDI の項目に当てた物

- 納品書 (`shipping.DeliveryNote.note_no`、35 字) = 出荷案内書ID (付表 2 の出荷案内 30・UN01006801)。EDI の文書サブタイプは一般納品書 (35101) です。seizou の納品書は印刷するだけで、会社どうしでは送りません。
- 請求書の消費税 = 課税分類コード S (標準税率)。率は 10% の 1 つです。
- 請求書は印刷するだけで、今は会社どうしでは送りません。
