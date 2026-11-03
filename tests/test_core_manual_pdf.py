"""使い方の冊子 (seizou.core.manual の booklet_context・render_booklet_pdf・manual_pdf の口と、seizou_manual_pdf の命令)。

本物の WeasyPrint は pdf の印の 1 本だけが呼ぶ。ほかは reports.render_pdf を差し替える。
断片の字は作り物。試験ごとに settings.BASE_DIR を一時の置き場に替え、その下の manual/ に書く
(fixture と道具は tests/test_core_manual.py の形を写した。既にあるテストは import しない)。
"""

import os
import re
import time
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from seizou.core import manual as core_manual
from seizou.core import reports, services
from seizou.core.manual import fragment_name
from seizou.core.screens import all_screens

ITEM_FRAGMENT = (
    "<section><h2>部品マスター</h2><p>作り物の説明の字</p>"
    '<img src="images/masters_item_list.png" alt="部品マスター"></section>'
)
ORDER_FRAGMENT = "<section><h2>受注</h2><p>作り物の受注の説明</p></section>"
INTRO_FRAGMENT = "<section><h2>作り物の前書き</h2><p>読み方の字</p></section>"
FAKE_PDF = b"%PDF-1.4 test"
UNAVAILABLE = "PDF の部品 (WeasyPrint) が使えません。印刷用の画面をお使いください。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(username="tester", password="tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def site(settings, tmp_path):
    """会社の置き場 (一時の置き場)。manual/ はまだ作らない。"""
    settings.BASE_DIR = tmp_path
    return tmp_path


@pytest.fixture
def manual(site):
    """会社の置き場の manual/ (空)。"""
    folder = site / "manual"
    folder.mkdir()
    return folder


@pytest.fixture
def fake_pdf(monkeypatch):
    """reports.render_pdf を、作り物の bytes を返す物に差し替える。受けた template と context を覚える。"""
    seen = {}

    def fake_render_pdf(template, context):
        seen["template"] = template
        seen["context"] = context
        return FAKE_PDF

    monkeypatch.setattr(reports, "render_pdf", fake_render_pdf)
    return seen


@pytest.fixture
def no_pdf(monkeypatch):
    """reports.render_pdf を、PdfUnavailable を投げる物に差し替える (WeasyPrint が無い所と同じ)。"""

    def unavailable(template, context):
        raise reports.PdfUnavailable(UNAVAILABLE)

    monkeypatch.setattr(reports, "render_pdf", unavailable)


def write_fragment(folder, name, text):
    (folder / f"{name}.html").write_text(text, encoding="utf-8")


def screen_anchors() -> list[str]:
    return [fragment_name(s.url_name) for s in all_screens()]


def booklet_html() -> str:
    return reports.render_html(core_manual.BOOKLET_TEMPLATE, core_manual.booklet_context())


def toc_anchors(html: str) -> list[str]:
    """冊子の目次 (nav class="booklet-toc") の中の link の飛び先の並び。"""
    nav = re.search(r'<nav class="booklet-toc"[^>]*>(.*?)</nav>', html, re.S)
    assert nav, "目次の nav が無い"
    return re.findall(r'href="#([^"]*)"', nav.group(1))


def chapter(html: str, anchor: str) -> str:
    """冊子の 1 章 (id="<anchor>" の div) の中身。"""
    box = re.search(rf'<div id="{re.escape(anchor)}" class="booklet-chapter">(.*?)</div>', html, re.S)
    assert box, f"{anchor} の章が無い"
    return box.group(1)


def cover_meta(html: str) -> str:
    meta = re.search(r'<p class="booklet-meta">(.*?)</p>', html, re.S)
    assert meta, "表紙の版と日付の行が無い"
    return meta.group(1)


def japanese_date(value) -> str:
    return f"{value.year}年{value.month}月{value.day}日"


# 1 段目: 冊子の材料・テンプレート・口・ボタン


@pytest.mark.django_db
def test_pdf_requires_login(client, site, fake_pdf):
    assert reverse("core:manual_pdf") == "/manual/pdf/"
    response = client.get(reverse("core:manual_pdf"))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]
    assert "template" not in fake_pdf


def test_pdf_returns_pdf_when_available(client_logged_in, manual, fake_pdf):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    response = client_logged_in.get(reverse("core:manual_pdf"))
    assert response.status_code == 200
    assert response["Content-Type"] == "application/pdf"
    assert response["Content-Disposition"] == 'inline; filename="seizou-manual.pdf"'
    assert "no-store" in response["Cache-Control"]
    assert response.content == FAKE_PDF
    assert fake_pdf["template"] == "core/manual_booklet.html"
    context = fake_pdf["context"]
    assert [s["anchor"] for s in context["sections"]] == screen_anchors()
    assert isinstance(context["company_name"], str)
    assert context["company_name"] == services.company().name


def test_pdf_falls_back_to_html_when_unavailable(client_logged_in, manual, no_pdf):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    response = client_logged_in.get(reverse("core:manual_pdf"))
    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/html")
    html = response.content.decode()
    assert html.lstrip().startswith("<!doctype html>")
    assert '<html lang="ja">' in html
    assert "PDF の準備がありません" in html
    assert 'role="alert"' in html
    assert "<h1>使い方</h1>" in html
    assert escape(services.company().name) in html
    assert ITEM_FRAGMENT in chapter(html, "masters_item_list")
    assert "&lt;h2&gt;" not in html
    assert "この画面の説明書はまだありません" in chapter(html, "masters_partner_list")
    assert 'id="seizou-drawer"' not in html
    assert 'aria-label="メニュー"' not in html
    assert "app.css" not in html


def test_booklet_has_cover_toc_and_chapters(db, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    html = booklet_html()
    assert 'class="booklet-cover"' in html
    assert f'<p class="booklet-company">{escape(services.company().name)}</p>' in html
    assert "<h1>使い方</h1>" in html
    assert '<p class="booklet-system">seizou</p>' in html
    version = core_manual.package_version()
    assert version
    meta = cover_meta(html)
    assert f"版 {version}" in meta
    assert f"説明書を作った日 {japanese_date(timezone.localdate())}" in meta
    assert toc_anchors(html) == screen_anchors()
    for anchor in screen_anchors():
        assert html.count(f'<div id="{anchor}" class="booklet-chapter">') == 1
    assert html.count('class="booklet-chapter"') == len(all_screens())
    style = re.search(r"<style>(.*?)</style>", html, re.S)
    assert style, "冊子の style が無い"
    for word in (
        "target-counter(attr(href), page)",
        "counter(page)",
        "counter(pages)",
        "string-set",
        "string(booklet-company)",
        "break-before: page",
        "img { display: none; }",
    ):
        assert word in style.group(1), word
    assert 'href="/static/css/report.css"' in html
    assert "csrf" not in html.lower()


def test_fragment_placeholder_and_unreadable(db, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    data = ORDER_FRAGMENT.encode("shift_jis")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    (manual / "orders_order_list.html").write_bytes(data)
    html = booklet_html()
    item = chapter(html, "masters_item_list")
    assert ITEM_FRAGMENT in item
    assert item.count("<h2>") == 1
    empty = chapter(html, "masters_partner_list")
    assert "<h2>取引先</h2>" in empty
    assert "この画面の説明書はまだありません" in empty
    unreadable = chapter(html, "orders_order_list")
    assert "UTF-8 で読めませんでした" in unreadable
    assert "/seizou:manual" in unreadable


def test_cover_without_version_and_date(db, site, monkeypatch):
    def missing(name):
        raise core_manual.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(core_manual.metadata, "version", missing)
    assert core_manual.package_version() == ""

    # manual/ が無い: 表紙・目次・「まだありません」の章の冊子を描く
    context = core_manual.booklet_context()
    assert context["has_manual"] is False
    assert context["manual_date"] is None
    html = reports.render_html(core_manual.BOOKLET_TEMPLATE, context)
    assert cover_meta(html) == ""
    assert "説明書を作った日" not in html
    assert "この画面の説明書はまだありません" in chapter(html, "core_home")

    # manual/ が空 (断片 0): 日付は出さない
    (site / "manual").mkdir()
    assert core_manual.manual_date() is None
    context = core_manual.booklet_context()
    assert context["has_manual"] is True
    html = reports.render_html(core_manual.BOOKLET_TEMPLATE, context)
    assert cover_meta(html) == ""


def test_manual_date_is_newest_fragment(manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    write_fragment(manual, "orders_order_list", ORDER_FRAGMENT)
    three_days_ago = time.time() - 3 * 24 * 60 * 60
    today = timezone.localdate()
    os.utime(manual / "orders_order_list.html", (three_days_ago, three_days_ago))
    assert core_manual.manual_date() == today
    os.utime(manual / "masters_item_list.html", (three_days_ago, three_days_ago))
    assert core_manual.manual_date() == today - timedelta(days=3)


def test_intro_is_first_chapter(db, manual):
    html = booklet_html()
    assert "index" not in toc_anchors(html)
    assert 'id="index"' not in html

    write_fragment(manual, "index", INTRO_FRAGMENT)
    html = booklet_html()
    assert toc_anchors(html) == ["index", *screen_anchors()]
    assert '<a href="#index">はじめに</a>' in html
    first = screen_anchors()[0]
    intro_at = html.index('<div id="index" class="booklet-chapter">')
    assert intro_at < html.index(f'<div id="{first}" class="booklet-chapter">')
    assert intro_at > html.index('<nav class="booklet-toc"')
    assert INTRO_FRAGMENT in chapter(html, "index")


def test_manual_screen_has_pdf_button(client_logged_in, manual):
    html = client_logged_in.get(reverse("core:manual")).content.decode()
    button = re.search(r'<a [^>]*href="/manual/pdf/"[^>]*>.*?</a>', html, re.S)
    assert button, "「PDF」のボタンが無い"
    assert 'target="_blank"' in button.group(0)
    assert "PDF" in button.group(0)
    assert "btn-primary" not in button.group(0)
    assert "印刷用を開く" in html


def test_pdf_is_not_a_screen():
    assert not [s for s in all_screens() if s.url_name == "core:manual_pdf"]


@pytest.mark.pdf
def test_booklet_pdf_with_weasyprint(db, manual, caplog):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    write_fragment(manual, "orders_order_list", ORDER_FRAGMENT)
    data = core_manual.render_booklet_pdf()
    assert data.startswith(b"%PDF")
    assert b"NotoSansJP" in data
    # 絵は display: none で取りに行かない (取りに行くと WeasyPrint の記録に断りが出る)
    assert "Failed to load image" not in caplog.text

    document = reports._render_document(core_manual.BOOKLET_TEMPLATE, core_manual.booklet_context())
    # 表紙 1・目次 1 以上・章は 1 画面 1 ページ以上
    assert len(document.pages) >= len(all_screens()) + 2
    page = document.pages[0]
    # A4 縦 = 210mm × 297mm (WeasyPrint の長さは CSS の px。1mm = 96 / 25.4 px)
    assert round(page.width * 25.4 / 96) == 210
    assert round(page.height * 25.4 / 96) == 297
