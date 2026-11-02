"""masters の公開する関数 (01 の 10.4)。ほかの部品と画面はこの関数だけを通してマスターを書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は受けるが、None 以外が来たら NotImplementedError を投げる。
  黙って捨てると会社の項目が消えるため。保存は core の fields ができてから足す。
- masters の決まり (品番の一意・区分の 3 値・取引先 × 取引先の品番の一意) に当たったら、
  日本語の文の ValueError を投げる。中心の決まり (RULES.md の番号) は masters に無い。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
"""

from __future__ import annotations

import unicodedata
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from django.db import transaction

from seizou.core.models import Company

from .models import Item, Partner, PartnerItem
from .models import Process

_ITEM_KEYS = frozenset(
    {"part_no", "name", "drawing_no", "drawing", "unit", "standard_price", "note"}
)
_PARTNER_KEYS = frozenset(
    {
        "code",
        "name",
        "short_name",
        "role",
        "closing_day",
        "payment_day",
        "rounding",
        "tax_mode",
        "note",
    }
)
_PROCESS_KEYS = frozenset({"code", "name", "is_outsourced", "sort_order"})
_ROLES = frozenset(value for value, _label in Partner.ROLE_CHOICES)
_ROUNDING_MODES = {
    "round": ROUND_HALF_UP,
    "ceil": ROUND_CEILING,
    "floor": ROUND_FLOOR,
}


def _save_ext(obj, ext):
    """会社ごとの項目 (ext) を拡張の表に書く。登録が無ければ core の fields が ValueError を投げる (黙って捨てない)。"""
    if ext is not None:
        from seizou.core import fields

        fields.save_ext(obj, ext)


def _refuse_unknown_keys(data, allowed, table_name):
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(
            f"知らない項目 ({'・'.join(unknown)}) があります。"
            f"{table_name}の列の名前だけを渡してください。"
        )


def _refuse_duplicate_part_no(part_no, *, exclude_pk=None):
    others = Item.objects.filter(part_no=part_no)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        raise ValueError(f"品番 {part_no} は既にあります。別の品番にしてください。")


def _refuse_duplicate_partner_code(code, *, exclude_pk=None):
    others = Partner.objects.filter(code=code)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        raise ValueError(f"取引先コード {code} は既にあります。別のコードにしてください。")


def _refuse_unknown_role(role):
    if role not in _ROLES:
        raise ValueError(
            f"区分 {role!r} は使えません。"
            "得意先 (customer)・外注先 (subcontractor)・両方 (both) のどれかにしてください。"
        )


def _refuse_duplicate_process_code(code):
    if Process.objects.filter(code=code).exists():
        raise ValueError(f"工程コード {code} は既にあります。別のコードにしてください。")


def create_item(data, user, *, ext=None):
    """品目を作る。data は Item の列の名前を鍵にした辞書 (part_no と name は必須)。"""
    _refuse_unknown_keys(data, _ITEM_KEYS, "品目")
    with transaction.atomic():
        _refuse_duplicate_part_no(data["part_no"])
        item = Item.objects.create(**data)
        _save_ext(item, ext)
        return item


def update_item(item, data, user, *, ext=None):
    """data にある鍵だけを書き換える。品番を変えるときも一意を見る。"""
    _refuse_unknown_keys(data, _ITEM_KEYS, "品目")
    with transaction.atomic():
        if "part_no" in data:
            _refuse_duplicate_part_no(data["part_no"], exclude_pk=item.pk)
        for key, value in data.items():
            setattr(item, key, value)
        item.save()
        _save_ext(item, ext)
        return item


def create_partner(data, user, *, ext=None):
    """取引先を作る。区分は customer・subcontractor・both のどれか。取引先コードは一意。"""
    _refuse_unknown_keys(data, _PARTNER_KEYS, "取引先")
    _refuse_unknown_role(data.get("role"))
    with transaction.atomic():
        _refuse_duplicate_partner_code(data["code"])
        partner = Partner.objects.create(**data)
        _save_ext(partner, ext)
        return partner


def update_partner(partner, data, user, *, ext=None):
    """data にある鍵だけを書き換える。区分と取引先コードは create_partner と同じ決まりで見る。"""
    _refuse_unknown_keys(data, _PARTNER_KEYS, "取引先")
    if "role" in data:
        _refuse_unknown_role(data["role"])
    with transaction.atomic():
        if "code" in data:
            _refuse_duplicate_partner_code(data["code"], exclude_pk=partner.pk)
        for key, value in data.items():
            setattr(partner, key, value)
        partner.save()
        _save_ext(partner, ext)
        return partner


def create_process(data, user):
    """工程を作る。工程コードは一意。data の鍵は code・name・is_outsourced・sort_order。"""
    _refuse_unknown_keys(data, _PROCESS_KEYS, "工程")
    with transaction.atomic():
        _refuse_duplicate_process_code(data["code"])
        return Process.objects.create(**data)


def map_partner_item(partner, item, partner_part_no, user, *, partner_item_name=""):
    """取引先の品番を自社の品目に対応させる。

    同じ (取引先, 取引先の品番) が既にあれば、同じ品目を指していても ValueError。
    呼ぶ側は先に resolve_partner_item で引き、無いときだけ呼ぶ。
    """
    with transaction.atomic():
        existing = (
            PartnerItem.objects.select_related("item")
            .filter(partner=partner, partner_part_no=partner_part_no)
            .first()
        )
        if existing is not None:
            raise ValueError(
                f"取引先 {partner} の品番 {partner_part_no} は、既に {existing.item} に対応しています。"
                "今の対応を使うか、別の品番にしてください。"
            )
        return PartnerItem.objects.create(
            partner=partner,
            item=item,
            partner_part_no=partner_part_no,
            partner_item_name=partner_item_name,
        )


def resolve_partner_item(partner, partner_part_no):
    """取引先の品番から自社の品目を引く。無ければ None。"""
    mapping = (
        PartnerItem.objects.select_related("item")
        .filter(partner=partner, partner_part_no=partner_part_no)
        .first()
    )
    return mapping.item if mapping is not None else None


def round_amount(partner, value):
    """金額 (数量 × 単価) の端数を 1 円の単位に丸める 1 か所の関数。

    方法は取引先の小計の計算方法。空なら会社の既定 (Company の 1 行の rounding。行が無ければ round)。
    round = 四捨五入、ceil = 切上 (ROUND_CEILING)、floor = 切捨 (ROUND_FLOOR)。
    """
    mode = partner.rounding or _company_rounding()
    return value.quantize(Decimal("1"), rounding=_ROUNDING_MODES[mode])


def _company_rounding():
    # core の services.company() ができたら、それに替える。
    company = Company.objects.first()
    return company.rounding if company is not None else "round"


# カタカナ (ァ〜ヶ) をひらがなに写す表。
_KATAKANA_TO_HIRAGANA = {code: code - 0x60 for code in range(0x30A1, 0x30F7)}


def _fold(text):
    """照合のために字を畳む。全角と半角、カタカナとひらがな、大文字と小文字を同じにする。"""
    text = unicodedata.normalize("NFKC", text)
    return text.translate(_KATAKANA_TO_HIRAGANA).casefold()


def _page_number(page):
    try:
        number = int(page)
    except (TypeError, ValueError):
        return 1
    return max(number, 1)


def candidates(kind, query, page, *, per_page=30):
    """選ぶ欄の候補を 1 ページ返す (05 の 2 節)。

    kind は "item" (表示の字は「品番 品名」) か "partner" (「コード 名前」)。
    表示の字と query を畳み、query を空白で切った語の全部を含む行を出す。空の query は全件。
    page が整数に読めなければ 1。母集団は数千件の前提で、全件を読んでから Python で絞る。
    """
    if kind == "item":
        rows = [
            (pk, f"{part_no} {name}")
            for pk, part_no, name in Item.objects.values_list("pk", "part_no", "name")
        ]
    elif kind == "partner":
        rows = [
            (pk, f"{code} {name}")
            for pk, code, name in Partner.objects.values_list("pk", "code", "name")
        ]
    else:
        raise ValueError(f"候補の種類 {kind!r} は使えません。item か partner にしてください。")

    terms = _fold(query or "").split()
    hits = [(pk, text) for pk, text in rows if all(term in _fold(text) for term in terms)]
    start = (_page_number(page) - 1) * per_page
    return {
        "results": [{"id": pk, "text": text} for pk, text in hits[start : start + per_page]],
        "pagination": {"more": start + per_page < len(hits)},
    }


_COPY_LABELS = {"unit": "単位", "unit_price": "単価"}


def _is_blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def _same_value(now, master_value):
    """今の欄の値とマスタの値を、マスタと同じ型にそろえて比べる。"""
    if _is_blank(now):
        return False
    if isinstance(master_value, Decimal):
        try:
            return Decimal(str(now).strip()) == master_value
        except InvalidOperation:
            return False
    return str(now).strip() == master_value


def _values_to_copy(source):
    if isinstance(source, Item):
        return {"unit": source.unit, "unit_price": source.standard_price}
    # 取引先から受注の欄に写す物は無い。
    return {}


def copy_values(source, current, copied_marks):
    """マスタの値を欄へ写す 4 つの規則 (05 の 2 節) を 1 か所で当てる。

    source は Item (単位と標準単価を、欄の unit と unit_price に写す) か Partner (写す物は無い)。
    current は今の欄の値の辞書、copied_marks は前に写した欄の名前の集合。
    規則: マスタに値が無ければ何もしない / 今の値と同じなら知らせない /
    欄が空か前に写した値なら写して「写した」/ 人が入れた値なら残して「残した」。
    返す物は (写す値の辞書, 知らせの文の一覧)。
    """
    values = {}
    copied = []
    kept = []
    for field, master_value in _values_to_copy(source).items():
        if _is_blank(master_value):
            continue
        now = current.get(field)
        if _same_value(now, master_value):
            continue
        if _is_blank(now) or field in copied_marks:
            values[field] = master_value
            copied.append(_COPY_LABELS[field])
        else:
            kept.append(_COPY_LABELS[field])

    messages = []
    if copied:
        messages.append(f"{'と'.join(copied)}を品目から写しました。")
    if kept:
        messages.append(f"{'と'.join(kept)}は入力のまま残しました。")
    return values, messages
