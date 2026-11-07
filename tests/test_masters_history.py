"""マスター (部品・取引先・工程・部品の取引先と単価・標準の工程の並び・休日) の変更の履歴 (決め HIST-0・HIST-1・C98-1・C98-6)。

部品の詳細の「履歴」のタブと「最後に直した」、会社ごとの項目の前と後、金額の閲覧の無い人の「(非表示)」、使用停止と再開、
部品の取引先と単価の行の作る・直す・消す、標準の工程の並び、休日を消した記録を見る。データは作り物だけ。
"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import fields as company_fields
from seizou.core import history
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.masters.models import ItemPrice, PartnerItem

MONEY_ON = {"department": "off", "write": "off", "money": "on"}
PASSWORD = "kumo-tests-2611"


def _account(login_id):
    accounts_services.issue_account(login_id, PASSWORD, [], [], {}, None)
    return get_user_model().objects.get(username=login_id)


def _detail(client, item):
    return client.get(reverse("masters:item_detail", args=[item.pk])).content.decode()


def _history_part(body):
    """詳細の「履歴」のタブの中身 (タブの radio から最後に直したの行まで)。"""
    start = body.index('aria-label="履歴"')
    return body[start : body.index("data-last-change", start)]


def _rows(item):
    return list(ChangeLog.objects.filter(owner_model="masters.item", owner_pk=item.pk))


@pytest.fixture
def item(user):
    return masters_services.create_item(
        {"part_no": "M-100", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("1100")}, user
    )


@pytest.fixture
def partner(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


# ---------------------------------------------------------------- (a) 品名と最後に直した


@pytest.mark.django_db
def test_update_item_name_shows_in_history_and_last_change(logged_in, user, item):
    masters_services.update_item(item, {"name": "作り物の軸 (改)"}, user)

    body = _detail(logged_in, item)
    part = _history_part(body)
    assert "品名: 作り物の軸 → 作り物の軸 (改)" in part
    assert "直した" in part
    assert re.search(r"最後に直した <span class=\"font-mono tabular-nums\">\d{4}-\d{2}-\d{2} \d{2}:\d{2}</span> ・ tester", body)

    count = ChangeLog.objects.count()
    masters_services.update_item(item, {"name": "作り物の軸 (改)"}, user)  # 何も変えない
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_create_item_is_recorded_as_created(user, item):
    rows = _rows(item)
    assert [row.action for row in rows] == ["created"]
    assert {"field": "part_no", "label": "自社品番", "type": "text", "before": "", "after": "M-100"} in rows[0].changes
    assert all(change["field"] not in ("drawing_preview", "drawing_preview_error", "drawing_page_count") for change in rows[0].changes)


# ---------------------------------------------------------------- (b) 会社ごとの項目


@pytest.mark.django_db
def test_company_field_before_and_after(monkeypatch, user, item):
    monkeypatch.setattr(company_fields, "_REGISTRY", {})
    company_fields.register("masters.Item", "fields.ItemExt", [{"name": "ext01", "label": "図面の版", "type": "text"}])
    stored = {"ext01": "A"}
    # 試験の置き場に拡張の表が無いので、拡張の表に書く所と読む所を、同じ辞書で差し替える
    monkeypatch.setattr(company_fields, "save_ext", lambda obj, ext: stored.update(ext or {}))
    monkeypatch.setattr(history, "ext_values", lambda model, pk: dict(stored))

    masters_services.update_item(item, {"name": "作り物の軸"}, user, ext={"ext01": "B"})

    row = ChangeLog.objects.first()
    assert row.changes == [{"field": "ext__ext01", "label": "図面の版", "type": "text", "before": "A", "after": "B"}]


# ---------------------------------------------------------------- (c) 標準単価


@pytest.mark.django_db
def test_standard_price_yen_and_masked(settings, client, logged_in, user, item):
    masters_services.update_item(item, {"standard_price": Decimal("1180")}, user)

    assert "標準単価: ¥1,100 → ¥1,180" in _history_part(_detail(logged_in, item))

    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))
    part = _history_part(_detail(client, item))
    assert "標準単価: (非表示) → (非表示)" in part
    assert "1,180" not in part


# ---------------------------------------------------------------- (d) 使用停止と再開


@pytest.mark.django_db
def test_stop_and_resume_titles(logged_in, user, item):
    masters_services.update_item(item, {"active": False}, user)
    masters_services.update_item(item, {"active": True}, user)

    assert [row.action for row in _rows(item)[:2]] == ["resumed", "stopped"]
    part = _history_part(_detail(logged_in, item))
    assert "使用停止にした" in part and "使用を再開した" in part
    assert "使用中: はい → いいえ" in part


# ---------------------------------------------------------------- (e) 部品の取引先と単価の行


@pytest.mark.django_db
def test_map_partner_item_once_on_item_detail(logged_in, user, item, partner):
    masters_services.map_partner_item(partner, item, "X-1", user)

    rows = [row for row in _rows(item) if row.model == "masters.partneritem"]
    assert len(rows) == 1 and rows[0].action == "created"
    assert "作り物の得意先 X-1 を作った" in _history_part(_detail(logged_in, item))
    assert not ChangeLog.objects.filter(owner_model="masters.partner", model="masters.partneritem").exists()


@pytest.mark.django_db
def test_map_partner_item_fills_blank_row(logged_in, user, item, partner):
    masters_services.add_partner_item({"partner": partner, "item": item, "partner_part_no": ""}, user)
    blank = PartnerItem.objects.get()
    before = ChangeLog.objects.count()

    masters_services.map_partner_item(partner, item, "X-1", user)

    assert ChangeLog.objects.count() == before + 1
    row = ChangeLog.objects.first()
    assert row.action == "updated" and row.object_pk == blank.pk
    assert {"field": "partner_part_no", "label": "取引先の品番", "type": "text", "before": "", "after": "X-1"} in row.changes
    part = _history_part(_detail(logged_in, item))
    assert "取引先の品番: — → X-1" in part

    count = ChangeLog.objects.count()
    masters_services.add_partner_item({"partner": partner, "item": item, "partner_part_no": ""}, user)  # 同じ行を返すだけ
    assert ChangeLog.objects.count() == count


@pytest.mark.django_db
def test_item_price_rows_on_item_detail(settings, client, logged_in, user, item, subcontractor):
    row = masters_services.add_partner_item({"partner": subcontractor, "item": item, "partner_part_no": "S-1"}, user)
    price = masters_services.add_item_price(
        {"item": item, "partner_item": row, "kind": ItemPrice.KIND_PURCHASE, "unit_price": Decimal("1100")}, user
    )
    masters_services.update_item_price(price, {"unit_price": Decimal("1180")}, user)

    part = _history_part(_detail(logged_in, item))
    assert "単価: ¥1,100 → ¥1,180" in part

    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))
    assert "単価: (非表示) → (非表示)" in _history_part(_detail(client, item))


# ---------------------------------------------------------------- (f) 標準の工程の並び


@pytest.mark.django_db
def test_item_processes_line_once(logged_in, user, item):
    turning = masters_services.create_process({"code": "P10", "name": "旋削"}, user)
    grinding = masters_services.create_process({"code": "P20", "name": "研削"}, user)
    checking = masters_services.create_process({"code": "P30", "name": "検査"}, user)
    masters_services.set_item_processes(item, [turning, checking], user)
    masters_services.set_item_processes(item, [turning, grinding, checking], user)

    rows = [row for row in _rows(item) if row.changes and row.changes[0]["field"] == "item_processes"]
    assert rows[0].changes == [
        {"field": "item_processes", "label": "標準の工程", "type": "text", "before": "旋削・検査", "after": "旋削・研削・検査"}
    ]
    assert "標準の工程: 旋削・検査 → 旋削・研削・検査" in _history_part(_detail(logged_in, item))

    count = ChangeLog.objects.count()
    masters_services.set_item_processes(item, [turning, grinding, checking], user)  # 同じ並び
    assert ChangeLog.objects.count() == count


# ---------------------------------------------------------------- (g) 休日


@pytest.mark.django_db
def test_delete_holiday_keeps_text(user):
    holiday = masters_services.create_holiday({"date": date(2026, 11, 23), "name": "作り物の休み", "kind": "company"}, user)
    masters_services.delete_holiday(holiday, user)

    row = ChangeLog.objects.first()
    assert row.action == "deleted"
    assert row.model == "masters.holiday"
    assert row.object_text == "2026-11-23 作り物の休み"


# ---------------------------------------------------------------- (h) 部品の取引先の行を消す (C98-1・C98-6)


@pytest.mark.django_db
def test_delete_partner_item_records_prices_and_row(settings, client, logged_in, user, item, subcontractor, partner):
    row = masters_services.add_partner_item({"partner": subcontractor, "item": item, "partner_part_no": "S-1"}, user)
    other = masters_services.add_partner_item({"partner": partner, "item": item, "partner_part_no": "C-1"}, user)
    for start in (date(2026, 11, 1), date(2026, 12, 1)):
        masters_services.add_item_price(
            {"item": item, "partner_item": row, "kind": ItemPrice.KIND_PURCHASE, "unit_price": Decimal("900"), "start_date": start},
            user,
        )
    common = masters_services.add_item_price({"item": item, "kind": ItemPrice.KIND_SALE, "unit_price": Decimal("1500")}, user)
    other_price = masters_services.add_item_price(
        {"item": item, "partner_item": other, "kind": ItemPrice.KIND_SALE, "unit_price": Decimal("1400")}, user
    )
    before = ChangeLog.objects.count()

    masters_services.delete_partner_item(row, user)

    deleted = list(ChangeLog.objects.filter(action="deleted").order_by("pk"))
    assert ChangeLog.objects.count() == before + 3
    assert [entry.model for entry in deleted] == ["masters.itemprice", "masters.itemprice", "masters.partneritem"]
    assert all(entry.owner_model == "masters.item" and entry.owner_pk == item.pk for entry in deleted)
    assert not PartnerItem.objects.filter(pk=row.pk).exists()
    assert deleted[-1].object_text == "作り物の協力企業 S-1"
    assert ItemPrice.objects.filter(pk__in=[common.pk, other_price.pk]).count() == 2

    part = _history_part(_detail(logged_in, item))
    assert part.count("を消した") == 3
    assert part.count("その時の値") >= 3

    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))
    part = _history_part(_detail(client, item))
    assert "単価: (非表示)" in part
    assert "¥900" not in part

    masters_services.delete_item_price(common, user)
    assert ChangeLog.objects.count() == before + 4
    assert ChangeLog.objects.first().model == "masters.itemprice"
