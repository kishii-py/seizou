"""htmx の要求のログインの切れ (seizou.core.middleware.HtmxLoginMiddleware)。

試験の settings には入れていないので、試験ごとに MIDDLEWARE に足す。
401 と HX-Redirect・戻る先・替えない応答・ひな形の 1 行を見る。
"""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.views import redirect_to_login
from django.http import HttpResponse, HttpResponseRedirect
from django.test import RequestFactory
from django.urls import reverse

import seizou.scaffold
from seizou.core import conf
from seizou.core.middleware import HtmxLoginMiddleware

MIDDLEWARE_PATH = "seizou.core.middleware.HtmxLoginMiddleware"


@pytest.fixture
def htmx_login(settings):
    settings.MIDDLEWARE = [*settings.MIDDLEWARE, MIDDLEWARE_PATH]


def next_of(hx_redirect) -> tuple[str, list[str]]:
    parts = urlsplit(hx_redirect)
    return parts.path, parse_qs(parts.query).get("next", [])


def clearable_key() -> str:
    return next(key for key in conf.DEFAULTS if key not in conf.LOCKED_KEYS)


@pytest.mark.django_db
def test_screen_partial_gets_401_and_whole_page_login(client, htmx_login):
    response = client.get(
        reverse("core:settings"),
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL="http://testserver/orders/?q=WK",
    )

    assert response.status_code == 401
    assert "Location" not in response
    path, back = next_of(response["HX-Redirect"])
    assert path == reverse("core:login")
    assert back == ["/orders/?q=WK"]
    assert "ログインが切れました" in response.content.decode()


@pytest.mark.django_db
def test_function_views_get_and_post_too(client, htmx_login):
    fragment = client.get(
        reverse("core:manual_fragment", args=["masters_item_list"]),
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL="http://testserver/masters/items/",
    )
    clear = client.post(
        reverse("core:setting_clear", args=[clearable_key()]),
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL="http://testserver/settings/",
    )

    assert fragment.status_code == 401
    assert next_of(fragment["HX-Redirect"]) == (reverse("core:login"), ["/masters/items/"])
    assert clear.status_code == 401
    assert next_of(clear["HX-Redirect"]) == (reverse("core:login"), ["/settings/"])


@pytest.mark.django_db
def test_without_htmx_header_stays_302(client, htmx_login):
    response = client.get(reverse("core:settings"))

    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))
    assert "HX-Redirect" not in response


@pytest.mark.django_db
def test_missing_or_foreign_current_url_keeps_location(client, htmx_login):
    url = reverse("core:settings")
    plain = client.get(url)["Location"]
    assert plain == "/login/?next=/settings/"

    responses = [
        client.get(url, HTTP_HX_REQUEST="true"),
        client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="https://other.example/x/"),
        client.get(url, HTTP_HX_REQUEST="true", HTTP_HX_CURRENT_URL="//other.example/x/"),
    ]

    for response in responses:
        assert response.status_code == 401
        assert response["HX-Redirect"] == plain


@pytest.mark.django_db
def test_logged_in_and_other_redirects_are_untouched(client, htmx_login):
    admin = get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")
    client.force_login(admin)

    screen = client.get(reverse("core:settings"), HTTP_HX_REQUEST="true")
    clear = client.post(
        reverse("core:setting_clear", args=[clearable_key()]),
        HTTP_HX_REQUEST="true",
        HTTP_HX_CURRENT_URL="http://testserver/settings/",
    )

    assert screen.status_code == 200
    assert clear.status_code == 302
    assert not clear["Location"].startswith(reverse("core:login"))
    assert "HX-Redirect" not in clear


def test_middleware_alone_with_request_factory():
    request = RequestFactory().get("/x/", HTTP_HX_REQUEST="true")

    to_login = HtmxLoginMiddleware(lambda req: redirect_to_login("/x/"))(request)
    elsewhere = HtmxLoginMiddleware(lambda req: HttpResponseRedirect("/elsewhere/"))(request)
    ok = HtmxLoginMiddleware(lambda req: HttpResponse("ok"))(request)

    assert to_login.status_code == 401
    assert to_login["HX-Redirect"] == "/login/?next=/x/"
    assert elsewhere.status_code == 302
    assert elsewhere["Location"] == "/elsewhere/"
    assert "HX-Redirect" not in elsewhere
    assert ok.status_code == 200
    assert ok.content == b"ok"


def test_scaffold_puts_middleware_after_authentication():
    lines = (Path(seizou.scaffold.__file__).parent / "settings.py.tmpl").read_text(encoding="utf-8").splitlines()
    stripped = [line.strip() for line in lines]

    assert stripped.count(f'"{MIDDLEWARE_PATH}",') == 1
    auth = stripped.index('"django.contrib.auth.middleware.AuthenticationMiddleware",')
    assert stripped.index(f'"{MIDDLEWARE_PATH}",') > auth
