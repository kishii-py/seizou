"""業務の流れの段の「仕事をする部品」(段の needs) と、案内の小窓の前後の画面の link (504 回目の本体の直し)。

訳: N406c が足した「協力企業に出す」の段は、指示書の一覧へ移るが、仕事 (工程を発注に出す) は発注の部品でする。
移る先の部品だけで段を出すと、指示書だけを開ける現場の人のダッシュボードに、進められない段 1 つだけの「はじめての方へ」の枠が出た。
案内の小窓の「前の段」「次の段」の link も、部署を見ずに出していたので、開けない画面への link が出た。
段に needs (仕事をする部品) を書き、枠と案内の link の両方で、移る先の部品と needs の両方を開ける人にだけ段を出す。
出所 = 作者の決め (部署で開けない段は外し、何も残らなければ枠ごと出さない) と、N406c の記録の「本体の判断が要る事」。
名前は作り物だけ。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import dashboard

ALL_ON = {"department": "on", "write": "on", "money": "on"}
PASSWORD = "kumo-tests-2611"
HTMX = {"HTTP_HX_REQUEST": "true"}
PURCHASE_LABEL = "発注・注文回答・出荷の知らせ・渡す"


@pytest.fixture
def people(settings, db):
    """生産管理 (指示書と発注)・購買 (発注だけ)・製造 (指示書だけ) の 3 人。部署の判定は on。"""
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    both = accounts_services.create_department("作り物の生産管理の部署", ["workorders", "purchasing"], [], None)
    buying = accounts_services.create_department("作り物の購買の部署", ["purchasing"], [], None)
    making = accounts_services.create_department("作り物の製造の部署", ["workorders"], [], None)
    accounts_services.issue_account("seisan", PASSWORD, [both], ["slip_edit"], {"see_money": True}, None)
    accounts_services.issue_account("kobai", PASSWORD, [buying], ["slip_edit"], {"see_money": True}, None)
    accounts_services.issue_account("seizo", PASSWORD, [making], ["slip_edit"], {"see_money": True}, None)
    users = get_user_model().objects
    return {name: users.get(username=name) for name in ("seisan", "kobai", "seizo")}


def _step_keys(guide, flow_key):
    if guide is None:
        return None
    flow = next((flow for flow in guide["flows"] if flow["key"] == flow_key), None)
    return None if flow is None else [step["key"] for step in flow["steps"]]


def test_only_the_subcontract_step_names_the_part_of_its_work():
    """needs を書いた段は「協力企業に出す」だけ (今ある段の出方を変えない)。"""
    named = [(step["key"], step["needs"]) for flow in dashboard.FLOWS for step in flow["steps"] if step.get("needs")]
    assert named == [("subcontract", "purchasing")]


def test_guide_frame_keeps_the_step_only_for_people_who_can_do_its_work(people):
    guide = dashboard.flow_guide()
    assert _step_keys(guide, "purchase")[0] == "subcontract"  # 人を渡さない形 (全部の段) には在る

    # 指示書と発注の両方を開ける人には、段が出る
    assert _step_keys(dashboard._open_guide(guide, people["seisan"]), "purchase")[0] == "subcontract"
    # 発注だけを開ける人には、指示書の一覧へ移る段は出ない (前からの形。移る先を開けない)
    assert "subcontract" not in _step_keys(dashboard._open_guide(guide, people["kobai"]), "purchase")
    # 指示書だけを開ける人には、仕事をする発注の部品を開けないので出ない。開ける段が 1 つも無く、枠ごと出ない
    assert dashboard._open_guide(guide, people["seizo"]) is None
    assert dashboard.today_tasks(people["seizo"]) is None


def test_flow_neighbors_follow_what_the_person_can_open(people):
    workorders, purchase_orders = "workorders:workorder_list", "purchasing:purchaseorder_list"
    to_purchase = {"label": "発注", "url": reverse(purchase_orders)}
    to_workorders = {"label": "協力企業に出す", "url": reverse(workorders)}

    # 人を渡さない形は、今までどおり部署を見ない
    assert dashboard.flow_neighbors(workorders)["next"] == to_purchase
    assert dashboard.flow_neighbors(purchase_orders)["prev"] == to_workorders

    # 両方を開ける人: 前後の link が出る
    assert dashboard.flow_neighbors(workorders, people["seisan"]) == {
        "flow": "発注から検収まで",
        "label": "協力企業に出す",
        "prev": None,
        "next": to_purchase,
    }
    both = dashboard.flow_neighbors(purchase_orders, people["seisan"])
    assert (both["label"], both["prev"]) == (PURCHASE_LABEL, to_workorders)

    # 発注だけを開ける人: 開けない指示書の一覧への link は出ない。発注の一覧の段の字と次の段は変わらない
    buying = dashboard.flow_neighbors(purchase_orders, people["kobai"])
    assert (buying["label"], buying["prev"]) == (PURCHASE_LABEL, None)
    assert buying["next"] == both["next"]
    # 指示書だけを開ける人: 指示書の一覧の段 (協力企業に出す) はその人の段でないので、流れの枠ごと出さない
    assert dashboard.flow_neighbors(workorders, people["seizo"]) is None


def test_guide_window_links_only_to_screens_the_person_can_open(client, people):
    url = reverse("core:screen_guide", args=["workorders_workorder_list"])
    purchase_url = reverse("purchasing:purchaseorder_list")

    client.force_login(people["seisan"])
    body = client.get(url, **HTMX).content.decode()
    assert "次の段: 発注" in body
    assert f'href="{purchase_url}"' in body

    client.force_login(people["seizo"])
    response = client.get(url, **HTMX)
    assert response.status_code == 200
    body = response.content.decode()
    assert "次の段" not in body and "前の段" not in body
    assert purchase_url not in body
    assert response.context["rows"]  # ユースケースの 4 行は、部署によらず出る

    client.force_login(people["kobai"])
    body = client.get(reverse("core:screen_guide", args=["purchasing_purchaseorder_list"]), **HTMX).content.decode()
    assert "前の段" not in body
    assert reverse("workorders:workorder_list") not in body
    assert "次の段: " in body
