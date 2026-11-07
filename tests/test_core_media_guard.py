"""上げたファイルの口 (seizou.core.media) の守りのテスト。N360 (指示書の候補 6 の (A)(B))。

見る所: 読み取りの元のファイル (intake/ の道。単価入りの注文書) を、金額の閲覧の判定 (access.can_see_money) で絞ること
(on は 404・log は返して記録に 1 行・off は今のまま。django と nginx の両方の配り方)。読み取りの詳細の「元のファイル」の行を、
金額の閲覧の無い人 (seizou.hide_money) に出さないこと。
利用者とファイルと得意先は作り物だけ。置き場 (MEDIA_ROOT) は試験ごとの一時のフォルダ (tmp_path)。
本物の API は呼ばない (読み取りの fixture が作り物の返事に差し替え、作った後は「呼ばれたら AssertionError」に戻す)。
"""

from __future__ import annotations

import logging

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import media
from seizou.intake import services as intake_services
from seizou.masters.services import create_partner
from tests.test_intake_extract import FAKE_KEY, REPLY_PATH, refuse_api, upload

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


# ---------------------------------------------------------------- 2. 読み取りの詳細の「元のファイル」の行 (tests/test_intake_views.py の fixture の形を写した物)


@pytest.fixture
def no_real_api(monkeypatch):
    monkeypatch.setattr(intake_services, "_ask_claude", refuse_api)


@pytest.fixture
def fake_reply(no_real_api, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    monkeypatch.setattr(intake_services, "_ask_claude", lambda model, block: REPLY_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def reader(db):
    return User.objects.create_user("tester", password=PASSWORD)


@pytest.fixture
def extraction(root, fake_reply, reader, monkeypatch):
    customer = create_partner({"code": "IV01", "name": "作り物の得意先", "role": "customer"}, reader)
    record = intake_services.extract_order(upload(), customer, reader)
    monkeypatch.setattr(intake_services, "_ask_claude", refuse_api)
    return record


def detail_body(client, extraction) -> str:
    response = client.get(reverse("intake:extraction_detail", args=[extraction.pk]))
    assert response.status_code == 200
    return response.content.decode()


def test_detail_hides_source_file_from_clerk_when_money_on(client, settings, extraction, clerk):
    money(settings, MONEY_ON)
    signed_in(client, clerk)
    body = detail_body(client, extraction)
    assert "元のファイル" not in body
    assert extraction.file.url not in body
    # 行を隠すだけでなく、URL を直に開いても返さない (1 と同じ口)。
    assert client.get(extraction.file.url).status_code == 404


def test_detail_shows_source_file_to_admin_when_money_on(client, settings, extraction, admin):
    money(settings, MONEY_ON)
    body = detail_body(signed_in(client, admin), extraction)
    assert "元のファイル" in body
    assert extraction.file.url in body


def test_detail_shows_source_file_in_default_log_mode(client, extraction, clerk):
    # tests/settings.py の SEIZOU = {} (= 金額の閲覧は記録だけ)。tests/test_intake_views.py の詳細と同じ見え方。
    body = detail_body(signed_in(client, clerk), extraction)
    assert "元のファイル" in body
    assert extraction.file.url in body


# ---------------------------------------------------------------- 3. ブラウザの中で開く型の許しの一覧と、ダウンロードの CSP の sandbox

INLINE_NAMES = ("x.png", "x.txt", "x.pdf")
DOWNLOAD_NAMES = ("x.rss", "x.xslt", "x.csv", "page.html")


@pytest.fixture
def kinds(root):
    drawings = root / "drawings"
    (drawings / "x.png").write_bytes(PNG)
    (drawings / "x.txt").write_text("作り物のテキスト", encoding="utf-8")
    (drawings / "x.pdf").write_bytes(b"%PDF-1.4\n")
    (drawings / "x.rss").write_text('<?xml version="1.0"?><rss><script>alert(1)</script></rss>', encoding="utf-8")
    (drawings / "x.xslt").write_text('<?xml version="1.0"?><xsl:stylesheet/>', encoding="utf-8")
    (drawings / "x.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    return drawings


def test_allowlisted_types_open_inline_without_csp(client, kinds, reader):
    signed_in(client, reader)
    for name in INLINE_NAMES:
        response = client.get(f"/media/drawings/{name}")
        assert response.status_code == 200, name
        assert response["Content-Disposition"].startswith("inline;"), name
        assert "Content-Security-Policy" not in response, name


def test_other_types_download_with_sandbox(client, kinds, reader):
    signed_in(client, reader)
    for name in DOWNLOAD_NAMES:
        response = client.get(f"/media/drawings/{name}")
        assert response.status_code == 200, name
        assert response["Content-Disposition"].startswith("attachment;"), name
        assert response["Content-Security-Policy"] == "sandbox", name
        assert response["X-Content-Type-Options"] == "nosniff", name


@pytest.mark.parametrize(
    ("name", "content_type", "download"),
    [
        ("a.rss", "application/rss+xml", True),
        ("a.xslt", "application/xslt+xml", True),
        ("a.bin", "application/octet-stream", True),
        ("a.webp", "image/webp", False),
        ("a.gif", "image/gif", False),
        ("a.jpg", "image/jpeg", False),
        ("a.txt", "text/plain; charset=utf-8", False),
        ("a.svg", "image/png", True),
    ],
)
def test_must_download_allowlist(name, content_type, download):
    assert media.must_download(name, content_type) is download


def test_nginx_mode_sends_sandbox_only_on_downloads(client, kinds, nginx, reader):
    signed_in(client, reader)
    rss = client.get("/media/drawings/x.rss", **nginx)
    assert rss.status_code == 200
    assert rss["X-Accel-Redirect"] == "/seizou-files/drawings/x.rss"
    assert rss["Content-Security-Policy"] == "sandbox"
    assert rss["Content-Disposition"].startswith("attachment;")

    png = client.get("/media/drawings/a.png", **nginx)
    assert png["X-Accel-Redirect"] == "/seizou-files/drawings/a.png"
    assert "Content-Security-Policy" not in png
