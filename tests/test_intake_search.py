"""読み取った注文書の注文日と金額の列と、一覧の絞りと CSV (エンタープライズ化の調べ 第一弾の候補 22。依頼書 N367 の 2。本人の決め 11-08 00:37)。

- 注文日は読み取った注文日の字。金額は「読み取った数量の字 × 読み取った単価の字」の合計で、単価が 1 つでも空なら空。
  列に入らない合計 (小数 3 位以下が残る) は丸めずに空。受注の金額の式では計算しない。
- 一覧で注文日・金額・得意先で絞れ、CSV は一覧と同じ列を出す。金額の閲覧の無い人には金額の列と絞りを出さない (土台)。
- migration 0004 の RunPython の関数は、今ある行 (extracted だけを持つ) の注文日と金額を埋める。
作り物の得意先と、本物の API を呼ばない偽の返事だけを使う (形は tests/test_intake_extract.py の reply_data・upload・no_real_api・
with_key・fake_reply を写した)。
"""

import csv
import importlib
import io
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.apps import apps as django_apps
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.intake import services
from seizou.intake.models import Extraction
from seizou.masters.services import create_partner

REPLY_PATH = Path(__file__).resolve().parent / "fixtures" / "intake" / "order_reply.json"
FAKE_KEY = "test-key-for-intake-tests-not-real"
PDF_BYTES = b"%PDF-1.4\n% made for tests only\n"
PASSWORD = "kumo-tests-2611"
#: 金額の閲覧だけを効かせる (部署と編集の権限は見ない)
MONEY_ON = {"department": "off", "write": "off", "money": "on"}
MIGRATION = "seizou.intake.migrations.0004_extraction_order_date_amount"


def reply_data():
    """作り物の返事の辞書 (毎回読み直すので、テストの中で書き換えてよい)。"""
    return json.loads(REPLY_PATH.read_text(encoding="utf-8"))


def upload(data=PDF_BYTES, name="order.pdf", content_type="application/pdf"):
    return SimpleUploadedFile(name, data, content_type=content_type)


def refuse_api(model, block):
    raise AssertionError("テストが本物の API を呼ぼうとした")


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


@pytest.fixture
def customer(user):
    return create_partner({"code": "IN01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def other_customer(user):
    return create_partner({"code": "IN02", "name": "作り物の別の得意先", "role": "customer"}, user)


def _reply(order_date="2026-11-02", lines=(("10", "1180"), ("3", "250.5"))):
    """作り物の返事の字。lines は (数量, 単価) の並び (単価 None は読めなかった単価)。"""
    data = reply_data()
    data["order_date"] = order_date
    template = data["lines"][0]
    data["lines"] = [
        {**template, "line_no": number, "part_no": f"X-{number}00", "qty": qty, "unit_price": unit_price}
        for number, (qty, unit_price) in enumerate(lines, start=1)
    ]
    return json.dumps(data, ensure_ascii=False)


def _extract(monkeypatch, customer, user, **reply):
    monkeypatch.setattr(services, "_ask_claude", lambda model, block: _reply(**reply))
    try:
        return services.extract_order(upload(), customer, user)
    finally:
        monkeypatch.setattr(services, "_ask_claude", refuse_api)


def _account(login_id, **flags):
    accounts_services.issue_account(login_id, PASSWORD, [], [], flags, None)
    return get_user_model().objects.get(username=login_id)


def _pks(response):
    return {row.pk for row in response.context["object_list"]}


def _csv_rows(response):
    return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))


# ---------------------------------------------------------------- extract_order が書く列


def test_extract_writes_order_date_and_amount(with_key, monkeypatch, customer, user):
    """(a) 単価のある 2 行: 注文日は読んだ日付、金額は 10 × 1180 + 3 × 250.5 = 12551.50。"""
    record = _extract(monkeypatch, customer, user)

    saved = Extraction.objects.get(pk=record.pk)
    assert saved.order_date == date(2026, 11, 2)
    assert saved.amount == Decimal("12551.50")


def test_missing_unit_price_or_date_leaves_blank(with_key, monkeypatch, customer, user):
    """(b) 単価が 1 行でも空なら金額は空。注文日が空なら注文日は空。"""
    no_price = _extract(monkeypatch, customer, user, lines=(("10", "1180"), ("5", None)))
    no_date = _extract(monkeypatch, customer, user, order_date=None)

    no_price.refresh_from_db()
    no_date.refresh_from_db()
    assert no_price.amount is None and no_price.order_date == date(2026, 11, 2)
    assert no_date.order_date is None and no_date.amount == Decimal("12551.50")


def test_amount_with_third_decimal_place_is_blank(with_key, monkeypatch, customer, user):
    """(c) 2.5 × 10.25 = 25.625 は列 (小数 2 位) に入らないので、丸めずに空。"""
    record = _extract(monkeypatch, customer, user, lines=(("2.5", "10.25"),))

    record.refresh_from_db()
    assert record.amount is None


def test_trailing_zeros_do_not_blank_amount():
    """読み取った字の末尾の 0 (1180.000) は値を変えないので、金額は空にしない (読むだけの関数を直に呼ぶ)。"""
    cleaned = {"order_date": None, "lines": [{"qty": "10", "unit_price": "1180.000"}]}

    assert services._order_date_and_amount(cleaned) == (None, Decimal("11800"))


# ---------------------------------------------------------------- 一覧の絞りと CSV


@pytest.fixture
def three(with_key, monkeypatch, customer, other_customer, user):
    """注文日と金額の違う 3 つの読み取り (得意先は 2 つ)。"""
    return {
        "early_small": _extract(monkeypatch, customer, user, order_date="2026-10-05", lines=(("2", "500"),)),
        "mid_large": _extract(monkeypatch, customer, user, order_date="2026-11-02", lines=(("10", "5000"),)),
        "late_other": _extract(monkeypatch, other_customer, user, order_date="2026-11-20", lines=(("4", "6000"),)),
    }


def test_list_filters_by_order_date_amount_and_customer(client_logged_in, three, customer):
    """(d) 注文日・金額・得意先の絞りで、当たる行だけが出る。"""
    url = reverse("intake:extraction_list")

    by_date = client_logged_in.get(url, {"f_order_date_from": "2026-11-01", "f_order_date_to": "2026-11-30"})
    by_amount = client_logged_in.get(url, {"f_amount_from": "20000", "f_amount_to": "30000"})
    by_customer = client_logged_in.get(url, {"f_customer": str(customer.pk)})
    all_three = client_logged_in.get(url, {"f_customer": str(customer.pk), "f_order_date_from": "2026-11-01", "f_amount_from": "10000"})

    assert _pks(by_date) == {three["mid_large"].pk, three["late_other"].pk}
    assert _pks(by_amount) == {three["late_other"].pk}
    assert _pks(by_customer) == {three["early_small"].pk, three["mid_large"].pk}
    assert _pks(all_three) == {three["mid_large"].pk}
    body = by_date.content.decode()
    for name in ("f_order_date_from", "f_amount_from", "f_customer"):
        assert f'name="{name}"' in body


def test_csv_has_order_date_amount_and_customer(client_logged_in, three):
    """(e) CSV に「注文日」「金額」「得意先」の列があり、値は一覧と同じ。"""
    response = client_logged_in.get(
        reverse("intake:extraction_list"), {"format": "csv", "f_amount_from": "20000", "f_amount_to": "30000"}
    )

    rows = _csv_rows(response)
    header = rows[0]
    assert {"注文日", "金額", "得意先"} <= set(header)
    assert len(rows) == 2
    line = dict(zip(header, rows[1]))
    assert line["注文日"] == "2026-11-20"
    assert line["得意先"].endswith("作り物の別の得意先")
    assert Decimal(line["金額"].replace(",", "").replace("¥", "")) == Decimal("24000")


def test_amount_column_and_filter_hidden_without_money(settings, client, three):
    """(f) money が on で金額の閲覧の無い人には、一覧と CSV に金額の列と絞りを出さず、値を渡しても効かせない。"""
    settings.SEIZOU = {"access_control": dict(MONEY_ON)}
    client.force_login(_account("genba"))
    url = reverse("intake:extraction_list")

    response = client.get(url, {"f_amount_from": "20000", "f_amount_to": "30000"})

    assert response.status_code == 200
    body = response.content.decode()
    assert 'name="f_amount_from"' not in body and 'name="f_amount_to"' not in body
    assert 'name="f_order_date_from"' in body
    assert "金額" not in [str(spec.label) for spec in response.context["columns"]]
    assert _pks(response) == {row.pk for row in three.values()}
    header = _csv_rows(client.get(url, {"format": "csv"}))[0]
    assert "金額" not in header and "注文日" in header


# ---------------------------------------------------------------- migration の RunPython


def test_migration_fills_existing_rows(customer, user):
    """(g) extracted だけを持つ今ある行に migration 0004 の関数を当てると、注文日と金額が埋まる。読めない行は空のまま。"""
    common = {"customer": customer, "file": "intake/2026/11/order.pdf", "read_by": user}
    filled = Extraction.objects.create(extracted=json.loads(_reply()), **common)
    no_price = Extraction.objects.create(extracted=json.loads(_reply(lines=(("1", None),))), **common)
    broken = Extraction.objects.create(extracted={"order_date": "作り物の字", "lines": "作り物の字"}, **common)
    assert filled.order_date is None and filled.amount is None

    importlib.import_module(MIGRATION).fill_order_date_and_amount(django_apps, None)

    for row in (filled, no_price, broken):
        row.refresh_from_db()
    assert (filled.order_date, filled.amount) == (date(2026, 11, 2), Decimal("12551.50"))
    assert (no_price.order_date, no_price.amount) == (date(2026, 11, 2), None)
    assert (broken.order_date, broken.amount) == (None, None)
