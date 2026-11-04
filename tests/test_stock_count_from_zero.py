"""在庫を数えていない部品に実地 0 を入れたら、0 から数え始める (精査 A7 の 4 節の 4。決め 11-04 14:4x の (4)。依頼書 N105)。

数量 0 の動きは棚卸しの調整の行だけ (表の制約と services の _move)。数えてある部品で差が 0 は今までどおり断る。
右上の知らせは get_messages(response.wsgi_request) の (level_tag, 字) で見る。部品と得意先は作り物。
"""

import re
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.db import IntegrityError, transaction
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.masters.models import Item
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from seizou.stock import services
from seizou.stock.models import KIND_ADJUSTED, KIND_FINISHED, KIND_ISSUED, KIND_RECEIVED, StockMove


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
        masters_services.create_item({"part_no": "T-300", "name": "作り物の環", "unit": "個"}, user),
    ]


def _line(user, customer, item, qty="10"):
    order = orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}],
        user,
        order_date=timezone.localdate(),
    )
    return order.lines.get()


def _messages(response):
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def _counted_from(item):
    return services.annotate_balance(Item.objects.filter(pk=item.pk), "pk").get().stock_counted_from


def _row_tds(body, pk):
    """一覧の行 (<tr id="row-<pk>">) の <td> の HTML の並び。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return re.findall(r"<td[^>]*>.*?</td>", match.group(0), re.S)


@pytest.mark.django_db
def test_uncounted_item_starts_from_zero(user, logged_in, items):
    zero, five = items[0], items[1]
    today = timezone.localdate()
    url = reverse("stock:adjust_create")
    assert services.balance(zero) is None  # 動きが無い = 在庫を数えていない

    response = logged_in.post(url, {"item": zero.pk, "counted_qty": "0", "moved_on": today.isoformat(), "note": "棚卸し"})

    assert response.status_code == 302
    move = StockMove.objects.get(item=zero)
    assert move.kind == KIND_ADJUSTED and move.qty == Decimal("0") and move.moved_on == today
    assert services.balance(zero) == Decimal("0")
    assert _counted_from(zero) == today
    assert _messages(response) == [("success", "0 から数え始めました。")]

    # 0 でない実地は今までどおり (差 = 実地。知らせは「保存しました。」)
    response = logged_in.post(url, {"item": five.pk, "counted_qty": "5", "moved_on": today.isoformat(), "note": ""})
    assert response.status_code == 302
    assert StockMove.objects.get(item=five).qty == Decimal("5")
    assert _messages(response)[-1] == ("success", "保存しました。")  # 前の知らせは読み出していないので残る (最後の 1 つを見る)


@pytest.mark.django_db
def test_counted_item_same_qty_is_still_refused(user, logged_in, items):
    zero, five = items[0], items[1]
    today = timezone.localdate()
    services.adjust(zero, Decimal("0"), today, user)  # 0 から数え始めた部品
    services.adjust(five, Decimal("5"), today, user)
    count = StockMove.objects.count()

    with pytest.raises(ValueError, match="今の在庫数と同じです。調整は要りません。"):
        services.adjust(zero, Decimal("0"), today, user)
    with pytest.raises(ValueError, match="今の在庫数と同じです。調整は要りません。"):
        services.adjust(five, Decimal("5"), today, user)

    response = logged_in.post(
        reverse("stock:adjust_create"), {"item": zero.pk, "counted_qty": "0", "moved_on": today.isoformat(), "note": ""}
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "alert-error" in body and "調整は要りません" in body
    assert StockMove.objects.count() == count
    assert services.balance(zero) == Decimal("0") and services.balance(five) == Decimal("5")


@pytest.mark.django_db
def test_counted_from_zero_item_is_listed_with_zero(user, logged_in, customer, items):
    plain, shipped = items[0], items[1]
    today = timezone.localdate()
    yesterday = today.fromordinal(today.toordinal() - 1)
    line = _line(user, customer, shipped, qty="10")
    shipping_services.ship(line, Decimal("7"), yesterday, user)  # 数え始める日より前の出荷 (引かない)
    shipping_services.ship(line, Decimal("2"), today, user)  # 数え始める日と同じ日の出荷 (数える前に入れた物)
    url = reverse("stock:stock_list")
    body = logged_in.get(url).content.decode()
    assert f'<tr id="row-{plain.pk}"' not in body and f'<tr id="row-{shipped.pk}"' not in body  # 数えていない部品は並ばない

    services.adjust(plain, Decimal("0"), today, user)
    move = services.adjust(shipped, Decimal("0"), today, user)
    assert move.qty == Decimal("2")  # 同じ日の出荷を受け持つ行 (N80 の数え始めの形)
    assert move.saved_message == "0 から数え始めました。"

    response = logged_in.get(url)
    labels = [col.label for col in response.context["columns"]]
    index = labels.index("在庫数")
    rows = {row["obj"].pk: row for row in response.context["rows"]}
    body = response.content.decode()
    for item in (plain, shipped):
        assert Decimal(str(rows[item.pk]["cells"][index]["value"])) == Decimal("0")
        cell = _row_tds(body, item.pk)[index]
        assert "text-error" not in cell
    assert _counted_from(plain) == today and _counted_from(shipped) == today

    # 数え始めた後の出荷は引く
    shipping_services.ship(line, Decimal("1"), today, user)
    assert services.balance(shipped) == Decimal("-1")


@pytest.mark.django_db
def test_zero_qty_other_kinds_are_refused(user, items):
    item = items[0]
    today = timezone.localdate()
    for kind in (KIND_FINISHED, KIND_RECEIVED, KIND_ISSUED):
        with transaction.atomic(), pytest.raises(ValueError, match="数量が 0 の動きは残せません"):
            services._move(item, kind, Decimal("0"), today, user)
        with pytest.raises(IntegrityError), transaction.atomic():
            StockMove.objects.create(item=item, kind=kind, qty=Decimal("0"), moved_on=today)
    assert StockMove.objects.count() == 0

    # 棚卸しの調整の 0 は表が受ける (0 から数え始める行)
    with transaction.atomic():
        StockMove.objects.create(item=item, kind=KIND_ADJUSTED, qty=Decimal("0"), moved_on=today)
    assert StockMove.objects.filter(item=item, kind=KIND_ADJUSTED, qty=0).count() == 1
