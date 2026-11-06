"""つながりの新規登録・詳細・編集の画面のテスト。core の土台の上。

鍵の値はテストのたびに signing.new_secret() で作り、環境変数に置く。固定の鍵の文字列をファイルに書かない。
値を含む本文の比べは、真偽にしてから assert する (失敗の文に値を出さないため)。取引先は作り物。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.connect import services, signing
from seizou.connect.models import Connection
from seizou.core.screens import all_screens
from seizou.masters.services import create_partner

ENV_NAME = "SEIZOU_PARTNER_SCREENCO_SECRET"
LOCAL_URL = "http://127.0.0.1:8002/connect/receive/order/"
PC_NAME_URL = "http://officepc:8002/connect/receive/order/"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def partner(user):
    return create_partner({"code": "SC01", "name": "作り物の取引先", "role": "both"}, user)


@pytest.fixture
def connection(partner, user, monkeypatch):
    monkeypatch.delenv(ENV_NAME, raising=False)
    return services.register_connection(partner, "", ENV_NAME, "receive", user)


def _post_data(partner, direction="receive", url="", secret_env=ENV_NAME):
    return {"partner": partner.pk, "direction": direction, "url": url, "secret_env": secret_env}


def test_screens_require_login(client, connection):
    for url in (
        reverse("connect:connection_create"),
        reverse("connect:connection_detail", args=[connection.pk]),
        reverse("connect:connection_update", args=[connection.pk]),
    ):
        response = client.get(url)
        assert response.status_code == 302, url
        assert reverse("core:login") in response["Location"], url


def test_list_has_create_button(client_logged_in):
    body = client_logged_in.get(reverse("connect:connection_list")).content.decode()

    assert "新規登録" in body
    assert f'href="{reverse("connect:connection_create")}"' in body


def test_create_registers_and_moves_to_detail(client_logged_in, partner):
    response = client_logged_in.post(reverse("connect:connection_create"), _post_data(partner))

    connection = Connection.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("connect:connection_detail", args=[connection.pk])
    assert (connection.partner, connection.direction, connection.url, connection.secret_env) == (
        partner,
        "receive",
        "",
        ENV_NAME,
    )


def test_create_accepts_pc_name_url(client_logged_in, partner):
    response = client_logged_in.post(
        reverse("connect:connection_create"), _post_data(partner, "send", PC_NAME_URL)
    )

    assert response.status_code == 302
    assert Connection.objects.get().url == PC_NAME_URL


def test_create_duplicate_is_red(client_logged_in, partner):
    data = _post_data(partner)
    client_logged_in.post(reverse("connect:connection_create"), data)

    response = client_logged_in.post(reverse("connect:connection_create"), data)

    body = response.content.decode()
    assert response.status_code == 200
    assert 'data-notice="stop"' in body and "既にあります" in body
    assert f'value="{ENV_NAME}"' in body
    assert Connection.objects.count() == 1


def test_update_changes_url(client_logged_in, partner, user):
    connection = services.register_connection(partner, LOCAL_URL, ENV_NAME, "send", user)
    url = reverse("connect:connection_update", args=[connection.pk])

    body = client_logged_in.get(url).content.decode()
    assert f'value="{LOCAL_URL}"' in body
    assert f'value="{ENV_NAME}"' in body

    response = client_logged_in.post(url, _post_data(partner, "send", PC_NAME_URL))

    assert response.status_code == 302
    assert response["Location"] == reverse("connect:connection_detail", args=[connection.pk])
    connection.refresh_from_db()
    assert connection.url == PC_NAME_URL
    assert Connection.objects.count() == 1


def test_detail_shows_secret_unset_with_env_line(client_logged_in, connection):
    body = client_logged_in.get(
        reverse("connect:connection_detail", args=[connection.pk])
    ).content.decode()

    assert "鍵の設定" in body and "まだ" in body
    assert f"{ENV_NAME}=" in body
    assert f"seizou_secret {ENV_NAME}" in body


def test_detail_shows_secret_set_without_value(client_logged_in, connection, monkeypatch):
    secret = signing.new_secret()
    monkeypatch.setenv(ENV_NAME, secret)
    value_part = secret.removeprefix(signing._SECRET_PREFIX)

    detail = client_logged_in.get(
        reverse("connect:connection_detail", args=[connection.pk])
    ).content.decode()
    listing = client_logged_in.get(reverse("connect:connection_list")).content.decode()

    shown = ("済み" in detail, "済み" in listing, f"{ENV_NAME}=" in detail)
    leaked = (value_part in detail, value_part in listing)
    assert shown == (True, True, False)
    assert leaked == (False, False)


def test_screens_in_catalog():
    names = {screen.url_name: screen.name for screen in all_screens()}

    assert names.get("connect:connection_create") == "取引先との接続の新規登録"
    assert names.get("connect:connection_detail") == "取引先との接続の詳細"
    assert names.get("connect:connection_update") == "取引先との接続の編集"
