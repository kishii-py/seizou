"""つながりの登録と編集 (connect.services の register_connection・update_connection) のテスト。

同じ取引先と向きの 2 行目を断ること、URL の確かめ (点の無い PC の名前を通す) と、編集の関数を見る。
鍵の値は使わない (名前だけ)。取引先は作り物。
"""

import pytest
from django.contrib.auth import get_user_model

from seizou.connect import services
from seizou.connect.models import Connection
from seizou.masters.services import create_partner

ENV_NAME = "SEIZOU_PARTNER_REGCO_SECRET"
OTHER_ENV_NAME = "SEIZOU_PARTNER_REGCO_OTHER_SECRET"
LOCALHOST_URL = "http://localhost:8002/connect/receive/order/"
PC_NAME_URL = "http://officepc:8002/connect/receive/order/"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def partner(user):
    return create_partner({"code": "RG01", "name": "作り物の取引先", "role": "both"}, user)


@pytest.fixture
def other_partner(user):
    return create_partner({"code": "RG02", "name": "作り物の協力企業", "role": "subcontractor"}, user)


def test_second_same_partner_and_direction_is_refused(partner, user):
    services.register_connection(partner, "", ENV_NAME, "receive", user)

    with pytest.raises(ValueError, match="既にあります"):
        services.register_connection(partner, "", OTHER_ENV_NAME, "receive", user)

    assert Connection.objects.filter(partner=partner, direction="receive").count() == 1


def test_other_direction_is_allowed(partner, user):
    received = services.register_connection(partner, "", ENV_NAME, "receive", user)
    sent = services.register_connection(partner, PC_NAME_URL, ENV_NAME, "send", user)

    assert (received.direction, sent.direction) == ("receive", "send")
    assert sorted(partner.connections.values_list("direction", flat=True)) == ["receive", "send"]


def test_dotless_hosts_are_accepted(partner, other_partner, user):
    local = services.register_connection(partner, LOCALHOST_URL, ENV_NAME, "send", user)
    pc = services.register_connection(other_partner, PC_NAME_URL, OTHER_ENV_NAME, "send", user)

    local.refresh_from_db()
    pc.refresh_from_db()
    assert local.url == LOCALHOST_URL
    assert pc.url == PC_NAME_URL


def test_bad_urls_are_refused(partner, user):
    for url in (
        "not a url",
        "ftp://officepc/x",
        "http://",
        "officepc:8002/connect/receive/order/",
        "http://office pc:8002/",
    ):
        with pytest.raises(ValueError, match="URL の形ではありません"):
            services.register_connection(partner, url, ENV_NAME, "send", user)
    assert Connection.objects.count() == 0


def test_update_connection_changes_values(partner, user):
    connection = services.register_connection(partner, "", ENV_NAME, "receive", user)

    updated = services.update_connection(
        connection, partner, PC_NAME_URL, OTHER_ENV_NAME, "send", user
    )

    assert isinstance(updated, Connection)
    assert updated.pk == connection.pk
    connection.refresh_from_db()
    assert (connection.partner, connection.url, connection.secret_env, connection.direction) == (
        partner,
        PC_NAME_URL,
        OTHER_ENV_NAME,
        "send",
    )
    assert Connection.objects.count() == 1


def test_update_connection_refuses_duplicate_but_allows_itself(partner, user):
    received = services.register_connection(partner, "", ENV_NAME, "receive", user)
    sent = services.register_connection(partner, PC_NAME_URL, OTHER_ENV_NAME, "send", user)

    with pytest.raises(ValueError, match="既にあります"):
        services.update_connection(sent, partner, "", OTHER_ENV_NAME, "receive", user)
    sent.refresh_from_db()
    assert (sent.direction, sent.url) == ("send", PC_NAME_URL)

    same = services.update_connection(received, partner, "", ENV_NAME, "receive", user)
    assert same.pk == received.pk
    assert Connection.objects.count() == 2


def test_update_connection_checks_env_name_and_url(partner, user):
    connection = services.register_connection(partner, LOCALHOST_URL, ENV_NAME, "send", user)

    with pytest.raises(ValueError, match="鍵の置き場の名前"):
        services.update_connection(
            connection, partner, LOCALHOST_URL, "seizou_partner_regco_secret", "send", user
        )
    with pytest.raises(ValueError, match="URL の形ではありません"):
        services.update_connection(connection, partner, "not a url", ENV_NAME, "send", user)

    connection.refresh_from_db()
    assert (connection.url, connection.secret_env, connection.direction) == (
        LOCALHOST_URL,
        ENV_NAME,
        "send",
    )
