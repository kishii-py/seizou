# 読み取り (Extraction) に、探すための注文日と金額の列を足し、今ある行を読み取った中身 (extracted) から埋める (依頼書 N367 の 2。本人の決め 11-08 00:37)。
#
# 埋め方は intake.services._order_date_and_amount のこの時の形を写した関数 (services を import しない = 後で services を直しても、この migration は変わらない)。
# 注文日 = 読み取った注文日の字 (YYYY-MM-DD)。金額 = 明細ごとの「読み取った数量の字 × 読み取った単価の字」の合計で、単価が 1 つでも空なら空。
# 合計が列に入らないとき (小数 3 位以下が残る・整数の部分が 12 桁を超える) は丸めずに空。字が読めない行は空のまま (例外にしない)。
# 生の SQL は書かない (R14)。逆に戻すときは何もしない (列ごと消える)。

import re
from datetime import date
from decimal import MAX_PREC, Decimal, InvalidOperation, localcontext

from django.db import migrations, models

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_AMOUNT_INT_DIGITS = 12
_AMOUNT_PLACES = 2


def _read_date(value):
    """注文日の字 (YYYY-MM-DD) の日付。字でないか読めなければ None。"""
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value.strip()):
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _read_number(value):
    """数の字か整数の Decimal。空・真偽・読めない字・有限でない数は None。"""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        number = Decimal(str(value).strip())
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def order_date_and_amount(extracted):
    """読み取った中身から (注文日, 金額)。形が違う所は None。"""
    if not isinstance(extracted, dict):
        return None, None
    order_date = _read_date(extracted.get("order_date"))
    lines = extracted.get("lines")
    if not isinstance(lines, list) or not lines:
        return order_date, None
    pairs = []
    for line in lines:
        if not isinstance(line, dict):
            return order_date, None
        qty = _read_number(line.get("qty"))
        unit_price = _read_number(line.get("unit_price"))
        if qty is None or unit_price is None:
            return order_date, None
        pairs.append((qty, unit_price))
    try:
        with localcontext() as context:
            context.prec = MAX_PREC
            total = sum((qty * unit_price for qty, unit_price in pairs), Decimal("0"))
            reduced = total.normalize()
    except ArithmeticError:
        # 桁の外の数 (検査を足す前に残した字) の掛け算は指数が溢れる。読めない行と同じに空にする
        return order_date, None
    if reduced.adjusted() >= _AMOUNT_INT_DIGITS or reduced.as_tuple().exponent < -_AMOUNT_PLACES:
        return order_date, None
    return order_date, total


def fill_order_date_and_amount(apps, schema_editor):
    """今ある読み取りの注文日と金額を、読み取った中身から埋める (埋める値のある行だけ、1 行ずつ update)。"""
    Extraction = apps.get_model("intake", "Extraction")
    for pk, extracted in Extraction.objects.order_by("pk").values_list("pk", "extracted").iterator():
        order_date, amount = order_date_and_amount(extracted)
        if order_date is None and amount is None:
            continue
        Extraction.objects.filter(pk=pk).update(order_date=order_date, amount=amount)


class Migration(migrations.Migration):

    dependencies = [
        ('intake', '0003_wording_received_orders'),
    ]

    operations = [
        migrations.AddField(
            model_name='extraction',
            name='amount',
            field=models.DecimalField(blank=True, decimal_places=2, editable=False, max_digits=14, null=True, verbose_name='金額'),
        ),
        migrations.AddField(
            model_name='extraction',
            name='order_date',
            field=models.DateField(blank=True, editable=False, null=True, verbose_name='注文日'),
        ),
        migrations.RunPython(fill_order_date_and_amount, migrations.RunPython.noop),
    ]
