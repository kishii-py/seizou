"""つながりを保存した後の黄の知らせ (受け口の URL が自社を指す・空・道の形が違う) と、自社を指すかを見る読むだけの関数のテスト。

意図: 送る向きの受け口の URL の打ち違いを、発注を送る前 (つながりを保存した直後) に分かるようにするため。保存は止めない
(本体の決め 11-11 14:2x。精査 Q3 の T1・T11)。url_points_to_self は点検 (seizou_doctor) も呼ぶので、名前と引数を変えない。

取引先と会社の名前と URL は作り物。鍵の置き場の名前だけを使い、鍵の値は置かない (この試験は署名をしない)。
helper と fixture は既にある試験のファイルから import し、そのファイルは変えない。
"""

import pytest
from django.urls import reverse

from seizou.connect import services
from seizou.connect.models import Connection
from tests.test_connect_connection_views import (  # noqa: F401 (fixture)
    ENV_NAME,
    _post_data,
    client_logged_in,
    partner,
    user,
)

OWN_PORT = 8001
SELF_URL = f"http://127.0.0.1:{OWN_PORT}/connect/receive/order/"
PARTNER_URL = "http://127.0.0.1:8002/connect/receive/order/"
WRONG_PATH_URL = "http://127.0.0.1:8002/connect/receive/orders/"
SITE = "https://a.example.jp"


@pytest.fixture
def own_port(settings):
    settings.SEIZOU = {"port": OWN_PORT}
    return settings


def _messages(response):
    return [(message.level_tag, str(message)) for message in response.context["messages"]]


# ---------------------------------------------------------------- (a) 自社を指すか


@pytest.mark.django_db
def test_url_points_to_self_by_loopback_and_own_port(own_port):
    shown = {
        url: services.url_points_to_self(url)
        for url in (
            SELF_URL,
            f"http://localhost:{OWN_PORT}/connect/receive/order/",
            f"http://[::1]:{OWN_PORT}/connect/receive/order/",
            f"http://LOCALHOST:{OWN_PORT}/connect/receive/order/",
            PARTNER_URL,
            f"http://officepc:{OWN_PORT}/connect/receive/order/",
            "",
            "127.0.0.1:8001/connect/receive/order/",
            "http://127.0.0.1:port/connect/receive/order/",
            None,
        )
    }

    assert shown == {
        SELF_URL: True,
        f"http://localhost:{OWN_PORT}/connect/receive/order/": True,
        f"http://[::1]:{OWN_PORT}/connect/receive/order/": True,
        f"http://LOCALHOST:{OWN_PORT}/connect/receive/order/": True,
        PARTNER_URL: False,
        f"http://officepc:{OWN_PORT}/connect/receive/order/": False,
        "": False,
        "127.0.0.1:8001/connect/receive/order/": False,
        "http://127.0.0.1:port/connect/receive/order/": False,
        None: False,
    }


@pytest.mark.django_db
def test_url_points_to_self_default_port_by_scheme(settings):
    settings.SEIZOU = {"port": 80}
    assert services.url_points_to_self("http://127.0.0.1/connect/receive/order/") is True
    assert services.url_points_to_self("https://127.0.0.1/connect/receive/order/") is False
    settings.SEIZOU = {"port": 443}
    assert services.url_points_to_self("https://localhost/connect/receive/order/") is True


@pytest.mark.django_db
def test_url_points_to_self_without_own_port(settings):
    settings.SEIZOU = {}
    assert services.url_points_to_self(SELF_URL) is False
    settings.SEIZOU = {"port": "8001"}
    assert services.url_points_to_self(SELF_URL) is False


@pytest.mark.django_db
def test_url_points_to_self_by_site_url(settings):
    # 置き方は tests/test_connect_chat_mail.py の settings.SEIZOU = {"mail": {..., "site_url": ...}} の見本に合わせる
    settings.SEIZOU = {"mail": {"site_url": SITE}}

    assert services.url_points_to_self(f"{SITE}/connect/receive/order/") is True
    assert services.url_points_to_self("https://A.Example.JP:8443/connect/receive/order/") is True
    assert services.url_points_to_self("https://b.example.jp/connect/receive/order/") is False

    settings.SEIZOU = {"mail": {"site_url": ""}}
    assert services.url_points_to_self(f"{SITE}/connect/receive/order/") is False
    settings.SEIZOU = {"mail": {"site_url": "http://[bad"}}
    assert services.url_points_to_self(f"{SITE}/connect/receive/order/") is False


# ---------------------------------------------------------------- (b)〜(d) 登録した後の知らせ


def test_register_self_url_is_warning(own_port, partner, user):  # noqa: F811
    saved = services.register_connection(partner, SELF_URL, ENV_NAME, "send", user)

    assert saved.saved_level == "warning"
    assert services.NOTE_URL_SELF in saved.saved_note
    assert services.NOTE_URL_PATH not in saved.saved_note
    # 保存は止めない
    assert Connection.objects.get().url == SELF_URL


def test_register_empty_url_is_warning(own_port, partner, user):  # noqa: F811
    saved = services.register_connection(partner, "", ENV_NAME, "send", user)

    assert saved.saved_level == "warning"
    assert saved.saved_note == services.NOTE_URL_EMPTY
    assert Connection.objects.get().url == ""


def test_register_wrong_path_is_warning(own_port, partner, user):  # noqa: F811
    saved = services.register_connection(partner, WRONG_PATH_URL, ENV_NAME, "send", user)

    assert saved.saved_level == "warning"
    assert saved.saved_note == services.NOTE_URL_PATH


def test_register_self_url_with_wrong_path_has_both_notes(own_port, partner, user):  # noqa: F811
    url = f"http://localhost:{OWN_PORT}/connect/receive/order"
    saved = services.register_connection(partner, url, ENV_NAME, "send", user)

    assert saved.saved_level == "warning"
    assert saved.saved_note == services.NOTE_URL_SELF + services.NOTE_URL_PATH


def test_register_partner_url_and_receive_have_no_notice(own_port, partner, user):  # noqa: F811
    sent = services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    received = services.register_connection(partner, "", ENV_NAME, "receive", user)

    assert getattr(sent, "saved_level", "") == ""
    assert getattr(sent, "saved_note", "") == ""
    assert getattr(received, "saved_level", "") == ""
    assert getattr(received, "saved_note", "") == ""


def test_connection_url_notes_reads_only(own_port, partner, user):  # noqa: F811
    connection = services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    connection.url = SELF_URL

    assert services.connection_url_notes(connection) == [services.NOTE_URL_SELF]
    connection.refresh_from_db()
    assert connection.url == PARTNER_URL
    connection.direction = "receive"
    connection.url = ""
    assert services.connection_url_notes(connection) == []


# ---------------------------------------------------------------- (e) 直した後の知らせ


def test_update_has_same_notes(own_port, partner, user):  # noqa: F811
    connection = services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)

    saved = services.update_connection(connection, partner, SELF_URL, ENV_NAME, "send", user)
    assert saved.saved_level == "warning"
    assert services.NOTE_URL_SELF in saved.saved_note

    saved = services.update_connection(connection, partner, "", ENV_NAME, "send", user)
    assert (saved.saved_level, saved.saved_note) == ("warning", services.NOTE_URL_EMPTY)

    saved = services.update_connection(connection, partner, WRONG_PATH_URL, ENV_NAME, "send", user)
    assert (saved.saved_level, saved.saved_note) == ("warning", services.NOTE_URL_PATH)

    saved = services.update_connection(connection, partner, PARTNER_URL, ENV_NAME, "send", user)
    assert getattr(saved, "saved_level", "") == ""

    saved = services.update_connection(connection, partner, "", ENV_NAME, "receive", user)
    assert getattr(saved, "saved_level", "") == ""
    assert Connection.objects.get().direction == "receive"


# ---------------------------------------------------------------- (f) 画面


def test_create_screen_shows_yellow_notice(own_port, client_logged_in, partner):  # noqa: F811
    response = client_logged_in.post(
        reverse("connect:connection_create"), _post_data(partner, "send", SELF_URL), follow=True
    )

    connection = Connection.objects.get()
    assert response.redirect_chain[-1][0] == reverse("connect:connection_detail", args=[connection.pk])
    messages = _messages(response)
    assert len(messages) == 1
    level, text = messages[0]
    assert level == "warning"
    assert text.startswith("保存しました。") and services.NOTE_URL_SELF in text
    assert services.NOTE_URL_SELF in response.content.decode()


def test_update_screen_shows_yellow_notice_then_green(own_port, client_logged_in, partner, user):  # noqa: F811
    connection = services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    url = reverse("connect:connection_update", args=[connection.pk])

    response = client_logged_in.post(url, _post_data(partner, "send", WRONG_PATH_URL), follow=True)
    assert _messages(response) == [("warning", "保存しました。" + services.NOTE_URL_PATH)]

    response = client_logged_in.post(url, _post_data(partner, "send", PARTNER_URL), follow=True)
    assert _messages(response) == [("success", "保存しました。")]
