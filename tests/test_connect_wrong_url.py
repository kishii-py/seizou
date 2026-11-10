"""受け口の URL を打ち違えたときの送る口と受け口 (依頼書 N382。精査 Q3 の T1 と T11 の一部)。

T1: 送る向きの受け口の URL を自社の受け口に向けると、自社が署名した本文が自社の受け口で通り、届いていない発注が
「送った」になっていた。受け口は、送り手の辞書 (buyer か supplier) の id が自社の id と同じ本文を断る。
自社の id が空の置き場 (試験の設定の SEIZOU = {}) では比べない。
T11: 送る向きの URL が空のときと、相手の受け口が 404 を返したときに、直す所が分かる文を返す。

会社の id は作り物 (a_machining・b_plating) で、取引先と品番も作り物だけを使う。
鍵は試験のたびに _new_secret で作って環境変数に置く。固定の鍵の文字列をファイルに書かない。
helper は既にある試験のファイルから import し、そのファイルは変えない。相手への HTTP は requests.post を偽の物に差し替える (外へは出ない)。
"""

import uuid

import pytest
from django.contrib.auth import get_user_model

from seizou.connect import services
from seizou.connect.models import Exchange, Inbox, Outbox
from seizou.masters import services as masters_services
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_receiver_guard import _post
from tests.test_connect_reply import _into_receive
from tests.test_connect_send import FakePost, FakeResponse, _create
from tests.test_connect_signing import _new_secret
from tests.test_r10_inbox_accept import _line, _payload

ENV_NAME = "SEIZOU_PARTNER_WRONGURL_SECRET"
#: 自社 (a_machining の役) の受け口の URL (ポートの打ち違えの形。偽の requests.post に渡すだけで、外へは出ない)
OWN_URL = "http://127.0.0.1:8045/connect/receive/order/"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
SELF = {"id": "a_machining", "name": "作り物の A 社"}
OTHER = {"id": "b_plating", "name": "作り物の B 社"}


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def secret(monkeypatch):
    value = _new_secret()
    monkeypatch.setenv(ENV_NAME, value)
    return value


@pytest.fixture
def receiver(user, secret):
    """受ける向きのつながりを 1 つ置き、署名に使う鍵を返す。"""
    partner = masters_services.create_partner({"code": "WU01", "name": "作り物の得意先", "role": "customer"}, user)
    services.register_connection(partner, "", ENV_NAME, "receive", user)
    return secret


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "WU02", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "WU-100", "name": "作り物の軸", "unit": "個"}, user)


def _order(buyer):
    data = _payload(str(uuid.uuid4()), [_line(1, "WU-100")])
    data["buyer"] = buyer
    return data


def _received_result():
    return Exchange.objects.get(direction=Exchange.DIRECTION_RECEIVED).result


# ---------------------------------------------------------------- (a)〜(d) 受け口が送り手を見る


def test_order_from_own_company_is_refused(client, settings, receiver):
    settings.SEIZOU = {"id": "a_machining"}

    response = _post(client, receiver, _order(SELF))

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_FROM_SELF
    assert Inbox.objects.count() == 0
    assert _received_result() == f"断った: {services.MESSAGE_FROM_SELF}"


def test_order_from_other_company_is_received(client, settings, receiver):
    settings.SEIZOU = {"id": "a_machining"}

    response = _post(client, receiver, _order(OTHER))

    assert response.status_code == 200
    assert Inbox.objects.count() == 1


def test_reply_from_own_company_is_refused_before_kind(client, settings, receiver):
    settings.SEIZOU = {"id": "a_machining"}
    # kind で分ける前に断るので、注文回答のほかの鍵 (order_id・reply_type など) は無くてよい。
    data = {"id": str(uuid.uuid4()), "kind": "order_reply", "supplier": SELF}

    response = _post(client, receiver, data)

    assert response.status_code == 400
    assert response.json()["message"] == services.MESSAGE_FROM_SELF


def test_empty_own_id_does_not_compare(client, settings, receiver):
    settings.SEIZOU = {}

    response = _post(client, receiver, _order({"id": "", "name": "作り物の id の無い会社"}))

    assert response.status_code == 200
    assert Inbox.objects.count() == 1


# ---------------------------------------------------------------- (e)〜(g) 送る口の返事


def _draft_with_reply(po):
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_DRAFT
    return Outbox.objects.get().response


def test_send_to_own_receiver_is_refused_and_stays_draft(monkeypatch, settings, user, secret, subcontractor, item):
    settings.SEIZOU = {"id": "a_machining"}
    # 1 つの取引先の送る向きと受ける向きは同じ鍵の置き場を使う。送る向きの URL を自社の受け口に打ち違えた形。
    services.register_connection(subcontractor, OWN_URL, ENV_NAME, "send", user)
    services.register_connection(subcontractor, "", ENV_NAME, "receive", user)
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert len(fake.calls) == 1
    assert _draft_with_reply(po) == "断られた: " + services.MESSAGE_FROM_SELF
    assert Inbox.objects.count() == 0


def test_empty_send_url_is_not_sent(monkeypatch, user, secret, subcontractor, item):
    services.register_connection(subcontractor, "", ENV_NAME, "send", user)
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)

    po = _create(subcontractor, item, user)

    assert fake.calls == []
    assert _draft_with_reply(po) == services.MESSAGE_NO_URL


@pytest.mark.parametrize(
    ("status", "expected"),
    [(404, services.MESSAGE_NOT_FOUND), (403, "返事が読めません (HTTP 403)")],
    ids=["not_found", "forbidden_as_before"],
)
def test_not_found_reply_names_the_path(monkeypatch, user, secret, subcontractor, item, status, expected):
    services.register_connection(subcontractor, PARTNER_URL, ENV_NAME, "send", user)
    monkeypatch.setattr("requests.post", FakePost(reply=FakeResponse(status)))

    po = _create(subcontractor, item, user)

    response = _draft_with_reply(po)
    assert response == expected
    if status == 404:
        assert response.startswith("届きません: 相手の受け口が見つかりません (HTTP 404)。")
