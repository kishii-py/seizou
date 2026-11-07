"""上の帯の「現品票を読む」(QR のボタン) と、その小窓 (workorders.views.workorder_reader)。

小窓で現品票の QR の字か「指示書」の欄から指示書を引き、読み取りの着地と同じ工程の行を出し、同じ小窓で実績を入れる。
別の住所で刷った現品票の QR は開かない (小窓の上の赤い帯)。QR を読む・小窓を開くだけでは何も記録しない。
Django の test client で HTML と表の状態を見る。カメラと画面の動き (欄を選ぶと送る・Enter で移らない) は朝に目で見る。
fixture は tests/test_workorders_tag.py の形 (工程 2 つで予定の数 4 ずつの指示書) を写した。
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.forms.models import model_to_dict
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import manual
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.progress.models import Progress
from seizou.workorders import services
from seizou.workorders import views as workorders_views
from seizou.workorders.models import WorkOrder

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
ORDER_DATE = date(2026, 11, 2)
DUE_DATE = date(2026, 11, 20)
PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}
NOT_TAG = "読み取れません。この QR は、このシステムの現品票の QR ではありません。"
NOT_FOUND = "読み取れません。その指示書が見つかりません。"
HELP_GOOD = "この工程を終えた良品の数 (不良を除く)。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner(
        {"code": "C001", "name": "作り物の得意先", "role": "customer"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, item):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100"), "due_date": DUE_DATE}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def bend(db):
    return Process.objects.create(code="BEND", name="曲げ", sort_order=2)


@pytest.fixture
def workorder(user, line, cut, bend):
    """工程 2 つ (1 = 切断、2 = 曲げ) の指示書。"""
    return services.issue_workorder(
        line,
        Decimal("4"),
        [{"process": cut, "planned_qty": Decimal("4")}, {"process": bend, "planned_qty": Decimal("4")}],
        user,
    )


@pytest.fixture
def on(settings):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}


def account(login_id, *, departments=()):
    accounts_services.issue_account(login_id, PASSWORD, list(departments), [], {}, None)
    return get_user_model().objects.get(username=login_id)


def _reader():
    return reverse("workorders:workorder_reader")


def _scan_path(pk):
    return reverse("workorders:workorder_scan", args=[pk])


def _header(body):
    match = re.search(r"<header.*?</header>", body, re.S)
    assert match, "上の帯が無い"
    return match.group(0)


def _right_group(header):
    """上の帯の右の組 (ml-auto の div) の中身。"""
    start = header.index('<div class="ml-auto')
    return header[start:]


def _read(client, **params):
    return client.get(_reader(), params, **MODAL)


def _input_buttons(body):
    return re.findall(r"<a [^>]*>実績を入力</a>", body)


def _assert_step_rows(body, workorder):
    """工程の行の小窓: 指示書番号・題・工程ごとに同じ小窓で開く「実績を入力」(next 無し)。"""
    assert workorder.wo_no in body
    assert "現品票の読み取り" in body
    create = reverse("progress:progress_create")
    steps = list(workorder.steps.order_by("seq"))
    for step in steps:
        link = f"{create}?workorder_step={step.pk}"
        assert body.count(f'href="{link}" data-modal-form="{link}"') == 1, step
    buttons = _input_buttons(body)
    assert len(buttons) == len(steps)
    assert all("next=" not in button for button in buttons)
    assert "alert-error" not in body


def _stops(body):
    """赤い帯 (alert-error) の字。帯は 1 文目を太字の span で分けて描くので、tag と空白を除いて並べる。"""
    blocks = re.findall(r'<div class="alert alert-error[^"]*".*?</div>', body, re.S)
    return [re.sub(r"\s+", "", re.sub(r"<[^>]+>", "", block)) for block in blocks]


def _has_stop(body, words):
    wanted = re.sub(r"\s+", "", words)
    return any(wanted in stop for stop in _stops(body))


def _assert_not_tag(body):
    assert "alert-error" in body
    assert _has_stop(body, NOT_TAG)
    assert "実績を入力" not in body


# --- (b) 上の帯のボタン ---


@pytest.mark.django_db
def test_topbar_has_reader_button(logged_in):
    body = logged_in.get(reverse("core:home")).content.decode()
    header = _header(body)
    assert header.count(f'hx-get="{_reader()}"') == 1
    assert header.count('aria-label="現品票を読む"') == 1
    right = _right_group(header)
    button = right.index('aria-label="現品票を読む"')
    assert button < right.index('aria-label="利用者のメニュー"')
    # 開き方は hx-get (data-modal-form にしない。画面の全体の data-modal-form の数を変えない)
    tag = re.search(r'<button [^>]*aria-label="現品票を読む"[^>]*>', header).group(0)
    assert "data-modal-form" not in tag
    assert 'hx-target="#seizou-modal-box"' in tag


# --- (c) 部署で分ける ---


@pytest.mark.django_db
def test_reader_button_follows_department(on, client):
    making = accounts_services.create_department("製造", ["workorders", "progress", "masters"], ["workorders"], None)
    client.force_login(account("genba", departments=[making]))
    assert 'aria-label="現品票を読む"' in _header(client.get(reverse("core:home")).content.decode())

    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping"], ["orders"], None)
    client.force_login(account("jimu", departments=[sales]))
    assert 'aria-label="現品票を読む"' not in _header(client.get(reverse("core:home")).content.decode())


# --- (d) 口の入り方 ---


@pytest.mark.django_db
def test_reader_requires_login(client):
    response = client.get(_reader(), **MODAL)
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))


@pytest.mark.django_db
def test_reader_without_modal_goes_to_list(logged_in):
    response = logged_in.get(_reader())
    assert response.status_code == 302
    assert response["Location"] == reverse("workorders:workorder_list")


@pytest.mark.django_db
def test_reader_modal_has_find_field(logged_in):
    response = logged_in.get(_reader(), **MODAL)
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    body = response.content.decode()
    assert "現品票を読む" in body
    select = re.search(r'<select [^>]*name="workorder"[^>]*>', body).group(0)
    assert 'id="id_workorder"' in select
    assert f'data-select-url="{reverse("workorders:workorder_select")}"' in select
    form = re.search(r"<form [^>]*hx-get=[^>]*>", body).group(0)
    assert f'hx-get="{_reader()}"' in form
    assert 'hx-target="#seizou-modal-box"' in form
    assert "data-enter-guard" in form
    assert "window.seizouEnterGuard" in body
    assert not _has_stop(body, NOT_TAG)
    assert not _has_stop(body, NOT_FOUND)


# --- (e) QR の字から工程の行 ---


@pytest.mark.django_db
def test_reader_code_shows_step_rows(logged_in, workorder):
    response = _read(logged_in, code="http://testserver" + _scan_path(workorder.pk))
    assert response.status_code == 200
    _assert_step_rows(response.content.decode(), workorder)


# --- (f) 見分けられない字 (別の住所の現品票を開かない) ---


@pytest.mark.django_db
@pytest.mark.parametrize(
    "code",
    [
        "http://other.example{scan}",
        "http://testserver:8002{scan}",
        "http://testserver{shipment}",
        "作り物の字",
        "javascript:alert(1)",
    ],
)
def test_reader_refuses_other_codes(logged_in, workorder, code):
    text = code.format(scan=_scan_path(workorder.pk), shipment=reverse("shipping:shipment_detail", args=[1]))
    response = _read(logged_in, code=text)
    assert response.status_code == 200
    body = response.content.decode()
    _assert_not_tag(body)
    assert workorder.wo_no not in body


@pytest.mark.django_db
def test_reader_accepts_other_scheme(logged_in, workorder):
    """同じ名前とポートで scheme だけが違う札 (http の時に刷り、https で読む) は開く。"""
    response = _read(logged_in, code="https://testserver" + _scan_path(workorder.pk))
    _assert_step_rows(response.content.decode(), workorder)


@pytest.mark.django_db
def test_reader_code_unknown_workorder(logged_in):
    response = _read(logged_in, code="http://testserver" + _scan_path(9999))
    assert response.status_code == 200
    body = response.content.decode()
    assert _has_stop(body, NOT_FOUND)
    assert not _has_stop(body, NOT_TAG)


# --- (g) 「指示書」の欄から ---


@pytest.mark.django_db
def test_reader_find_field_shows_step_rows(logged_in, workorder):
    response = _read(logged_in, workorder=str(workorder.pk))
    assert response.status_code == 200
    _assert_step_rows(response.content.decode(), workorder)


@pytest.mark.django_db
@pytest.mark.parametrize("value", ["9999", "²", "abc"])
def test_reader_find_field_unknown(logged_in, value):
    response = _read(logged_in, workorder=value)
    assert response.status_code == 200
    body = response.content.decode()
    assert _has_stop(body, NOT_FOUND)
    assert "実績を入力" not in body


@pytest.mark.django_db
def test_reader_find_field_empty(logged_in, workorder):
    response = _read(logged_in, workorder="")
    assert response.status_code == 200
    body = response.content.decode()
    assert "現品票を読む" in body
    assert not _has_stop(body, NOT_TAG)
    assert not _has_stop(body, NOT_FOUND)
    assert workorder.wo_no not in body


# --- (h) 何も記録しない ---


@pytest.mark.django_db
def test_reader_records_nothing(logged_in, workorder):
    before = (Progress.objects.count(), model_to_dict(WorkOrder.objects.get(pk=workorder.pk)))
    _read(logged_in, code="http://testserver" + _scan_path(workorder.pk))
    _read(logged_in, code="http://other.example" + _scan_path(workorder.pk))
    _read(logged_in, workorder=str(workorder.pk))
    _read(logged_in, workorder="9999")
    _read(logged_in, workorder="")
    after = (Progress.objects.count(), model_to_dict(WorkOrder.objects.get(pk=workorder.pk)))
    assert after == before


# --- (i) 同じ小窓で実績を入れる ---


@pytest.mark.django_db
def test_reader_records_progress_in_same_modal(logged_in, workorder):
    first = workorder.steps.order_by("seq").first()
    create = reverse("progress:progress_create")

    opened = logged_in.get(f"{create}?workorder_step={first.pk}", **MODAL)
    assert opened.status_code == 200
    body = opened.content.decode()
    select = re.search(r'<select name="workorder_step".*?</select>', body, re.S).group(0)
    assert f'<option value="{first.pk}" selected>' in select
    assert 'id="id_qty_helptext"' in body
    assert HELP_GOOD in body

    before = Progress.objects.count()
    data = {"workorder_step": first.pk, "qty": "2", "recorded_at": "2026-11-03 15:00", "note": ""}
    saved = logged_in.post(f"{create}?workorder_step={first.pk}", data, **MODAL)
    assert saved.status_code == 204
    assert "seizou:saved" in saved["HX-Trigger"]
    assert Progress.objects.count() == before + 1


# --- (j) 画面の全体の着地は今のまま ---


@pytest.mark.django_db
def test_scan_page_inputs_have_no_modal(logged_in, workorder):
    body = logged_in.get(_scan_path(workorder.pk)).content.decode()
    buttons = _input_buttons(body)
    assert len(buttons) == 2
    assert all("data-modal-form" not in button for button in buttons)
    assert all("next=" in button for button in buttons)


# --- (k) 断りの頭 ---


def test_reader_refusals_follow_heads():
    assert "読み取れません。" in manual.REFUSAL_HEADS
    for text in (workorders_views.READER_NOT_TAG, workorders_views.READER_NOT_FOUND):
        assert text.startswith(manual.REFUSAL_HEADS)
        assert text.startswith("読み取れません。")


# --- (l) カメラの節 (読み取りの小窓だけ) ---

CAMERA_TEXTS = {
    "insecure": "読み取れません。カメラは https の住所で開いたときだけ使えます。",
    "denied": "読み取れません。ブラウザがカメラを使うことを許していません。",
    "nocamera": "読み取れません。この端末でカメラを開けませんでした。",
}


@pytest.mark.django_db
def test_reader_modal_has_camera_section(logged_in):
    body = logged_in.get(_reader(), **MODAL).content.decode()
    assert "data-qr-reader" in body
    assert f'data-reader-url="{_reader()}"' in body
    video = re.search(r"<video [^>]*>", body).group(0)
    for word in ("autoplay", "playsinline", "muted", "hidden"):
        assert word in video, word
    assert body.count("data-qr-message=") == 3
    for key, words in CAMERA_TEXTS.items():
        # 包みの中身 = 次の包みか script の前まで (帯は _notices.html が描く 1 つの div)
        wrap = re.search(rf'<div hidden data-qr-message="{key}">(.*?)(?=<div hidden data-qr-message=|<script)', body, re.S)
        assert wrap, key
        assert _has_stop(wrap.group(1), words), key
    jsqr = body.index("vendor/jsQR.js")
    reader = body.index("workorders/qr-reader.js")
    assert jsqr < reader
    # カメラの帯は hidden の包みの中だけ (止まった帯は出ていない)
    assert not _has_stop(body, NOT_TAG)
    assert not _has_stop(body, NOT_FOUND)


# --- (m) 工程の行の小窓にはカメラが無い ---


@pytest.mark.django_db
def test_step_rows_modal_has_no_camera(logged_in, workorder):
    body = _read(logged_in, code="http://testserver" + _scan_path(workorder.pk)).content.decode()
    assert "<video" not in body
    assert "<script" not in body
    assert "data-qr-reader" not in body


# --- (n) 同梱の JS と読み取りの JS ---


def test_reader_scripts_are_served():
    from django.contrib.staticfiles import finders

    reader = finders.find("workorders/qr-reader.js")
    assert reader
    assert finders.find("vendor/jsQR.js")
    text = Path(reader).read_text(encoding="utf-8")
    for word in ("classList", "className", "innerHTML"):
        assert word not in text, word
