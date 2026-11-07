"""上げたファイルの口 (seizou.core.media) の守りのテスト。N360 (指示書の候補 6 の (A)(B))。

見る所: 読み取りの元のファイル (intake/ の道。単価入りの注文書) を、金額の閲覧の判定 (access.can_see_money) で絞ること
(on は 404・log は返して記録に 1 行・off は今のまま。django と nginx の両方の配り方)。
利用者とファイルは作り物だけ。置き場 (MEDIA_ROOT) は試験ごとの一時のフォルダ (tmp_path)。
"""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth import get_user_model

from seizou.accounts import services as accounts_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
PNG = b"\x89PNG\r\n\x1a\n"
ORDER_PDF = "/media/intake/2026/11/order.pdf"
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
MONEY_LOG = {"department": "off", "write": "off", "money": "log"}
MONEY_OFF = {"department": "off", "write": "off", "money": "off"}


# ---------------------------------------------------------------- fixture (tests/test_core_media.py と tests/test_core_access.py の形を写した物)


@pytest.fixture
def root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    drawings = settings.MEDIA_ROOT / "drawings"
    drawings.mkdir(parents=True)
    (drawings / "a.png").write_bytes(PNG)
    (drawings / "page.html").write_text("<script>alert(1)</script>", encoding="utf-8")
    intake = settings.MEDIA_ROOT / "intake" / "2026" / "11"
    intake.mkdir(parents=True)
    (intake / "order.pdf").write_bytes(b"%PDF-1.4\n")
    return settings.MEDIA_ROOT


@pytest.fixture
def nginx(settings):
    settings.SEIZOU_FILE_DELIVERY = "nginx"
    return {"HTTP_X_SEIZOU_PROXY": "nginx"}


def money(settings, states):
    settings.SEIZOU = {"access_control": dict(states)}


def account(login_id, *, departments=(), permissions=(), **flags):
    """アカウントを発行し、控えの無い利用者の物を返す。"""
    accounts_services.issue_account(login_id, PASSWORD, list(departments), list(permissions), flags, None)
    return User.objects.get(username=login_id)


def access_records(caplog):
    return [r for r in caplog.records if r.name == "seizou.access"]


def signed_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def clerk(db):
    """金額の閲覧の無い人 (現場の人)。"""
    return account("clerk")


@pytest.fixture
def keiri(db):
    """金額の閲覧のある人 (経理)。"""
    return account("keiri", see_money=True)


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


# ---------------------------------------------------------------- 1. 読み取りの元のファイルの金額の判定


def test_money_on_hides_order_file_from_clerk(client, settings, root, clerk):
    money(settings, MONEY_ON)
    response = signed_in(client, clerk).get(ORDER_PDF)
    assert response.status_code == 404


def test_money_on_serves_order_file_to_money_viewers(client, settings, root, keiri, admin):
    money(settings, MONEY_ON)
    for user in (keiri, admin):
        response = signed_in(client, user).get(ORDER_PDF)
        assert response.status_code == 200, user.username
        assert response["Content-Type"] == "application/pdf"


def test_money_log_serves_and_records_one_line(client, settings, root, clerk, caplog):
    money(settings, MONEY_LOG)
    with caplog.at_level(logging.WARNING, logger="seizou.access"):
        response = signed_in(client, clerk).get(ORDER_PDF)
    assert response.status_code == 200
    records = access_records(caplog)
    assert len(records) == 1
    assert "読み取りの元のファイル" in records[0].getMessage()
    assert "clerk" in records[0].getMessage()


def test_money_off_serves_without_record(client, settings, root, clerk, caplog):
    money(settings, MONEY_OFF)
    with caplog.at_level(logging.WARNING, logger="seizou.access"):
        response = signed_in(client, clerk).get(ORDER_PDF)
    assert response.status_code == 200
    assert access_records(caplog) == []


def test_money_on_keeps_other_prefixes(client, settings, root, clerk, caplog):
    money(settings, MONEY_ON)
    with caplog.at_level(logging.WARNING, logger="seizou.access"):
        response = signed_in(client, clerk).get("/media/drawings/a.png")
    assert response.status_code == 200
    assert access_records(caplog) == []


def test_money_on_hides_order_file_in_nginx_mode(client, settings, root, nginx, clerk, keiri):
    money(settings, MONEY_ON)
    refused = signed_in(client, clerk).get(ORDER_PDF, **nginx)
    assert refused.status_code == 404
    assert "X-Accel-Redirect" not in refused

    served = signed_in(client, keiri).get(ORDER_PDF, **nginx)
    assert served.status_code == 200
    assert served["X-Accel-Redirect"] == "/seizou-files/intake/2026/11/order.pdf"
