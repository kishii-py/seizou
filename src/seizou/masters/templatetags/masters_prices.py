"""部品の詳細の「取引先」のタブの単価の行 (決め C19-1 の「取引先の行ごとに数量帯の単価を並べる」・PRICE-2)。読むだけで、表に書かない。

- item_price_rows: {% item_price_rows object tab.rows as price_rows %}。タブの行 (部品の取引先) ごとにその行の単価の行を並べた物と、
  共通の単価の行と、数量の字に添える単位の字を返す。{"rows": [{"obj", "url", "prices"}], "common": [単価の行], "qty_unit": 字}。
  画面の class は文脈を足す口を持たない (R15 で tab_for を上書きできない) ので、view でなく tag で組む (orders_detail の tag と同じ形)。
  単価の行は services.item_prices の 1 回の問い合わせを partner_item_id で分ける (タブの行の数だけ問い合わせを増やさない)。
  qty_unit を tag で組む訳: テンプレートからは関数に引数を渡せないため (単価の後ろの字は引数の要らない ItemPrice.unit_text を読む)。
- price_pick: {% price_pick object request.GET seizou.hide_money as pick %}。単価表から選ぶ小窓 (C100) の中身。GET の kind・partner・on・qty を読み、
  services.price_choices の行を 3 段に分けて返す。金額の閲覧が無い人には単価の行を引かない。
"""

from django import template

from .. import services
from ..models import PRICE_BASIS_UNIT, price_basis_text
import unicodedata
from datetime import date
from decimal import Decimal, InvalidOperation

from django.utils import timezone

from seizou.core import seeds
from seizou.core.templatetags.seizou_tags import comma, yen

from ..models import ItemPrice, Partner

register = template.Library()


@register.simple_tag
def item_price_rows(item, rows):
    by_partner_item, common = {}, []
    for price in services.item_prices(item):
        if price.partner_item_id is None:
            common.append(price)
        else:
            by_partner_item.setdefault(price.partner_item_id, []).append(price)
    return {
        "rows": [{"obj": row["obj"], "url": row["url"], "prices": by_partner_item.get(row["obj"].pk, [])} for row in rows],
        "common": common,
        "qty_unit": price_basis_text(PRICE_BASIS_UNIT, item.unit),
    }


# ---------------------------------------------------------------- 単価表から選ぶ小窓 (C100)

#: 金額の閲覧が無い人が小窓を開いたときの小窓の上の赤い帯の字 (単価の行は引かない)
PICK_HIDDEN_MESSAGE = "単価表から選べません。金額の閲覧の権限がありません。単価は明細の行に打って入れてください。"
#: 単価の種類 (売りか発注か) が読めないときの小窓の上の赤い帯の字
PICK_KIND_MESSAGE = "単価表を出せません。単価の種類 (売りか発注か) が読めません。明細の行の「単価表から選ぶ」から開き直してください。"
#: 段の見出し (期限の内の取引先の行・期限の内の共通の行・期限切れの行)
SECTION_PARTNER = "この取引先の単価"
SECTION_COMMON = "共通の単価 (どの取引先にも同じ)"
SECTION_EXPIRED = "見積の有効期限が切れた単価"
_KINDS = (ItemPrice.KIND_SALE, ItemPrice.KIND_PURCHASE)


def _pick_partner(raw):
    """GET の partner (取引先の pk の字) から取引先を引く。数の字でなければ None。"""
    raw = (raw or "").strip()
    return Partner.objects.filter(pk=int(raw)).first() if raw.isdigit() else None


def _pick_date(raw):
    """GET の on (YYYY-MM-DD) を日付に読む。空か読めなければ今日。"""
    try:
        return date.fromisoformat((raw or "").strip())
    except ValueError:
        return timezone.localdate()


def _pick_qty(raw):
    """GET の qty を数に読む。NFKC にそろえて「,」を外し、桁の中 (core.seeds.decimal_fits) の 0 より大きい数ならその値、ほかは None (受注の明細の数の欄と同じそろえ方)。

    訳: GET の値は誰でも書けるので、「1E+999999999」のような桁の外の数を数量の行 (comma で小数点の形の字にする) に出さない
    (字にすると 10 億桁になり、1 回の要求で記憶を使い切る)。出所 = Claude Security の走査の候補 11-07 の 30。
    変えてよい条件 = core.seeds.decimal_fits の桁を変える時 (ここは呼ぶだけ)。
    """
    text = unicodedata.normalize("NFKC", raw or "").replace(",", "").strip()
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if seeds.decimal_fits(value) and value > 0 else None


def _pick_row(choice, item, qty_unit, kind):
    """小窓の 1 行の字 (1 段目 = 単価と単価の後ろの字・2 段目 = 取引先・数量の下限・工程・適用開始日・見積・備考を「・」でつなぐ)。
    単価の後ろの字は小窓の部品で組む (単価の行の unit_text は行ごとに部品を読むため)。字に HTML を組まない (自動の escape を通す)。"""
    price = choice.price
    head = f"{yen(price.unit_price)} / {price_basis_text(price.price_basis, item.unit)}"
    parts = []
    if price.partner_item_id is None:
        parts.append("共通")
    else:
        partner = price.partner_item.partner
        whose = partner.short_name or partner.name
        if price.partner_item.partner_part_no:
            whose = f"{whose} (品番 {price.partner_item.partner_part_no})"
        parts.append(whose)
    parts.append("数量によらない" if price.min_qty is None else f"{comma(price.min_qty)} {qty_unit}から")
    if kind == ItemPrice.KIND_PURCHASE:
        parts.append(price.process.name if price.process_id else "工程によらない")
    if price.start_date:
        parts.append(f"{price.start_date:%Y-%m-%d} から")
    if price.quote_no or price.quote_date or price.quote_expires:
        quote = "見積" + (f" {price.quote_no}" if price.quote_no else "")
        dates = [text for text in (
            f"発行 {price.quote_date:%Y-%m-%d}" if price.quote_date else "",
            f"期限 {price.quote_expires:%Y-%m-%d}" if price.quote_expires else "",
        ) if text]
        if dates:
            quote += f" ({'・'.join(dates)})"
        parts.append(quote)
    if price.note:
        parts.append(price.note)
    return {"price": price, "fits": choice.fits, "expired": choice.expired, "head": head, "detail": "・".join(parts)}


@register.simple_tag
def price_pick(item, query, hide_money):
    """{% price_pick object request.GET seizou.hide_money as pick %}。選ぶ小窓の中身の辞書 (読むだけ)。

    金額の閲覧が無い人 (hide_money が真) には単価の行を引かず、notice (小窓の上の赤い帯) と空の sections だけを返す。
    ほかは notice (kind が読めないときだけ)・kind・kind_label・partner_label・partner・on_date・qty・qty_unit・sections を返す。
    sections は 3 段 (この取引先の単価・共通の単価・期限切れ) の空でない物で、1 段 = {"label", "rows"}。行の並びは services.price_choices。
    画面の class は文脈を足す口を持たない (R15 で get_context_data を上書きできない) ので、GET の値を読んで並べる仕事を tag で組む。
    """
    if bool(hide_money):
        return {"notice": {"message": PICK_HIDDEN_MESSAGE, "code": ""}, "sections": []}
    kind = query.get("kind")
    kind = kind if kind in _KINDS else None
    partner = _pick_partner(query.get("partner"))
    on_date = _pick_date(query.get("on"))
    qty = _pick_qty(query.get("qty"))
    qty_unit = price_basis_text(PRICE_BASIS_UNIT, item.unit)
    result = {
        "notice": None if kind else {"message": PICK_KIND_MESSAGE, "code": ""},
        "kind": kind,
        "kind_label": dict(ItemPrice.KIND_CHOICES).get(kind, ""),
        "partner_label": "取引先" if kind == ItemPrice.KIND_PURCHASE else "得意先",
        "partner": partner,
        "on_date": on_date,
        "qty": qty,
        "qty_unit": qty_unit,
        "sections": [],
    }
    if kind is None:
        return result
    groups = {SECTION_PARTNER: [], SECTION_COMMON: [], SECTION_EXPIRED: []}
    for choice in services.price_choices(item, kind=kind, partner=partner, on_date=on_date, qty=qty):
        if choice.expired:
            label = SECTION_EXPIRED
        elif choice.price.partner_item_id is None:
            label = SECTION_COMMON
        else:
            label = SECTION_PARTNER
        groups[label].append(_pick_row(choice, item, qty_unit, kind))
    result["sections"] = [{"label": label, "rows": rows} for label, rows in groups.items() if rows]
    return result
