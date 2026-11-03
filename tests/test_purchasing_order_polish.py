"""発注の詳細の磨き (精査 A1 の不具合 10・想定外 S2・S6・S9、精査 A3 の不具合 5)。

- 出荷の知らせの合計は、行と同じく末尾の 0 を落とした字。
Django の test client で HTML を見る。取引先と品目は作り物。相手への HTTP は requests.post を偽の物に差し替える。
"""

from datetime import date

import pytest
from django.contrib.auth import get_user_model

from seizou.masters import services as masters_services
from tests.test_purchasing_shipping_notice import FakePost as NoHttpPost
from tests.test_purchasing_shipping_notice import _detail, _notice, _notice_cell, _order


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def no_http(monkeypatch):
    """つながりの無い協力企業への発注で、HTTP を呼んだら落とす。"""
    fake = NoHttpPost()
    monkeypatch.setattr("requests.post", fake)
    return fake


@pytest.mark.django_db
def test_notice_total_drops_trailing_zeros(client_logged_in, user, subcontractor, item, no_http):
    whole = _order(subcontractor, item, user)
    _notice(whole.lines.get(), "4.00", date(2026, 11, 5), "SH-0001")
    _notice(whole.lines.get(), "6.00", date(2026, 11, 6), "SH-0002")
    half = _order(subcontractor, item, user)
    _notice(half.lines.get(), "2.50", date(2026, 11, 5), "SH-0003")
    _notice(half.lines.get(), "4", date(2026, 11, 6), "SH-0004")

    whole_cell = _notice_cell(_detail(client_logged_in, whole))
    half_cell = _notice_cell(_detail(client_logged_in, half))

    assert "合計 10<" in whole_cell
    assert "合計 10.00" not in whole_cell
    assert "合計 6.5<" in half_cell
    assert "合計 6.50" not in half_cell
    assert no_http.calls == 0
