"""指示書の工程を協力企業に出す発注の画面 (見比べのまとめの M01。N406b の 2 段目 = 画面と文書)。

発注の新規登録の ?step= (指示書の詳細の「協力企業に出す」の行き先。明細の行の隠しの欄 workorder_step と「渡した日」の欄)・
発注の詳細の明細の字と ⋮ の「渡した日」・渡した日の画面 (purchasing:purchaseorderline_handed)・受入の新規登録の明細の欄の下の字を、
Django の test client で HTML を見て確かめる。作り物のデータは tests/test_purchasing_subcontract.py の build_workorder と subcontract_po で作る。
相手の受け口への HTTP は requests.post を偽の物に差し替える (外へは出ない)。名前と品番は作り物だけ。
"""

import json
import re
from datetime import date
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.purchasing import services
from seizou.purchasing.models import PurchaseOrder, PurchaseOrderLine
from seizou.workorders import services as workorders_services
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_purchasing_subcontract import HANDED, ORDER_DATE, build_workorder, line_of, subcontract_po

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
NO_WRITE = {"department": "off", "write": "on", "money": "off"}
PLAN_START = date(2026, 11, 9)
PLAN_END = date(2026, 11, 12)
DAY = date(2026, 11, 6)
ENV_NAME = "SEIZOU_PARTNER_PLATEVIEW_SECRET"
PARTNER_URL = "https://partner.example.test/connect/receive/order/"
HANDED_HELP = (
    "指示書の工程を指す明細に写します。空なら、まだ渡していない扱いです。"
    "後から発注の詳細の明細の ⋮ の「渡した日」で入れられます。"
)
HANDED_SCREEN_HELP = "空にして保存すると、まだ渡していない扱いに戻ります。"


@pytest.fixture
def wo(user):
    return build_workorder(user)


@pytest.fixture
def fake(monkeypatch):
    post = FakePost()
    monkeypatch.setattr("requests.post", post)
    return post


@pytest.fixture
def sent_partner(user, monkeypatch, fake):
    """向き「送る」のつながりのある協力企業 (取り消した明細を作るため。送る物は偽の requests.post が受ける)。"""
    monkeypatch.setenv(ENV_NAME, _new_secret())
    partner = masters_services.create_partner({"code": "S091", "name": "作り物のめっき屋 見る", "role": "subcontractor"}, user)
    connect_services.register_connection(partner, PARTNER_URL, ENV_NAME, "send", user)
    return partner


def _step_text(wo, step=None):
    step = step or wo.step
    return f"指示書 {wo.workorder.wo_no} の工程 {step.seq} {step.process.name}"


def _open(client, *steps, **params):
    """発注の新規登録を ?step=<工程の pk> (繰り返し) で開く。"""
    response = client.get(reverse("purchasing:purchaseorder_create"), {"step": [str(step) for step in steps], **params})
    assert response.status_code == 200
    return response


def _price(user, wo, partner):
    """部品と協力企業に出す工程の、取引先の発注の単価の行 (出し先の既定の元)。"""
    partner_item = masters_services.add_partner_item({"partner": partner, "item": wo.item}, user)
    return masters_services.add_item_price(
        {"item": wo.item, "partner_item": partner_item, "kind": "purchase", "process": wo.processes[1], "unit_price": Decimal("30")},
        user,
    )


def _post(partner, *rows, handed_on=None):
    """発注の新規登録の POST。rows は {"item", "process", "qty", "workorder_step"} の辞書 (無い鍵は空で送る)。"""
    data = {
        "partner": str(partner.pk),
        "order_date": ORDER_DATE.isoformat(),
        "note": "",
        "lines-TOTAL_FORMS": str(len(rows)),
        "lines-INITIAL_FORMS": "0",
        "lines-MIN_NUM_FORMS": "0",
        "lines-MAX_NUM_FORMS": "1000",
    }
    if handed_on is not None:
        data["handed_on"] = handed_on
    for index, row in enumerate(rows):
        data.update(
            {
                f"lines-{index}-item": str(row["item"].pk),
                f"lines-{index}-process": str(row["process"].pk) if row.get("process") else "",
                f"lines-{index}-qty": row.get("qty", "10"),
                f"lines-{index}-unit": "",
                f"lines-{index}-price_basis": "unit",
                f"lines-{index}-unit_price": "30",
                f"lines-{index}-due_date": "",
                f"lines-{index}-note": "",
                f"lines-{index}-workorder_step": str(row["workorder_step"].pk) if row.get("workorder_step") else "",
            }
        )
    return data


def _field_positions(body, names):
    return [body.index(f'name="{name}"') for name in names]


def _detail(client, po):
    return client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk])).content.decode()


def _detail_rows(body):
    """発注の詳細の明細の表の行 (<tr data-row-menu> ... </tr>) の HTML の並び。"""
    return re.findall(r"<tr data-row-menu>.*?</tr>", body, re.S)


def _handed_url(line):
    return reverse("purchasing:purchaseorderline_handed", args=[line.pk])


def _has_handed_link(body, line):
    url = _handed_url(line)
    return f'href="{url}?next=' in body and f'data-modal-form="{url}"' in body


# ---------------------------------------------------------------- (a) 指示書の工程から開く発注の新規登録


@pytest.mark.django_db
def test_open_from_step_fills_line_and_handed_field(logged_in, user, wo):
    workorders_services.plan_step(wo.step, PLAN_START, PLAN_END, user)
    response = _open(logged_in, wo.step.pk)
    form = response.context["form"]
    body = response.content.decode()

    (row,) = [row for row in form.lines.forms if row.initial]
    order_line = wo.order.lines.get()
    assert row.initial["item"] == wo.item.pk
    assert row.initial["process"] == wo.processes[1].pk
    assert Decimal(row.initial["qty"]) == Decimal("10")
    assert row.initial["unit"] == (order_line.unit_code or None)
    assert row.initial["due_date"] == PLAN_END
    assert row.initial["workorder_step"] == wo.step.pk
    # 明細の行の隠しの欄と、部品の欄の下の字
    assert re.search(rf'<input type="hidden" name="lines-0-workorder_step" value="{wo.step.pk}"', body)
    assert _step_text(wo) in body
    # 「渡した日」の欄 (今日)。欄の並びは 取引先・発注日・渡した日・備考
    assert form["handed_on"].value() == timezone.localdate()
    assert re.search(rf'name="handed_on" value="{timezone.localdate().isoformat()}"', body)
    positions = _field_positions(body, ("partner", "order_date", "handed_on", "note"))
    assert positions == sorted(positions)
    assert HANDED_HELP in body
    # 明細の表の見出しは今のまま (見える欄を足さない)
    assert "<th>指示書の工程</th>" not in body and "<th>渡した日</th>" not in body
    # 足す行の雛形にも隠しの欄
    assert 'name="lines-__prefix__-workorder_step"' in body


@pytest.mark.django_db
def test_open_without_plan_leaves_due_date_empty(logged_in, wo):
    form = _open(logged_in, wo.step.pk).context["form"]
    (row,) = [row for row in form.lines.forms if row.initial]
    assert row.initial["due_date"] is None


@pytest.mark.django_db
def test_two_steps_make_two_rows(logged_in, user, wo):
    other = build_workorder(user, code="2")
    form = _open(logged_in, wo.step.pk, other.step.pk).context["form"]
    rows = [row.initial for row in form.lines.forms if row.initial]
    assert [row["workorder_step"] for row in rows] == [wo.step.pk, other.step.pk]
    assert [row["item"] for row in rows] == [wo.item.pk, other.item.pk]


@pytest.mark.django_db
def test_skipped_steps_make_no_rows(logged_in, user, wo):
    cancelled = build_workorder(user, code="2")
    workorders_services.cancel_workorder(cancelled.workorder, user)
    order_cancelled = build_workorder(user, code="3")
    orders_services.cancel_order(order_cancelled.order, user)
    sent = build_workorder(user, code="4")
    subcontract_po(user, sent.subcontractor, sent.step)
    not_outsourced = wo.steps[0]

    response = _open(
        logged_in, cancelled.step.pk, order_cancelled.step.pk, sent.step.pk, not_outsourced.pk, "abc", 999999
    )
    form = response.context["form"]
    assert [row for row in form.lines.forms if row.initial] == []
    assert "handed_on" not in form.fields
    assert 'name="handed_on"' not in response.content.decode()


@pytest.mark.django_db
def test_cancelled_line_frees_the_step_again(logged_in, user, wo):
    po, _line = subcontract_po(user, wo.subcontractor, wo.step)
    assert [row for row in _open(logged_in, wo.step.pk).context["form"].lines.forms if row.initial] == []
    services.cancel_purchase_order(po, user)
    rows = [row.initial for row in _open(logged_in, wo.step.pk).context["form"].lines.forms if row.initial]
    assert [row["workorder_step"] for row in rows] == [wo.step.pk]


@pytest.mark.django_db
def test_plain_create_has_no_handed_field(logged_in):
    response = logged_in.get(reverse("purchasing:purchaseorder_create"))
    form = response.context["form"]
    body = response.content.decode()
    assert "handed_on" not in form.fields
    assert 'name="handed_on"' not in body
    assert [name for name in form.fields if not name.startswith("ext__")] == ["partner", "order_date", "note"]
    assert "の工程" not in body.split('id="po-lines"', 1)[1].split("</tbody>", 1)[0]


@pytest.mark.django_db
def test_step_rows_follow_from_line_rows(logged_in, user, wo):
    order_line = wo.order.lines.get()
    form = _open(logged_in, wo.step.pk, from_line=order_line.pk).context["form"]
    rows = [row.initial for row in form.lines.forms if row.initial]
    assert len(rows) == 2
    assert "workorder_step" not in rows[0]
    assert rows[1]["workorder_step"] == wo.step.pk


# ---------------------------------------------------------------- (b) 出し先の既定


@pytest.mark.django_db
def test_single_default_subcontractor_fills_partner(logged_in, user, wo):
    _price(user, wo, wo.subcontractor)
    form = _open(logged_in, wo.step.pk).context["form"]
    assert form["partner"].value() == wo.subcontractor.pk


@pytest.mark.django_db
def test_two_default_subcontractors_leave_partner_empty(logged_in, user, wo):
    _price(user, wo, wo.subcontractor)
    other = masters_services.create_partner({"code": "S092", "name": "作り物のめっき屋 乙", "role": "subcontractor"}, user)
    _price(user, wo, other)
    form = _open(logged_in, wo.step.pk).context["form"]
    assert not form["partner"].value()


@pytest.mark.django_db
def test_rows_with_different_defaults_leave_partner_empty(logged_in, user, wo):
    other = build_workorder(user, code="2")
    _price(user, wo, wo.subcontractor)
    partner_item = masters_services.add_partner_item({"partner": other.subcontractor, "item": other.item}, user)
    masters_services.add_item_price(
        {"item": other.item, "partner_item": partner_item, "kind": "purchase", "process": other.processes[1], "unit_price": Decimal("30")},
        user,
    )
    form = _open(logged_in, wo.step.pk, other.step.pk).context["form"]
    assert not form["partner"].value()


# ---------------------------------------------------------------- (c) 保存


@pytest.mark.django_db
def test_save_links_step_and_handed_on(logged_in, wo):
    data = _post(
        wo.subcontractor,
        {"item": wo.item, "process": wo.processes[1], "workorder_step": wo.step},
        handed_on=HANDED.isoformat(),
    )
    response = logged_in.post(reverse("purchasing:purchaseorder_create"), data)
    assert response.status_code == 302
    line = PurchaseOrderLine.objects.get()
    assert line.workorder_step == wo.step
    assert line.handed_on == HANDED


@pytest.mark.django_db
def test_save_with_empty_handed_on_is_not_handed(logged_in, wo):
    data = _post(wo.subcontractor, {"item": wo.item, "process": wo.processes[1], "workorder_step": wo.step}, handed_on="")
    assert logged_in.post(reverse("purchasing:purchaseorder_create"), data).status_code == 302
    line = PurchaseOrderLine.objects.get()
    assert line.workorder_step == wo.step
    assert line.handed_on is None


@pytest.mark.django_db
def test_save_with_other_item_is_refused_and_keeps_step(logged_in, user, wo):
    other_item = masters_services.create_item({"part_no": "T-900", "name": "作り物の別の軸", "unit": "個"}, user)
    data = _post(
        wo.subcontractor,
        {"item": other_item, "process": wo.processes[1], "workorder_step": wo.step},
        handed_on=HANDED.isoformat(),
    )
    response = logged_in.post(reverse("purchasing:purchaseorder_create"), data)
    body = response.content.decode()
    assert response.status_code == 200
    assert not PurchaseOrder.objects.exists()
    # 右上の赤の知らせ (知らせの強さの 2)。字は 1 段目の services の断りのまま (頭の 1 文は太字の枠に分けて描く)
    assert 'data-notice="stop"' in body
    assert "保存できません。" in body
    assert f"明細 1 の部品が、{_step_text(wo)} と違います。" in body
    # 止まった後も、隠しの欄と字と「渡した日」の欄が残る (送った値から作る)
    assert re.search(rf'<input type="hidden" name="lines-0-workorder_step" value="{wo.step.pk}"', body)
    assert _step_text(wo) in body
    assert re.search(rf'name="handed_on" value="{HANDED.isoformat()}"', body)


@pytest.mark.django_db
def test_plain_save_keeps_line_dict_keys(logged_in, user, wo):
    """指示書の工程の無い行の明細の辞書は今のまま (鍵を足さない)。送った形に「渡した日」の欄は無い。"""
    from seizou.purchasing.forms import PurchaseOrderForm

    form = PurchaseOrderForm(data=_post(wo.subcontractor, {"item": wo.item, "process": wo.processes[1]}))
    assert "handed_on" not in form.fields
    assert form.is_valid(), form.errors
    (line,) = form.service_kwargs()["lines"]
    assert "workorder_step" not in line and "handed_on" not in line

    step_form = PurchaseOrderForm(
        data=_post(
            wo.subcontractor,
            {"item": wo.item, "process": wo.processes[1], "workorder_step": wo.step},
            {"item": wo.item, "process": wo.processes[1]},
            handed_on=HANDED.isoformat(),
        )
    )
    assert step_form.is_valid(), step_form.errors
    first, second = step_form.service_kwargs()["lines"]
    assert first["workorder_step"] == wo.step and first["handed_on"] == HANDED
    assert "workorder_step" not in second and "handed_on" not in second


# ---------------------------------------------------------------- (d) 発注の詳細の明細の字と ⋮ の「渡した日」


@pytest.mark.django_db
def test_detail_shows_step_and_handed_text(logged_in, user, wo):
    po = services.create_purchase_order(
        wo.subcontractor,
        [
            {"workorder_step": wo.step, "qty": Decimal("4"), "unit_price": Decimal("30"), "handed_on": HANDED},
            {"workorder_step": wo.step, "qty": Decimal("6"), "unit_price": Decimal("30")},
            {"item": wo.item, "qty": Decimal("2"), "unit_price": Decimal("30")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    handed, waiting, plain = po.lines.order_by("line_no")
    body = _detail(logged_in, po)
    rows = _detail_rows(body)
    assert len(rows) == 3
    workorder_url = reverse("workorders:workorder_detail", args=[wo.workorder.pk])

    assert _step_text(wo) in rows[0] and f'href="{workorder_url}"' in rows[0]
    assert f"渡した日 {HANDED.isoformat()}" in rows[0]
    assert _has_handed_link(rows[0], handed)

    assert _step_text(wo) in rows[1] and "まだ渡していない" in rows[1]
    assert _has_handed_link(rows[1], waiting)

    assert "指示書" not in rows[2] and "渡した日" not in rows[2] and "まだ渡していない" not in rows[2]
    assert not _has_handed_link(rows[2], plain)
    # 列は足さない
    assert "<th>渡した日</th>" not in body


@pytest.mark.django_db
def test_handed_item_hidden_for_reader_cancelled_order_and_line(settings, logged_in, user, wo, sent_partner):
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    assert _has_handed_link(_detail(logged_in, po), line)

    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    assert not _has_handed_link(_detail(logged_in, po), line)
    settings.SEIZOU = {}

    services.cancel_purchase_order(po, user)
    body = _detail(logged_in, po)
    assert not _has_handed_link(body, line)
    assert _step_text(wo) in body

    other = build_workorder(user, code="2")
    sent = services.create_purchase_order(
        sent_partner,
        [
            {"workorder_step": other.step, "qty": Decimal("10"), "unit_price": Decimal("30")},
            {"workorder_step": other.step, "qty": Decimal("5"), "unit_price": Decimal("30")},
        ],
        user,
        order_date=ORDER_DATE,
    )
    first, second = sent.lines.order_by("line_no")
    services.change_purchase_order(sent, {}, [{"line": first}], user)
    assert line_of(second.pk).is_cancelled
    body = _detail(logged_in, sent)
    assert _has_handed_link(body, first)
    assert not _has_handed_link(body, second)


# ---------------------------------------------------------------- (e) 渡した日の画面


@pytest.mark.django_db
def test_handed_screen_shows_lead_field_and_button(logged_in, user, wo):
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    for page in (logged_in.get(_handed_url(line)), logged_in.get(_handed_url(line), **MODAL)):
        body = page.content.decode()
        assert page.status_code == 200
        assert f"{wo.item} を {wo.subcontractor.name} へ ({_step_text(wo)})" in body
        assert "data-form-lead" in body
        assert re.search(rf'name="handed_on" value="{HANDED.isoformat()}"', body)
        assert HANDED_SCREEN_HELP in body
        assert f"{po.po_no}-1 の渡した日を保存する" in body


@pytest.mark.django_db
def test_handed_screen_defaults_to_today_when_empty(logged_in, user, wo):
    _po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    body = logged_in.get(_handed_url(line)).content.decode()
    assert re.search(rf'name="handed_on" value="{timezone.localdate().isoformat()}"', body)


@pytest.mark.django_db
def test_handed_screen_clears_and_sets(logged_in, user, wo):
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    label = f"{po.po_no}-1"

    cleared = logged_in.post(_handed_url(line), {"handed_on": ""}, **MODAL)
    assert cleared.status_code == 204
    saved = json.loads(cleared["HX-Trigger"])["seizou:saved"]
    assert saved["message"] == f"{label} の渡した日を空にしました (まだ渡していない扱いです)。"
    assert saved["level"] == "success"
    assert line_of(line.pk).handed_on is None
    row = _detail_rows(_detail(logged_in, po))[0]
    assert "まだ渡していない" in row and "渡した日 " not in row

    detail_url = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    again = logged_in.post(_handed_url(line), {"handed_on": DAY.isoformat(), "next": detail_url})
    assert again.status_code == 302 and again["Location"] == detail_url
    assert line_of(line.pk).handed_on == DAY
    row = _detail_rows(_detail(logged_in, po))[0]
    assert f"渡した日 {DAY.isoformat()}" in row and "まだ渡していない" not in row


@pytest.mark.django_db
def test_handed_screen_refuses_without_write(settings, logged_in, user, wo):
    po, line = subcontract_po(user, wo.subcontractor, wo.step)
    settings.SEIZOU = {"access_control": dict(NO_WRITE)}
    assert logged_in.get(_handed_url(line)).status_code == 200
    refused = logged_in.post(_handed_url(line), {"handed_on": ""}).content.decode()
    assert "発注の編集の権限がありません。" in refused
    assert line_of(line.pk).handed_on == HANDED


@pytest.mark.django_db
def test_handed_screen_on_plain_line_opens_and_refuses_on_save(logged_in, user, wo):
    """指示書の工程の無い明細でも画面は開く (上の 1 行は工程の括弧なし)。保存は services の断りの赤。"""
    po = services.create_purchase_order(
        wo.subcontractor, [{"item": wo.item, "qty": Decimal("2"), "unit_price": Decimal("30")}], user, order_date=ORDER_DATE
    )
    line = po.lines.get()
    body = logged_in.get(_handed_url(line)).content.decode()
    assert f"{wo.item} を {wo.subcontractor.name} へ" in body
    assert "指示書" not in body.split("data-form-lead", 1)[1].split("</p>", 1)[0]
    refused = logged_in.post(_handed_url(line), {"handed_on": DAY.isoformat()}, **MODAL)
    body = refused.content.decode()
    assert refused.status_code == 200
    # 小窓の上の赤の帯 (知らせの強さの 2)。字は 1 段目の services の断りのまま (頭の 1 文は太字の枠に分けて描く)
    assert "alert alert-error" in body and "保存できません。" in body
    assert "渡した日は、指示書の工程を指す明細だけに入れられます。" in body


@pytest.mark.django_db
def test_handed_screen_is_not_in_menu(logged_in):
    from seizou.purchasing.urls import MENU

    assert all("handed" not in url_name for _label, url_name in MENU)


# ---------------------------------------------------------------- (f) 受入の新規登録の明細の欄の下の字


@pytest.mark.django_db
def test_receipt_form_says_it_becomes_progress(logged_in, user, wo):
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    body = logged_in.get(reverse("purchasing:receipt_create"), {"line": line.pk}).content.decode()
    assert f"{_step_text(wo)} の実績になります。" in body

    plain = services.create_purchase_order(
        wo.subcontractor, [{"item": wo.item, "qty": Decimal("2"), "unit_price": Decimal("30")}], user, order_date=ORDER_DATE
    ).lines.get()
    body = logged_in.get(reverse("purchasing:receipt_create"), {"line": plain.pk}).content.decode()
    assert "の実績になります。" not in body


@pytest.mark.django_db
def test_receipt_saved_message_is_services_text(logged_in, user, wo):
    _po, line = subcontract_po(user, wo.subcontractor, wo.step)
    response = logged_in.post(
        reverse("purchasing:receipt_create"), {"line": line.pk, "qty": "4", "received_on": DAY.isoformat()}, **MODAL
    )
    assert response.status_code == 204
    message = json.loads(response["HX-Trigger"])["seizou:saved"]["message"]
    assert message.startswith("受入を保存しました。")
    assert message != "受入を保存しました。"


@pytest.mark.django_db
def test_receipt_without_handed_on_shows_yellow_band_in_modal(logged_in, user, wo):
    """渡した日の無い明細の受入の R33 の知らせは、小窓の上の黄の帯と「このまま保存」(知らせの強さの 3)。"""
    _po, line = subcontract_po(user, wo.subcontractor, wo.step, handed_on=None)
    response = logged_in.post(
        reverse("purchasing:receipt_create"), {"line": line.pk, "qty": "4", "received_on": DAY.isoformat()}, **MODAL
    )
    body = response.content.decode()
    assert response.status_code == 200
    assert "alert alert-warning" in body and "R33" in body and "このまま保存" in body
    assert f"{_step_text(wo)} の実績になります。" in body
