"""静的なファイルの URL の中身の印 (core の seizou_static の tag。N442)。

tag が Django の static の URL の後ろに `?v=<中身の SHA-256 の頭 10 字>` を付けること、`as 変数` の形も同じこと、
見つからないファイルは印を付けずに Django の URL のまま返すこと、ファイルを直すと印が変わること、控えを使うことを見る。
src/seizou/ の下のテンプレート (scaffold/ を除く) が Django の static の library を load していないことも見る
(新しいテンプレートが印の無い URL を書かないための見張り)。
ファイルを直す試験は一時のフォルダ (tmp_path) を STATICFILES_DIRS に足して、作り物の JS で確かめる。
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.template import Context, Template
from django.templatetags.static import static as django_static
from django.urls import reverse

import seizou
from seizou.core import reports

SEIZOU_DIR = Path(seizou.__file__).resolve().parent
LOAD = re.compile(r"\{%\s*load\s+(.*?)\s*%\}", re.S)
STATIC_TAG = re.compile(r"\{%\s*static\s")
SCRIPT_SRC = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")
STYLESHEET = re.compile(r"<link rel=\"stylesheet\" href=\"([^\"]+)\"[^>]*>")
STAMPED = re.compile(r"/static/[^?\"]+\?v=[0-9a-f]{10}")


def _render(text: str, **context) -> str:
    return Template(text).render(Context(context))


def _expected_stamp(file_path) -> str:
    return hashlib.sha256(Path(file_path).read_bytes()).hexdigest()[:10]


def _stamp_lib():
    from seizou.core.templatetags import seizou_static

    return seizou_static


@pytest.fixture
def static_dir(settings, tmp_path):
    """作り物の JS を置く一時のフォルダを STATICFILES_DIRS に足す (finders の控えは setting_changed で作り直される)。"""
    folder = tmp_path / "static"
    folder.mkdir()
    settings.STATICFILES_DIRS = [folder]
    return folder


def test_tag_adds_content_stamp_of_found_file():
    url = _render("{% load seizou_static %}{% static 'css/app.css' %}")

    found = finders.find("css/app.css")
    assert found
    assert re.fullmatch(r"/static/css/app\.css\?v=[0-9a-f]{10}", url)
    assert url == f"/static/css/app.css?v={_expected_stamp(found)}"
    assert url == _stamp_lib().stamped_static("css/app.css")


def test_as_variable_gives_same_url():
    direct = _render("{% load seizou_static %}{% static 'css/app.css' %}")
    stored = _render("{% load seizou_static %}{% static 'css/app.css' as css %}[{{ css }}]")

    assert stored == f"[{direct}]"
    assert "?v=" in direct


def test_variable_path_is_stamped_too():
    url = _render("{% load seizou_static %}{% static path %}", path="js/seizou-modal.js")

    assert url == f"/static/js/seizou-modal.js?v={_expected_stamp(finders.find('js/seizou-modal.js'))}"


@pytest.mark.parametrize("path", ["js/n442-no-such-file.js", "../pyproject.toml", "js/"])
def test_missing_file_keeps_django_url(path):
    """見つからない・根の外を指す・ファイルでない (フォルダ) 時は、例外を出さずに Django の URL のまま。"""
    url = _render("{% load seizou_static %}{% static path %}", path=path)

    assert url == django_static(path)
    assert "?v=" not in url


def test_query_in_django_url_gets_ampersand(monkeypatch):
    """Django の URL に既に ? があれば、印は &v= で付ける。"""
    from django.contrib.staticfiles.storage import staticfiles_storage

    monkeypatch.setattr(staticfiles_storage, "url", lambda name: f"/static/{name}?from=storage")
    url = _stamp_lib().stamped_static("css/app.css")

    assert url == f"/static/css/app.css?from=storage&v={_expected_stamp(finders.find('css/app.css'))}"


def test_stamp_follows_file_changes(static_dir):
    """中身を直すと印が変わる。更新時刻が同じ秒の中でも、大きさか時刻が変われば作り直す。"""
    target = static_dir / "n442-sample.js"
    base_ns = 1_700_000_000 * 10**9
    tag = "{% load seizou_static %}{% static 'n442-sample.js' %}"

    target.write_text("var a = 1;\n", encoding="utf-8")
    os.utime(target, ns=(base_ns, base_ns))
    first = _render(tag)
    assert first == f"/static/n442-sample.js?v={_expected_stamp(target)}"

    # 更新時刻は ns まで同じで、大きさだけが変わる
    target.write_text("var a = 12;\n", encoding="utf-8")
    os.utime(target, ns=(base_ns, base_ns))
    second = _render(tag)
    assert second != first
    assert second == f"/static/n442-sample.js?v={_expected_stamp(target)}"

    # 大きさは同じで、更新時刻が同じ秒の中で変わる
    target.write_text("var a = 34;\n", encoding="utf-8")
    os.utime(target, ns=(base_ns + 500_000_000, base_ns + 500_000_000))
    assert target.stat().st_mtime_ns == base_ns + 500_000_000
    third = _render(tag)
    assert third not in (first, second)
    assert third == f"/static/n442-sample.js?v={_expected_stamp(target)}"


def test_same_file_is_read_once(static_dir, monkeypatch):
    """同じファイルを 2 回出しても、中身を読むのは 1 回 (控えを使う)。"""
    lib = _stamp_lib()
    (static_dir / "n442-once.js").write_text("var once = 1;\n", encoding="utf-8")
    calls = []
    original = lib._read_stamp

    def counting(file_path):
        calls.append(file_path)
        return original(file_path)

    monkeypatch.setattr(lib, "_read_stamp", counting)
    tag = "{% load seizou_static %}{% static 'n442-once.js' %}"

    first = _render(tag)
    second = _render(tag)

    assert first == second
    assert "?v=" in first
    assert len(calls) == 1


def test_report_fetcher_reads_stamped_css():
    """帳票の PDF の取り口 (core.reports.fetch_local) は、印の付いた URL の CSS も読む (? の後ろはファイルの探し方に関わらない)。"""
    url = _stamp_lib().stamped_static("css/report.css")
    assert "?v=" in url

    body, content_type = reports.fetch_local(reports.BASE_URL + url.lstrip("/"))

    assert body == Path(finders.find("css/report.css")).read_bytes()
    assert content_type == "text/css"


def _templates():
    for path in sorted(SEIZOU_DIR.rglob("*.html")):
        if path.relative_to(SEIZOU_DIR).parts[0] == "scaffold":
            continue
        yield path


def _loaded_libraries(text: str) -> set[str]:
    names = set()
    for found in LOAD.finditer(text):
        names.update(found.group(1).split())
    return names


def test_no_template_loads_django_static_library():
    """src/seizou/ の下のテンプレート (scaffold/ を除く) は Django の static を load せず、{% static を使う物は seizou_static を load する。"""
    django_static_loads = []
    missing_stamp = []
    users = 0
    for path in _templates():
        text = path.read_text(encoding="utf-8")
        libraries = _loaded_libraries(text)
        name = str(path.relative_to(SEIZOU_DIR))
        if "static" in libraries:
            django_static_loads.append(name)
        if STATIC_TAG.search(text):
            users += 1
            if "seizou_static" not in libraries:
                missing_stamp.append(name)

    assert django_static_loads == []
    assert missing_stamp == []
    assert users >= 26


def _assert_all_stamped(body: str) -> None:
    scripts = SCRIPT_SRC.findall(body)
    sheets = [href for href in STYLESHEET.findall(body) if "app.css" in href]
    assert scripts
    assert len(sheets) == 1
    for url in [*scripts, *sheets]:
        assert STAMPED.fullmatch(url), url


@pytest.mark.django_db
def test_login_page_urls_are_stamped(client):
    body = client.get(reverse("core:login")).content.decode()

    _assert_all_stamped(body)


@pytest.mark.django_db
def test_dashboard_urls_are_stamped(client):
    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    client.force_login(user)

    body = client.get(reverse("core:home")).content.decode()

    _assert_all_stamped(body)
