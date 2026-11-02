"""masters の公開する関数 (01 の 10.4)。ほかの部品と画面はこの関数だけを通してマスターを書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は受けるが、None 以外が来たら NotImplementedError を投げる。
  黙って捨てると会社の項目が消えるため。保存は core の fields ができてから足す。
- masters の決まり (品番の一意・役の 3 値・取引先 × 取引先の品番の一意) に当たったら、
  日本語の文の ValueError を投げる。中心の決まり (RULES.md の番号) は masters に無い。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
"""

from __future__ import annotations

from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal

from django.db import transaction

from seizou.core.models import Company

from .models import Item, Partner, PartnerItem

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
_ROLES = frozenset(value for value, _label in Partner.ROLE_CHOICES)
_ROUNDING_MODES = {
    "round": ROUND_HALF_UP,
    "ceil": ROUND_CEILING,
    "floor": ROUND_FLOOR,
}


def _refuse_ext(ext):
    if ext is not None:
        raise NotImplementedError("会社ごとの項目の保存は core の fields ができてから")


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
            f"役 {role!r} は使えません。"
            "得意先 (customer)・外注先 (subcontractor)・両方 (both) のどれかにしてください。"
        )


def create_item(data, user, *, ext=None):
    """品目を作る。data は Item の列の名前を鍵にした辞書 (part_no と name は必須)。"""
    _refuse_ext(ext)
    _refuse_unknown_keys(data, _ITEM_KEYS, "品目")
    with transaction.atomic():
        _refuse_duplicate_part_no(data["part_no"])
        return Item.objects.create(**data)


def update_item(item, data, user, *, ext=None):
    """data にある鍵だけを書き換える。品番を変えるときも一意を見る。"""
    _refuse_ext(ext)
    _refuse_unknown_keys(data, _ITEM_KEYS, "品目")
    with transaction.atomic():
        if "part_no" in data:
            _refuse_duplicate_part_no(data["part_no"], exclude_pk=item.pk)
        for key, value in data.items():
            setattr(item, key, value)
        item.save()
        return item


def create_partner(data, user, *, ext=None):
    """取引先を作る。役は customer・subcontractor・both のどれか。取引先コードは一意。"""
    _refuse_ext(ext)
    _refuse_unknown_keys(data, _PARTNER_KEYS, "取引先")
    _refuse_unknown_role(data.get("role"))
    with transaction.atomic():
        _refuse_duplicate_partner_code(data["code"])
        return Partner.objects.create(**data)


def update_partner(partner, data, user, *, ext=None):
    """data にある鍵だけを書き換える。役と取引先コードは create_partner と同じ決まりで見る。"""
    _refuse_ext(ext)
    _refuse_unknown_keys(data, _PARTNER_KEYS, "取引先")
    if "role" in data:
        _refuse_unknown_role(data["role"])
    with transaction.atomic():
        if "code" in data:
            _refuse_duplicate_partner_code(data["code"], exclude_pk=partner.pk)
        for key, value in data.items():
            setattr(partner, key, value)
        partner.save()
        return partner


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
