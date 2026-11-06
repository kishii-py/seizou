"""マニュアルのページ (seizou.core.manual) と、会社の置き場の manual/ の断片の約束。

断片の字は作り物。試験ごとに settings.BASE_DIR を一時の置き場に替え、その下の manual/ に書く。
"""

import re
from urllib.parse import urljoin

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core.manual import fragment_name, manual_flows
from seizou.core.screens import all_screens

ITEM_FRAGMENT = "<section><h2>部品マスター</h2><p>作り物の説明の字</p></section>"


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


def write_fragment(folder, name, text):
    (folder / f"{name}.html").write_text(text, encoding="utf-8")


def manual_page(client, **params):
    return client.get(reverse("core:manual"), params)


def toc_anchors(html: str) -> list[str]:
    """目次 (nav aria-label="目次") の中の link の飛び先の並び。"""
    nav = re.search(r'<nav aria-label="目次"[^>]*>(.*?)</nav>', html, re.S)
    assert nav, "目次の nav が無い"
    return re.findall(r'href="#([^"]*)"', nav.group(1))


def section_box(html: str, anchor: str) -> str:
    """本文の 1 画面の箱 (id="<anchor>" の div) の中身。"""
    box = re.search(rf'<div id="{re.escape(anchor)}"[^>]*>(.*?)</div>', html, re.S)
    assert box, f"{anchor} の箱が無い"
    return box.group(1)


def toc_line(html: str, anchor: str) -> str:
    nav = re.search(r'<nav aria-label="目次"[^>]*>(.*?)</nav>', html, re.S)
    line = re.search(rf'<a [^>]*href="#{re.escape(anchor)}"[^>]*>(.*?)</a>', nav.group(1), re.S)
    assert line, f"目次に {anchor} の行が無い"
    return line.group(1)


@pytest.mark.django_db
def test_manual_requires_login(client, site):
    response = manual_page(client)
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_topbar_has_manual_button(client_logged_in, site):
    html = client_logged_in.get(reverse("core:home")).content.decode()
    assert 'href="/manual/"' in html
    assert 'aria-label="使い方"' in html


def test_manual_in_screen_catalog():
    rows = [s for s in all_screens() if s.url_name == "core:manual"]
    assert len(rows) == 1
    assert rows[0].name == "使い方"
    assert rows[0].path == "/manual/"


def toc_head() -> list[str]:
    """前書きの無い置き場 (fixture の manual は空) の目次の頭の行 = 業務の流れがあれば flows (依頼書 N265 = 候補 C48 の 1 の 2)。"""
    return ["flows"] if manual_flows() else []


def test_toc_follows_screen_catalog(client_logged_in, manual):
    response = manual_page(client_logged_in)
    assert response.status_code == 200
    expected = [fragment_name(s.url_name) for s in all_screens()]
    assert toc_anchors(response.content.decode()) == [*toc_head(), *expected]
    assert "core_manual" in expected


@pytest.mark.parametrize("params", [{}, {"print": "1"}])
def test_toc_head_has_intro_and_flows(client_logged_in, manual, params):
    """使い方の画面と印刷の目次の頭は「はじめに」(前書きの箱 id="index") と「業務の流れ」(id="flows")、その後が目録の並び。

    依頼書 N265 (候補 C48 の 1 の 2・1 の 3。決め C48-2 = a)。前書きの箱は目次の nav の後・業務の流れの箱の前。
    """
    write_fragment(manual, "index", "<section><h2>作り物の前書き</h2><p>読み方の字</p></section>")
    html = manual_page(client_logged_in, **params).content.decode()
    screens = [fragment_name(s.url_name) for s in all_screens()]
    assert manual_flows()
    assert toc_anchors(html) == ["index", "flows", *screens]
    assert toc_line(html, "index") == "はじめに"
    assert toc_line(html, "flows") == "業務の流れ"
    assert html.index('<nav aria-label="目次"') < html.index('<div id="index"') < html.index('<div id="flows"')
    assert html.count('id="index"') == 1


@pytest.mark.parametrize("params", [{}, {"print": "1"}])
def test_toc_has_no_intro_line_when_intro_unreadable(client_logged_in, manual, params):
    """前書きを UTF-8 で読めないときは、目次に「はじめに」の行も id="index" の箱も出さない (依頼書 N265)。"""
    (manual / "index.html").write_bytes("<section><h2>作り物の前書き</h2></section>".encode("shift_jis"))
    html = manual_page(client_logged_in, **params).content.decode()
    assert "UTF-8 で読めませんでした" in html
    assert toc_anchors(html) == [*toc_head(), *[fragment_name(s.url_name) for s in all_screens()]]
    assert 'id="index"' not in html


def test_toc_folds_in_details_open_by_default(client_logged_in, manual):
    """目次は details で開け閉めし、HTML は開いた形で描く。狭い窓 (1024px 未満) で閉じるのは nav の中の script。

    依頼書 N256 (候補 C48 の 1)。JS の無い窓と広い画面で全部の行が見えるよう、閉じて描いて広い窓で開く形にしない。
    """
    html = manual_page(client_logged_in).content.decode()
    nav = re.search(r'<nav aria-label="目次"[^>]*>(.*?)</nav>', html, re.S).group(1)
    details = re.search(r"<details\b([^>]*)>(.*?)</details>", nav, re.S)
    assert details, "目次の details が無い"
    assert re.search(r"\sopen\b", details.group(1))
    summary = re.search(r"<summary\b[^>]*>(.*?)</summary>", details.group(2), re.S)
    assert summary, "目次の summary が無い"
    assert f"目次 ({len(all_screens())} 画面)" in summary.group(1)
    links = re.findall(r'href="#([^"]*)"', nav)
    assert links
    assert re.findall(r'href="#([^"]*)"', details.group(2)) == links
    assert 'window.matchMedia("(min-width: 64rem)")' in nav[details.end() :]


def test_fragment_is_shown(client_logged_in, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    html = manual_page(client_logged_in).content.decode()
    assert ITEM_FRAGMENT in section_box(html, "masters_item_list")
    assert "&lt;h2&gt;" not in html
    assert "(まだ)" not in toc_line(html, "masters_item_list")


def test_missing_fragment_shows_placeholder(client_logged_in, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    html = manual_page(client_logged_in).content.decode()
    box = section_box(html, "masters_partner_list")
    assert "<h2>取引先</h2>" in box
    assert "この画面の説明書はまだありません" in box
    assert "/seizou:manual" not in box
    assert "(まだ)" in toc_line(html, "masters_partner_list")


def test_index_fragment_comes_first(client_logged_in, manual):
    write_fragment(manual, "index", "<section><h2>作り物の前書き</h2><p>読み方の字</p></section>")
    html = manual_page(client_logged_in).content.decode()
    first = fragment_name(all_screens()[0].url_name)
    intro_at = html.index("作り物の前書き")
    assert intro_at < html.index(f'<div id="{first}"')
    assert intro_at > html.index('<nav aria-label="目次"')


def test_no_manual_folder_is_not_error(client_logged_in, site):
    assert not (site / "manual").exists()
    response = manual_page(client_logged_in)
    assert response.status_code == 200
    html = response.content.decode()
    assert "この会社の説明書はまだありません" in html
    assert "この画面の説明書はまだありません" in section_box(html, "core_home")


def test_unreadable_fragment_is_reported(client_logged_in, manual):
    data = "<section><h2>部品マスター</h2><p>作り物の説明の字</p></section>".encode("shift_jis")
    with pytest.raises(UnicodeDecodeError):
        data.decode("utf-8")
    (manual / "masters_item_list.html").write_bytes(data)
    response = manual_page(client_logged_in)
    assert response.status_code == 200
    box = section_box(response.content.decode(), "masters_item_list")
    assert "UTF-8 で読めませんでした" in box
    assert "/seizou:manual" in box


# 2 段目: 印刷用の 1 枚と絵の口

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"tests-only-not-a-real-picture"


def write_image(folder, name, data=PNG_BYTES):
    images = folder / "images"
    images.mkdir(exist_ok=True)
    (images / name).write_bytes(data)


def test_print_has_all_fragments_without_menu(client_logged_in, manual):
    write_fragment(manual, "masters_item_list", ITEM_FRAGMENT)
    order_fragment = "<section><h2>受注</h2><p>作り物の受注の説明</p></section>"
    write_fragment(manual, "orders_order_list", order_fragment)
    response = manual_page(client_logged_in, print="1")
    assert response.status_code == 200
    html = response.content.decode()
    assert html.lstrip().lower().startswith("<!doctype html>")
    assert html.count("<html") == 1
    assert '<html lang="ja" data-theme="light">' in html
    assert ITEM_FRAGMENT in section_box(html, "masters_item_list")
    assert order_fragment in section_box(html, "orders_order_list")
    assert "この画面の説明書はまだありません" in section_box(html, "masters_partner_list")
    assert 'id="seizou-drawer"' not in html
    assert 'aria-label="横断して検索"' not in html
    assert 'aria-label="メニュー"' not in html
    assert "<title>使い方 | " in html
    assert toc_anchors(html) == [*toc_head(), *[fragment_name(s.url_name) for s in all_screens()]]


@pytest.mark.django_db
def test_print_requires_login(client, site):
    response = manual_page(client, print="1")
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_manual_image_is_served(client_logged_in, manual):
    write_image(manual, "masters_item_list.png")
    response = client_logged_in.get("/manual/images/masters_item_list.png")
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert b"".join(response.streaming_content) == PNG_BYTES


@pytest.mark.parametrize("name", ["..", ".hidden.png", "a.svg", "a.html", "missing.png", "..a.png"])
def test_manual_image_refuses_bad_names(client_logged_in, manual, name):
    write_image(manual, ".hidden.png")
    write_image(manual, "a.svg", b"<svg></svg>")
    write_image(manual, "a.html", b"<p></p>")
    (manual / "..a.png").write_bytes(PNG_BYTES)
    response = client_logged_in.get(reverse("core:manual_image", args=[name]))
    assert response.status_code == 404


def test_manual_image_outside_folder_is_refused(client_logged_in, manual):
    """manual/images/ の外 (manual/ の直下) にある絵は、名前の形が正しくても出さない。"""
    (manual / "outside.png").write_bytes(PNG_BYTES)
    write_image(manual, "inside.png")
    try:
        (manual / "images" / "link.png").symlink_to(manual / "outside.png")
    except OSError:
        pytest.skip("この OS の利用者は symlink を作れない (Windows で権限が無いとき)")
    assert client_logged_in.get(reverse("core:manual_image", args=["inside.png"])).status_code == 200
    assert client_logged_in.get(reverse("core:manual_image", args=["link.png"])).status_code == 404


@pytest.mark.django_db
def test_manual_image_requires_login(client, manual):
    write_image(manual, "masters_item_list.png")
    response = client.get(reverse("core:manual_image", args=["masters_item_list.png"]))
    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_relative_image_path_resolves():
    expected = reverse("core:manual_image", args=["x.png"])
    page = reverse("core:manual")
    assert urljoin(page, "images/x.png") == expected
    assert urljoin(f"{page}?print=1", "images/x.png") == expected
