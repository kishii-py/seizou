"""在庫の一覧の行の ⋮ (候補 C78。初見テスト R5 の V58)。在庫の一覧の各行の最後に「操作」の欄を出し、その部品の「動きを見る」「棚卸しの調整」「払い出し」を開く。

棚卸しの調整と払い出しは部品を選んだ形 (?item=) で入力を開き、小窓で保存すると画面を移らずに一覧の行の在庫数が変わる。
「動きを見る」は在庫の動きの一覧を、絞りの「部品」をその部品にして開く (?f_item=。絞りの枠と札に出る)。作り物のデータだけを使う。
"""

import json
import re
from decimal import Decimal
from urllib.parse import quote

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.masters import services as masters_services
from seizou.stock import services

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}


@pytest.fixture
def items(user):
    return [
        masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user),
        masters_services.create_item({"part_no": "T-200", "name": "作り物の板", "unit": "枚"}, user),
        masters_services.create_item({"part_no": "T-300", "name": "作り物の環", "unit": "個"}, user),
    ]


def _today():
    return timezone.localdate().isoformat()


def _row(body, pk):
    """一覧の行 (<tr id="row-<pk>"> から </tr> まで) の HTML。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def _row_tds(body, pk):
    """一覧の行 (<tr id="row-<pk>">) の <td> の HTML の並び。"""
    return re.findall(r"<td[^>]*>.*?</td>", _row(body, pk), re.S)


def _input_link(name, pk, back):
    """⋮ の入力の項目の href (= data-modal-form) の字。部品を選んだ形 (?item=) と、開いた元の画面 (&amp;next=)。"""
    return f"{reverse(name)}?item={pk}&amp;next={back}"


@pytest.mark.django_db
def test_stock_list_rows_have_row_menu(user, logged_in, items):
    services.adjust(items[0], Decimal("7"), timezone.localdate(), user)
    services.adjust(items[1], Decimal("4"), timezone.localdate(), user)
    url = reverse("stock:stock_list")

    response = logged_in.get(url)
    body = response.content.decode()
    labels = [col.label for col in response.context["columns"]]

    assert '<th class="whitespace-nowrap font-medium">操作</th>' in body
    for item, other in ((items[0], items[1]), (items[1], items[0])):
        pk = item.pk
        row = _row(body, pk)
        detail = reverse("masters:item_detail", args=[pk])
        assert row.startswith(
            f'<tr id="row-{pk}" class="row-hover h-10 cursor-pointer" data-row-menu '
            f"onclick=\"if(!event.target.closest('a, details')){{window.location='{detail}'}}\""
        )
        assert '<summary class="btn btn-ghost btn-xs" aria-label="行の操作" title="行の操作">⋮</summary>' in row
        assert f'href="{reverse("stock:stockmove_list")}?f_item={pk}"' in row
        for name in ("stock:adjust_create", "stock:issue_create"):
            link = _input_link(name, pk, url)
            assert f'href="{link}" data-modal-form="{link}"' in row, name
        for word in ("動きを見る", "棚卸しの調整", "払い出し"):
            assert word in row
        # ⋮ はその行の部品だけを指す (ほかの行の pk が混ざらない)
        assert f"?f_item={other.pk}\"" not in row and f"?item={other.pk}&amp;" not in row
        tds = _row_tds(body, pk)
        assert len(tds) == len(response.context["columns"]) + 1  # 最後の欄が「操作」
        weekday = f"({'月火水木金土日'[timezone.localdate().weekday()]})"
        assert weekday in tds[labels.index("最終の動きの日")]  # 写した元 (core/_rows.html) の曜日が残る


@pytest.mark.django_db
def test_stock_list_search_rows_keep_menu_and_next(user, logged_in, items):
    services.adjust(items[0], Decimal("7"), timezone.localdate(), user)
    services.adjust(items[1], Decimal("4"), timezone.localdate(), user)
    url = reverse("stock:stock_list")
    pk = items[0].pk

    response = logged_in.get(url, {"q": "T-100"}, HTTP_HX_REQUEST="true", HTTP_HX_TARGET="rows")
    body = response.content.decode()

    assert response.status_code == 200
    assert "<html" not in body  # 行だけの応答
    assert "data-row-menu" in body and "操作</th>" in body
    back = quote(url + "?q=T-100", safe="/")  # Django の urlencode の filter と同じ符号化
    assert f'?item={pk}&amp;next={back}" data-modal-form="' in body
    assert f'<tr id="row-{items[1].pk}"' not in body


@pytest.mark.django_db
def test_stock_inputs_open_with_item_selected(logged_in, items):
    item = items[0]
    option = f'<option value="{item.pk}" selected>{item}</option>'
    assert str(item) == "T-100 作り物の軸"
    for name in ("stock:adjust_create", "stock:issue_create"):
        params = {"item": item.pk, "next": reverse("stock:stock_list")}

        response = logged_in.get(reverse(name), params)  # 画面の全体 (JS が無いとき・新しいタブ)
        assert response.status_code == 200, name
        assert option in response.content.decode(), name
        assert response.context["cancel_url"] == reverse("stock:stock_list"), name

        response = logged_in.get(reverse(name), params, **MODAL)  # 小窓
        body = response.content.decode()
        assert response.status_code == 200, name
        assert response.context["in_modal"], name
        assert "<html" not in body, name
        assert option in body, name


@pytest.mark.django_db
def test_stock_modal_save_updates_list_balance(user, logged_in, items):
    item = items[0]
    services.adjust(item, Decimal("7"), timezone.localdate(), user)

    response = logged_in.post(
        reverse("stock:adjust_create"),
        {"item": item.pk, "counted_qty": "5", "moved_on": _today(), "note": "数え直し"},
        **MODAL,
    )
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"])["seizou:saved"]["level"] == "success"
    assert services.balance(item) == Decimal("5")

    response = logged_in.post(
        reverse("stock:issue_create"), {"item": item.pk, "qty": "2", "moved_on": _today(), "note": ""}, **MODAL
    )
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"])["seizou:saved"]["level"] == "success"
    assert services.balance(item) == Decimal("3")

    listed = logged_in.get(reverse("stock:stock_list"))
    labels = [col.label for col in listed.context["columns"]]
    index = labels.index("在庫数")
    row = next(row for row in listed.context["rows"] if row["obj"].pk == item.pk)
    assert Decimal(str(row["cells"][index]["value"])) == Decimal("3")


@pytest.mark.django_db
def test_move_list_opens_with_item_filter(user, logged_in, items):
    shaft, plate = items[0], items[1]
    services.adjust(shaft, Decimal("7"), timezone.localdate(), user)
    services.record_issued(shaft, Decimal("2"), timezone.localdate(), user)
    services.adjust(plate, Decimal("4"), timezone.localdate(), user)

    response = logged_in.get(reverse("stock:stockmove_list"), {"f_item": str(shaft.pk)})

    assert {row["obj"].item_id for row in response.context["rows"]} == {shaft.pk}
    assert len(response.context["rows"]) == 2
    assert [(chip["heading"], chip["text"]) for chip in response.context["filter_chips"]] == [("部品", str(shaft))]
    assert f'<option value="{shaft.pk}" selected>{shaft}</option>' in response.content.decode()  # 絞りの「部品」の欄


@pytest.mark.django_db
def test_other_lists_have_no_row_menu(logged_in, items):
    body = logged_in.get(reverse("masters:item_list")).content.decode()

    assert f'<tr id="row-{items[0].pk}"' in body
    assert "操作</th>" not in body
    assert "data-row-menu" not in body
