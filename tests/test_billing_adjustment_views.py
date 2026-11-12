"""調整の画面 (依頼書 N409b。売掛の 2 段目): 請求書の詳細の入金の枠の「調整を記録する」と調整の並び・調整の記録・調整の取消・移るだけの口。

使う場面: 得意先が振込手数料 660 円を引いて振り込んだ。経理は入金 15,840 を記録し、続けて −660 の調整を記録すると、請求書は入金済みになる。
調整は金額に関わる操作 (core.access.MONEY_ACTIONS) なので、金額の閲覧の無い人 (金額の判定が on) には入口を出さず、画面を開いた時点で断る。
調整の入口は入金と同じ小窓で開く (data-modal-form。510 回目に本体が billing/_form_modal.html に調整の 2 つの枝を足した。URL を直に開けば画面の全体)。
画面に出すマイナスの金額は「-¥660」にそろえる (保存のボタンの字と保存の後の知らせも。510 回目)。
見た目の確かめは開発サーバーを起こさず、応答の HTML (欄の並び・知らせの段の印・ボタンの字) で見る。
作り物の組み方は tests/test_billing_payment_views.py の関数を借りる (test_ で始まる名前は import しない)。合計 16,500 の請求書 (出荷 4 と 6)。
"""

import json
import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.messages import get_messages
from django.urls import reverse

from seizou.billing import services
from seizou.billing.models import Adjustment, Invoice
from seizou.core.screens import all_screens
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.billing_helpers import shipped
from tests.test_billing_payment_views import ALL_ON, MODAL, RECEIVED_ON, _customer, _invoice, _order_line, _panel, account

ADJUSTED_ON = date(2026, 12, 26)
FEE = {"adjusted_on": "2026-12-26", "amount": "-660", "reason": "fee", "note": "作り物の振込手数料"}
HEADING = "この画面は開けません"
MONEY_REFUSAL = "調整は金額が主な内容"


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def invoice(user, item):
    """合計 16,500 の請求書 (締日 20・翌月 25 日払いの得意先)。"""
    customer = _customer(user, closing_day=20, payment_month="next", payment_day=25)
    return _invoice(user, customer, _order_line(user, customer, item))


@pytest.fixture
def paid_short(user, invoice):
    """手数料の 660 円を引いた 15,840 円の入金を記録した請求書 (残 660)。"""
    services.record_payment(invoice, RECEIVED_ON, Decimal("15840"), user)
    return invoice


def _create_url(invoice):
    return reverse("billing:adjustment_create", args=[invoice.pk])


def _cancel_url(adjustment):
    return reverse("billing:adjustment_cancel", args=[adjustment.pk])


def _detail(client, invoice):
    return client.get(reverse("billing:invoice_detail", args=[invoice.pk]))


def _summary(panel):
    """入金の枠の 合計・入金済み・調整・残 の {名: 字}。"""
    return dict(re.findall(r'<dt class="text-xs text-base-content/60">([^<]+)</dt><dd class="tabular-nums">(?:<span class="text-error">)?([^<]+)<', panel))


def _adjustment_rows(panel):
    return re.findall(r"<tr[^>]*data-adjustment-row>(.*?)</tr>", panel, re.S)


def _field_names(body):
    start = re.search(r'<form method="post"[^>]*data-enter-guard', body).start()  # 入力のカードの form (ログアウトの form でない)
    form = body[start : body.index("</form>", start)]
    return [name for name in re.findall(r'name="([^"]+)"', form) if name not in {"csrfmiddlewaretoken", "next", "confirmed"}]


def _button(body):
    """保存のボタンの字 (core/_form_card.html の data-submit-label のあるボタンの最後の字)。"""
    match = re.search(r"<button[^>]*data-submit-label[^>]*>(.*?)</button>", body, re.S)
    assert match, "保存のボタンが無い"
    return re.sub(r"<[^>]+>", "", match.group(1)).strip()


# --- 請求書の詳細の入口 ---


def test_detail_shows_adjustment_button_for_issued_invoice(client_logged_in, invoice):
    panel = _panel(_detail(client_logged_in, invoice))
    assert f'href="{_create_url(invoice)}?next=' in panel
    assert "調整を記録する" in panel
    # 入金の記録と同じく小窓で開く (href は Cmd を押して新しいタブで開く時の画面の全体)
    assert f'data-modal-form="{_create_url(invoice)}?next=' in panel
    assert 'data-modal-form="' + reverse("billing:payment_create", args=[invoice.pk]) in panel
    assert _summary(panel) == {"合計": "¥16,500", "入金済み": "¥0", "調整": "¥0", "残": "¥16,500"}
    # 調整の無い請求書には調整の並びを出さない
    assert _adjustment_rows(panel) == [] and "<h3" not in panel


def test_detail_hides_adjustment_button_on_cancelled_invoice(client_logged_in, user, invoice):
    services.cancel_invoice(invoice, user)
    assert _create_url(invoice) not in _detail(client_logged_in, invoice).content.decode()


@pytest.mark.django_db
def test_detail_hides_adjustment_button_and_amounts_without_money(on, client, user, paid_short):
    adjustment = services.record_adjustment(paid_short, ADJUSTED_ON, Decimal("-660"), user)
    client.force_login(account("jimu", permissions=["billing"]))

    detail = _detail(client, paid_short)
    panel = _panel(detail)
    assert _create_url(paid_short) not in detail.content.decode()
    assert _cancel_url(adjustment) not in panel
    # 調整の並びは出るが、金額の列は見出しごと出ない (入金の並びと同じ)
    (row,) = _adjustment_rows(panel)
    assert "振込手数料" in row
    assert "660" not in panel and "<th class=\"text-right\">金額</th>" not in panel
    assert _summary(panel) == {}


# --- 調整の記録 ---


def test_create_get_fields_initial_and_button(client_logged_in, paid_short):
    response = client_logged_in.get(_create_url(paid_short))

    body = response.content.decode()
    assert response.status_code == 200
    assert _field_names(body) == ["adjusted_on", "amount", "reason", "note"]
    for label in ("値引き", "振込手数料", "相殺", "端数", "その他"):
        assert f">{label}</option>" in body
    assert '<option value="fee" selected>振込手数料</option>' in body
    # 入金済みが 0 より大きく残のある請求書では、金額の初めの値が −残 (振込手数料の差を 1 回で消す)
    assert re.search(r'name="amount" value="-660"', body)
    assert "残を減らすときはマイナスで入れます (振込手数料 660 円なら -660)。" in body
    assert _button(body) == "-¥660 の調整を記録する"
    # 頭の 1 行 (入金の記録の画面と同じ形)
    assert f"請求書 <span class=\"font-mono\">{paid_short.invoice_no}</span> (作り物の得意先)。" in body
    for text in ("合計 <span class=\"tabular-nums\">¥16,500</span>", "入金済み <span class=\"tabular-nums\">¥15,840</span>",
                 "調整 <span class=\"tabular-nums\">¥0</span>", "残 <span class=\"tabular-nums\">¥660</span>"):
        assert text in body, text
    assert response.context["cancel_url"] == reverse("billing:invoice_list")
    assert body.count("btn-primary") == 1


def test_create_get_without_payment_leaves_amount_empty(client_logged_in, invoice):
    body = client_logged_in.get(_create_url(invoice)).content.decode()
    assert re.search(r'<input[^>]*name="amount"', body)
    assert not re.search(r'name="amount" value=', body)
    assert _button(body) == "調整を記録する"


def test_create_post_clears_balance_and_returns_to_invoice(client_logged_in, user, paid_short):
    detail_url = reverse("billing:invoice_detail", args=[paid_short.pk])
    response = client_logged_in.post(_create_url(paid_short) + f"?next={detail_url}", FEE)

    adjustment = Adjustment.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("billing:adjustment_detail", args=[adjustment.pk])
    assert (adjustment.amount, adjustment.reason, adjustment.recorded_by) == (Decimal("-660"), Adjustment.REASON_FEE, user)

    hop = client_logged_in.get(response["Location"])
    assert hop.status_code == 302
    assert hop["Location"] == detail_url

    detail = client_logged_in.get(hop["Location"])
    body = detail.content.decode()
    panel = _panel(detail)
    assert f"請求書 {paid_short.invoice_no} に調整 (振込手数料 -¥660) を記録しました。" in body
    assert _summary(panel) == {"合計": "¥16,500", "入金済み": "¥15,840", "調整": "-¥660", "残": "¥0"}
    assert Invoice.objects.get(pk=paid_short.pk).payment_status() == Invoice.PAYMENT_PAID
    # 枠の頭の入金の印も入金済み (「入金済み」は合計の並びの見出しにもあるので、ほかの 2 つの状態の字が無い事で見る)
    assert "一部入金" not in panel and "未入金" not in panel
    (row,) = _adjustment_rows(panel)
    for text in ("2026-12-26", "-¥660", "振込手数料", "作り物の振込手数料", "tester", ">記録</td>", _cancel_url(adjustment)):
        assert text in row, text
    # 調整の取消も小窓で開く (入金の取消と同じ)
    assert f'data-modal-form="{_cancel_url(adjustment)}"' in row
    # 残が 0 なので、頭の右の緑の「入金を記録する」は出ない (枠の白いボタンは出る)
    assert body.count("btn-primary") == 0


@pytest.mark.parametrize("amount, message", [
    ("0", "調整の金額は 0 でない数にしてください。"),
    ("-660.5", "調整の金額は 1 円単位の整数で入れてください。"),
    ("660円", "金額は -660 のように数字で入れてください。"),
])
def test_create_post_bad_amount_is_field_error(client_logged_in, paid_short, amount, message):
    response = client_logged_in.post(_create_url(paid_short), {**FEE, "amount": amount})

    body = response.content.decode()
    assert response.status_code == 200
    assert message in body
    assert 'data-notice="stop"' in body and "記録できません。" in body and "「金額」" in body
    assert Adjustment.objects.count() == 0


def test_create_post_plus_amount_raises_balance(client_logged_in, invoice):
    response = client_logged_in.post(_create_url(invoice), {**FEE, "amount": "1,000", "reason": "other"})
    assert response.status_code == 302
    invoice = Invoice.objects.get(pk=invoice.pk)
    assert invoice.balance() == Decimal("17500")


def test_create_on_cancelled_invoice_is_refused(client_logged_in, user, invoice):
    services.cancel_invoice(invoice, user)
    response = client_logged_in.post(_create_url(invoice), FEE)

    body = response.content.decode()
    assert response.status_code == 200
    assert 'data-notice="stop"' in body
    # 知らせは 1 文目 (頭) を太字の span に分けて描く (core/_toasts.html)
    assert '<span class="font-semibold">記録できません。</span>' in body
    assert f"請求書 {invoice.invoice_no} は取消です。発行の請求書を選び直してください。" in body
    assert Adjustment.objects.count() == 0


# --- 小窓 (請求書の詳細を離れずに記録して取り消す。510 回目) ---


def test_create_in_modal_shows_the_card_and_saves_without_leaving(client_logged_in, paid_short):
    """小窓で開いた調整の記録は、画面の全体と同じカード (頭の 1 行つき) を出し、保存すると移らずに知らせを返す。"""
    opened = client_logged_in.get(_create_url(paid_short), **MODAL)
    body = opened.content.decode()
    assert opened.status_code == 200
    assert "<html" not in body and 'class="seizou-modal-wide"' in body
    assert "data-adjustment-card" in body
    assert f"請求書 <span class=\"font-mono\">{paid_short.invoice_no}</span> (作り物の得意先)。" in body
    assert "残 <span class=\"tabular-nums\">¥660</span>" in body
    assert _field_names(body) == ["adjusted_on", "amount", "reason", "note"]
    assert _button(body) == "-¥660 の調整を記録する"

    saved = client_logged_in.post(_create_url(paid_short), FEE, **MODAL)
    assert saved.status_code == 204
    note = json.loads(saved["HX-Trigger"])["seizou:saved"]
    assert note == {"message": f"請求書 {paid_short.invoice_no} に調整 (振込手数料 -¥660) を記録しました。", "level": "success"}
    assert list(get_messages(saved.wsgi_request)) == []
    assert Adjustment.objects.get().amount == Decimal("-660")


def test_create_in_modal_bad_amount_stays_in_the_modal(client_logged_in, paid_short):
    """小窓で金額に 0 を入れて押すと、小窓の中身のまま、上の赤い帯と欄の下の字で止まる (右上の知らせは小窓の後ろに隠れるので使わない)。"""
    response = client_logged_in.post(_create_url(paid_short), {**FEE, "amount": "0"}, **MODAL)

    body = response.content.decode()
    assert response.status_code == 200
    assert "<html" not in body and "data-adjustment-card" in body
    assert 'data-notice="stop"' in body and "記録できません。" in body
    assert "調整の金額は 0 でない数にしてください。" in body
    assert Adjustment.objects.count() == 0


def test_cancel_in_modal_shows_the_yellow_line_and_saves_without_leaving(client_logged_in, user, paid_short):
    adjustment = services.record_adjustment(paid_short, ADJUSTED_ON, Decimal("-660"), user)
    url = _cancel_url(adjustment)

    opened = client_logged_in.get(url, **MODAL)
    body = opened.content.decode()
    assert opened.status_code == 200
    assert "<html" not in body and 'class="seizou-modal-wide"' in body
    assert f"請求書 <span class=\"font-mono\">{paid_short.invoice_no}</span> の調整 (調整日 2026-12-26・振込手数料・金額 -¥660) を取り消します。" in body
    # 小窓は閉じるボタンだけ (「キャンセル」の link を出さない。入金の取消と同じ)
    assert "#payments" not in body
    assert '<button class="btn btn-error" type="submit">' in body and "取り消す</button>" in body

    saved = client_logged_in.post(url, {}, **MODAL)
    assert saved.status_code == 204
    assert json.loads(saved["HX-Trigger"])["seizou:saved"] == {"message": "取り消しました。", "level": "success"}
    assert Adjustment.objects.get(pk=adjustment.pk).status == Adjustment.STATUS_CANCELLED


# --- 調整の取消 ---


def test_cancel_adjustment_restores_balance(client_logged_in, user, paid_short):
    adjustment = services.record_adjustment(paid_short, ADJUSTED_ON, Decimal("-660"), user)
    url = _cancel_url(adjustment)

    page = client_logged_in.get(url)
    text = page.content.decode()
    assert page.status_code == 200
    assert f"請求書 <span class=\"font-mono\">{paid_short.invoice_no}</span> の調整 (調整日 2026-12-26・振込手数料・金額 -¥660) を取り消します。" in text
    detail_url = reverse("billing:invoice_detail", args=[paid_short.pk])
    assert f'href="{detail_url}#payments"' in text  # 「キャンセル」は請求書の詳細の入金の枠へ戻る
    assert '<button class="btn btn-error" type="submit">' in text and "取り消す</button>" in text

    response = client_logged_in.post(url, {})
    assert response.status_code == 302
    assert response["Location"] == reverse("billing:adjustment_detail", args=[adjustment.pk])
    detail = client_logged_in.get(response["Location"], follow=True)
    panel = _panel(detail)
    assert _summary(panel) == {"合計": "¥16,500", "入金済み": "¥15,840", "調整": "¥0", "残": "¥660"}
    (row,) = _adjustment_rows(panel)
    assert ">取消</td>" in row and url not in row
    assert Adjustment.objects.get(pk=adjustment.pk).status == Adjustment.STATUS_CANCELLED

    again = client_logged_in.post(url, {})
    body = again.content.decode()
    assert again.status_code == 200
    assert 'data-notice="stop"' in body and "取り消し済み" in body


@pytest.mark.django_db
def test_cancel_counted_adjustment_warns_then_cancels(settings, client_logged_in, user):
    """前回の残を写した後の請求書がある会社: 数えた調整の取消は黄の知らせ (R36) と「このまま取り消す」(取消の画面の土台の字)。"""
    settings.SEIZOU = {"invoice_previous_balance": True}
    customer = masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer", "closing_day": 31, "payment_month": "next", "payment_day": 25},
        user,
    )
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    line = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("100"), "unit_price": Decimal("1000")}], user, order_date=date(2026, 8, 1)
    ).lines.get()
    october = services.create_invoice(customer, [shipped(user, customer, line, "10", date(2026, 10, 15))], user, issued_on=date(2026, 10, 31))
    adjustment = services.record_adjustment(october, date(2026, 10, 31), Decimal("-660"), user)
    november = services.create_invoice(customer, [shipped(user, customer, line, "5", date(2026, 11, 5))], user, issued_on=date(2026, 11, 30))

    first = client_logged_in.post(_cancel_url(adjustment), {})
    body = first.content.decode()
    assert first.status_code == 200
    assert 'data-notice="check"' in body and "R36" in body
    assert f"調整 (2026-10-31・振込手数料) は、請求書 {november.invoice_no} の前回の残に数えて刷っています。" in body
    assert "このまま取り消す</button>" in body
    assert 'name="confirmed" value="1"' in body
    assert 'data-notice="stop"' not in body
    assert Adjustment.objects.get(pk=adjustment.pk).status == Adjustment.STATUS_RECORDED

    # 小窓でも同じ (黄の知らせは小窓の上の帯。取消はまだ行われない)
    in_modal = client_logged_in.post(_cancel_url(adjustment), {}, **MODAL)
    text = in_modal.content.decode()
    assert in_modal.status_code == 200
    assert "<html" not in text and "alert-warning" in text and "R36" in text
    assert f"請求書 {november.invoice_no} の前回の残に数えて刷っています。" in text
    assert "このまま取り消す</button>" in text and 'name="confirmed" value="1"' in text
    assert Adjustment.objects.get(pk=adjustment.pk).status == Adjustment.STATUS_RECORDED

    second = client_logged_in.post(_cancel_url(adjustment), {"confirmed": "1"})
    assert second.status_code == 302
    assert Adjustment.objects.get(pk=adjustment.pk).status == Adjustment.STATUS_CANCELLED


# --- 金額の閲覧の無い人 ---


@pytest.mark.django_db
def test_money_less_user_is_refused_on_open_and_on_post(on, client, user, paid_short):
    adjustment = services.record_adjustment(paid_short, ADJUSTED_ON, Decimal("-1"), user)
    client.force_login(account("jimu", permissions=["billing"]))

    opened = client.get(_create_url(paid_short))
    body = opened.content.decode()
    assert opened.status_code == 403
    assert HEADING in body and MONEY_REFUSAL in body
    assert 'name="amount"' not in body and "16,500" not in body

    refused = client.post(_create_url(paid_short), FEE)
    body = refused.content.decode()
    assert refused.status_code == 200
    assert 'data-notice="stop"' in body and MONEY_REFUSAL in body
    assert Adjustment.objects.count() == 1

    cancel = client.get(_cancel_url(adjustment))
    assert cancel.status_code == 403 and MONEY_REFUSAL in cancel.content.decode()
    assert client.post(_cancel_url(adjustment), {}).status_code == 200
    assert Adjustment.objects.get(pk=adjustment.pk).status == Adjustment.STATUS_RECORDED

    # 金額の閲覧のある経理は開いて記録できる
    client.force_login(account("keiri", permissions=["billing"], see_money=True))
    assert client.get(_create_url(paid_short)).status_code == 200
    assert client.post(_create_url(paid_short), FEE).status_code == 302
    assert Adjustment.objects.count() == 2


# --- 入金の並びの期日の列 ---


def test_payment_rows_show_maturity_column(client_logged_in, user, invoice):
    """入金の並びに「期日」の列 (入金の方法の後)。手形の入金は期日が並び、期日の無い振込は空の欄 (「—」を出さない)。"""
    services.record_payment(invoice, RECEIVED_ON, Decimal("6000"), user, method="bill", matures_on=date(2027, 2, 28))
    services.record_payment(invoice, date(2026, 12, 20), Decimal("500"), user)

    panel = _panel(_detail(client_logged_in, invoice))
    head = re.search(r"<thead>(.*?)</thead>", panel, re.S).group(1)
    assert re.findall(r"<th[^>]*>([^<]+)</th>", head) == ["入金日", "金額", "入金の方法", "期日", "備考", "記録した人", "記録の日時", "状態", "操作"]
    rows = re.findall(r'<tr class="hover:bg-base-200">(.*?)</tr>', panel, re.S)
    cells = [re.findall(r"<td[^>]*>(.*?)</td>", row, re.S) for row in rows]
    by_method = {row[2]: row[3] for row in cells}
    assert by_method == {"手形": "2027-02-28", "振込": ""}


# --- 目録と無い行 ---


def test_new_screens_are_in_catalog():
    by_url_name = {screen.url_name: screen for screen in all_screens()}
    assert by_url_name["billing:adjustment_create"].usecase == (
        "使う人: 経理 / いつ: 振込手数料や値引きで請求書の残が消えないとき / 何のために: 調整を記録して請求書の残を消す"
        " / 次にすること: 請求書の詳細の入金の枠で残を確かめる"
    )
    assert by_url_name["billing:adjustment_cancel"].usecase.startswith("使う人: 経理 / いつ: 調整を間違えて記録したとき")
    assert by_url_name["billing:adjustment_create"].name == "調整の記録"
    assert by_url_name["billing:adjustment_cancel"].name == "調整を取り消す"


def test_unknown_adjustment_is_404(client_logged_in, invoice):
    assert client_logged_in.get(reverse("billing:adjustment_detail", args=[99999])).status_code == 404
    assert client_logged_in.get(reverse("billing:adjustment_cancel", args=[99999])).status_code == 404
    assert client_logged_in.get(reverse("billing:adjustment_create", args=[99999])).status_code == 404
