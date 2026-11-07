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
| `lines[].unit` | 単位の名前の字 (例「個」) | `purchasing.PurchaseOrderLine.unit` | 10 字 | `orders.OrderLine.unit` | 10 字 | — (単位の名前。EDI は注文数量の数量単位属性 (注文 254) を UNECE Rec.20 のコードで持つ。下の「単位」) | — |
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

## コードの値

### 知らせの種類 (kind)

| kind | seizou の字 | EDI の文書 | 取引プロセスID の値 | 文書タイプコード (UNCL1001) | 文書サブタイプコード |
|---|---|---|---|---|---|
| `order` | 注文 | 注文 | SMEOrder | 220 | 22001 (注文書) |
| `order_reply` | 注文回答 | 注文回答 | SMEOrderResponse | 231 | 23101 (注文回答書) |
| `shipping_notice` | 出荷の知らせ | 出荷案内 | SMEDespatchAdvice | 351 | 35100 (出荷案内書) |
| `acceptance` | 検収 | 出荷回答 | SMEReceivingAdvice | 632 | 63201 (出荷回答書) |

取引プロセスID の値は付表 2 と本編の字です (付表 4 の取引プロセスID の表は、出荷回答を SMERecievingAdvice と綴っています)。

### 注文回答の答え (reply_type)

値は中小企業共通 EDI の注文回答タイプコード (UNCL4343) の字そのもの (`core/edi_codes.py`)。明細ごとの答え (`lines[].reply_type`) は注文回答明細回答タイプコードで、同じ字の AP・CA・RE を使う。

| reply_type | 意味 | 注文回答タイプコード (UNCL4343) |
|---|---|---|
| `AP` | 受諾 (受け入れた) | AP |
| `CA` | 条件付受諾 (単価・数量・納期を直して受け入れた。直した明細は `lines[]`) | CA |
| `RE` | 辞退 (断った) | RE |
| `AJ` | 保留 (答えを待たせる) | AJ |

### 単位 (lines[].unit)

今は単位の名前の字 (例「個」) を送り、EDI の数量単位コード (UNECE Rec.20。例 H87 = 個、KGM = kg) は送っていません。EDI は数量単位コードの指定が無い数量を、数えられる品目 (定貫品目) の数と見なします (本編 7.3.2 の「数量単位コード」)。

## 形

- 本文: UTF-8 の JSON です。鍵を名前の順に並べ、空白を入れません。署名はこの本文の bytes のままにかけ、見出し webhook-id・webhook-timestamp・webhook-signature の 3 つで送ります。
- 日付: YYYY-MM-DD です (EDI の Date 型と同じ形)。
- 日時 (sent_at): 時差を付けた ISO 8601 の字です (例「2026-11-06T23:50:12.123456+09:00」。小数の秒は付かないことがあります)。EDI の DateTime 型 (YYYY-MM-DDThh:mm:ss) とは形が違います。
- 数: 数量・単価・出荷と検収の数は、区切りの無い 10 進の字 (JSON の文字列) です (例「10.00」)。小数は 2 桁までです。行番号 (line_no) は JSON の整数です。
- 通貨: 送りません。EDI は通貨を指定しない業務アプリを、日本円 (JPY) を指定したと見なします (本編 7.3.2 の「通貨コード」)。
- 単価: 税抜きです。EDI の注文単価も、税計算方式の指定が無ければ税抜きです (付表 2 の注文 245 の定義)。
- 字: 使える字の範囲は限っていません。EDI の電文は、JIS X 0201・JIS X 0208・JIS X 0212 (と対応の取れる JIS X 0213) の字に限ります (本編 7.3.2 の「文字コード」)。

## 受け取れる字数を超えたとき

- 受け口で断る物: 注文の発注番号・品番・品名・単位・明細の備考で、上の表の受け取れる字数を超える字 (品番は前後の空白を外して数えます)、注文の明細の数量・単価・金額 (数量 × 単価)、出荷の知らせの数と出荷番号、検収の数。断った注文は「受信した注文」に入りません。断った理由は日本語の文 (例「注文の 2 行目の品番は 35 字までにしてください。」) で送った側に返り、送った側の相手の返事の欄に「断られた: <理由>」の形で残ります。字や数を直して送り直してください。
- 欄の字数で切って残す物: 検収の発注番号 (35 字)、出荷の知らせの受注番号 (35 字)、注文回答の断った理由 (500 字)。
- 受けた中身に残すだけの物 (発注した会社の名前・取引先コード・発注の備考など): 字数を見ず、受注の表には入れません。

## 送り合わない書類と欄で、EDI の項目に当てた物

- 納品書 (`shipping.DeliveryNote.note_no`、35 字) = 出荷案内書ID (付表 2 の出荷案内 30・UN01006801)。EDI の文書サブタイプは一般納品書 (35101) です。seizou の納品書は印刷するだけで、会社どうしでは送りません。
- 請求書の消費税 = 課税分類コード S (標準税率)。率は 10% の 1 つです。
- 請求書は印刷するだけで、今は会社どうしでは送りません。
