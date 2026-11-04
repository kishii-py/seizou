"""送れなかった発注の知らせと送り直しの後の画面・受入の残りの数・断られた発注と取消の発注の受入・発注の取消と送り直しの釦の字 (N96。精査 A7 の 1-1 の S2・S15・S6・S9・不具合 7)。

相手の受け口への HTTP は requests.post を偽の物に差し替えて見る (tests.test_purchasing_views の FakePost)。
右上の知らせは get_messages(response.wsgi_request) の (level_tag, 字) で見る。取引先と品目は作り物。
"""

import pytest
import requests
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.purchasing.models import PurchaseOrder
from tests import test_purchasing_views
from tests.test_purchasing_views import FakePost, _post_data

# 発注の画面の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# user = 普通の利用者 tester、subcontractor と items は作り物、send_connection は向き「送る」のつながり (鍵は試験ごとに作る)。
user = test_purchasing_views.user
client_logged_in = test_purchasing_views.client_logged_in
subcontractor = test_purchasing_views.subcontractor
items = test_purchasing_views.items
send_connection = test_purchasing_views.send_connection


def _notices(response):
    """その要求で足した右上の知らせ (level_tag と字) の並び。"""
    return [(m.level_tag, str(m)) for m in get_messages(response.wsgi_request)]


def _detail_url(po):
    return reverse("purchasing:purchaseorder_detail", args=[po.pk])


# ---------------------------------------------------------------- 1 送れなかった発注の知らせと送り直しの後の画面


@pytest.mark.django_db
def test_create_delivered_keeps_saved_message(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost())

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert response["Location"] == _detail_url(po)
    # 届いたときは今のまま
    assert _notices(response) == [("success", "保存しました。")]
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_create_undelivered_is_yellow_only(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))

    response = client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))

    po = PurchaseOrder.objects.get()
    assert response.status_code == 302
    assert response["Location"] == _detail_url(po)
    notices = _notices(response)
    assert len(notices) == 1
    level, text = notices[0]
    assert level == "warning"
    assert text.startswith("注文は相手に届いていません。")
    assert text.endswith("直したら「送り直す」を押してください。")
    assert "success" not in [level for level, _text in notices]
    assert po.status == PurchaseOrder.STATUS_DRAFT


@pytest.mark.django_db
def test_resend_returns_to_detail(monkeypatch, client_logged_in, subcontractor, items, send_connection):
    monkeypatch.setattr("requests.post", FakePost(raises=requests.ConnectionError("作り物の失敗")))
    created = client_logged_in.post(reverse("purchasing:purchaseorder_create"), _post_data(subcontractor, items))
    po = PurchaseOrder.objects.get()
    # 詳細を開いて保存の知らせを読み終える (読んでいない知らせは次の要求に持ち越される)
    client_logged_in.get(created["Location"])
    send_url = reverse("purchasing:purchaseorder_send", args=[po.pk])

    # 届かないまま送り直す: 発注の一覧でなく詳細へ戻り、黄の知らせだけ
    response = client_logged_in.post(send_url)

    assert response.status_code == 302
    assert response["Location"] == _detail_url(po)
    notices = _notices(response)
    assert len(notices) == 1
    assert notices[0][0] == "warning"
    assert notices[0][1].startswith("注文は相手に届いていません。")
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_DRAFT
    client_logged_in.get(response["Location"])

    # 届く形で送り直す: 詳細へ戻り、緑の「相手に送りました。」
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    response = client_logged_in.post(send_url)

    assert response.status_code == 302
    assert response["Location"] == _detail_url(po)
    assert _notices(response) == [("success", "相手に送りました。")]
    assert fake.calls == 1
    assert PurchaseOrder.objects.get(pk=po.pk).status == PurchaseOrder.STATUS_SENT
