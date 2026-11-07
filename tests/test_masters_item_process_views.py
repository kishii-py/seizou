"""部品の詳細の「標準の工程」のタブで、工程を足す・並べ替える・外す (決め C19-3 = b・C112-1 = a。候補 C112 の その 2)。

タブの中身は masters/_item_processes.html。3 つの画面は masters:itemprocess_add・itemprocess_update・itemprocess_remove で、
どれも masters.services の口 (add_item_processes・move_item_process・remove_item_process) を通り、並びを書くのは set_item_processes だけ。
部品と工程の名前は作り物だけ。
"""

import json
import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.masters import services as masters_services
from seizou.masters.models import ItemProcess, Process
from seizou.orders import services as orders_services
from seizou.workorders.models import WorkOrder
from tests.test_masters_detail_tabs import PROCESSES_MEANING

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 空のタブの入れ方の 1 行 (押せる人にだけ出す)
HOW_TO_ADD = "「工程を足す」で、工程の一覧にある工程を選んで足せます。"
OUTSOURCED_BADGE = 'badge-outline border-base-300">協力企業に出す</span>'
INACTIVE_BADGE = 'badge-warning">使用停止</span>'


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "IP-100", "name": "作り物のシャフト", "unit": "個"}, user)


@pytest.fixture
def procs(user):
    """作り物の工程 4 つ (並び順は CUT → BEND → PAINT → INSP)。PAINT は協力企業に出す工程。"""
    cut = masters_services.create_process({"code": "CUT", "name": "作り物の切断", "sort_order": 1}, user)
    bend = masters_services.create_process({"code": "BEND", "name": "作り物の曲げ", "sort_order": 2}, user)
    paint = masters_services.create_process(
        {"code": "PAINT", "name": "作り物の塗装", "sort_order": 3, "is_outsourced": True}, user
    )
    insp = masters_services.create_process({"code": "INSP", "name": "作り物の検査", "sort_order": 4}, user)
    return cut, bend, paint, insp


def codes(item):
    return [(row.seq, row.process.code) for row in item.item_processes.order_by("seq")]


def detail(client, item):
    return client.get(reverse("masters:item_detail", args=[item.pk])).content.decode()


def saved_message(response):
    return json.loads(response["HX-Trigger"])["seizou:saved"]["message"]


# ---------------------------------------------------------------- タブの中身


@pytest.mark.django_db
def test_detail_tab_has_entry_rows_and_badges(client_logged_in, user, item, procs):
    cut, _bend, paint, insp = procs
    masters_services.set_item_processes(item, [paint, insp, cut], user)
    masters_services.update_process(insp, {"active": False}, user)

    body = detail(client_logged_in, item)

    assert body.count('id="item-processes"') == 1
    add_url = reverse("masters:itemprocess_add") + f"?item={item.pk}&amp;next="
    assert f'href="{add_url}' in body and f'data-modal-form="{add_url}' in body
    assert "工程を足す" in body
    for row in item.item_processes.all():
        update_url = reverse("masters:itemprocess_update", args=[row.pk])
        remove_url = reverse("masters:itemprocess_remove", args=[row.pk])
        assert f'href="{update_url}?next=' in body and f'data-modal-form="{update_url}?next=' in body
        assert re.search(rf'<a class="flex items-center gap-2 text-error" href="{re.escape(remove_url)}\?next=[^"]*" data-modal-form="[^"]*">.*?外す</a>', body)
        assert f'href="{reverse("masters:process_detail", args=[row.process_id])}"' in body
    assert body.count('aria-label="行の操作"') == 3
    assert body.count("data-row-menu") == 3
    assert body.count(OUTSOURCED_BADGE) == 1
    assert body.count(INACTIVE_BADGE) == 1
    assert '<tr class="h-10 text-base-content/50" data-row-menu>' in body
    assert body.count('<tr class="h-10" data-row-menu>') == 2
    assert ">操作</th>" in body
    refresh = re.search(r'<div id="item-processes-refresh"[^>]*>', body)
    assert refresh and 'hx-select="#item-processes"' in refresh.group(0)


@pytest.mark.django_db
def test_detail_tab_one_row_has_remove_but_no_move(client_logged_in, user, item, procs):
    cut, *_ = procs
    masters_services.set_item_processes(item, [cut], user)
    row = item.item_processes.get()

    body = detail(client_logged_in, item)

    assert reverse("masters:itemprocess_remove", args=[row.pk]) in body
    assert reverse("masters:itemprocess_update", args=[row.pk]) not in body
    assert "順を直す" not in body


@pytest.mark.django_db
def test_detail_tab_empty_has_meaning_and_how_to_add(client_logged_in, item, procs):
    body = detail(client_logged_in, item)

    # 箱は 1 つ (土台の箱の中に写した箱が入らない)
    assert re.search(r'p-6 text-center">\s*<div class="flex flex-col items-center justify-center p-6 text-center"', body) is None
    assert body.count(PROCESSES_MEANING) == 1
    assert body.count(HOW_TO_ADD) == 1
    assert body.index(PROCESSES_MEANING) < body.index(HOW_TO_ADD)
    assert body.count(f'data-modal-form="{reverse("masters:itemprocess_add")}?item={item.pk}') == 1
    assert body.count('id="item-processes"') == 1


# ---------------------------------------------------------------- 追加の小窓


@pytest.mark.django_db
def test_add_modal_lists_active_processes_not_in_list(client_logged_in, user, item, procs):
    cut, bend, paint, insp = procs
    masters_services.set_item_processes(item, [cut], user)
    masters_services.update_process(insp, {"active": False}, user)

    body = client_logged_in.get(reverse("masters:itemprocess_add") + f"?item={item.pk}", **MODAL).content.decode()

    assert "IP-100 作り物のシャフト" in body
    assert f'name="processes" value="{bend.pk}"' in body and f'name="processes" value="{paint.pk}"' in body
    assert f'name="processes" value="{cut.pk}"' not in body  # 並びにある工程
    assert f'name="processes" value="{insp.pk}"' not in body  # 使用停止の工程
    assert "PAINT 作り物の塗装 (協力企業に出す)" in body
    assert "BEND 作り物の曲げ (協力企業に出す)" not in body


@pytest.mark.django_db
def test_add_modal_post_appends_in_process_order(client_logged_in, user, item, procs):
    cut, bend, paint, _insp = procs
    masters_services.set_item_processes(item, [bend], user)
    url = reverse("masters:itemprocess_add") + f"?item={item.pk}"

    response = client_logged_in.post(url, {"item": item.pk, "processes": [paint.pk, cut.pk]}, **MODAL)

    assert response.status_code == 204
    assert saved_message(response) == "保存しました。"
    assert codes(item) == [(1, "BEND"), (2, "CUT"), (3, "PAINT")]


@pytest.mark.django_db
def test_add_modal_post_without_choice_stays(client_logged_in, user, item, procs):
    cut, *_ = procs
    masters_services.set_item_processes(item, [cut], user)

    response = client_logged_in.post(reverse("masters:itemprocess_add"), {"item": item.pk}, **MODAL)

    assert response.status_code == 200
    body = response.content.decode()
    notice = body[body.index('data-notice="stop"') :]
    assert "足す工程" in notice[: notice.index("</div>") + 200]
    assert codes(item) == [(1, "CUT")]


@pytest.mark.django_db
def test_add_full_page_post_goes_to_item_detail(client_logged_in, item, procs):
    cut, *_ = procs

    response = client_logged_in.post(reverse("masters:itemprocess_add"), {"item": item.pk, "processes": [cut.pk]})

    assert response.status_code == 302
    assert response["Location"] == reverse("masters:item_detail", args=[item.pk])
    assert codes(item) == [(1, "CUT")]


@pytest.mark.django_db
def test_add_after_someone_else_added_the_same_process(client_logged_in, user, item, procs):
    cut, bend, *_ = procs
    url = reverse("masters:itemprocess_add") + f"?item={item.pk}"
    client_logged_in.get(url, **MODAL)
    # 小窓を開いた後に、ほかの人が CUT を先に足した
    masters_services.add_item_processes({"item": item, "processes": [cut]}, user)

    response = client_logged_in.post(url, {"item": item.pk, "processes": [cut.pk, bend.pk]}, **MODAL)

    assert response.status_code == 204
    assert codes(item) == [(1, "CUT"), (2, "BEND")]


# ---------------------------------------------------------------- 並べ替えの小窓


@pytest.mark.django_db
def test_move_modal_and_post(client_logged_in, user, item, procs):
    cut, bend, paint, _insp = procs
    masters_services.set_item_processes(item, [cut, bend, paint], user)
    third = item.item_processes.get(process=paint)
    url = reverse("masters:itemprocess_update", args=[third.pk])

    body = client_logged_in.get(url, **MODAL).content.decode()
    assert re.search(r'name="seq"[^>]*value="3"|value="3"[^>]*name="seq"', body)
    assert "1 で先頭、3 で最後です。" in body

    for bad in ("0", "4"):
        response = client_logged_in.post(url, {"seq": bad}, **MODAL)
        assert response.status_code == 200
        assert 'data-notice="stop"' in response.content.decode()
        assert codes(item) == [(1, "CUT"), (2, "BEND"), (3, "PAINT")]

    response = client_logged_in.post(url, {"seq": "1"}, **MODAL)
    assert response.status_code == 204
    assert codes(item) == [(1, "PAINT"), (2, "CUT"), (3, "BEND")]


# ---------------------------------------------------------------- 外すの小窓


@pytest.mark.django_db
def test_remove_modal_and_post(client_logged_in, user, item, procs):
    cut, bend, *_ = procs
    masters_services.set_item_processes(item, [cut, bend], user)
    row = item.item_processes.get(process=cut)
    url = reverse("masters:itemprocess_remove", args=[row.pk])

    body = client_logged_in.get(url, **MODAL).content.decode()
    assert "外します" in body
    assert "工程の一覧にある工程と、発行済みの指示書の工程はそのまま残ります。" in body
    assert re.search(r'<button class="btn btn-error" type="submit">.*?外す</button>', body, re.S)

    response = client_logged_in.post(url, {}, **MODAL)
    assert response.status_code == 204
    assert saved_message(response) == "CUT 作り物の切断 を標準の工程から外しました。"
    assert Process.objects.count() == 4
    assert codes(item) == [(1, "BEND")]


@pytest.mark.django_db
def test_remove_last_row_shows_empty_tab(client_logged_in, user, item, procs):
    # 決め C112-1 = a: 最後の 1 行も外せ、タブは空の箱に入れ替わる
    _cut, bend, *_ = procs
    masters_services.set_item_processes(item, [bend], user)
    row = item.item_processes.get()

    response = client_logged_in.post(reverse("masters:itemprocess_remove", args=[row.pk]), {}, **MODAL)

    assert response.status_code == 204
    assert saved_message(response) == "BEND 作り物の曲げ を標準の工程から外しました。"
    assert item.item_processes.count() == 0
    body = detail(client_logged_in, item)
    assert body.count(PROCESSES_MEANING) == 1
    assert body.count(HOW_TO_ADD) == 1
    assert body.count('id="item-processes"') == 1


# ---------------------------------------------------------------- 権限


@pytest.mark.django_db
def test_without_item_permission_no_entries_and_posts_refused(settings, client_logged_in, user, item, procs):
    cut, bend, paint, _insp = procs
    masters_services.set_item_processes(item, [cut, bend], user)
    first, second = item.item_processes.order_by("seq")
    settings.SEIZOU = {"access_control": {"department": "off", "write": "on", "money": "off"}}

    body = detail(client_logged_in, item)
    assert reverse("masters:itemprocess_add") not in body
    assert reverse("masters:itemprocess_update", args=[first.pk]) not in body
    assert reverse("masters:itemprocess_remove", args=[first.pk]) not in body
    assert ">操作</th>" not in body
    assert "data-row-menu" not in body

    refused = "部品マスターの編集の権限がありません。"
    posts = (
        (reverse("masters:itemprocess_add"), {"item": item.pk, "processes": [paint.pk]}),
        (reverse("masters:itemprocess_update", args=[second.pk]), {"seq": "1"}),
        (reverse("masters:itemprocess_remove", args=[first.pk]), {}),
    )
    for url, data in posts:
        response = client_logged_in.post(url, data)
        assert response.status_code == 200
        html = response.content.decode()
        assert 'data-notice="stop"' in html and refused in html
    assert codes(item) == [(1, "CUT"), (2, "BEND")]


# ---------------------------------------------------------------- 指示書の発行の写し


@pytest.mark.django_db
def test_workorder_copies_reordered_list(client_logged_in, user, item, procs):
    cut, bend, paint, insp = procs
    masters_services.set_item_processes(item, [cut, bend, paint, insp], user)
    masters_services.update_process(insp, {"active": False}, user)
    client_logged_in.post(
        reverse("masters:itemprocess_update", args=[item.item_processes.get(process=paint).pk]), {"seq": "1"}, **MODAL
    )
    customer = masters_services.create_partner({"code": "C112", "name": "作り物の得意先", "role": "customer"}, user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("100")}], user, order_date=date(2026, 11, 7)
    )

    response = client_logged_in.post(reverse("workorders:workorder_create"), {"order_line": order.lines.get().pk, "qty": "5"})

    assert response.status_code == 302
    workorder = WorkOrder.objects.get()
    # 並べ替えた後の順で写る。使用停止の工程は写らない (今のまま)
    assert [step.process for step in workorder.steps.order_by("seq")] == [paint, cut, bend]
    assert ItemProcess.objects.filter(item=item).count() == 4
