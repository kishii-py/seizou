"""一覧の絞りの候補を役と使用中と名前で整えた形 (N142。土台は N122 の lookup の choices。使用中は N135)。

取引先の候補: 出荷と納品書の得意先・InboxListView の取引先は、取引先の種類が得意先か両方の物。発注の取引先は協力企業か両方の物。
どれも使用中の物だけ (使用停止は update_partner に active=False を渡す。編集の画面と同じ道)。
工程の候補 (指示書と実績の一覧): 字は工程の名前だけ (工程コードを付けない)。並びは並び順 (同じなら工程コード)。
候補は取引先と工程の表から作るので、出荷や指示書の行が無くても出る。名前は作り物だけ。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters import services as masters_services


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def partners(user):
    """得意先・両方・協力企業・使用停止の得意先・使用停止の協力企業。コードの順 = 取引先の ordering。"""

    def make(code, name, role, *, active=True):
        partner = masters_services.create_partner({"code": code, "name": name, "role": role}, user)
        if not active:
            masters_services.update_partner(partner, {"active": False}, user)
        return partner

    return {
        "customer": make("C001", "作り物の得意先", "customer"),
        "both": make("C002", "作り物の両方の取引先", "both"),
        "subcontractor": make("S001", "作り物の協力企業", "subcontractor"),
        "stopped_customer": make("C003", "作り物の古い得意先", "customer", active=False),
        "stopped_subcontractor": make("S002", "作り物の古い協力企業", "subcontractor", active=False),
    }


def _options(response, name):
    (options,) = [f["options"] for f in response.context["filters"] if f["name"] == name]
    return options


@pytest.mark.django_db
def test_shipment_list_customer_choices_are_active_customers(logged_in, partners):
    response = logged_in.get(reverse("shipping:shipment_list"))
    customer, both = partners["customer"], partners["both"]
    assert _options(response, "customer") == [(str(customer.pk), customer.name), (str(both.pk), both.name)]
    assert f'<option value="{both.pk}">{both.name}</option>' in response.content.decode()


@pytest.mark.django_db
def test_delivery_note_list_customer_choices_are_active_customers(logged_in, partners):
    response = logged_in.get(reverse("shipping:deliverynote_list"))
    customer, both = partners["customer"], partners["both"]
    assert _options(response, "customer") == [(str(customer.pk), customer.name), (str(both.pk), both.name)]
    # 使用停止の得意先を GET で渡しても、候補に無いので絞らない (空と同じ。知らせは出さない)
    stopped = logged_in.get(reverse("shipping:deliverynote_list"), {"f_customer": str(partners["stopped_customer"].pk)})
    assert stopped.context["filter_active"] is False


@pytest.mark.django_db
def test_inbox_list_partner_choices_are_active_customers(logged_in, partners):
    response = logged_in.get(reverse("connect:inbox_list"))
    customer, both = partners["customer"], partners["both"]
    assert _options(response, "partner") == [(str(customer.pk), customer.name), (str(both.pk), both.name)]


@pytest.mark.django_db
def test_purchase_order_list_partner_choices_are_active_subcontractors(logged_in, partners):
    response = logged_in.get(reverse("purchasing:purchaseorder_list"))
    both, subcontractor = partners["both"], partners["subcontractor"]
    # 発注の取引先の候補は名前の順 (N120 の _subcontractor_choices のまま。「両」は「協」より前)
    assert _options(response, "partner") == [(str(both.pk), both.name), (str(subcontractor.pk), subcontractor.name)]


@pytest.mark.django_db
def test_process_choices_show_names_only_in_sort_order(logged_in, user):
    # 工程コードの順 (AAA・MMM・ZZZ) と並び順 (3・2・1) を逆にして、並びが並び順で決まることを見る
    inspect = masters_services.create_process({"code": "AAA", "name": "作り物の検査", "sort_order": 3}, user)
    bend = masters_services.create_process({"code": "MMM", "name": "作り物の曲げ", "sort_order": 2}, user)
    cut = masters_services.create_process({"code": "ZZZ", "name": "作り物の切断", "sort_order": 1}, user)
    expected = [(str(cut.pk), "作り物の切断"), (str(bend.pk), "作り物の曲げ"), (str(inspect.pk), "作り物の検査")]

    for url in (reverse("workorders:workorder_list"), reverse("progress:record_list")):
        response = logged_in.get(url)
        assert _options(response, "process") == expected
        body = response.content.decode()
        assert f'<option value="{cut.pk}">作り物の切断</option>' in body
        assert "ZZZ 作り物の切断" not in body
        # 候補の値 (pk) で絞れる (絞りの値の読み方は N122 の lookup の choices のまま)
        chosen = logged_in.get(url, {"f_process": str(cut.pk)})
        assert chosen.context["filter_active"] is True
