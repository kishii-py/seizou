"""詳細の「関係する物」のタブの行が、詳細の画面を持たない明細のとき、親の記録の詳細へ移る link になる事を見る (本体の直し 11-12)。

訳: 指示書の詳細の「協力企業」のタブには、協力企業への発注の明細が並ぶ。発注の明細には詳細の画面が無いので、行は字だけで、
そこから発注の詳細へ移れなかった。詳細の基本の表の値は、明細なら親の記録 (model の detail_link_target) の詳細へ移る link にしてある
(土台の record_url。作者の決め)。タブの行も、自分の詳細の画面が無い行だけ、同じ決め方で親の詳細への link にする。
部署でその部品を開けない人には link を出さない (record_url と同じ判定)。
作り物の協力企業と部品だけを使う。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from tests.test_purchasing_subcontract import build_workorder, subcontract_po

PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}


def _tab(body: str) -> str:
    """指示書の詳細の本文のうち、「協力企業」のタブの中身 (次のタブの前まで)。"""
    return body.split('aria-label="協力企業"', 1)[1].split('name="detail-tabs"', 1)[0]


@pytest.mark.django_db
def test_subcontract_tab_row_links_to_the_purchase_order(logged_in, user):
    wo = build_workorder(user)
    po, _line = subcontract_po(user, wo.subcontractor, wo.step)

    body = logged_in.get(reverse("workorders:workorder_detail", args=[wo.workorder.pk])).content.decode()

    text = f"{po.po_no}-1 {wo.item.part_no} {wo.item.name}"
    assert f'href="{reverse("purchasing:purchaseorder_detail", args=[po.pk])}"><span class="truncate">{text}</span>' in _tab(body)


@pytest.mark.django_db
def test_row_stays_plain_for_people_who_cannot_open_the_part(settings, client, user):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    wo = build_workorder(user)
    po, _line = subcontract_po(user, wo.subcontractor, wo.step)
    making = accounts_services.create_department("作り物の製造の部署", ["workorders"], [], None)
    accounts_services.issue_account("seizo", PASSWORD, [making], [], {}, None)
    client.force_login(get_user_model().objects.get(username="seizo"))

    response = client.get(reverse("workorders:workorder_detail", args=[wo.workorder.pk]))

    assert response.status_code == 200
    tab = _tab(response.content.decode())
    assert f"{po.po_no}-1" in tab  # 行の字は出る
    assert reverse("purchasing:purchaseorder_detail", args=[po.pk]) not in tab  # 発注の部品を開けない人には link にしない
