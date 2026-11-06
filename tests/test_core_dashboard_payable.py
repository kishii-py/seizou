"""ダッシュボードの「今月の支払の見込み」のカード (core.dashboard.payable_this_month と purchasing の tag)。

Django の test client で HTML を見る。発注はつながりの無い協力企業で作り、HTTP は呼ばない (呼んだら落とす)。
日は timezone.localdate() から数える (関数の試験は固定の日を渡す)。名前は作り物だけ。
"""

import re
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.core import dashboard
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("つながりが無いのに HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def partners(user):
    return [
        masters_services.create_partner(
            {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "rounding": "round"}, user
        ),
        masters_services.create_partner(
            {"code": "S002", "name": "作り物の別の協力企業", "role": "subcontractor", "rounding": "round"}, user
        ),
    ]


@pytest.fixture
def shaft(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def home(client):
    response = client.get(reverse("core:home"))
    assert response.status_code == 200
    return response.content.decode()


#: 数字のカードの題の <p> の開き (core/home.html)
TITLE_HEAD = '<p class="text-xs font-medium text-base-content/70">'


def card_of(body, label):
    """題が label の数字のカードの HTML (題の <p> から次のカードの題の手前まで。tests/test_core_stat_help.py の cards() と同じ切り方)。

    カードの外側は div で、行き先の link は点線の行の後ろにあるので、点線の行の閉じ (</p></div>) では切らない (候補 C30 の 7)。
    """
    start = body.index(f"{TITLE_HEAD}{label}</p>")
    end = body.find(TITLE_HEAD, start + len(TITLE_HEAD))
    return body[start : end if end != -1 else body.index('data-tour="flow-guide"', start)]


class _NoParts:
    def get_model(self, app_label, name):
        raise LookupError(f"{app_label}.{name}")


def _line(partner, item, user, unit_price):
    order = purchasing_services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("100"), "unit_price": Decimal(unit_price)}],
        user,
        order_date=date(2026, 10, 1),
    )
    return order.lines.get()


@pytest.mark.django_db
def test_payable_this_month_sums_receipts(user, partners, shaft, no_http, monkeypatch):
    today = date(2026, 11, 15)
    assert dashboard.payable_this_month(today=today) == Decimal("0")

    first = _line(partners[0], shaft, user, "45")
    second = _line(partners[1], shaft, user, "60")
    purchasing_services.receive(first, Decimal("10"), date(2026, 11, 1), user)  # 450
    purchasing_services.receive(second, Decimal("40"), date(2026, 11, 30), user)  # 2400
    purchasing_services.receive(first, Decimal("200"), date(2026, 10, 31), user)  # 先月 = 入らない

    assert dashboard.payable_this_month(today=today) == Decimal("2850")
    assert dashboard.payable_this_month(today=date(2026, 10, 2)) == Decimal("9000")

    monkeypatch.setattr(dashboard, "apps", _NoParts())
    assert dashboard.payable_this_month(today=today) is None


@pytest.mark.django_db
def test_home_shows_payable_card(user, logged_in, partners, shaft, no_http):
    line = _line(partners[0], shaft, user, "60")
    purchasing_services.receive(line, Decimal("40"), timezone.localdate(), user)

    body = home(logged_in)

    start = body.index("今月の支払の見込み")
    card = card_of(body, "今月の支払の見込み")
    assert "¥2,400" in card and "円" not in card
    # 行き先の link はカードの中 (点線の下の「<画面の名前>を見る」)
    assert re.search(rf'<a [^>]*href="{re.escape(reverse("purchasing:receipt_list"))}"', card)
    assert f"{timezone.localdate().month} 月に受け入れた数量" in card
    # 6 枚目のカードとして数字のカードの並びに入る (入金のカードが 7 枚目に続くので、試験の settings では xl 以上で 4 列 × 2 段)
    assert body.index("受信した注文") < start
    assert "xl:grid-cols-4" in body
    for word in ("品目", "注残の明細", "はじめに"):
        assert word not in body, word


@pytest.mark.django_db
def test_home_hides_amount_without_money(user, logged_in, partners, shaft, no_http, settings):
    line = _line(partners[0], shaft, user, "60")
    purchasing_services.receive(line, Decimal("40"), timezone.localdate() - timedelta(days=0), user)
    settings.SEIZOU = {"access_control": {"department": "off", "write": "off", "money": "on"}}

    body = home(logged_in)

    # 金額のカードはカードごと出さない (並びから外す。決め 11-05 09:3x と C11-1 の a)。
    assert "今月の支払の見込み" not in body
    assert "2,400" not in body
