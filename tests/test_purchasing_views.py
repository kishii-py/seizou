"""発注の画面 (core の土台の上)。相手の受け口への HTTP は requests.post を偽の物に差し替えて見る。

鍵はテストのたびに作り、環境変数に置く。取引先と品目は作り物。
"""

import pytest
import requests
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Outbox
from seizou.masters import services as masters_services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_signing import _new_secret

ENV_NAME = "SEIZOU_PARTNER_TESTSUB_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"


class FakeResponse:
    def __init__(self, status_code, data=None):
        self.status_code = status_code
        self._data = data or {}

    def json(self):
        return self._data


class FakePost:
    def __init__(self, reply=None, raises=None):
        self.reply = reply if reply is not None else FakeResponse(200, {"status": "received"})
        self.raises = raises
        self.calls = 0

    def __call__(self, url, data=None, headers=None, timeout=None, **kwargs):
        self.calls += 1
        if self.raises is not None:
            raise self.raises
        return self.reply


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
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
    ]


@pytest.fixture
def send_connection(monkeypatch, subcontractor, user):
    monkeypatch.setenv(ENV_NAME, _new_secret())
    return connect_services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)


def _post_data(partner, items, total=2):
    data = {
        "partner": partner.pk,
        "order_date": "2026-11-02",
        "note": "作り物の発注",
        "lines-TOTAL_FORMS": str(total),
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
        "lines-0-item": items[0].pk,
        "lines-0-qty": "10",
        "lines-0-unit_price": "25.5",
        "lines-0-due_date": "2026-11-20",
        "lines-1-item": items[1].pk,
        "lines-1-qty": "3",
        "lines-1-unit": "KGM",
        "lines-1-unit_price": "100",
    }
    return data


def _open_tag(body, start):
    """画面の HTML から、start (例 '<select name="partner"') で始まる開きタグを > まで切り出す (tests/test_core_ux_r2.py と同じ見方)。"""
    begin = body.index(start)
    return body[begin : body.index(">", begin)]


@pytest.mark.django_db
def test_list_requires_login(client):
    response = client.get(reverse("purchasing:purchaseorder_list"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


@pytest.mark.django_db
def test_create_posts_and_sends(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    response = client_logged_in.post(
        reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items)
    )

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("purchasing:purchaseorder_detail", args=[po.pk])
    assert fake.calls == 1
    assert Outbox.objects.count() == 1
    assert po.status == PurchaseOrder.STATUS_SENT
    assert [line.line_no for line in po.lines.all()] == [1, 2]
    assert po.lines.get(line_no=2).unit == "kg"

    detail = client_logged_in.get(response["Location"]).content.decode()
    assert "受け取った" in detail
    assert "送った" in detail
    assert "T-100" in detail and "作り物の板" in detail
    # 「送った」の発注には送り直すボタンを出さず、「⋯」の「取消」は出す (相手がまだ出荷していなければ取消を相手へ送る)
    assert reverse("purchasing:purchaseorder_send", args=[po.pk]) not in detail
    assert reverse("purchasing:purchaseorder_cancel", args=[po.pk]) in detail


@pytest.mark.django_db
def test_create_with_unreachable_partner_stays_draft(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))

    response = client_logged_in.post(
        reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items)
    )

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert po.status == PurchaseOrder.STATUS_DRAFT
    detail = client_logged_in.get(response["Location"]).content.decode()
    assert "届きません" in detail
    assert "送り直す" in detail
    assert reverse("purchasing:purchaseorder_send", args=[po.pk]) in detail


@pytest.mark.django_db
def test_resend_button_sends_again(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    po = PurchaseOrder.objects.get()
    assert po.status == PurchaseOrder.STATUS_DRAFT

    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    response = client_logged_in.post(reverse("purchasing:purchaseorder_send", args=[po.pk]))

    assert response.status_code == 302
    assert fake.calls == 1
    assert Outbox.objects.filter(purchase_order=po).count() == 2
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_resend_without_connection_is_red_notice(monkeypatch, client_logged_in, subcontractor, items):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    po = PurchaseOrder.objects.get()

    response = client_logged_in.post(reverse("purchasing:purchaseorder_send", args=[po.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "つながりがありません" in body
    assert fake.calls == 0
    assert Outbox.objects.count() == 0


@pytest.mark.django_db
def test_create_without_lines_is_red_notice(client_logged_in, subcontractor, items):
    data = _post_data(subcontractor, items, total=1)
    for key in ("lines-0-item", "lines-0-qty", "lines-0-unit_price", "lines-0-due_date"):
        data.pop(key)

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), data)

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "明細がありません" in body
    assert PurchaseOrder.objects.count() == 0


@pytest.mark.django_db
def test_create_keeps_line_errors_on_screen(client_logged_in, subcontractor, items):
    data = _post_data(subcontractor, items)
    data["lines-1-qty"] = ""

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), data)

    assert response.status_code == 200
    body = response.content.decode()
    assert "作り物の発注" in body  # 入れた値は消えない
    assert PurchaseOrder.objects.count() == 0


@pytest.mark.django_db
def test_customer_only_partner_is_not_a_choice(client_logged_in, user, subcontractor):
    masters_services.create_partner({"code": "C001", "name": "作り物の得意先だけ", "role": "customer"}, user)
    masters_services.create_partner({"code": "B001", "name": "作り物の両方の取引先", "role": "both"}, user)

    response = client_logged_in.get(reverse("purchasing:purchaseorder_create"))

    assert response.status_code == 200
    body = response.content.decode()
    # 取引先の欄は字を打って探す欄。候補の口は取引先の口に role=supplier で、daisyUI の select の class は付かない (候補 C01-先 の 3)。
    tag = _open_tag(body, '<select name="partner"')
    select_url = reverse("masters:partner_select")
    assert f'data-select-url="{select_url}?role=supplier"' in tag
    assert 'class="w-full"' in tag and "select w-full" not in tag
    # その口の候補は協力企業と両方の取引先で、得意先だけの取引先は無い。
    texts = [row["text"] for row in client_logged_in.get(select_url, {"role": "supplier"}).json()["items"]]
    assert any("作り物の協力企業" in text for text in texts)
    assert any("作り物の両方の取引先" in text for text in texts)
    assert not any("作り物の得意先だけ" in text for text in texts)
    assert "作り物の得意先だけ" not in body
    assert 'id="po-line-template"' in body and "行を足す" in body


@pytest.mark.django_db
def test_cancel_view_cancels_draft_with_reason(monkeypatch, client_logged_in, subcontractor, items):
    monkeypatch.setattr("requests.post", FakePost())
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    po = PurchaseOrder.objects.get()

    response = client_logged_in.post(
        reverse("purchasing:purchaseorder_cancel", args=[po.pk]), {"reason": "数を間違えた"}
    )

    assert response.status_code == 302
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_CANCELLED
    assert po.note.endswith("取消の理由: 数を間違えた")


@pytest.mark.django_db
def test_list_shows_orders_and_searches(monkeypatch, client_logged_in, user, subcontractor, items):
    monkeypatch.setattr("requests.post", FakePost())
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    other = masters_services.create_partner(
        {"code": "S002", "name": "作り物の別の協力企業", "role": "subcontractor"}, user
    )
    client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(other, items))

    body = client_logged_in.get(reverse("purchasing:purchaseorder_list")).content.decode()
    assert "PO-000001" in body and "PO-000002" in body
    assert "下書き" in body
    assert reverse("masters:partner_modal", args=[subcontractor.pk]) in body

    rows = client_logged_in.get(
        reverse("purchasing:purchaseorder_list"), {"q": "S002"}, HTTP_HX_REQUEST="true"
    ).content.decode()
    assert "PO-000002" in rows and "PO-000001" not in rows


@pytest.mark.django_db
def test_menu_has_purchase_entry(client_logged_in):
    body = client_logged_in.get(reverse("core:home")).content.decode()
    assert reverse("purchasing:purchaseorder_list") in body
    assert "発注" in body


# ---------------------------------------------------------------- 押す前の画面に送ることを書く (初見テスト R5 の V08。候補 C01)

HEAD_NOW = "「必須」の欄と明細を 1 行以上入れて「発注する」を押します。"
HEAD_SEND = "止まったときも消えません。つながりのある取引先には、「発注する」を押すと保存と同時に注文を送ります。"


@pytest.mark.django_db
def test_create_head_says_order_is_sent_on_save(monkeypatch, client_logged_in):
    monkeypatch.delenv("SEIZOU_PRACTICE", raising=False)

    response = client_logged_in.get(reverse("purchasing:purchaseorder_create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert HEAD_NOW in body
    # 今の 1 文の直後に、要素を挟まずに続く (1 つの段落として折り返す)。
    assert HEAD_SEND in body
    assert "送った発注は、相手が出荷し終えるまでは、変更と取消を相手へ送れます。</p>" in body
    assert "自分では取り消せません" not in body


@pytest.mark.django_db
def test_create_head_hides_send_sentence_in_practice_mode(monkeypatch, client_logged_in):
    monkeypatch.setenv("SEIZOU_PRACTICE", "1")

    response = client_logged_in.get(reverse("purchasing:purchaseorder_create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert HEAD_NOW in body
    assert "つながりのある取引先には" not in body


# ---------------------------------------------------------------- 部品の欄は字を打って探す欄で、単位だけを写す (初見テスト R5 の V50・V140。候補 C01-先 の 4)


@pytest.mark.django_db
def test_create_item_fields_use_purchase_item_select(client_logged_in):
    body = client_logged_in.get(reverse("purchasing:purchaseorder_create")).content.decode()

    select_url = reverse("purchasing:item_select")
    # 1 行目の欄と「行を足す」の雛形の欄の両方。daisyUI の select の class と「詳細を見る」の口 (purchasing:item_modal は無い) は付かない。
    for start in ('<select name="lines-0-item"', '<select name="lines-__prefix__-item"'):
        tag = _open_tag(body, start)
        assert f'data-select-url="{select_url}"' in tag
        assert 'class="w-full"' in tag
        assert "select w-full" not in tag
        assert "data-modal-url" not in tag
    assert "単位が空なら部品の単位を写します。単価は協力企業と決めた値を打ちます (部品の標準単価は写しません)。" in body


@pytest.mark.django_db
def test_purchase_item_select_returns_unit_without_standard_price(client_logged_in, user, items):
    stopped = masters_services.create_item({"part_no": "T-900", "name": "作り物の古い軸", "unit": "個"}, user)
    masters_services.update_item(stopped, {"active": False}, user)

    data = client_logged_in.get(reverse("purchasing:item_select")).json()

    # 候補は使用中の部品だけ。どの候補も単位を持ち、標準単価を持たない。
    assert sorted(row["id"] for row in data["items"]) == sorted(item.pk for item in items)
    assert all("unit" in row and "standard_price" not in row for row in data["items"])
    assert {row["id"]: row["unit"] for row in data["items"]} == {items[0].pk: "個", items[1].pk: "枚"}
    # 同じ人で受注の部品の口を引くと標準単価がある (金額の閲覧がある人でも、発注の口は標準単価を返さない)。
    masters_data = client_logged_in.get(reverse("masters:item_select")).json()
    assert masters_data["items"] and all("standard_price" in row for row in masters_data["items"])


@pytest.mark.django_db
def test_purchase_item_select_searches_by_text(client_logged_in, items):
    data = client_logged_in.get(reverse("purchasing:item_select"), {"q": "T-200"}).json()

    assert [row["id"] for row in data["items"]] == [items[1].pk]
