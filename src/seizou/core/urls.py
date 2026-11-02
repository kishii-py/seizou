"""URL の根 (ROOT_URLCONF)。

core 自身の URL (名前空間 core) と、存在する部品 (seizou.parts.PARTS) の urls を順に含める。
部品の urls は <app>/urls.py に app_name を持つ。無い部品は飛ばす。
開発 (DEBUG か runserver) のときだけ、上げたファイル (MEDIA_URL。図面など) を MEDIA_ROOT から返す道を足す。
本番は web サーバーが返す (ここは足さない)。
"""

import re
import sys
from importlib import import_module, util

from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path
from django.views.static import serve

from seizou.core.search import SearchView
from seizou.core.views import HomeView, RememberLoginView
from seizou.parts import PARTS

core_patterns = [
    path("", HomeView.as_view(), name="home"),
    path("search/", SearchView.as_view(), name="search"),
    path("login/", RememberLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
]

urlpatterns = [
    path("", include((core_patterns, "core"))),
]

for _part in PARTS:
    if _part == "seizou.core":
        continue
    _name = f"{_part}.urls"
    if util.find_spec(_name) is None:
        continue
    _module = import_module(_name)
    _prefix = _part.rsplit(".", 1)[-1]
    urlpatterns.append(path(f"{_prefix}/", include(_module)))


def is_runserver() -> bool:
    """開発のサーバー (manage.py runserver) で動いているか。"""
    return len(sys.argv) > 1 and sys.argv[1] == "runserver"


def media_patterns() -> list:
    """上げたファイルを MEDIA_ROOT から返す道。DEBUG のときと runserver のときだけ (本番は web サーバーの仕事)。

    DEBUG のときは Django の static() を使う。static() は DEBUG が偽だと何も返さないので、
    DEBUG が偽の runserver では同じ形の道を自分で作る。MEDIA_URL が空か外の URL (http://…) なら足さない。
    """
    media_url = getattr(settings, "MEDIA_URL", "") or ""
    media_root = getattr(settings, "MEDIA_ROOT", "") or ""
    if not media_url or not media_root or "://" in media_url:
        return []
    if settings.DEBUG:
        return static(media_url, document_root=media_root)
    if is_runserver():
        prefix = re.escape(media_url.lstrip("/"))
        return [re_path(rf"^{prefix}(?P<path>.*)$", serve, kwargs={"document_root": media_root})]
    return []


urlpatterns += media_patterns()
