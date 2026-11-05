"""使い方の自動の付け足し (依頼書 N168): 絵・入れる物の表・止まるときの文。

断片の字は作り物。試験ごとに settings.BASE_DIR を一時の置き場に替え、その下の manual/ に断片と絵を書く。置き場のファイルは直さない。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.core import manual, reports
from tests.test_core_manual import section_box
from tests.test_wording import REFUSAL_HEADS

ITEM_LIST = "<section><h2>部品マスター</h2><p>作り物の説明の字</p></section>"
PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def client_logged_in(client, db):
    client.force_login(get_user_model().objects.create_user(username="tester", password="tests-only"))
    return client


@pytest.fixture
def folder(settings, tmp_path):
    settings.BASE_DIR = tmp_path
    (tmp_path / "manual" / "images").mkdir(parents=True)
    return tmp_path / "manual"


def write(folder, name, text):
    (folder / f"{name}.html").write_text(text, encoding="utf-8")


def page_box(client, anchor, **params):
    response = client.get(reverse("core:manual"), params)
    assert response.status_code == 200
    return section_box(response.content.decode(), anchor)


def test_shot_is_added_to_page_print_and_booklet(client_logged_in, folder):
    write(folder, "masters_item_list", ITEM_LIST)
    (folder / "images" / "masters_item_list.png").write_bytes(PNG)
    for box in (page_box(client_logged_in, "masters_item_list"), page_box(client_logged_in, "masters_item_list", print="1")):
        assert ITEM_LIST in box
        assert '<figure class="seizou-manual-shot"><img src="images/masters_item_list.png" alt="' in box
    booklet = {section["anchor"]: section for section in manual.booklet_context()["sections"]}
    assert 'src="images/masters_item_list.png"' in booklet["masters_item_list"]["html"]
    assert (folder / "masters_item_list.html").read_text(encoding="utf-8") == ITEM_LIST


def test_shot_is_added_to_modal_with_absolute_path(client_logged_in, folder):
    write(folder, "masters_item_list", ITEM_LIST)
    (folder / "images" / "masters_item_list.png").write_bytes(PNG)
    url = reverse("core:manual_fragment", args=["masters_item_list"])
    html = client_logged_in.get(url, HTTP_HX_REQUEST="true").content.decode()
    assert f'src="{reverse("core:manual")}images/masters_item_list.png"' in html
    assert "seizou-manual-shot" in html


def test_no_shot_without_file_or_with_own_img(client_logged_in, folder):
    write(folder, "masters_item_list", ITEM_LIST)
    assert "seizou-manual-shot" not in page_box(client_logged_in, "masters_item_list")
    own = '<section><h2>部品マスター</h2><img src="images/masters_item_list.png" alt="作り物"></section>'
    write(folder, "masters_item_list", own)
    (folder / "images" / "masters_item_list.png").write_bytes(PNG)
    box = page_box(client_logged_in, "masters_item_list")
    assert own in box
    assert "seizou-manual-shot" not in box


def test_input_table_on_order_create(client_logged_in, folder):
    write(folder, "orders_order_create", "<section><h2>受注の新規登録</h2><p>作り物の説明の字</p></section>")
    box = page_box(client_logged_in, "orders_order_create")
    assert "<h3>入れる物</h3><table><thead><tr><th>欄の名前</th><th>必須</th><th>説明</th></tr></thead>" in box
    assert "<tr><td>得意先</td><td>必須</td><td></td></tr>" in box
    assert "<tr><td>備考</td><td></td><td></td></tr>" in box


def test_input_table_on_company_update_and_not_twice(client_logged_in, folder):
    write(folder, "core_company_update", "<section><h2>会社の情報</h2><p>作り物の説明の字</p></section>")
    box = page_box(client_logged_in, "core_company_update")
    assert "<tr><td>住所</td><td></td><td>納品書と請求書の会社の欄に出す。" in box
    write(folder, "core_company_update", "<section><h2>会社の情報</h2><h3 id=\"input\">入れる物</h3><p>作り物の表</p></section>")
    assert "<th>欄の名前</th>" not in page_box(client_logged_in, "core_company_update")


def test_refusal_texts_follow_heads():
    assert set(manual.REFUSAL_HEADS) == set(REFUSAL_HEADS)
    orders = manual.refusal_texts("orders")
    assert "保存できません。明細がありません。明細を 1 行以上入れてください。" in orders
    assert all(text.startswith(manual.REFUSAL_HEADS) for text in orders)
    connect = manual.refusal_texts("connect")
    assert any(text.startswith("送れません。") for text in connect)
    assert not any(text.startswith("注文のデータの") for text in connect)
    assert manual.refusal_texts("no_such_part") == []


def test_refusals_once_per_part_in_booklet(client_logged_in, folder):
    write(folder, "orders_order_list", "<section><h2>受注の一覧</h2></section>")
    sections = manual.booklet_context()["sections"]
    orders = [section for section in sections if section["url_name"].startswith("orders:")]
    assert orders[0]["app_refusals"] == manual.refusal_texts("orders")
    assert all(section["app_refusals"] == [] for section in orders[1:])
    assert "止まるときの文 (この部品)" not in next(s for s in sections if s["anchor"] == "orders_order_list")["html"]
    assert "<h3>止まるときの文 (この部品)</h3>" in page_box(client_logged_in, "orders_order_list")
    html = reports.render_html(manual.BOOKLET_TEMPLATE, manual.booklet_context())
    assert html.count('<div class="booklet-refusals">') == sum(1 for section in sections if section["app_refusals"]) > 1
