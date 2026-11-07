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
    assert '<h3>入れる物</h3><div class="overflow-x-auto"><table><thead><tr><th>欄の名前</th><th>必須</th><th>説明</th></tr></thead>' in box
    assert "<tr><td>得意先</td><td>必須</td><td></td></tr>" in box
    assert "<tr><td>備考</td><td></td><td></td></tr>" in box


def test_input_table_in_scroll_box_on_connection_create(client_logged_in, folder):
    """入れる物の表だけが横に送れる箱に入り、見出しは箱の外 (使い方の画面・印刷用・「?」の小窓)。依頼書 N232 (候補 C68)。"""
    write(folder, "connect_connection_create", "<section><h2>取引先との接続の新規登録</h2><p>作り物の接続の説明の字</p></section>")
    url = reverse("core:manual")
    responses = (
        client_logged_in.get(url),
        client_logged_in.get(url, {"print": "1"}),
        client_logged_in.get(reverse("core:manual_fragment", args=["connect_connection_create"]), HTTP_HX_REQUEST="true"),
    )
    for response in responses:
        assert response.status_code == 200
        # 箱の閉じの </div> は section_box (最初の </div> で切る) では見えないので、応答の全部で、この断片の字のすぐ後から見る
        html = response.content.decode()
        head = '<p>作り物の接続の説明の字</p></section><h3>入れる物</h3><div class="overflow-x-auto"><table><thead>'
        assert head in html
        start = html.index(head)
        end = html.index("</table>", start)
        assert html[end : end + len("</table></div>")] == "</table></div>"
        assert "127.0.0.1:8002/connect/receive/order/" in html[start:end]


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


# 画面ごとの止まるときの文 (依頼書 N256 = 候補 C48 の 2)。その画面の view の service からたどれる文だけを出し、画面から起きない文の表を除く。

SCREEN_HEADING = "<h3>この画面で止まるときの文</h3>"


def modal_html(client, anchor):
    """「?」の小窓の応答の字 (画面の全部。断片は呼ぶ前に書いておく)。"""
    response = client.get(reverse("core:manual_fragment", args=[anchor]), HTTP_HX_REQUEST="true")
    assert response.status_code == 200
    return response.content.decode()


def write_screen(folder, anchor):
    write(folder, anchor, f"<section><h2>作り物の画面 {anchor}</h2><p>作り物の説明の字</p></section>")


def test_refusals_once_per_part_in_booklet(client_logged_in, folder):
    write(folder, "orders_order_list", "<section><h2>受注の一覧</h2></section>")
    write(folder, "orders_order_create", "<section><h2>受注の新規登録</h2><p>作り物の説明の字</p></section>")
    sections = manual.booklet_context()["sections"]
    orders = [section for section in sections if section["url_name"].startswith("orders:")]
    assert orders[0]["app_refusals"] == manual.refusal_texts("orders")
    assert all(section["app_refusals"] == [] for section in orders[1:])
    assert SCREEN_HEADING not in next(s for s in sections if s["anchor"] == "orders_order_list")["html"]
    # 一覧の画面は service を持たないので節が出ない。入力の画面の節は小窓で見る (入れる物の表の箱の後なので section_box では見えない)
    assert "止まるときの文" not in page_box(client_logged_in, "orders_order_list")
    assert SCREEN_HEADING in modal_html(client_logged_in, "orders_order_create")
    html = reports.render_html(manual.BOOKLET_TEMPLATE, manual.booklet_context())
    assert html.count('<div class="booklet-refusals">') == sum(1 for section in sections if section["app_refusals"]) > 1


def test_workorder_detail_has_no_refusals(client_logged_in, folder):
    write_screen(folder, "workorders_workorder_detail")
    html = modal_html(client_logged_in, "workorders_workorder_detail")
    assert "止まるときの文" not in html
    assert "planned_qty" not in html


def test_workorder_create_shows_only_its_refusals(client_logged_in, folder):
    write_screen(folder, "workorders_workorder_create")
    html = modal_html(client_logged_in, "workorders_workorder_create")
    assert SCREEN_HEADING in html
    assert "発行できません。工程がありません。工程を 1 つ以上選んでください。" in html
    assert "planned_qty" not in html
    assert "開始日が終了日より後です" not in html
    assert "開始日か終了日の片方だけが入っています" not in html
    assert "取り消せません。" not in html


def test_workorder_cancel_shows_cancel_refusals(client_logged_in, folder):
    write_screen(folder, "workorders_workorder_cancel")
    html = modal_html(client_logged_in, "workorders_workorder_cancel")
    assert SCREEN_HEADING in html
    # 実績の文は cancel_refusal の返す字で raise の字でないので、止まるときの文に集まらない (納品書と請求書の取消と同じ。初見テスト R5 の候補 C79)
    for text in (
        "取り消せません。この指示書は取り消し済みです。指示書の詳細を開き直してください。",
        "取り消せません。取消の理由が … 字を超えます。理由を短くしてください。",
    ):
        assert text in html
    assert "発行できません。" not in html


@pytest.mark.parametrize("anchor", ["core_home", "masters_partner_list"])
def test_screens_without_service_have_no_refusals(client_logged_in, folder, anchor):
    write_screen(folder, anchor)
    for html in (modal_html(client_logged_in, anchor), page_box(client_logged_in, anchor), page_box(client_logged_in, anchor, print="1")):
        assert "止まるときの文" not in html
        assert "access_control" not in html
        assert "答えの YAML の settings に書いてください" not in html


def test_setting_update_shows_screen_refusals_without_table_rows(client_logged_in, folder):
    write_screen(folder, "core_setting_update")
    html = modal_html(client_logged_in, "core_setting_update")
    assert SCREEN_HEADING in html
    assert "保存できません。brand_color は # と 16 進の 6 桁 (例 #15803d) にしてください。" in html
    assert "access_control" not in html
    assert "答えの YAML の settings に書いてください" not in html
    assert "hidden_fields" not in html
    assert "真偽 (true / false)" not in html


def test_partner_import_follows_functions_passed_by_name(client_logged_in, folder):
    """import_partners は _import_rows に create_partner を名前で渡す。呼び出しだけでなく名前でたどるので、その先の文も出る。"""
    write_screen(folder, "masters_partner_import")
    html = modal_html(client_logged_in, "masters_partner_import")
    assert "保存できません。取引先コード … は既にあります。別のコードにしてください。" in html
    assert "取引先の種類 … は使えません" not in html
    assert "支払の月 … は使えません" not in html


def test_not_from_screen_rows_match_services_texts():
    """表の全部の行が、表を当てる前の文の中にある (services の文を直したときに、表が何も言わずに効かなくなるのを防ぐ)。"""
    assert len(manual.NOT_FROM_SCREEN) == 15
    for app, text, reason in manual.NOT_FROM_SCREEN:
        assert text in manual.all_refusal_texts(app), (app, text)
        assert text not in manual.refusal_texts(app), (app, text)
        assert reason
    for app in {app for app, _text, _reason in manual.NOT_FROM_SCREEN}:
        assert manual.refusal_texts(app), app


def test_booklet_drops_not_from_screen_rows(folder, db):
    sections = manual.booklet_context()["sections"]
    refusals = {section["url_name"].split(":")[0]: section["app_refusals"] for section in sections if section["app_refusals"]}
    assert refusals["workorders"]
    assert not any("planned_qty" in text for text in refusals["workorders"])
    assert refusals["core"]
    assert not any("access_control" in text for text in refusals["core"])
    assert not any("答えの YAML の settings に書いてください" in text for text in refusals["core"])
