"""URL の根 (ROOT_URLCONF)。

core 自身の URL (名前空間 core) と、存在する部品 (seizou.parts.PARTS) の urls を順に含める。
部品の urls は <app>/urls.py に app_name を持つ。無い部品は飛ばす。
上げたファイル (MEDIA_URL の下。図面など) は、開発機でも本番でも core.media の 1 つの口 (ログインを見る) が返す。
配り方 (Django が送るか、nginx に渡すか) は、会社の設定 SEIZOU_FILE_DELIVERY で選ぶ。
稼働の確認の口 (/health/。core.health) はログインを見ない。監視の道具と nginx が開く。
印の口 (/favicon.ico。core.media.favicon) は、ログインを見ずに、ロゴから作った印があればその絵へ、無ければ static の favicon.svg へ移す。Django の誤りの画面と、PDF や CSV を開いたタブは <link rel="icon"> を持たず、ブラウザがこの URL を取りに来るため。
"""

import re
from importlib import import_module, util

from django.conf import settings
from django.contrib.auth import views as auth_views
from django.urls import include, path, re_path

from seizou.core.guide import screen_guide
from seizou.core.health import health
from seizou.core.login_guard import LockoutAuthenticationForm
from seizou.core.manual import ManualView, manual_fragment, manual_image, manual_pdf
from seizou.core.media import favicon, protected_file
from seizou.core.search import SearchView
from seizou.core.settings_screen import CompanyUpdateView, SettingsView, SettingUpdateView, setting_clear
from seizou.core.views import HomeView, RememberLoginView
from seizou.core.views import rule_help
from seizou.parts import PARTS


def file_patterns() -> list:
    """上げたファイルを、ログインした人にだけ返す道 (/media/<path>。開発機でも本番でも同じ。core.media)。

    MEDIA_URL が空か外の URL (http://…) なら足さない (外の置き場は、その置き場が返す)。
    """
    media_url = getattr(settings, "MEDIA_URL", "") or ""
    if not media_url or "://" in media_url:
        return []
    prefix = re.escape(media_url.strip("/"))
    return [re_path(rf"^{prefix}/(?P<name>.+)$", protected_file, name="file")]


core_patterns = [
    path("", HomeView.as_view(), name="home"),
    path("search/", SearchView.as_view(), name="search"),
    path("manual/", ManualView.as_view(), name="manual"),
    path("manual/pdf/", manual_pdf, name="manual_pdf"),
    path("manual/fragment/<str:anchor>/", manual_fragment, name="manual_fragment"),
    path("manual/images/<str:name>", manual_image, name="manual_image"),
    path("rules/<str:code>/", rule_help, name="rule_help"),
    path("guide/<str:anchor>/", screen_guide, name="screen_guide"),
    path("settings/", SettingsView.as_view(), name="settings"),
    path("settings/company/", CompanyUpdateView.as_view(), name="company_update"),
    path("settings/<str:key>/edit/", SettingUpdateView.as_view(), name="setting_update"),
    path("settings/<str:key>/clear/", setting_clear, name="setting_clear"),
    path("login/", RememberLoginView.as_view(authentication_form=LockoutAuthenticationForm), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("health/", health, name="health"),
    path("favicon.ico", favicon, name="favicon"),
]
core_patterns += file_patterns()

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

