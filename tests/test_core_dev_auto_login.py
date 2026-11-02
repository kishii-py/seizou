"""開発機の自動ログイン (seizou.core.middleware.DevAutoLoginMiddleware)。

見る所: 動く条件 (DEBUG・runserver・SEIZOU_DEV_AUTO_LOGIN の 3 つ)、ログインしていない要求を有効な superuser に入れ替える事、
ログインした人には何もしない事、superuser がいなければそのまま通す事、session に書かない事。
"""

from __future__ import annotations

import sys

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import MiddlewareNotUsed
from django.test import RequestFactory

from seizou.core import middleware


@pytest.fixture
def dev(settings, monkeypatch):
    """開発機の形: DEBUG が真、runserver、会社の設定が真。"""
    settings.DEBUG = True
    settings.SEIZOU_DEV_AUTO_LOGIN = True
    monkeypatch.setattr(sys, "argv", ["manage.py", "runserver", "8001"])


def passthrough(request):
    return request.user


def anonymous_request():
    request = RequestFactory().get("/")
    request.user = AnonymousUser()
    return request


@pytest.mark.django_db
def test_anonymous_request_becomes_first_active_superuser(dev):
    model = get_user_model()
    model.objects.create_user(username="plain", password="tests-only")
    model.objects.create_superuser(username="retired", password="tests-only", is_active=False)
    first = model.objects.create_superuser(username="first", password="tests-only")
    model.objects.create_superuser(username="second", password="tests-only")

    user = middleware.DevAutoLoginMiddleware(passthrough)(anonymous_request())

    assert user == first
    assert user.is_authenticated


@pytest.mark.django_db
def test_logged_in_user_is_left_alone(dev):
    model = get_user_model()
    model.objects.create_superuser(username="admin", password="tests-only")
    plain = model.objects.create_user(username="plain", password="tests-only")
    request = RequestFactory().get("/")
    request.user = plain

    assert middleware.DevAutoLoginMiddleware(passthrough)(request) == plain


@pytest.mark.django_db
def test_without_superuser_the_request_stays_anonymous(dev):
    get_user_model().objects.create_user(username="plain", password="tests-only")

    user = middleware.DevAutoLoginMiddleware(passthrough)(anonymous_request())

    assert not user.is_authenticated


@pytest.mark.django_db
def test_does_not_write_the_session(dev, client):
    get_user_model().objects.create_superuser(username="admin", password="tests-only")

    response = client.get("/")

    # 試験の client は runserver でないので鎖には入らないが、middleware を直に通しても session は空のまま。
    request = anonymous_request()
    request.session = {}
    middleware.DevAutoLoginMiddleware(passthrough)(request)
    assert request.session == {}
    assert response.status_code in (200, 302)


@pytest.mark.parametrize(
    ("debug", "flag", "argv"),
    [
        (False, True, ["manage.py", "runserver"]),
        (True, False, ["manage.py", "runserver"]),
        (True, True, ["gunicorn", "settings.wsgi"]),
        (True, True, ["manage.py", "migrate"]),
        (True, True, ["pytest"]),
    ],
)
def test_not_used_unless_all_three_conditions_hold(settings, monkeypatch, debug, flag, argv):
    settings.DEBUG = debug
    settings.SEIZOU_DEV_AUTO_LOGIN = flag
    monkeypatch.setattr(sys, "argv", argv)
    assert middleware.dev_auto_login_enabled() is False
    with pytest.raises(MiddlewareNotUsed):
        middleware.DevAutoLoginMiddleware(passthrough)


def test_enabled_when_all_three_conditions_hold(dev):
    assert middleware.dev_auto_login_enabled() is True


def test_runserver_must_be_a_whole_argument(settings, monkeypatch):
    settings.DEBUG = True
    settings.SEIZOU_DEV_AUTO_LOGIN = True
    monkeypatch.setattr(sys, "argv", ["manage.py", "runserver_plus"])
    assert middleware.dev_auto_login_enabled() is False
