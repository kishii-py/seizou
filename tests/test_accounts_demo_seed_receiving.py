"""作り物の会社の部署「製造」と「受注と出荷」に、入荷の画面を入れる (本人の決め 11-13 07:3x。526 回目の本体の直し)。

訳: 作り物の会社 (seizou new --demo) の 3 人 (現場 genba・事務 jimu・経理 keiri) の部署には、入荷の部品が入っていなかった。
入荷が無いと指示書と出荷を止める設定の会社 (例の B 社) では、3 人では受注を先へ進められず、受注の詳細の緑の「入荷を記録する」
(517 回目) も管理者にしか出なかった。入荷の記録は書く権限の要らない現場の記録なので、部署で開けるかだけで決まる。
現場は左のメニューから、事務は受注の詳細の緑のボタンから入荷を記録できるようにする。経理は今のまま (開けない)。
取引先と部品は作り物。データは services で作る。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import demo_seed
from seizou.accounts.models import Department
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.test_detail_header import _actions

User = get_user_model()
ALL_ON = {"department": "on", "write": "on", "money": "on"}
RECEIVE = "入荷を記録する"


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin-tester", password="pass-for-tests-only")


@pytest.fixture
def seeded(admin):
    demo_seed.load(admin)
    return {name: User.objects.get(username=name) for name in ("genba", "jimu", "keiri")}


@pytest.mark.django_db
def test_demo_departments_open_receiving(seeded):
    apps = {row.name: row.apps for row in Department.objects.all()}
    assert "receiving" in apps["製造"]
    assert "receiving" in apps["受注と出荷"]
    assert "receiving" not in apps["経理"]
    assert "receiving" not in apps["総務"]


@pytest.mark.django_db
def test_demo_people_can_record_receiving_where_it_is_required(settings, client, admin, seeded):
    """入荷が無いと止める設定の会社で、事務には受注の詳細の緑の「入荷を記録する」が出て、現場は入荷の入力を開ける。経理は開けない。"""
    settings.SEIZOU = {"access_control": dict(ALL_ON), "receiving_required": "stop"}
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, admin)
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, admin)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}], admin, order_date=date(2026, 11, 13)
    )
    line = order.lines.get()
    detail = reverse("orders:order_detail", args=[order.pk])
    new_receiving = reverse("receiving:receiving_create")

    client.force_login(seeded["jimu"])
    body = client.get(detail).content.decode()
    assert f'href="{new_receiving}?order_line={line.pk}"' in _actions(body) and RECEIVE in _actions(body)
    assert client.get(f"{new_receiving}?order_line={line.pk}").status_code == 200

    client.force_login(seeded["genba"])
    assert client.get(new_receiving).status_code == 200
    assert client.get(reverse("receiving:receiving_list")).status_code == 200

    client.force_login(seeded["keiri"])
    assert client.get(new_receiving).status_code == 403
    assert RECEIVE not in _actions(client.get(detail).content.decode())
