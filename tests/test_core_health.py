"""稼働の確認の口 (seizou.core.health。/health/)。

ログインなしで 200 と {"ok": true}・DB に行かない事・GET と HEAD の外は 405・画面の目録に出ない事を見る。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core.screens import all_screens

URL = "/health/"


@pytest.mark.django_db
def test_health_answers_ok_without_login_and_db(client, django_assert_num_queries):
    assert reverse("core:health") == URL

    with django_assert_num_queries(0):
        response = client.get(URL)

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert response["Content-Type"].startswith("application/json")
    cache_control = response["Cache-Control"]
    assert "no-cache" in cache_control or "max-age=0" in cache_control


@pytest.mark.django_db
def test_health_head_and_other_methods(client):
    assert client.head(URL).status_code == 200
    assert client.post(URL).status_code == 405
    assert client.put(URL).status_code == 405


@pytest.mark.django_db
def test_health_is_the_same_for_logged_in_and_htmx(client):
    anonymous = client.get(URL)
    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    client.force_login(user)
    logged_in = client.get(URL)
    client.logout()
    htmx = client.get(URL, HTTP_HX_REQUEST="true")

    for response in (logged_in, htmx):
        assert response.status_code == 200
        assert response.content == anonymous.content
        assert response.json() == {"ok": True}


def test_health_is_not_a_screen():
    names = {screen.url_name for screen in all_screens()}
    paths = {screen.path for screen in all_screens()}

    assert names
    assert "core:health" not in names
    assert URL not in paths
