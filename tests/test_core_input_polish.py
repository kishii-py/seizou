"""入力の磨き (決め 11-04 22:0x): 書きかけの離脱の確認・Enter で次の欄・日付の曜日。

JS は動かさず、字と読み込みの並びと node --check を見る (ブラウザの確かめは本体の手)。
曜日は filter weekday_ja と、一覧の日付の枡・詳細の日付の項目・CSV (曜日を足さない) を見る。
試験の画面は部品マスターの休日 (作り物の休日の名前)。
納期を一番見る画面 (生産管理ボード・受注と発注の一覧・ダッシュボード・詳細の頭の札・出荷を選ぶ表) の曜日は、
末尾の W04 の段 (決め N132 の当て漏れ。指示書 11-07 の R3)。
"""

import csv
import io
import re
import shutil
import subprocess
from datetime import date, datetime, timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.test import RequestFactory
from django.urls import reverse
from django.utils import timezone

import seizou.core
from seizou.core.templatetags.seizou_tags import weekday_ja
from seizou.core.views import BaseListView
from seizou.masters import services as masters_services
from seizou.masters.models import Holiday
from seizou.orders import services as orders_services
from seizou.orders.templatetags.orders_detail import order_head_chips
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.templatetags.purchasing_tags import purchase_order_head_chips
from seizou.shipping import services as shipping_services

CORE = Path(seizou.core.__file__).resolve().parent
FORM_GUARD = CORE / "static" / "js" / "seizou-form-guard.js"
ENTER_GUARD = CORE / "templates" / "core" / "_enter_guard.html"
SCRIPT_SRC = re.compile(r"<script [^>]*src=\"([^\"]+)\"[^>]*>")
WEEKDAY = '<span class="text-xs text-base-content/60">(火)</span>'


class _HolidayByName(BaseListView):
    """日付が 2 列目の一覧 (1 列目の link でない日付の枡を見る)。"""

    model = Holiday
    screen_name = "作り物の休日の一覧"
    columns = ("name", "date")
    template_name = "core/list.html"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def holiday(db):
    # 2026-11-03 は火曜
    return Holiday.objects.create(date=date(2026, 11, 3), name="作り物の会社の休み", kind="company")


def text(response) -> str:
    return response.content.decode()


def test_base_loads_form_guard_after_modal(logged_in):
    body = text(logged_in.get(reverse("core:home")))
    scripts = SCRIPT_SRC.findall(body)
    guard = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-form-guard.js")]
    modal = [i for i, src in enumerate(scripts) if src.endswith("js/seizou-modal.js")]
    assert len(guard) == 1 and len(modal) == 1
    assert guard[0] == modal[0] + 1
    assert "defer" in re.search(r"<script [^>]*js/seizou-form-guard\.js[^>]*>", body).group(0)


def test_form_guard_js_words():
    script = FORM_GUARD.read_text(encoding="utf-8")
    for word in (
        "form[data-enter-guard]",
        'addEventListener("input"',
        'addEventListener("change"',
        'addEventListener("submit"',
        '"beforeunload"',
        "event.preventDefault()",
        "returnValue",
        "window.confirm(MESSAGE)",
        "入力した内容が消えます。閉じますか？",
        '"cancel"',
        '"close"',
        '"dialog"',
        "seizou-modal",
        "dataset.seizouDirty",
    ):
        assert word in script, word
    for word in (".classList", ".className", ".innerHTML", "insertAdjacentHTML", "outerHTML"):
        assert word not in script, word


def test_enter_guard_moves_to_next_field_and_saves_with_ctrl_enter(logged_in):
    body = text(logged_in.get(reverse("masters:holiday_create")))
    assert "data-enter-guard" in body
    for word in (
        'document.addEventListener("keydown"',
        "Enter で保存まで進めない",
        "isComposing",
        "keyCode === 229",
        "TEXTAREA",
        "Ctrl+Enter",
        "event.ctrlKey || event.metaKey",
        'button[type="submit"]',
        ".focus()",
        ".click()",
        ".ts-wrapper",
        "window.seizouEnterGuard",
    ):
        assert word in body, word
    # 最初の form にだけ付ける形をやめた (小窓の form にも付く)
    assert 'document.querySelector("form[data-enter-guard]")' not in body
    template = ENTER_GUARD.read_text(encoding="utf-8")
    for word in (".classList", ".className", ".innerHTML"):
        assert word not in template, word


@pytest.mark.skipif(shutil.which("node") is None, reason="node が無い (JS の字の形は本体の門の node --check で見る)")
def test_js_passes_node_check(tmp_path):
    inline = re.search(r"<script>(.*?)</script>", ENTER_GUARD.read_text(encoding="utf-8"), re.S).group(1)
    enter_guard = tmp_path / "enter_guard.js"
    enter_guard.write_text(inline, encoding="utf-8")
    for path in (FORM_GUARD, enter_guard):
        result = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, result.stderr


def test_weekday_ja_filter():
    days = [weekday_ja(date(2026, 11, day)) for day in range(2, 9)]
    assert days == ["月", "火", "水", "木", "金", "土", "日"]
    assert weekday_ja(None) == ""
    assert weekday_ja("") == ""
    assert weekday_ja("2026-11-03") == ""
    assert weekday_ja(3) == ""
    assert weekday_ja(datetime(2026, 11, 3, 23, 30)) == "火"
    # 時刻つきは画面の時刻 (Asia/Tokyo) の日付で数える (UTC の 11-03 15:30 は東京の 11-04 0:30)
    assert weekday_ja(datetime(2026, 11, 3, 15, 30, tzinfo=dt_timezone.utc)) == "水"


def test_list_date_cells_have_weekday(logged_in, user, holiday):
    body = text(logged_in.get(reverse("masters:holiday_list")))
    assert "2026-11-03" + " " + WEEKDAY + "</a></td>" in body
    request = RequestFactory().get("/")
    request.user = user
    response = _HolidayByName.as_view()(request)
    response.render()
    assert '<td class="whitespace-nowrap font-mono tabular-nums">2026-11-03 ' + WEEKDAY + "</td>" in text(response)


def test_detail_date_has_weekday(logged_in, holiday):
    body = text(logged_in.get(reverse("masters:holiday_detail", args=[holiday.pk])))
    assert '<span class="font-mono tabular-nums">2026-11-03</span> ' + WEEKDAY + "</dd>" in body
    # 時刻つきの項目 (登録した日時) にも添える (filter が datetime も受ける)。日付でない項目には添えない
    assert body.count('<span class="text-xs text-base-content/60">(') == 2
    # 時刻つきの項目は 日付 (曜日) 時刻 の順で、帯と一覧と同じ Y-m-d の形 (精査 A9 の 3 節の 3)
    assert re.search(r'</span> <span class="text-xs text-base-content/60">\(.\)</span> <span class="font-mono tabular-nums">\d\d:\d\d</span></dd>', body)
    assert "2026年11月3日" not in body
    assert "作り物の会社の休み</dd>" in body


def test_csv_has_no_weekday(logged_in, holiday):
    response = logged_in.get(reverse("masters:holiday_list"), {"format": "csv"})
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows[1][0] == "2026-11-03"
    assert "(火)" not in response.content.decode("utf-8-sig")


# --- W04: 納期を一番見る画面にも曜日 (決め N132「一覧と盤と詳細の日付に曜日を添える」の当て漏れ。指示書 11-07 の R3) ---
# 土台 (core/_rows.html) を写した部品ごとの行のテンプレートと、ダッシュボードの表・詳細の頭の札・出荷を選ぶ表に曜日が無かった。
# 生産管理の人が「2026-11-14」を見て土曜だと気づかず、前の金曜までの段取りを組みそこねないように。CSV と並べ替えは変えない。

DUE = date(2026, 11, 14)  # 土曜
ORDER_DAY = date(2026, 11, 2)  # 月曜
SHIPPED = date(2026, 11, 5)  # 木曜
SAT = '<span class="text-xs text-base-content/60">(土)</span>'
MON = '<span class="text-xs text-base-content/60">(月)</span>'
THU = '<span class="text-xs text-base-content/60">(木)</span>'


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, item):
    """受注日 11-02 (月)・納期 11-14 (土) の明細 1 行の受注。"""
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": DUE}],
        user,
        order_date=ORDER_DAY,
    )


@pytest.fixture
def purchase_order(user, item):
    """発注日 11-02 (月)・納期 11-14 (土) の明細 1 行の発注 (つながりの無い協力企業なので送らない)。"""
    partner = masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    return purchasing_services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": DUE}],
        user,
        order_date=ORDER_DAY,
        send=False,
    )


def _tr(body, pk):
    """一覧の行 (<tr id="row-<pk>" ...>...</tr>) の HTML。"""
    match = re.search(rf'<tr id="row-{pk}".*?</tr>', body, re.S)
    assert match, f"row-{pk} が無い"
    return match.group(0)


def test_board_dates_have_weekday_but_csv_does_not(logged_in, order):
    line = order.lines.get()
    row = _tr(text(logged_in.get(reverse("orders:orderline_list"))), line.pk)
    assert '<span class="font-mono tabular-nums">2026-11-14</span> ' + SAT in row  # 納期
    assert '<span class="font-mono tabular-nums">2026-11-02</span> ' + MON in row  # 受注日
    csv_body = logged_in.get(reverse("orders:orderline_list"), {"format": "csv"}).content.decode("utf-8-sig")
    assert "2026-11-14" in csv_body and "(土)" not in csv_body and "(月)" not in csv_body


def test_order_and_purchase_order_lists_have_weekday(logged_in, order, purchase_order):
    row = _tr(text(logged_in.get(reverse("orders:order_list"))), order.pk)
    assert '<td class="whitespace-nowrap font-mono tabular-nums">2026-11-02 ' + MON + "</td>" in row
    row = _tr(text(logged_in.get(reverse("purchasing:purchaseorder_list"))), purchase_order.pk)
    assert '<td class="whitespace-nowrap font-mono tabular-nums">2026-11-02 ' + MON + "</td>" in row


def test_head_chips_earliest_due_has_weekday(logged_in, order, purchase_order):
    chips = {chip["label"]: chip["value"] for chip in order_head_chips(order)}
    assert chips["一番早い納期"] == "2026-11-14 (土)"
    chips = {chip["label"]: chip["value"] for chip in purchase_order_head_chips(purchase_order)}
    assert chips["一番早い納期"] == "2026-11-14 (土)"
    # 詳細の画面の札と、右の列の「日付」のカードが同じ日と曜日を出す
    body = text(logged_in.get(reverse("orders:order_detail", args=[order.pk])))
    assert "2026-11-14 (土)" in body
    assert '<span class="font-mono tabular-nums">2026-11-14</span> ' + SAT in body


def test_home_due_alert_and_recent_order_dates_have_weekday(logged_in, user, customer, item):
    today = timezone.localdate()
    late_due = today - timedelta(days=3)
    orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": late_due}],
        user,
        order_date=today,
    )
    body = text(logged_in.get(reverse("core:home")))
    late_day = f'<span class="text-xs text-base-content/60">({weekday_ja(late_due)})</span>'
    today_day = f'<span class="text-xs text-base-content/60">({weekday_ja(today)})</span>'
    assert f"{late_due:%Y-%m-%d} {late_day}</td>" in body  # 納期アラートの納期
    assert f"{today:%Y-%m-%d} {today_day}</td>" in body  # 最近の受注の受注日


def test_shipment_pick_tables_have_weekday(logged_in, user, customer, order):
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED, user)
    cell = '<td class="whitespace-nowrap">2026-11-05 ' + THU + "</td>"
    assert cell in text(logged_in.get(reverse("shipping:deliverynote_create")))  # 納品書を作る
    # 請求書の候補は納品書の出た出荷だけなので、納品書を作ってから見る
    shipping_services.create_delivery_note(customer, [shipment], user)
    assert cell in text(logged_in.get(reverse("billing:invoice_create")))  # 請求書の新規登録
