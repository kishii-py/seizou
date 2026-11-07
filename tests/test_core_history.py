"""変更の履歴 (core.models.ChangeLog・core.history・core.services.record_history。決め HIST-0・HIST-1・C40-1) の試験。

設定と会社の情報の履歴、止まって戻ったときに残らないこと、詳細の「履歴」のタブと「最後に直した」、値の型、
金額の名前の絞りを金額の閲覧の無い人に出さないこと (決め C106-1) を見る。データは作り物だけ。
"""

import json
import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.db import transaction
from django.test import RequestFactory
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import history
from seizou.core import services as core_services
from seizou.core.models import ChangeLog, SettingOverride
from seizou.core.views import BaseListView, FilterSpec
from seizou.masters import services as masters_services
from seizou.masters.models import Item
from seizou.orders import services as orders_services
from seizou.orders.models import OrderLine

MONEY_ON = {"department": "off", "write": "off", "money": "on"}
PASSWORD = "kumo-tests-2611"


def _account(login_id, **flags):
    """作り物の利用者 (部署も権限も無し)。see_money=True で金額の閲覧あり (無ければ無し)。"""
    accounts_services.issue_account(login_id, PASSWORD, [], [], flags, None)
    return get_user_model().objects.get(username=login_id)


def _radios(body):
    found = re.findall(r'<input type="radio"( checked)? name="detail-tabs" aria-label="([^"]+)">', body)
    return [(label, bool(checked)) for checked, label in found]


@pytest.fixture
def item(user):
    return masters_services.create_item(
        {"part_no": "H-100", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("1100")}, user
    )


@pytest.fixture
def tracked_item(monkeypatch, item):
    """部品の model に印 (keeps_history) を付けた部品。作った時の行は消して、空の履歴から始める (試験の行の数をそろえるため)。"""
    monkeypatch.setattr(Item, "keeps_history", True, raising=False)
    ChangeLog.objects.all().delete()
    return item


# ---------------------------------------------------------------- (a) 設定


@pytest.mark.django_db
def test_set_setting_records_once_and_clear_records_back(user):
    core_services.set_setting("over_shipment", "stop", user)

    rows = list(ChangeLog.objects.all())
    assert len(rows) == 1
    row = rows[0]
    assert row.model == "core.settingoverride"
    assert row.object_text == "受注より多い出荷 (R01・R04)"
    assert row.action == "updated"
    assert row.actor == user and row.actor_label == "tester"
    assert row.changes == [
        {"field": "over_shipment", "label": "受注より多い出荷 (R01・R04)", "type": "text", "before": "知らせる", "after": "止める"}
    ]

    core_services.set_setting("over_shipment", "stop", user)  # 同じ値 = 前と後が同じ
    assert ChangeLog.objects.count() == 1

    core_services.clear_setting("over_shipment", user)
    assert ChangeLog.objects.count() == 2
    back = ChangeLog.objects.first()
    assert back.action == "updated"
    assert back.changes[0]["before"] == "止める" and back.changes[0]["after"] == "知らせる"

    core_services.clear_setting("over_shipment", user)  # 上書きの行が無い = 値が変わらない
    assert ChangeLog.objects.count() == 2


# ---------------------------------------------------------------- (b) 会社の情報


@pytest.mark.django_db
def test_update_company_keeps_address_and_hides_bank_account(user):
    company = core_services.company()
    core_services.update_company(company, {"address": "作り物の町 1-2"}, user)
    core_services.update_company(company, {"address": "作り物の町 3-4", "bank_account": "作り物銀行 普通 7654321"}, user)

    rows = list(ChangeLog.objects.filter(model="core.company").order_by("pk"))
    assert len(rows) == 2
    assert rows[0].changes == [{"field": "address", "label": "住所", "type": "text", "before": "", "after": "作り物の町 1-2"}]
    by_field = {item["field"]: item for item in rows[1].changes}
    assert by_field["address"]["before"] == "作り物の町 1-2" and by_field["address"]["after"] == "作り物の町 3-4"
    assert by_field["bank_account"]["label"] == "振込先"
    assert by_field["bank_account"]["before"] == "" and by_field["bank_account"]["after"] == history.HIDDEN_VALUE
    # SQLite の JSON の検索に頼らず、全部の行の中身を字にして探す
    dumped = json.dumps([row.changes for row in ChangeLog.objects.all()], ensure_ascii=False)
    assert "7654321" not in dumped
    assert all("7654321" not in row.object_text for row in ChangeLog.objects.all())

    core_services.update_company(company, {"address": "作り物の町 3-4"}, user)  # 変わらない
    assert ChangeLog.objects.filter(model="core.company").count() == 2


@pytest.mark.django_db
def test_settings_screen_shows_settings_history(logged_in, user):
    user.is_superuser = True
    user.save()
    core_services.set_setting("over_shipment", "stop", user)

    body = logged_in.get(reverse("core:settings")).content.decode()

    assert 'id="settings-history-title"' in body
    section = body[body.index('id="settings-history-title"'):]
    assert "受注より多い出荷 (R01・R04): 知らせる → 止める" in section
    assert "直した" in section
    assert "tester" in section


# ---------------------------------------------------------------- (c) 止まって戻る


@pytest.mark.django_db
def test_rollback_leaves_no_history(user, tracked_item):
    before = history.saved_state(tracked_item)
    with pytest.raises(RuntimeError):
        with transaction.atomic():
            Item.objects.filter(pk=tracked_item.pk).update(name="作り物の軸 (直し)")
            core_services.record_history(tracked_item, user, before=before)
            assert ChangeLog.objects.count() == 1
            raise RuntimeError("止まった")
    assert ChangeLog.objects.count() == 0


# ---------------------------------------------------------------- (d) 詳細のタブ


@pytest.mark.django_db
def test_detail_has_history_tab_last_with_yen(logged_in, user, tracked_item):
    before = history.saved_state(tracked_item)
    Item.objects.filter(pk=tracked_item.pk).update(standard_price=Decimal("1180"))
    core_services.record_history(tracked_item, user, before=before)

    body = logged_in.get(reverse("masters:item_detail", args=[tracked_item.pk])).content.decode()

    assert _radios(body)[-1] == ("履歴", False)
    assert "標準単価: ¥1,100 → ¥1,180" in body
    assert "標準単価 (円):" not in body
    assert "data-last-change" in body
    assert re.search(r"最後に直した <span class=\"font-mono tabular-nums\">\d{4}-\d{2}-\d{2} \d{2}:\d{2}</span> ・ tester", body)


@pytest.mark.django_db
def test_history_money_items_hidden_for_people_without_money(settings, client, user, tracked_item):
    """金額の閲覧の無い人の履歴には、金額の項目を項目ごと出さず、項目が残らない行は行ごと出さない (決め 11-05 09:3x・11-07 21:0x の 5)。

    「(非表示)」の字も出さない。タブの数と「最後に直した」も出す行で数える。
    """
    before = history.saved_state(tracked_item)
    Item.objects.filter(pk=tracked_item.pk).update(standard_price=Decimal("1180"))
    core_services.record_history(tracked_item, user, before=before)
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))
    url = reverse("masters:item_detail", args=[tracked_item.pk])

    # 標準単価だけを直した行は、項目が残らないので行ごと出ない。
    body = client.get(url).content.decode()
    assert "(非表示)" not in body and "標準単価" not in body and "1,180" not in body
    assert 'aria-label="履歴">履歴<span class="badge badge-sm tabular-nums">0</span>' in body
    assert "まだ履歴がありません。" in body
    assert "最後に直した —" in body

    # 品名と標準単価を一緒に直した行は、品名だけで出る。
    before = history.saved_state(tracked_item)
    Item.objects.filter(pk=tracked_item.pk).update(name="作り物の軸 (改)", standard_price=Decimal("1200"))
    core_services.record_history(tracked_item, user, before=before)
    body = client.get(url).content.decode()
    assert "→ 作り物の軸 (改)" in body
    assert "(非表示)" not in body and "標準単価" not in body and "1,200" not in body
    assert 'aria-label="履歴">履歴<span class="badge badge-sm tabular-nums">1</span>' in body
    assert "まだ履歴がありません。" not in body

    # 金額の閲覧のある人には 2 行とも、金額の項目つきで出る。
    client.force_login(_account("keiri", see_money=True))
    body = client.get(url).content.decode()
    assert "標準単価: ¥1,100 → ¥1,180" in body and "標準単価: ¥1,180 → ¥1,200" in body
    assert 'aria-label="履歴">履歴<span class="badge badge-sm tabular-nums">2</span>' in body


@pytest.mark.django_db
def test_detail_without_mark_has_no_history(monkeypatch, logged_in, item):
    monkeypatch.setattr(Item, "keeps_history", False)
    body = logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()

    assert 'aria-label="履歴"' not in body
    assert "data-last-change" not in body
    assert "最後に直した" not in body


@pytest.mark.django_db
def test_detail_last_change_dash_without_rows(logged_in, tracked_item):
    body = logged_in.get(reverse("masters:item_detail", args=[tracked_item.pk])).content.decode()

    assert "最後に直した —" in body
    assert "まだ履歴がありません。" in body


# ---------------------------------------------------------------- (e) 並びと上限と畳み


@pytest.mark.django_db
def test_timeline_newest_first_limit_and_folded_created(logged_in, user, tracked_item):
    core_services.record_history(tracked_item, None)  # 人の無い行 = システム
    for n in range(100):
        core_services.record_history(
            tracked_item, user, changes=[{"field": "note", "label": "備考", "type": "text", "before": str(n), "after": str(n + 1)}]
        )

    timeline = history.timeline(tracked_item, user)
    assert timeline.count == 101
    assert len(timeline.rows) == 100
    assert timeline.rows[0]["items"][0]["after"] == "100"  # 新しい順
    assert timeline.last is timeline.rows[0]

    body = logged_in.get(reverse("masters:item_detail", args=[tracked_item.pk])).content.decode()
    assert "先頭の 100 件を出しています (全 101 件)。" in body

    short = history.timeline(tracked_item, user, limit=200)
    created = short.rows[-1]
    assert created["title"] == "作った" and created["folded"] is True
    assert created["who"] == "システム"
    assert {"label": "品名", "value": "作り物の軸"}.items() <= next(i for i in created["items"] if i["label"] == "品名").items()


@pytest.mark.django_db
def test_created_values_are_folded_in_details(logged_in, tracked_item):
    core_services.record_history(tracked_item, None)

    body = logged_in.get(reverse("masters:item_detail", args=[tracked_item.pk])).content.decode()

    folded = re.search(r"<details class=\"mt-1 text-sm\"><summary[^>]*>その時の値</summary>(.*?)</details>", body, re.S)
    assert folded is not None
    assert "品名: 作り物の軸" in folded.group(1)
    assert '<span data-history-who>システム</span>' in body


# ---------------------------------------------------------------- (f) DB の値と比べる


@pytest.mark.django_db
def test_unsaved_object_values_do_not_make_rows(user, tracked_item):
    before = history.saved_state(tracked_item)
    tracked_item.name = "保存していない名前"

    assert core_services.record_history(tracked_item, user, before=before) is None
    assert ChangeLog.objects.count() == 0


# ---------------------------------------------------------------- (g) 型


@pytest.mark.django_db
def test_value_types_bool_json_fk(user, item):
    state = history.saved_state(item)
    assert state["active"] == {"label": "使用中", "type": "bool", "value": "true"}
    assert state["standard_price"]["type"] == "money" and state["standard_price"]["value"] == "1100"

    row = SettingOverride.objects.create(key="menu", value={"b": 1, "a": "字"}, updated_by=user)
    state = history.saved_state(row)
    assert state["value"]["value"] == '{"a": "字", "b": 1}'
    assert state["updated_by"]["value"] == "tester"
    assert "updated_at" not in state  # auto_now の列は残さない

    before = history.saved_state(item)
    Item.objects.filter(pk=item.pk).update(active=False)
    stopped = core_services.record_history(item, user, before=before)
    assert stopped.action == "stopped"
    assert stopped.changes == [{"field": "active", "label": "使用中", "type": "bool", "before": "true", "after": "false"}]
    before = history.saved_state(item)
    Item.objects.filter(pk=item.pk).update(active=True)
    assert core_services.record_history(item, user, before=before).action == "resumed"


@pytest.mark.django_db
def test_plain_text_forms():
    assert history.plain_text(Decimal("1180.00")) == "1180"
    assert history.plain_text(Decimal("0.0125")) == "0.0125"
    assert history.plain_text(date(2026, 11, 6)) == "2026-11-06"
    assert history.plain_text(True) == "true"
    assert history.plain_text(None) == ""


# ---------------------------------------------------------------- (h) 金額の名前の絞り


class _LineList(BaseListView):
    """試験だけの一覧 (受注の明細。金額の列と金額の範囲の絞り)。"""

    model = OrderLine
    columns = ("qty", "amount")
    filters = ({"name": "amount", "label": "金額", "kind": "number_range", "field": "amount"},)


@pytest.fixture
def two_lines(user):
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    part = masters_services.create_item({"part_no": "H-200", "name": "作り物の板", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [
            {"item": part, "qty": Decimal("10"), "unit_price": Decimal("1500"), "due_date": date(2026, 11, 20)},
            {"item": part, "qty": Decimal("2"), "unit_price": Decimal("300"), "due_date": date(2026, 11, 12)},
        ],
        user,
        order_date=date(2026, 11, 2),
    )


def _view(person, query=""):
    request = RequestFactory().get("/lines/" + query)
    request.user = person
    view = _LineList()
    view.setup(request)
    return view


@pytest.mark.django_db
def test_money_filter_only_for_people_who_see_money(settings, user, two_lines):
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    user.is_superuser = True
    user.save()

    seen = _view(user, "?f_amount_from=1000")
    assert [spec.name for spec in seen.filter_specs()] == ["amount"]
    assert seen.get_queryset().count() == 1

    hidden_person = _account("genba")
    hidden = _view(hidden_person, "?f_amount_from=1000")
    assert hidden.filter_specs() == []
    assert hidden.get_queryset().count() == 2


def test_filter_spec_money_mark():
    assert FilterSpec(OrderLine, {"name": "amount", "kind": "number_range", "field": "amount"}).money is True
    assert FilterSpec(OrderLine, {"name": "qty", "kind": "number_range", "field": "qty"}).money is False
    flag = FilterSpec(OrderLine, {"name": "open", "label": "残りがある物だけ", "kind": "flag", "when": lambda qs: qs})
    assert flag.money is False
