"""作り物のデータの読み込み (seizou new --demo。01 の 10.6)。

load(seeds_dir) が、種のフォルダ (答えの YAML と同じ場所の <id>/) の 6 つのファイルを読み、
部品の services の関数を通して入れる。入れる順は 品目 → 取引先 → 品番の対応 → 工程 → 受注 → つながり。
表には直接書かない (書くのは services)。読むだけは表を引いてよい。

- core は本来ほかの部品を呼ばない (01 の 1 節)。この module だけは例外で、ほかの部品の import を関数の中に置く。
- 2 回入れても重ならない。自然な鍵 (品番・取引先コード・取引先 × 取引先の品番・工程コード・
  得意先 × 得意先の注文番号・取引先 × 向き) で既にある物は飛ばす (上書きしない)。
- 1 行でも services が断ったら、ファイルと位置を日本語の文に入れて ValueError を上げ、全体を巻き戻す。
  YAML は行の番号が取れないので「N 件目」で示す (明細の変換の誤りは「M 行目の明細」まで。services が断った受注は、受注の位置と注文番号)。CSV は行の番号 (見出しが 1 行目)。
- CSV の見出しと YAML の鍵は、部品の表の verbose_name (日本語の項目名)。対応の表は手で書かず、model の _meta から引く。
  画面の字を直す前の見出し (FORMER_HEADERS) も読む (種の見出しは別の係が持つので、すぐには変えない)。
- 取引先の支払の条件は 支払の月 (当月・翌月・翌々月) と 支払日 (1〜31 の数。31 は月末) の 2 列。
- 会社ごとの項目 (items.csv の見出しのうち部品の表に無い物) は、core.fields の登録の見出しと突き合わせて ext= にする。
- 続けて、新しい 6 種類を入れる (種のファイルが無い種類は飛ばし、出力の行も出さない)。
  標準の工程 (item_processes.csv)・図面 (items.csv の「図面ファイル」の欄と files/)・
  指示書 (workorders.yaml)・出荷 (shipments.yaml)・納品書 (delivery_notes.yaml)・請求書 (invoices.yaml)。
  受注の明細は 得意先の取引先コード × 得意先の注文番号 × 行番号、出荷は 受注の明細 × 出荷日 で指す。
  納品書と指示書の発行日は、services が今日にするので、読み込みの日になる。
- 最後に、部品が持つ種 (部品の demo_seed.load(seeds_dir, user)) を PART_SEEDS の順に呼ぶ。業務マニュアル・権限・在庫・出荷の知らせと検収・入金・休日 の 6 つ。
  受注の後 (つながりの前) に、部品の demo_seed に load_after_orders(seeds_dir, user) があれば先に呼ぶ
  (届いた注文の写しの受注を、出荷と納品書の種が指せるようにするため。数は同じ種類に足し、出力の行は 1 行)。
  部品が入っていないときと、入れた物も飛ばした物も 0 のときは、出力の行を出さない。
"""

from __future__ import annotations

import csv
import datetime
from contextlib import contextmanager
from decimal import Decimal, InvalidOperation
import inspect
from importlib import import_module
from pathlib import Path

import yaml
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.files import File
from django.db import models, transaction

from seizou.core import fields as company_fields
from seizou.core.exceptions import RuleViolation

KINDS = ("品目", "取引先", "品番の対応", "工程", "受注", "つながり")
SEED_FILES = {
    "品目": "items.csv",
    "取引先": "partners.csv",
    "品番の対応": "partner_items.csv",
    "工程": "processes.yaml",
    "受注": "orders.yaml",
    "つながり": "connections.yaml",
}
YAML_ROOT_KEYS = {"工程": "工程", "受注": "受注", "つながり": "つながり"}

ORDER_KEYS = ("得意先の取引先コード", "受注日", "得意先の注文番号", "備考", "明細")
LINE_KEYS = ("自社品番", "数量", "単位", "単価", "納期", "備考")
CONNECTION_KEYS = ("取引先コード", "受け口の URL", "鍵の置き場の名前", "向き")
PROCESS_KEYS = ("工程コード", "名前", "協力企業に出すか", "並び順", "計画に使う")
PARTNER_ITEM_HEADERS = ("取引先コード", "自社品番", "取引先の品番", "取引先の品名")

# 新しい 6 種類 (今の 6 種類の後)。種のファイルが無い種類は飛ばす。図面は files/ のフォルダがあるときだけ回す。
MORE_KINDS = ("標準の工程", "図面", "指示書", "出荷", "納品書", "請求書")
MORE_SEED_FILES = {
    "標準の工程": "item_processes.csv",
    "図面": "files",
    "指示書": "workorders.yaml",
    "出荷": "shipments.yaml",
    "納品書": "delivery_notes.yaml",
    "請求書": "invoices.yaml",
}
MORE_YAML_ROOT_KEYS = {"指示書": "指示書", "出荷": "出荷", "納品書": "納品書", "請求書": "請求書"}

# 部品が持つ種 (種類の名前, 部品の app)。部品の demo_seed.load(seeds_dir, user) が {"入れた": n, "飛ばした": m} を返す。
PART_SEEDS = (("業務マニュアル", "seizou.manuals"), ("権限", "seizou.accounts"), ("在庫", "seizou.stock"), ("出荷の知らせと検収", "seizou.connect"), ("入金", "seizou.billing"), ("休日", "seizou.masters"))
# 種のフォルダを読まない部品の種は、種のフォルダに印のファイルがあるときだけ入れる (中身は読まない)。
PART_SEED_MARKERS = {"seizou.accounts": "accounts.yaml"}

ITEM_PROCESS_HEADERS = ("自社品番", "工程コード", "順")
LINE_REF_KEYS = ("得意先の取引先コード", "得意先の注文番号", "行番号")
WORKORDER_KEYS = (*LINE_REF_KEYS, "数量", "工程")
WORKORDER_STEP_KEYS = ("工程コード", "予定の数", "開始日", "終了日")
SHIPMENT_KEYS = (*LINE_REF_KEYS, "数量", "出荷日", "備考")
SHIPMENT_REF_KEYS = ("得意先の注文番号", "行番号", "出荷日")
DELIVERY_NOTE_KEYS = ("得意先の取引先コード", "出荷")
INVOICE_KEYS = ("得意先の取引先コード", "発行日", "出荷")

TRUE_WORDS = frozenset({"true", "1", "はい", "yes"})
FALSE_WORDS = frozenset({"false", "0", "いいえ", "no"})


@contextmanager
def _guard(source, where):
    """services と変換の誤りに、ファイルと位置を付けて ValueError に上げ直す (rollback は呼んだ側の atomic)。"""
    try:
        yield
    except (ValueError, RuleViolation) as exc:
        raise ValueError(f"{source} の {where}: {exc}") from exc


def _is_blank(raw):
    return raw is None or (isinstance(raw, str) and raw.strip() == "")


#: 画面の字 (verbose_name) を直す前の見出し → 列の名前。種の CSV がこの字のままでも読む。
FORMER_HEADERS = {
    "masters.Item": {"標準単価": "standard_price"},
    "masters.Partner": {"小計の計算方法": "rounding", "消費税の計算方法": "tax_mode"},
}


def _fields_by_label(model):
    """見出し (verbose_name) → 列。主キーは除く。前の見出し (FORMER_HEADERS) でも引ける。"""
    fields = {f.verbose_name: f for f in model._meta.concrete_fields if not f.primary_key}
    by_name = {f.name: f for f in fields.values()}
    for header, name in FORMER_HEADERS.get(model._meta.label, {}).items():
        if name in by_name:
            fields.setdefault(header, by_name[name])
    return fields


def _choice_value(field, raw):
    text = str(raw).strip()
    by_label = {str(label): value for value, label in field.flatchoices}
    if text in by_label:
        return by_label[text]
    if text in {value for value, _label in field.flatchoices}:
        return text
    raise ValueError(f"{field.verbose_name}「{text}」は使えません。{'・'.join(by_label)} のどれかにしてください。")


def _coerce(field, raw):
    """種の 1 つの値 (CSV の字か YAML の値) を、列の型の値にする。空は、空を許す列なら空 (数と日付は None)。"""
    if isinstance(field, models.BooleanField):
        if isinstance(raw, bool):
            return raw
        if _is_blank(raw):
            return field.default if field.has_default() else False
        word = str(raw).strip().lower()
        if word in TRUE_WORDS:
            return True
        if word in FALSE_WORDS:
            return False
        raise ValueError(f"{field.verbose_name}「{raw}」は はい か いいえ にしてください。")
    if _is_blank(raw):
        if field.null:
            return None
        if field.blank:
            return ""
        raise ValueError(f"{field.verbose_name}が空です。値を入れてください。")
    if field.choices:
        return _choice_value(field, raw)
    try:
        if isinstance(field, models.DecimalField):
            return Decimal(str(raw).strip())
        if isinstance(field, models.IntegerField):
            return int(str(raw).strip())
        if isinstance(field, models.DateField):
            if isinstance(raw, datetime.date):
                return raw
            return datetime.date.fromisoformat(str(raw).strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{field.verbose_name}「{raw}」の形が違います。") from None
    text = str(raw).strip()
    limit = getattr(field, "max_length", None)
    if limit and len(text) > limit:
        raise ValueError(f"{field.verbose_name}は {limit} 字までです ({len(text)} 字ありました)。")
    return text


def _coerce_ext(column, raw):
    """会社ごとの項目 (登録の列) の値。型は登録の型 (text・number・date・choice・bool)。"""
    if column.type == "bool":
        return _coerce(models.BooleanField(column.label), raw)
    if column.type == "number":
        return _coerce(models.DecimalField(column.label, null=True, blank=True), raw)
    if column.type == "date":
        return _coerce(models.DateField(column.label, null=True, blank=True), raw)
    return "" if _is_blank(raw) else str(raw).strip()


def _open_file(seeds_dir, kind):
    path = seeds_dir / SEED_FILES[kind]
    if not path.is_file():
        raise FileNotFoundError(f"種のファイル {path} がありません。種のフォルダに {SEED_FILES[kind]} を置いてください。")
    return path


def _read_csv(path, known, required, *, extra_ok=False):
    """CSV を読み、(見出しの並び, [(行の番号, {見出し: 字})]) を返す。utf-8-sig で BOM を外す。"""
    with open(path, encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        headers = [h.strip() for h in (reader.fieldnames or [])]
        reader.fieldnames = headers
        missing = [h for h in required if h not in headers]
        if missing:
            raise ValueError(f"{path.name} の見出しに「{'」「'.join(missing)}」がありません。")
        unknown = [h for h in headers if h not in known]
        if unknown and not extra_ok:
            raise ValueError(f"{path.name} の見出し「{'」「'.join(unknown)}」は、部品の表にありません。")
        rows = [(reader.line_num, {k: v for k, v in row.items() if k is not None}) for row in reader]
    return headers, rows


def _read_yaml(path, root_key):
    """YAML を読み、根の鍵の下の並びを返す。"""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ValueError(f"{path.name} を YAML として読めません: {exc}") from exc
    if not isinstance(data, dict) or root_key not in data:
        raise ValueError(f"{path.name} に「{root_key}」の並びがありません。")
    entries = data[root_key] or []
    if not isinstance(entries, list):
        raise ValueError(f"{path.name} の「{root_key}」は並びで書いてください。")
    return entries


def _check_entry_keys(path, number, entry, allowed, what):
    if not isinstance(entry, dict):
        raise ValueError(f"{path.name} の {number} 件目の{what}の形が違います (鍵と値の並びではありません)。")
    unknown = [key for key in entry if key not in allowed]
    if unknown:
        raise ValueError(f"{path.name} の {number} 件目の{what}の鍵「{'」「'.join(unknown)}」が分かりません。")


def _partner_by_code(code):
    from seizou.masters.models import Partner

    partner = Partner.objects.filter(code=str(code).strip()).first()
    if partner is None:
        raise ValueError(f"取引先コード {code} が取引先にありません。取引先の種に行を足してください。")
    return partner


def _item_by_part_no(part_no):
    from seizou.masters.models import Item

    item = Item.objects.filter(part_no=str(part_no).strip()).first()
    if item is None:
        raise ValueError(f"自社品番 {part_no} が品目にありません。品目の種に行を足してください。")
    return item


def _load_items(seeds_dir, user, notices):
    from seizou.masters import services
    from seizou.masters.models import Item

    path = _open_file(seeds_dir, "品目")
    by_label = _fields_by_label(Item)
    registration = company_fields.registration_for(Item)
    ext_by_label = {c.label: c for c in registration.columns} if registration else {}
    headers, rows = _read_csv(path, by_label, ("自社品番", "品名"), extra_ok=True)

    extra = [h for h in headers if h not in by_label]
    if extra and registration is None:
        notices.append(f"会社ごとの項目が登録されていないので {len(extra)} 列を入れませんでした ({'・'.join(extra)})。")
        extra = []
    stray = [h for h in extra if h not in ext_by_label]
    if stray:
        raise ValueError(f"{path.name} の見出し「{'」「'.join(stray)}」は、部品の表にも会社ごとの項目にもありません。")

    made = skipped = 0
    for line_no, row in rows:
        with _guard(path.name, f"{line_no} 行目"):
            data = {}
            for header, raw in row.items():
                field = by_label.get(header)
                if field is None or header == "図面ファイル":
                    continue
                data[field.name] = _coerce(field, raw)
            if Item.objects.filter(part_no=data["part_no"]).exists():
                skipped += 1
                continue
            ext = {ext_by_label[h].name: _coerce_ext(ext_by_label[h], row.get(h)) for h in extra} or None
            services.create_item(data, user, ext=ext)
            made += 1
    return made, skipped


def _load_partners(seeds_dir, user, notices):
    from seizou.masters import services
    from seizou.masters.models import Partner

    path = _open_file(seeds_dir, "取引先")
    by_label = _fields_by_label(Partner)
    _headers, rows = _read_csv(path, by_label, ("取引先コード", "名前", "取引先の種類"))

    made = skipped = 0
    for line_no, row in rows:
        with _guard(path.name, f"{line_no} 行目"):
            data = {by_label[h].name: _coerce(by_label[h], raw) for h, raw in row.items()}
            if Partner.objects.filter(code=data["code"]).exists():
                skipped += 1
                continue
            services.create_partner(data, user)
            made += 1
    return made, skipped


def _load_partner_items(seeds_dir, user, notices):
    from seizou.masters import services
    from seizou.masters.models import PartnerItem

    path = _open_file(seeds_dir, "品番の対応")
    by_label = _fields_by_label(PartnerItem)
    _headers, rows = _read_csv(path, PARTNER_ITEM_HEADERS, ("取引先コード", "自社品番", "取引先の品番"))

    made = skipped = 0
    for line_no, row in rows:
        with _guard(path.name, f"{line_no} 行目"):
            partner = _partner_by_code(row["取引先コード"])
            part_no = _coerce(by_label["取引先の品番"], row["取引先の品番"])
            if services.resolve_partner_item(partner, part_no) is not None:
                skipped += 1
                continue
            item = _item_by_part_no(row["自社品番"])
            name = _coerce(by_label["取引先の品名"], row.get("取引先の品名"))
            services.map_partner_item(partner, item, part_no, user, partner_item_name=name)
            made += 1
    return made, skipped


def _load_processes(seeds_dir, user, notices):
    from seizou.masters import services
    from seizou.masters.models import Process

    path = _open_file(seeds_dir, "工程")
    by_label = _fields_by_label(Process)
    made = skipped = 0
    for number, entry in enumerate(_read_yaml(path, YAML_ROOT_KEYS["工程"]), start=1):
        _check_entry_keys(path, number, entry, PROCESS_KEYS, "工程")
        with _guard(path.name, f"{number} 件目の工程"):
            data = {by_label[label].name: _coerce(by_label[label], raw) for label, raw in entry.items()}
            if Process.objects.filter(code=data["code"]).exists():
                skipped += 1
                continue
            services.create_process(data, user)
            made += 1
    return made, skipped


def _order_line(path, number, position, entry, line_fields):
    where = f"{number} 件目の受注の {position} 行目の明細"
    _check_entry_keys(path, f"{number} 件目の受注の {position} 行目", entry, LINE_KEYS, "明細")
    with _guard(path.name, where):
        line = {
            "item": _item_by_part_no(entry.get("自社品番")),
            "qty": _coerce(line_fields["数量"], entry.get("数量")),
            "unit_price": _coerce(line_fields["単価"], entry.get("単価")),
        }
        # 単位が空なら鍵を渡さない (渡さなければ品目の単位が写る。D11 の約束)。
        for label, key in (("単位", "unit"), ("納期", "due_date"), ("備考", "note")):
            if not _is_blank(entry.get(label)):
                line[key] = _coerce(line_fields[label], entry[label])
        return line


def _load_orders(seeds_dir, user, notices):
    from seizou.orders import services
    from seizou.orders.models import Order, OrderLine

    path = _open_file(seeds_dir, "受注")
    order_fields = _fields_by_label(Order)
    line_fields = _fields_by_label(OrderLine)
    made = skipped = notes_dropped = 0
    for number, entry in enumerate(_read_yaml(path, YAML_ROOT_KEYS["受注"]), start=1):
        _check_entry_keys(path, number, entry, ORDER_KEYS, "受注")
        with _guard(path.name, f"{number} 件目の受注"):
            customer = _partner_by_code(entry.get("得意先の取引先コード"))
            order_date = _coerce(order_fields["受注日"], entry.get("受注日"))
            customer_order_no = _coerce(order_fields["得意先の注文番号"], entry.get("得意先の注文番号"))
            if not customer_order_no:
                raise ValueError("得意先の注文番号が空です。2 回入れても重ならない鍵なので、受注ごとに書いてください。")
            if Order.objects.filter(customer=customer, customer_order_no=customer_order_no).exists():
                skipped += 1
                continue
            if not _is_blank(entry.get("備考")):
                notes_dropped += 1
        raw_lines = entry.get("明細")
        if not isinstance(raw_lines, list) or not raw_lines:
            raise ValueError(f"{path.name} の {number} 件目の受注に明細がありません。")
        lines = [
            _order_line(path, number, position, raw, line_fields)
            for position, raw in enumerate(raw_lines, start=1)
        ]
        # create_order は明細をまとめて見るので、断られた明細の位置は取れない。受注の位置と注文番号で示す。
        with _guard(path.name, f"{number} 件目の受注 (得意先の注文番号 {customer_order_no})"):
            services.create_order(
                customer, lines, user, order_date=order_date, customer_order_no=customer_order_no
            )
            made += 1
    if notes_dropped:
        notices.append(f"受注の備考 {notes_dropped} 件は、create_order が受けないので入れませんでした。")
    return made, skipped


def _load_connections(seeds_dir, user, notices):
    from seizou.connect import services
    from seizou.connect.models import Connection

    path = _open_file(seeds_dir, "つながり")
    by_label = _fields_by_label(Connection)
    made = skipped = 0
    for number, entry in enumerate(_read_yaml(path, YAML_ROOT_KEYS["つながり"]), start=1):
        _check_entry_keys(path, number, entry, CONNECTION_KEYS, "つながり")
        with _guard(path.name, f"{number} 件目のつながり"):
            partner = _partner_by_code(entry.get("取引先コード"))
            direction = _coerce(by_label["向き"], entry.get("向き"))
            if Connection.objects.filter(partner=partner, direction=direction).exists():
                skipped += 1
                continue
            url = _coerce(by_label["受け口の URL"], entry.get("受け口の URL"))
            secret_env = _coerce(by_label["鍵の置き場の名前"], entry.get("鍵の置き場の名前"))
            services.register_connection(partner, url, secret_env, direction, user)
            made += 1
    return made, skipped


def _process_by_code(code):
    from seizou.masters.models import Process

    process = Process.objects.filter(code=str(code).strip()).first()
    if process is None:
        raise ValueError(f"工程コード {code} が工程にありません。工程の種に行を足してください。")
    return process


def _order_line_by_key(customer, customer_order_no, line_no):
    """得意先 × 得意先の注文番号 × 行番号で、受注の明細を 1 つ引く。無い・2 つ以上は日本語の ValueError。"""
    from seizou.orders.models import Order, OrderLine

    order_no = _coerce(Order._meta.get_field("customer_order_no"), customer_order_no)
    if not order_no:
        raise ValueError("得意先の注文番号が空です。受注の明細を指すので、書いてください。")
    number = _coerce(OrderLine._meta.get_field("line_no"), line_no)
    found = list(
        OrderLine.objects.filter(order__customer=customer, order__customer_order_no=order_no, line_no=number)[:2]
    )
    where = f"取引先コード {customer.code}・得意先の注文番号 {order_no}・行番号 {number}"
    if not found:
        raise ValueError(f"受注の明細が見つかりません ({where})。受注の種に行を足してください。")
    if len(found) > 1:
        raise ValueError(f"受注の明細が 2 つ以上あります ({where})。")
    return found[0]


def _line_of_entry(entry):
    return _order_line_by_key(
        _partner_by_code(entry.get("得意先の取引先コード")),
        entry.get("得意先の注文番号"),
        entry.get("行番号"),
    )


def _shipments_of_entry(path, number, entry, customer, what):
    """納品書と請求書の件の「出荷」の並びを、受注の明細 × 出荷日で出荷に引き直す。"""
    from seizou.shipping.models import Shipment

    refs = entry.get("出荷")
    if not isinstance(refs, list) or not refs:
        raise ValueError(f"{path.name} の {number} 件目の{what}に出荷がありません。")
    shipped_field = Shipment._meta.get_field("shipped_at")
    shipments = []
    for position, ref in enumerate(refs, start=1):
        _check_entry_keys(path, f"{number} 件目の{what}の {position} 番目", ref, SHIPMENT_REF_KEYS, "出荷")
        with _guard(path.name, f"{number} 件目の{what}の {position} 番目の出荷"):
            line = _order_line_by_key(customer, ref.get("得意先の注文番号"), ref.get("行番号"))
            shipped_at = _coerce(shipped_field, ref.get("出荷日"))
            found = list(Shipment.objects.filter(order_line=line, shipped_at=shipped_at)[:2])
            if not found:
                raise ValueError(
                    f"出荷が見つかりません (得意先の注文番号 {ref.get('得意先の注文番号')}・行番号 {ref.get('行番号')}"
                    f"・出荷日 {shipped_at})。出荷の種に行を足してください。"
                )
            if len(found) > 1:
                raise ValueError(f"同じ明細に同じ日の出荷が 2 つあります (出荷日 {shipped_at})。")
            shipments.append(found[0])
    return shipments


def _load_item_processes(seeds_dir, user, notices):
    from seizou.masters import services
    from seizou.masters.models import ItemProcess

    path = seeds_dir / MORE_SEED_FILES["標準の工程"]
    if not path.is_file():
        return None
    seq_field = ItemProcess._meta.get_field("seq")
    _headers, rows = _read_csv(path, ITEM_PROCESS_HEADERS, ITEM_PROCESS_HEADERS)

    grouped: dict = {}
    for line_no, row in rows:
        with _guard(path.name, f"{line_no} 行目"):
            part_no = (row.get("自社品番") or "").strip()
            code = (row.get("工程コード") or "").strip()
            if not part_no:
                raise ValueError("自社品番が空です。")
            if not code:
                raise ValueError("工程コードが空です。")
            seq = _coerce(seq_field, row.get("順"))
            steps = grouped.setdefault(part_no, {})
            if seq in steps:
                raise ValueError(f"自社品番 {part_no} に順 {seq} が 2 つあります。順は部品ごとに重ならない数にしてください。")
            steps[seq] = (line_no, code)

    made = skipped = 0
    for part_no, steps in grouped.items():
        first_line = min(line_no for line_no, _code in steps.values())
        with _guard(path.name, f"{first_line} 行目 (自社品番 {part_no})"):
            item = _item_by_part_no(part_no)
            if item.item_processes.exists():
                skipped += 1
                continue
            processes = [_process_by_code(steps[seq][1]) for seq in sorted(steps)]
            services.set_item_processes(item, processes, user)
            made += 1
    return made, skipped


def _load_drawings(seeds_dir, user, notices):
    from seizou.masters import services
    from seizou.masters.models import Item

    files_dir = seeds_dir / MORE_SEED_FILES["図面"]
    if not files_dir.is_dir():
        return None
    path = _open_file(seeds_dir, "品目")
    by_label = _fields_by_label(Item)
    _headers, rows = _read_csv(path, by_label, ("自社品番", "品名"), extra_ok=True)

    made = skipped = 0
    for line_no, row in rows:
        name = (row.get("図面ファイル") or "").strip()
        if not name:
            continue
        with _guard(path.name, f"{line_no} 行目"):
            if "/" in name or "\\" in name or ".." in name:
                raise ValueError(f"図面ファイル「{name}」は files/ の中のファイルの名前だけを書いてください (フォルダの道は書けません)。")
            source = files_dir / name
            if not source.is_file():
                raise ValueError(f"図面ファイル「{name}」が files/ にありません。")
            item = _item_by_part_no(row.get("自社品番"))
            if item.drawing:
                skipped += 1
                continue
            # 開いたまま update_item に渡し、保存の後に閉じる (先に閉じると空のファイルになる)。
            with open(source, "rb") as handle:
                services.update_item(item, {"drawing": File(handle, name=name)}, user)
            made += 1
    return made, skipped


def _load_workorders(seeds_dir, user, notices):
    from seizou.workorders import services
    from seizou.workorders.models import WorkOrder, WorkOrderStep

    path = seeds_dir / MORE_SEED_FILES["指示書"]
    if not path.is_file():
        return None
    qty_field = WorkOrder._meta.get_field("qty")
    planned_field = WorkOrderStep._meta.get_field("planned_qty")
    start_field = WorkOrderStep._meta.get_field("planned_start")
    end_field = WorkOrderStep._meta.get_field("planned_end")
    made = skipped = 0
    for number, entry in enumerate(_read_yaml(path, MORE_YAML_ROOT_KEYS["指示書"]), start=1):
        _check_entry_keys(path, number, entry, WORKORDER_KEYS, "指示書")
        raw_steps = entry.get("工程")
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError(f"{path.name} の {number} 件目の指示書に工程がありません。")
        for position, raw in enumerate(raw_steps, start=1):
            _check_entry_keys(path, f"{number} 件目の指示書の {position} 番目", raw, WORKORDER_STEP_KEYS, "工程")
        with _guard(path.name, f"{number} 件目の指示書"):
            line = _line_of_entry(entry)
            if WorkOrder.objects.filter(order_line=line).exclude(status=WorkOrder.STATUS_CANCELLED).exists():
                skipped += 1
                continue
            qty = _coerce(qty_field, entry.get("数量"))
            steps = []
            dates = []
            for raw in raw_steps:
                steps.append(
                    {
                        "process": _process_by_code(raw.get("工程コード")),
                        "planned_qty": _coerce(planned_field, raw.get("予定の数")),
                    }
                )
                dates.append((_coerce(start_field, raw.get("開始日")), _coerce(end_field, raw.get("終了日"))))
            workorder = services.issue_workorder(line, qty, steps, user)
            for step, (start, end) in zip(workorder.steps.order_by("seq"), dates):
                if start is not None or end is not None:
                    services.plan_step(step, start, end, user)
            made += 1
    return made, skipped


def _load_shipments(seeds_dir, user, notices):
    from seizou.shipping import services
    from seizou.shipping.models import Shipment

    path = seeds_dir / MORE_SEED_FILES["出荷"]
    if not path.is_file():
        return None
    qty_field = Shipment._meta.get_field("qty")
    date_field = Shipment._meta.get_field("shipped_at")
    note_field = Shipment._meta.get_field("note")
    # 段の始めに 1 度だけ読む。同じ回で同じ明細の 2 行目 (分納) を飛ばさないため。
    already = set(Shipment.objects.values_list("order_line_id", flat=True))
    placed = set()
    made = skipped = 0
    for number, entry in enumerate(_read_yaml(path, MORE_YAML_ROOT_KEYS["出荷"]), start=1):
        _check_entry_keys(path, number, entry, SHIPMENT_KEYS, "出荷")
        with _guard(path.name, f"{number} 件目の出荷"):
            line = _line_of_entry(entry)
            if line.pk in already:
                skipped += 1
                continue
            shipped_at = _coerce(date_field, entry.get("出荷日"))
            if (line.pk, shipped_at) in placed:
                raise ValueError(f"同じ明細に同じ日の出荷が 2 つあります (出荷日 {shipped_at})。納品書で指せないので、日をずらしてください。")
            placed.add((line.pk, shipped_at))
            qty = _coerce(qty_field, entry.get("数量"))
            note = _coerce(note_field, entry.get("備考"))
            # 作り物の種は相手に送らない (届いた注文の写しの受注の出荷も、出荷の知らせを送らず返事の欄に書く)
            services.ship(line, qty, shipped_at, user, note=note, send=False)
            made += 1
    return made, skipped


def _load_delivery_notes(seeds_dir, user, notices):
    from seizou.shipping import services
    from seizou.shipping.models import DeliveryNote

    path = seeds_dir / MORE_SEED_FILES["納品書"]
    if not path.is_file():
        return None
    made = skipped = 0
    for number, entry in enumerate(_read_yaml(path, MORE_YAML_ROOT_KEYS["納品書"]), start=1):
        _check_entry_keys(path, number, entry, DELIVERY_NOTE_KEYS, "納品書")
        with _guard(path.name, f"{number} 件目の納品書"):
            customer = _partner_by_code(entry.get("得意先の取引先コード"))
        shipments = _shipments_of_entry(path, number, entry, customer, "納品書")
        if any(s.delivery_note_id and s.delivery_note.status == DeliveryNote.STATUS_ISSUED for s in shipments):
            skipped += 1
            continue
        with _guard(path.name, f"{number} 件目の納品書"):
            services.create_delivery_note(customer, shipments, user)
            made += 1
    return made, skipped


def _load_invoices(seeds_dir, user, notices):
    from seizou.billing import services
    from seizou.billing.models import Invoice

    path = seeds_dir / MORE_SEED_FILES["請求書"]
    if not path.is_file():
        return None
    issued_field = Invoice._meta.get_field("issued_on")
    made = skipped = 0
    for number, entry in enumerate(_read_yaml(path, MORE_YAML_ROOT_KEYS["請求書"]), start=1):
        _check_entry_keys(path, number, entry, INVOICE_KEYS, "請求書")
        with _guard(path.name, f"{number} 件目の請求書"):
            customer = _partner_by_code(entry.get("得意先の取引先コード"))
            issued_on = _coerce(issued_field, entry.get("発行日"))
        shipments = _shipments_of_entry(path, number, entry, customer, "請求書")
        if any(s.invoice_lines.filter(invoice__status=Invoice.STATUS_ISSUED).exists() for s in shipments):
            skipped += 1
            continue
        with _guard(path.name, f"{number} 件目の請求書"):
            services.create_invoice(customer, shipments, user, issued_on=issued_on)
            made += 1
    return made, skipped


LOADERS = {
    "品目": _load_items,
    "取引先": _load_partners,
    "品番の対応": _load_partner_items,
    "工程": _load_processes,
    "受注": _load_orders,
    "つながり": _load_connections,
}

# 新しい 6 種類。種のファイル (図面は files/) が無ければ None を返し、要約は 0 件・出力の行も出さない。
MORE_LOADERS = {
    "標準の工程": _load_item_processes,
    "図面": _load_drawings,
    "指示書": _load_workorders,
    "出荷": _load_shipments,
    "納品書": _load_delivery_notes,
    "請求書": _load_invoices,
}


def _first_admin():
    return get_user_model().objects.filter(is_superuser=True).order_by("pk").first()


def _load_part_seed(app, seeds_dir, user):
    """部品の demo_seed.load(seeds_dir, user) を呼ぶ。部品が入っていないか、入れた物も飛ばした物も 0 なら None。"""
    if not apps.is_installed(app):
        return None
    marker = PART_SEED_MARKERS.get(app)
    if marker is not None and not (Path(seeds_dir) / marker).is_file():
        return None
    load_seed = import_module(f"{app}.demo_seed").load
    if "seeds_dir" in inspect.signature(load_seed).parameters:
        counts = load_seed(seeds_dir, user)
    else:
        # 種のフォルダを読まない部品 (権限の部署とアカウントは固定の種)。返す物が入れ子なら数を足す
        counts = load_seed(user)
    if "入れた" not in counts:
        counts = {
            "入れた": sum(c["入れた"] for c in counts.values()),
            "飛ばした": sum(c["飛ばした"] for c in counts.values()),
        }
    made, skipped = counts["入れた"], counts["飛ばした"]
    if made == 0 and skipped == 0:
        return None
    return made, skipped


def _load_part_seeds_after_orders(seeds_dir, user):
    """部品の demo_seed.load_after_orders(seeds_dir, user) を、受注の後・出荷の前に呼ぶ (届いた注文の写しの受注を、出荷と納品書の種が指せるようにするため)。

    返す物は 種類 → (入れた, 飛ばした) の辞書。口の無い部品と 0 件の部品は入れない。
    """
    found = {}
    for kind, app in PART_SEEDS:
        if not apps.is_installed(app):
            continue
        marker = PART_SEED_MARKERS.get(app)
        if marker is not None and not (Path(seeds_dir) / marker).is_file():
            continue
        load_early = getattr(import_module(f"{app}.demo_seed"), "load_after_orders", None)
        if load_early is None:
            continue
        counts = load_early(seeds_dir, user)
        made, skipped = counts["入れた"], counts["飛ばした"]
        if made == 0 and skipped == 0:
            continue
        found[kind] = (made, skipped)
    return found


def load(seeds_dir):
    """種のフォルダ (Path) を読んで入れる。種類ごとの {"入れた": n, "飛ばした": m} と、「注意」の文の並びを返す。"""
    seeds_dir = Path(seeds_dir)
    if not seeds_dir.is_dir():
        raise FileNotFoundError(
            f"種のフォルダ {seeds_dir} がありません。答えの YAML と同じ場所に <id>/ を置いてください。"
        )
    user = _first_admin()
    notices: list[str] = []
    summary: dict = {}
    read_more: list[str] = []
    early: dict = {}
    with transaction.atomic():
        for kind in KINDS:
            made, skipped = LOADERS[kind](seeds_dir, user, notices)
            summary[kind] = {"入れた": made, "飛ばした": skipped}
            if kind == "受注":
                early = _load_part_seeds_after_orders(seeds_dir, user)
        for kind in MORE_KINDS:
            result = MORE_LOADERS[kind](seeds_dir, user, notices)
            if result is not None:
                read_more.append(kind)
            made, skipped = result or (0, 0)
            summary[kind] = {"入れた": made, "飛ばした": skipped}
        for kind, app in PART_SEEDS:
            result = _load_part_seed(app, seeds_dir, user)
            if kind in early:
                # 受注の後の口の数は同じ種類に足す (出力の行は部品の種の段に 1 行)
                before = result or (0, 0)
                result = (before[0] + early[kind][0], before[1] + early[kind][1])
            if result is not None:
                read_more.append(kind)
            made, skipped = result or (0, 0)
            summary[kind] = {"入れた": made, "飛ばした": skipped}
    summary["注意"] = notices

    for kind in (*KINDS, *read_more):
        counts = summary[kind]
        print(f"{kind}: 入れた {counts['入れた']} 件・飛ばした {counts['飛ばした']} 件")
    for notice in notices:
        print(f"注意: {notice}")
    return summary
