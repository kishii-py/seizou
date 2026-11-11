"""締めの一括の 2 段目 (依頼書 N402b)。請求書の一覧から開く「締日でまとめて作る」の画面 (billing:invoice_closing)。

経理が年月と締日を選び、その日が締日の得意先の表で、作る得意先と作らない得意先の訳を確かめてから、請求書を 1 回でまとめて作る。
- (a) 請求書の一覧に「締日でまとめて作る」のボタンがある (金額の閲覧の無い人の一覧には無い)
- (b) ?month=2026-10&closing_day=31 で開くと、締日 31 の得意先の行・期間・「作る」と、発行日の初めの値 2026-10-31 が出る
- (c) 送ると作る得意先の請求書ができ、請求書の一覧へ移り、知らせが出る (人の手が要る得意先があれば黄)
- (d) 作る物が無い (2 回目) と、請求書の数が変わらず、赤の知らせに「発行できません。」
- (e) 金額の閲覧の無い人は送っても断られる。開いた表に金額が出ない
- (f) 締日の選ぶ欄は、締日でまとめる得意先の締日だけ (重ならず、31 は「月末」)
- (g) 締日が空の得意先の出荷は「締日が空」の行で出て、請求書の新規登録へのリンクがある (送っても作らない)
- 見た目: 欄の並び・表の列の並び・知らせの段・保存のボタンの字
今日は全部の試験で 2026-11-11 に固定する (年月の選ぶ欄は今日から数える)。作り物のデータだけを使う。
"""

import html
import re
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Invoice
from seizou.core import access
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.shipping import services as shipping_services
from tests.billing_helpers import shipped
from tests.test_core_access import ALL_ON, account

TODAY = date(2026, 11, 11)
ORDER_DATE = date(2026, 1, 5)
OCT_31 = date(2026, 10, 31)
NO_MONEY = {"department": "off", "write": "off", "money": "on"}
OCT_QUERY = {"month": "2026-10", "closing_day": "31"}


@pytest.fixture(autouse=True)
def today(monkeypatch):
    """今日を 2026-11-11 に固定する (form の年月の選択肢と、services の締める日が今日より後かの検査)。"""
    monkeypatch.setattr("seizou.billing.forms.timezone.localdate", lambda *args, **kwargs: TODAY)
    monkeypatch.setattr("seizou.billing.services.timezone.localdate", lambda *args, **kwargs: TODAY)
    return TODAY


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def _customer(user, code, closing_day, **extra):
    data = {
        "code": code,
        "name": f"作り物の得意先 {code}",
        "role": "customer",
        "closing_day": closing_day,
        "payment_month": "next",
        "payment_day": 31,
        **extra,
    }
    return masters_services.create_partner(data, user)


def _line(user, customer, item, qty="1000", price="100"):
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal(qty), "unit_price": Decimal(price)}], user, order_date=ORDER_DATE
    )
    return order.lines.get()


@pytest.fixture
def world(user, item):
    """締日 31 の 2 社 (A・B)・締日 20 の 1 社 (T)・都度の 1 社 (P。締日 25) と、その出荷 (単価 100)。

    A は 10 月に 4 と 6 (税抜 ¥1,000)、B は 10 月に 3 (税抜 ¥300)。締日が空の得意先は (g) と黄の知らせの試験で足す (_loose)。
    """
    a = _customer(user, "C001", 31)
    b = _customer(user, "C002", 31)
    t = _customer(user, "C003", 20)
    p = _customer(user, "C006", 25, billing_method="per_delivery")
    lines = {key: _line(user, partner, item) for key, partner in {"a": a, "b": b, "t": t, "p": p}.items()}
    ships = SimpleNamespace(
        a_oct05=shipped(user, a, lines["a"], "4", date(2026, 10, 5)),
        a_oct25=shipped(user, a, lines["a"], "6", date(2026, 10, 25)),
        b_oct10=shipped(user, b, lines["b"], "3", date(2026, 10, 10)),
        t_oct15=shipped(user, t, lines["t"], "5", date(2026, 10, 15)),
        p_oct08=shipped(user, p, lines["p"], "1", date(2026, 10, 8)),
    )
    return SimpleNamespace(a=a, b=b, t=t, p=p, lines=lines, ships=ships, item=item)


def _loose(user, item):
    """締日が空の得意先 (N) と、納品書を作っていない 10 月の出荷 1 つ。"""
    n = _customer(user, "C004", None)
    shipment = shipping_services.ship(_line(user, n, item), Decimal("7"), date(2026, 10, 12), user)
    return n, shipment


def _url(**query):
    base = reverse("billing:invoice_closing")
    if not query:
        return base
    return base + "?" + "&".join(f"{key}={value}" for key, value in query.items())


def _post(client, month="2026-10", closing_day="31", issued_on="2026-10-31", *, follow=False):
    data = {"month": month, "closing_day": closing_day, "issued_on": issued_on}
    return client.post(reverse("billing:invoice_closing"), data, follow=follow)


def _body(response) -> str:
    return html.unescape(response.content.decode())


def _options(body, name):
    """選ぶ欄の (値, 字, 選んでいるか) の並び。"""
    select = re.search(rf'<select name="{name}"[^>]*>(.*?)</select>', body, re.S)
    assert select, f"{name} の選ぶ欄が無い"
    return [
        (value, label.strip(), bool(selected))
        for value, selected, label in re.findall(r'<option value="([^"]*)"(\s+selected)?[^>]*>(.*?)</option>', select.group(1), re.S)
    ]


def _input_value(body, name):
    tag = re.search(rf'<input [^>]*name="{name}"[^>]*>', body)
    assert tag, f"{name} の欄が無い"
    value = re.search(r'value="([^"]*)"', tag.group(0))
    return value.group(1) if value else None


def _plan_table(body):
    match = re.search(r"<table [^>]*data-closing-plan[^>]*>(.*?)</table>", body, re.S)
    assert match, "締めの表が無い"
    return match.group(1)


def _headers(table):
    return [re.sub(r"<[^>]+>", "", cell).strip() for cell in re.findall(r"<th[^>]*>(.*?)</th>", table, re.S)]


def _rows(table):
    """表の行。{得意先のコード: (state, 行の HTML, 枡の字の並び)}。"""
    rows = {}
    for state, code, inner in re.findall(r'<tr [^>]*data-state="([^"]+)"[^>]*data-code="([^"]+)"[^>]*>(.*?)</tr>', table, re.S):
        cells = [" ".join(re.sub(r"<[^>]+>", " ", cell).split()) for cell in re.findall(r"<td[^>]*>(.*?)</td>", inner, re.S)]
        rows[code] = (state, inner, cells)
    return rows


def _button(body):
    """入力のカードの主の保存のボタン。(ボタンの字, data-submit-label の有無)。"""
    match = re.search(r'<button class="btn btn-primary" type="submit"([^>]*)>(.*?)</button>', body, re.S)
    assert match, "保存のボタンが無い"
    return re.sub(r"<[^>]+>", "", match.group(2)).strip(), "data-submit-label" in match.group(1)


def _messages(response):
    return [(message.level_tag, message.message) for message in get_messages(response.wsgi_request)]


# ---------------------------------------------------------------- (a)


@pytest.mark.django_db
def test_a_list_has_closing_button(client_logged_in):
    body = _body(client_logged_in.get(reverse("billing:invoice_list")))

    link = re.search(r'<a class="btn btn-ghost" href="([^"]+)">(.*?)</a>', body, re.S)
    assert link, "一覧の上に締日でまとめて作るのボタンが無い"
    assert link.group(1) == reverse("billing:invoice_closing")
    assert re.sub(r"<[^>]+>", "", link.group(2)).strip() == "締日でまとめて作る"


@pytest.mark.django_db
def test_a_list_hides_closing_button_without_money(settings, client_logged_in):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}

    body = _body(client_logged_in.get(reverse("billing:invoice_list")))

    assert reverse("billing:invoice_closing") not in body
    assert "締日でまとめて作る" not in body


# ---------------------------------------------------------------- (b)


@pytest.mark.django_db
def test_b_month_end_plan_table(client_logged_in, world):
    response = client_logged_in.get(_url(**OCT_QUERY))

    body = _body(response)
    assert response.status_code == 200
    rows = _rows(_plan_table(body))
    assert list(rows) == ["C001", "C002"]
    state, _inner, cells = rows["C001"]
    assert state == "make"
    assert cells == ["作り物の得意先 C001", "2026-10-01〜2026-10-31", "2 件", "¥1,000", "作る", ""]
    assert rows["C002"][2][1:5] == ["2026-10-01〜2026-10-31", "1 件", "¥300", "作る"]
    assert _input_value(body, "issued_on") == "2026-10-31"
    assert "2026-10-31 締め: 作る 2 社・作らない 0 社" in body


@pytest.mark.django_db
def test_b_skipped_row_has_reason_and_reissue_link(client_logged_in, user, world):
    # A の 10 月 5 日の出荷だけで先に請求書を作る (10 月 25 日の出荷が残る = 作り直しが要る)
    first = services.create_invoice(world.a, [world.ships.a_oct05], user, issued_on=OCT_31)

    body = _body(client_logged_in.get(_url(**OCT_QUERY)))

    state, inner, cells = _rows(_plan_table(body))["C001"]
    assert state == "exists"
    assert cells[4] == "作らない"
    assert f"請求書 {first.invoice_no} の後に、この期間の出荷が 1 件あります" in cells[5]
    assert f'href="{reverse("billing:invoice_reissue", args=[first.pk])}"' in inner
    assert "2026-10-31 締め: 作る 1 社・作らない 1 社" in body


@pytest.mark.django_db
def test_b_waiting_lump_row_links_to_create(client_logged_in, user, world):
    # 一式 300 を 100 出荷し、受注を取り消した明細 (出し切らずに終えた一式。tests/test_billing_closing.py の _ended_lump と同じ形)
    lump_line = orders_services.create_order(
        world.b,
        [{"item": world.item, "qty": Decimal("300"), "unit_price": Decimal("50000"), "price_basis": "lump"}],
        user,
        order_date=ORDER_DATE,
    ).lines.get()
    shipped(user, world.b, lump_line, "100", date(2026, 10, 20))
    orders_services.cancel_order(lump_line.order, user)

    body = _body(client_logged_in.get(_url(**OCT_QUERY)))

    state, inner, cells = _rows(_plan_table(body))["C002"]
    assert state == "waiting"
    assert cells[4] == "作らない"
    assert "金額を決めていない一式の明細があります" in cells[5]
    assert f'href="{reverse("billing:invoice_create")}?customer={world.b.pk}"' in inner


@pytest.mark.django_db
def test_b_unreadable_query_falls_back_to_defaults(client_logged_in, world):
    response = client_logged_in.get(_url(month="2026-13", closing_day="abc"))

    body = _body(response)
    assert response.status_code == 200
    assert [value for value, _label, selected in _options(body, "month") if selected] == ["2026-10"]
    assert [value for value, _label, selected in _options(body, "closing_day") if selected] == ["31"]
    assert _input_value(body, "issued_on") == "2026-10-31"
    assert list(_rows(_plan_table(body))) == ["C001", "C002"]


@pytest.mark.django_db
def test_b_month_choices_are_this_month_and_eleven_before(client_logged_in, world):
    body = _body(client_logged_in.get(reverse("billing:invoice_closing")))

    months = _options(body, "month")
    assert len(months) == 12
    assert months[0][:2] == ("2026-11", "2026 年 11 月")
    assert months[1] == ("2026-10", "2026 年 10 月", True)
    assert months[-1][:2] == ("2025-12", "2025 年 12 月")
    # 初めは前の月の月末 (並びに 31 がある)
    assert _input_value(body, "issued_on") == "2026-10-31"


@pytest.mark.django_db
def test_b_month_choices_roll_back_over_new_year(monkeypatch, client_logged_in, world):
    monkeypatch.setattr("seizou.billing.forms.timezone.localdate", lambda *args, **kwargs: date(2027, 1, 15))

    body = _body(client_logged_in.get(reverse("billing:invoice_closing")))

    months = _options(body, "month")
    assert months[0][:2] == ("2027-01", "2027 年 1 月")
    assert months[1] == ("2026-12", "2026 年 12 月", True)
    assert months[-1][:2] == ("2026-02", "2026 年 2 月")
    assert _input_value(body, "issued_on") == "2026-12-31"


@pytest.mark.django_db
def test_b_closing_day_20_plan(client_logged_in, world):
    body = _body(client_logged_in.get(_url(month="2026-10", closing_day="20")))

    rows = _rows(_plan_table(body))
    assert list(rows) == ["C003"]
    assert rows["C003"][2][1] == "2026-09-21〜2026-10-20"
    assert _input_value(body, "issued_on") == "2026-10-20"


# ---------------------------------------------------------------- (c)


@pytest.mark.django_db
def test_c_post_makes_invoices_and_goes_to_list(client_logged_in, world):
    response = _post(client_logged_in)

    assert response.status_code == 302
    assert response["Location"] == reverse("billing:invoice_list")
    assert sorted(Invoice.objects.values_list("customer__code", flat=True)) == ["C001", "C002"]
    assert {invoice.issued_on for invoice in Invoice.objects.all()} == {OCT_31}
    [(level, text)] = _messages(response)
    assert level == "success"
    assert "2026-10-31 締めで 2 社の請求書を作りました" in text


@pytest.mark.django_db
def test_c_issued_on_is_what_the_person_entered(client_logged_in, world):
    _post(client_logged_in, issued_on="2026-11-02")

    assert {invoice.issued_on for invoice in Invoice.objects.all()} == {date(2026, 11, 2)}


@pytest.mark.django_db
def test_c_needs_hand_is_warning_toast(client_logged_in, user, world):
    _loose(user, world.item)

    response = _post(client_logged_in)
    [(level, text)] = _messages(response)
    assert level == "warning"
    assert "社の請求書を作りました" in text
    assert "作らなかった得意先 1 社: 作り物の得意先 C004 (締日が空)" in text


@pytest.mark.django_db
def test_c_green_toast_on_the_list(client_logged_in, world):
    """見た目: 人の手が要る得意先が無ければ、移った請求書の一覧の右上に緑の知らせ (成功しました。5 秒で消える)。"""
    response = _post(client_logged_in, follow=True)

    body = _body(response)
    assert response.redirect_chain[-1][0] == reverse("billing:invoice_list")
    green = re.search(r'<div class="seizou-toast [^"]*bg-primary[^"]*" role="status" data-auto>(.*?)</div>\s*</div>', body, re.S)
    assert green, "緑の知らせが無い"
    assert "成功しました。" in green.group(1)
    assert "2026-10-31 締めで 2 社の請求書を作りました" in green.group(1)


@pytest.mark.django_db
def test_c_warning_toast_stays(client_logged_in, user, world):
    """見た目: 人の手が要る得意先があれば、黄の知らせ (確認してください。閉じるまで残る)。"""
    _loose(user, world.item)

    response = _post(client_logged_in, follow=True)

    body = _body(response)
    warning = re.search(r'<div class="seizou-toast [^"]*bg-warning[^"]*" role="alert">(.*?)</div>\s*</div>', body, re.S)
    assert warning, "黄の知らせが無い"
    assert "確認してください。" in warning.group(1)
    assert "data-auto" not in warning.group(0)
    assert "作らなかった得意先 1 社" in warning.group(1)


# ---------------------------------------------------------------- (d)


@pytest.mark.django_db
def test_d_second_post_is_red_and_makes_nothing(client_logged_in, world):
    _post(client_logged_in)
    count = Invoice.objects.count()

    response = _post(client_logged_in)

    body = _body(response)
    assert response.status_code == 200
    assert Invoice.objects.count() == count
    stop = re.search(r'<div [^>]*data-notice="stop"[^>]*>(.*?)</div>\s*</div>', body, re.S)
    assert stop, "赤の知らせが無い"
    assert "発行できません。" in stop.group(1)
    assert "2026-10-31 締めで作る請求書がありません" in stop.group(1)


@pytest.mark.django_db
def test_d_future_closing_is_red(client_logged_in, world):
    response = _post(client_logged_in, month="2026-11", closing_day="31", issued_on="2026-11-30")

    body = _body(response)
    assert response.status_code == 200
    assert Invoice.objects.count() == 0
    assert 'data-notice="stop"' in body
    assert "締める日 (2026-11-30) がまだ来ていません" in body


@pytest.mark.django_db
def test_d_bad_choice_is_field_error(client_logged_in, world):
    response = _post(client_logged_in, month="2020-01")

    body = _body(response)
    assert response.status_code == 200
    assert Invoice.objects.count() == 0
    stop = re.search(r'<div [^>]*data-notice="stop"[^>]*>(.*?)</div>\s*</div>', body, re.S)
    assert stop and "発行できません。" in stop.group(1) and "「年月」" in stop.group(1)


# ---------------------------------------------------------------- (e)


@pytest.mark.django_db
def test_e_without_money_post_is_refused(settings, client_logged_in, world):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}

    response = _post(client_logged_in)

    body = _body(response)
    assert response.status_code == 200
    assert Invoice.objects.count() == 0
    assert 'data-notice="stop"' in body
    assert "請求書は金額が主な内容" in body


@pytest.mark.django_db
def test_e_without_money_table_has_no_amount(settings, client_logged_in, world):
    settings.SEIZOU = {"access_control": dict(NO_MONEY)}

    response = client_logged_in.get(_url(**OCT_QUERY))

    # 締めの一括は金額に関わる操作なので、金額の閲覧の無い人には開いた時点で断る (本人の決め 11-12 07:2x。
    # 直す前は金額の列の無い表が開き、保存で断られた)。開けない画面に、締めの表と金額は出ない
    assert response.status_code == 403
    body = response.content.decode()
    assert "請求書は金額が主な内容のため、金額の閲覧の権限が無い方は作れません。" in body
    assert "data-closing-plan" not in body and "税抜の金額" not in body
    assert "作り物の得意先 C001" not in body


@pytest.mark.django_db
def test_e_closing_is_a_money_action_in_billing_kind(on_all):
    """締めの一括は請求書を作る操作の別の入口なので、請求書を作るのと同じ扱い (金額の閲覧が要る)。"""
    assert access.MONEY_ACTIONS["billing.create_closing_invoices"] == access.MONEY_ACTIONS["billing.create_invoice"]
    assert access.ACTION_KINDS["billing.create_closing_invoices"] == "billing"
    clerk = account("clerk", permissions=["billing"])
    with pytest.raises(access.AccessDenied):
        access.require_write(clerk, "billing.create_closing_invoices")
    keiri = account("keiri", permissions=["billing"], see_money=True)
    access.require_write(keiri, "billing.create_closing_invoices")


@pytest.fixture
def on_all(settings, db):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


# ---------------------------------------------------------------- (f)


@pytest.mark.django_db
def test_f_closing_day_choices(client_logged_in, user, world):
    _loose(user, world.item)
    # 協力企業の締日 (15) は得意先の締日でないので並べない
    masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor", "closing_day": 15}, user
    )

    body = _body(client_logged_in.get(reverse("billing:invoice_closing")))

    assert _options(body, "closing_day") == [("20", "20 日", False), ("31", "月末", True)]


@pytest.mark.django_db
def test_f_no_customers_only_month_end(client_logged_in):
    body = _body(client_logged_in.get(reverse("billing:invoice_closing")))

    assert _options(body, "closing_day") == [("31", "月末", True)]
    assert "この締日の得意先に、請求できる出荷はありません。" in body
    assert "2026-10-31 締め: 作る 0 社・作らない 0 社" in body
    assert _button(body) == ("請求書を作る", False)


@pytest.mark.django_db
def test_f_smallest_day_when_no_month_end(client_logged_in, user, item):
    _customer(user, "C010", 25)
    _customer(user, "C011", 10)

    body = _body(client_logged_in.get(reverse("billing:invoice_closing")))

    assert _options(body, "closing_day") == [("10", "10 日", True), ("25", "25 日", False)]
    assert _input_value(body, "issued_on") == "2026-10-10"


# ---------------------------------------------------------------- (g)


@pytest.mark.django_db
def test_g_no_closing_day_row_and_post_skips_it(client_logged_in, user, world):
    n, _shipment = _loose(user, world.item)

    body = _body(client_logged_in.get(_url(**OCT_QUERY)))

    state, inner, cells = _rows(_plan_table(body))["C004"]
    assert state == "no_closing_day"
    assert cells[1] == "締日が空"
    assert cells[2] == "1 件"
    assert cells[4] == "作らない"
    assert "締日が空なので入れませんでした" in cells[5]
    assert f'href="{reverse("billing:invoice_create")}?customer={n.pk}"' in inner
    assert f'href="{reverse("masters:partner_detail", args=[n.pk])}"' in inner
    assert "2026-10-31 締め: 作る 2 社・作らない 1 社" in body

    _post(client_logged_in)

    assert not Invoice.objects.filter(customer=n).exists()
    assert Invoice.objects.count() == 2


# ---------------------------------------------------------------- 見た目 (欄・列・ボタン・開き直し)


@pytest.mark.django_db
def test_look_fields_columns_and_button(client_logged_in, world):
    body = _body(client_logged_in.get(_url(**OCT_QUERY)))

    positions = [body.index(f'name="{name}"') for name in ("month", "closing_day", "issued_on")]
    assert positions == sorted(positions), "欄の並びは 年月 → 締日 → 発行日"
    for label in ("年月", "締日", "発行日"):
        assert f">{label}</label>" in body
    assert _headers(_plan_table(body)) == ["得意先", "締め期間", "出荷の件数", "税抜の金額", "作るか", "訳"]
    # 字は押した結果の社の数と税抜の合計。JS が数え直さない (欄の入力で字が変わらない) よう、型を置かない
    assert _button(body) == ("2 社・税抜 ¥1,300 の請求書を作る", False)
    # 表は欄の下 (保存のボタンの行は窓の下の端に貼り付く)
    assert body.index('name="issued_on"') < body.index("data-closing-plan")


@pytest.mark.django_db
def test_look_redraws_with_htmx_get_on_change(client_logged_in, world):
    body = _body(client_logged_in.get(_url(**OCT_QUERY)))

    wrapper = re.search(r'<div id="invoice-closing"([^>]*)>', body)
    assert wrapper, "表と欄を包む箱が無い"
    attrs = wrapper.group(1)
    assert f'hx-get="{reverse("billing:invoice_closing")}"' in attrs
    assert 'hx-trigger="change from:#id_month, change from:#id_closing_day"' in attrs
    assert 'hx-include="#id_month, #id_closing_day"' in attrs
    assert 'hx-select="#invoice-closing"' in attrs
    assert 'hx-push-url="true"' in attrs
    # 発行日は送らない (開き直すと締める日に戻る)
    assert "id_issued_on" not in attrs


@pytest.mark.django_db
def test_look_htmx_get_returns_same_page(client_logged_in, world):
    response = client_logged_in.get(
        _url(month="2026-10", closing_day="20"), HTTP_HX_REQUEST="true", HTTP_HX_TARGET="invoice-closing"
    )

    body = _body(response)
    assert response.status_code == 200
    assert '<div id="invoice-closing"' in body
    assert list(_rows(_plan_table(body))) == ["C003"]
    assert _input_value(body, "issued_on") == "2026-10-20"
