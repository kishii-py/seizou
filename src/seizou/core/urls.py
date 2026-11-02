"""URL の根 (ROOT_URLCONF)。

core 自身の URL (名前空間 core) と、存在する部品 (seizou.parts.PARTS) の urls を順に含める。
部品の urls は <app>/urls.py に app_name を持つ。無い部品は飛ばす。
"""

from importlib import import_module, util

from django.contrib.auth import views as auth_views
from django.urls import include, path

from seizou.core.views import HomeView, RememberLoginView
from seizou.parts import PARTS

core_patterns = [
    path("", HomeView.as_view(), name="home"),
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
