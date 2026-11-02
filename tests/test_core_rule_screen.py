"""決まりの検査の画面 (seizou.core.middleware) と .seizou/violations.md のテスト。

違反の結果は tests/fixtures/rules/ の違反の見本を検査して作り、起動のときの結果 (_last_result) に置く。
DEBUG を真にするテストは、BASE_DIR を一時のフォルダにする (リポジトリに .seizou/ を作らない)。
middleware は test client の最初の要求で組み立てられるので、settings を置いてから要求する。
"""

from __future__ import annotations

import os
from io import StringIO
from pathlib import Path

import pytest
from django.conf import settings as django_settings
from django.contrib.auth import get_user_model
from django.core.exceptions import MiddlewareNotUsed
from django.core.management import CommandError, call_command
from django.http import HttpResponse
from django.urls import reverse

import seizou.core.rules as rules
from seizou.core.middleware import RuleNoticeMiddleware, rule_notices
from seizou.core.models import Company
from seizou.core.rules import Result, Violation

REPO = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures" / "rules"
# R13 の違反 (画面から表に直接書く) と、R16 の違反 (行が 0 のフォルダの違反を含む)
BAD_PATHS = [FIXTURES / "r13" / "bad", FIXTURES / "r16" / "bad"]
SCAFFOLD_SETTINGS = REPO / "src" / "seizou" / "scaffold" / "settings.py.tmpl"
MIDDLEWARE_NAME = "seizou.core.middleware.RuleNoticeMiddleware"
COMPANY_NAME = "作り物の製作所"
TITLE = "決まりに沿っていません"


@pytest.fixture
def dev(settings, tmp_path):
    """開発の設定。BASE_DIR は一時のフォルダ (.seizou/violations.md の置き場)。"""
    settings.DEBUG = True
    settings.BASE_DIR = tmp_path
    return tmp_path


@pytest.fixture
def company(db):
    return Company.objects.create(name=COMPANY_NAME)


def put_result(monkeypatch, result):
    monkeypatch.setattr(rules, "_last_result", result)


def make_middleware():
    return RuleNoticeMiddleware(lambda request: HttpResponse())


def shown(html: str) -> str:
    """画面で名指しする所 (写す文の textarea より前)。"""
    return html.split("<textarea", 1)[0]


def violations(count: int) -> tuple[Violation, ...]:
    return tuple(
        Violation("R14", "SQL を直接書かない", f"site/v{i:02d}.py", i, "部品の services の関数を通してください")
        for i in range(1, count + 1)
    )


# 画面


@pytest.mark.django_db
def test_middleware_not_used_when_debug_false(settings, tmp_path, monkeypatch, client, company):
    settings.DEBUG = False
    settings.BASE_DIR = tmp_path
    put_result(monkeypatch, rules.check(BAD_PATHS))
    user = get_user_model().objects.create_user(username="tester", password="tests-only")
    client.force_login(user)

    response = client.get(reverse("core:home"))

    assert response.status_code == 200
    assert TITLE not in response.content.decode()
    with pytest.raises(MiddlewareNotUsed):
        make_middleware()
    assert not (tmp_path / ".seizou").exists()


@pytest.mark.django_db
@pytest.mark.parametrize("url_name", ["core:home", "core:login", "masters:item_list"])
def test_rule_page_on_any_url(dev, monkeypatch, client, company, url_name):
    put_result(monkeypatch, rules.check(BAD_PATHS))

    response = client.get(reverse(url_name))

    assert response.status_code == 500
    html = response.content.decode()
    for word in (TITLE, COMPANY_NAME, "R13", "R16", "views.py", "直し方", "この文を写す", "navigator.clipboard", "読み込み直す"):
        assert word in html, word


@pytest.mark.django_db
@pytest.mark.parametrize("url", ["/static/css/app.css", "/__debug__/render_panel/"])
def test_static_and_debug_paths_pass(dev, monkeypatch, client, url):
    put_result(monkeypatch, rules.check(BAD_PATHS))

    response = client.get(url)

    assert response.status_code == 404
    assert TITLE not in response.content.decode()


def test_htmx_request_gets_hx_redirect(dev, monkeypatch, client):
    put_result(monkeypatch, rules.check(BAD_PATHS))
    url = reverse("masters:item_list")

    response = client.get(url, headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert response["HX-Redirect"] == url
    assert response.content == b""


@pytest.mark.django_db
@pytest.mark.parametrize("result", [None, Result((), 0, 3)], ids=["no_result", "no_violations"])
def test_no_page_when_no_result_or_no_violations(dev, monkeypatch, client, company, result):
    put_result(monkeypatch, result)

    response = client.get(reverse("core:login"))

    assert rule_notices() is None
    assert response.status_code == 200
    assert TITLE not in response.content.decode()


# 写す文


def test_copy_text_matches_seizou_check(dev, monkeypatch):
    put_result(monkeypatch, rules.check(BAD_PATHS))
    out = StringIO()
    with pytest.raises(CommandError):
        call_command("seizou_check", *[str(p) for p in BAD_PATHS], stdout=out)

    text = rule_notices()["text"]

    count = len(rules.last_result().violations)
    first, rest = text.split("\n", 1)
    assert first == f"決まりの検査で違反が {count} 件あります。/seizou:fix で直してください。"
    assert rest == out.getvalue()
    assert rest.splitlines()[-1].startswith(f"合計: 違反 {count} 件、")


@pytest.mark.django_db
def test_only_twenty_named_but_all_copied(dev, monkeypatch, client, company):
    put_result(monkeypatch, Result(violations(21), 0, 21))

    notices = rule_notices()
    html = client.get(reverse("core:home")).content.decode()

    assert len(notices["items"]) == 20
    assert notices["more"] == 1
    assert len(notices["text"].splitlines()[1:-1]) == 21
    assert "site/v20.py:20" in shown(html)
    assert "site/v21.py" not in shown(html)
    assert "ほか 1 件" in shown(html)
    assert "R14 site/v21.py:21 SQL を直接書かない 直し方: " in html


@pytest.mark.django_db
def test_line_zero_shows_path_only(dev, monkeypatch, client, company):
    folder = "site/masters/migrations"
    put_result(monkeypatch, Result((Violation("R16", "部品の表を変えない", folder, 0, "拡張の表に足してください"),), 0, 1))

    html = client.get(reverse("core:home")).content.decode()

    assert folder in shown(html)
    assert f"{folder}:0" not in shown(html)
    assert f"R16 {folder}:0 部品の表を変えない 直し方: 拡張の表に足してください" in rule_notices()["text"]


@pytest.mark.django_db
def test_does_not_run_check_per_request(dev, monkeypatch, client, company):
    put_result(monkeypatch, rules.check(BAD_PATHS))

    def broken(paths):
        raise AssertionError("要求のたびに検査を走らせた")

    monkeypatch.setattr(rules, "check", broken)

    response = client.get(reverse("core:home"))

    assert response.status_code == 500
    assert TITLE in response.content.decode()


# .seizou/violations.md


def test_violations_md_same_as_copy_text(dev, monkeypatch):
    put_result(monkeypatch, rules.check(BAD_PATHS))

    make_middleware()

    path = dev / ".seizou" / "violations.md"
    assert path.read_bytes() == rule_notices()["text"].encode("utf-8")


def test_violations_md_not_rewritten_when_same(dev, monkeypatch):
    put_result(monkeypatch, rules.check(BAD_PATHS))
    path = dev / ".seizou" / "violations.md"
    path.parent.mkdir()
    path.write_bytes(rule_notices()["text"].encode("utf-8"))
    old = 1_000_000_000
    os.utime(path, (old, old))

    make_middleware()

    assert path.stat().st_mtime == old


def test_violations_md_removed_when_zero(dev, monkeypatch):
    path = dev / ".seizou" / "violations.md"
    path.parent.mkdir()
    path.write_text("前の違反\n", encoding="utf-8")

    put_result(monkeypatch, None)
    make_middleware()
    assert path.read_text(encoding="utf-8") == "前の違反\n"

    put_result(monkeypatch, Result((), 0, 3))
    make_middleware()
    assert not path.exists()


# settings


def test_middleware_line_in_settings():
    assert django_settings.MIDDLEWARE[-1] == MIDDLEWARE_NAME
    assert f'"{MIDDLEWARE_NAME}",' in SCAFFOLD_SETTINGS.read_text(encoding="utf-8")
