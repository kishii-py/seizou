"""注文書の見張り (intake の extract_to_inbox と命令 seizou_intake_watch) のテスト。本物の API は呼ばない (_ask_claude を差し替える)。会社名と品番は作り物。

autouse の fixture が _ask_claude を「呼ばれたら AssertionError」に差し替え、返事が要るテストだけが作り物の返事
(tests/fixtures/intake/order_reply.json の buyer.name を書き換えた物) を返す関数に差し替える。
鍵はテストごとに setenv か delenv で明示する。HTTP は requests.post を「呼んだら落とす」に差し替える。
ファイルは先頭のバイトだけを合わせた作り物の bytes。上げたファイルと見張るフォルダは試験の一時のフォルダに置く。
"""

import json
from datetime import timedelta
from io import StringIO
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from django.utils import timezone

from seizou.connect.models import Inbox
from seizou.intake import services, watch
from seizou.intake.models import Extraction
from seizou.masters.services import create_partner
from seizou.orders.models import Order
from tests.test_connect_send import FakePost
from tests.test_intake_extract import FAKE_KEY, PDF_BYTES, PNG_BYTES, refuse_api, reply_data

UNKNOWN_SENDER = "知らない作り物の会社"


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    fake = FakePost()
    monkeypatch.setattr("requests.post", fake)
    yield fake
    assert fake.calls == []


@pytest.fixture(autouse=True)
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture
def with_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)


@pytest.fixture
def without_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def customer(user):
    return create_partner({"code": "IW01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return create_partner({"code": "IW02", "name": "作り物の協力企業", "role": "subcontractor"}, user)


def answer_with(monkeypatch, sender):
    """_ask_claude を、差出人を sender にした作り物の返事を返す関数に差し替える。呼ばれた model の一覧を返す。"""
    data = reply_data()
    data["buyer"]["name"] = sender
    text = json.dumps(data, ensure_ascii=False)
    calls = []

    def _fake(model, block):
        calls.append(model)
        return text

    monkeypatch.setattr(services, "_ask_claude", _fake)
    return calls


def upload(data=PDF_BYTES, name="order.pdf"):
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def test_extract_to_inbox_matches_sender(with_key, monkeypatch, customer, user):
    answer_with(monkeypatch, "作り物の得意先")

    inbox, created = services.extract_to_inbox(upload(), user)

    assert created is True
    extraction = Extraction.objects.get()
    inbox.refresh_from_db()
    assert inbox.partner == customer
    assert inbox.source == Inbox.SOURCE_INTAKE
    assert inbox.extraction == extraction
    assert inbox.sender_name == "作り物の得意先"
    assert inbox.status == Inbox.STATUS_RECEIVED
    assert inbox.payload == extraction.extracted
    assert extraction.customer == customer
    assert extraction.status == Extraction.STATUS_INBOX
    assert extraction.read_by == user
    assert Order.objects.count() == 0  # R10: 見張りは受注を作らない


def test_unknown_sender_is_waiting(with_key, monkeypatch, customer, user):
    answer_with(monkeypatch, UNKNOWN_SENDER)

    inbox, created = services.extract_to_inbox(upload(), user)

    assert created is True
    inbox.refresh_from_db()
    assert inbox.partner is None
    assert inbox.sender_name == UNKNOWN_SENDER
    assert str(inbox).startswith("注文 差出人の確認待ち")
    extraction = Extraction.objects.get()
    assert extraction.customer is None
    assert extraction.status == Extraction.STATUS_INBOX
    assert str(extraction).startswith("差出人の確認待ち")
    assert Order.objects.count() == 0


def test_two_matching_customers_is_waiting(with_key, monkeypatch, customer, user):
    # 取引先の名前は一意でない (一意なのはコードだけ)。同じ名前が 2 つなら人が選ぶ。
    create_partner({"code": "IW03", "name": "作り物の得意先", "role": "both"}, user)
    answer_with(monkeypatch, "作り物の得意先")

    inbox, _created = services.extract_to_inbox(upload(), user)

    inbox.refresh_from_db()
    assert inbox.partner is None
    assert inbox.sender_name == "作り物の得意先"


@pytest.mark.parametrize(
    ("sender", "code"),
    [
        pytest.param("作り物の　得意先", "IW01", id="full-width-space"),
        pytest.param(" ＸＹＺ作り物 ", "IW04", id="full-width-letters"),
        pytest.param("ｘｙｚ作り物", "IW04", id="case"),
        pytest.param("作り物短縮", "IW05", id="short-name"),
        pytest.param("作り物の協力企業", None, id="subcontractor-only"),
        pytest.param("", None, id="empty"),
    ],
)
def test_sender_matches_by_normalized_name_or_short_name(customer, subcontractor, user, sender, code):
    create_partner({"code": "IW04", "name": "XYZ作り物", "role": "customer"}, user)
    create_partner(
        {"code": "IW05", "name": "作り物の長い名前の得意先", "short_name": "作り物短縮", "role": "both"}, user
    )

    found = services._match_sender(sender)

    assert (found.code if found is not None else None) == code


def test_same_content_is_one_inbox_row(with_key, monkeypatch, customer, user):
    calls = answer_with(monkeypatch, "作り物の得意先")

    first, first_created = services.extract_to_inbox(upload(), user)
    second, second_created = services.extract_to_inbox(upload(name="order-again.pdf"), user)

    assert (first_created, second_created) == (True, False)
    assert second.pk == first.pk
    assert len(calls) == 1  # 2 回目は API を呼ばない
    assert Extraction.objects.count() == 1
    assert Inbox.objects.count() == 1  # R11


def test_watched_extraction_is_not_ordered_from_intake_screen(with_key, monkeypatch, client, customer, user):
    answer_with(monkeypatch, UNKNOWN_SENDER)
    services.extract_to_inbox(upload(), user)
    extraction = Extraction.objects.get()

    with pytest.raises(ValueError, match=services.MESSAGE_IN_INBOX):
        services.accept_extracted(extraction, {}, user)
    assert Order.objects.count() == 0

    client.force_login(user)
    response = client.get(reverse("intake:extraction_detail", args=[extraction.pk]))
    assert response.status_code == 200
    body = response.content.decode()
    assert "届いた注文へ回した" in body
    assert reverse("intake:extraction_accept", args=[extraction.pk]) not in body


# ここから命令 (seizou_intake_watch)。見張るフォルダは tmp_path/scan、見張りの記録の置き場 (BASE_DIR) は tmp_path/site。


@pytest.fixture
def scan(settings, tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    settings.BASE_DIR = site
    folder = tmp_path / "scan"
    folder.mkdir()
    # 負の値にして、書いた直後のファイルも読む (Windows の CI ではファイルの時刻が time.time() より数ミリ秒先に出て、0 だと読まれない事があった)
    monkeypatch.setattr(watch, "SETTLE_SECONDS", -5)
    return folder


def run_command(*args):
    """命令を打ち、(標準出力, 標準エラー) の字を返す。"""
    out, err = StringIO(), StringIO()
    call_command("seizou_intake_watch", *args, stdout=out, stderr=err)
    return out.getvalue(), err.getvalue()


def test_once_reads_and_moves_to_done(with_key, monkeypatch, scan, customer, user):
    answer_with(monkeypatch, "作り物の得意先")
    (scan / "order.pdf").write_bytes(PDF_BYTES)
    (scan / "memo.txt").write_text("作り物のメモ", encoding="utf-8")

    out, _err = run_command(str(scan), "--once", "--user", "tester")

    assert (scan / "done" / "order.pdf").exists()
    assert not (scan / "order.pdf").exists()
    assert (scan / "memo.txt").exists()  # 読む拡張子でない物は動かさない
    inbox = Inbox.objects.get()
    assert inbox.partner == customer
    assert inbox.source == Inbox.SOURCE_INTAKE
    assert f"読んだ: order.pdf → 届いた注文 {inbox.pk} (取引先 作り物の得意先)" in out
    assert "1 周見ました: 読んだ 1 件・同じ中身 0 件・読めなかった 0 件" in out
    assert not (Path(scan.parent / "site") / watch.RECORD).exists()  # --once は記録を書かない
    assert Order.objects.count() == 0


def test_unreadable_file_goes_to_failed_with_reason(with_key, scan, user):
    (scan / "bad.pdf").write_text("作り物の字だけのファイル", encoding="utf-8")

    out, _err = run_command(str(scan), "--once", "--user", "tester")

    assert (scan / "failed" / "bad.pdf").exists()
    reason = (scan / "failed" / "bad.pdf.txt").read_text(encoding="utf-8")
    assert reason == f"{services.MESSAGE_UNREADABLE_TYPE}\n"
    assert Inbox.objects.count() == 0
    assert Extraction.objects.count() == 0
    assert "読めなかった: bad.pdf → failed/bad.pdf" in out
    assert "読めなかった 1 件" in out


def test_no_key_goes_to_failed_and_keeps_going(without_key, scan, user):
    (scan / "a.pdf").write_bytes(PDF_BYTES)
    (scan / "b.png").write_bytes(PNG_BYTES)
    (scan / "failed").mkdir()
    (scan / "failed" / "a.pdf").write_bytes(b"%PDF-1.4\n% older file for tests\n")  # 同じ名前があれば -2 を付ける

    out, err = run_command(str(scan), "--once", "--user", "tester")

    assert (scan / "failed" / "a-2.pdf").exists()
    assert (scan / "failed" / "b.png").exists()
    for name in ("a-2.pdf.txt", "b.png.txt"):
        assert (scan / "failed" / name).read_text(encoding="utf-8") == f"{services.MESSAGE_NO_KEY}\n"
    assert "鍵 (ANTHROPIC_API_KEY) が無いので" in err
    assert "読めなかった 2 件" in out
    assert Inbox.objects.count() == 0


def test_waiting_row_from_command(with_key, monkeypatch, scan, customer, user):
    answer_with(monkeypatch, UNKNOWN_SENDER)
    (scan / "order.pdf").write_bytes(PDF_BYTES)

    out, _err = run_command(str(scan), "--once", "--user", "tester")

    assert (scan / "done" / "order.pdf").exists()
    inbox = Inbox.objects.get()
    assert inbox.partner is None
    assert f"読んだ: order.pdf → 届いた注文 {inbox.pk} (差出人の確認待ち: {UNKNOWN_SENDER})" in out

    (scan / "again.pdf").write_bytes(PDF_BYTES)  # 同じ中身をもう一度置く
    out, _err = run_command(str(scan), "--once", "--user", "tester")
    assert (scan / "done" / "again.pdf").exists()
    assert f"同じ中身: again.pdf → 届いた注文 {inbox.pk} に入っています" in out
    assert Inbox.objects.count() == 1


def test_folder_from_settings_and_bad_arguments(with_key, monkeypatch, settings, scan, customer, user):
    answer_with(monkeypatch, "作り物の得意先")
    (scan / "order.pdf").write_bytes(PDF_BYTES)
    settings.SEIZOU = {"intake_watch_folder": f" {scan} "}

    out, _err = run_command("--once", "--user", "tester")
    assert "1 周見ました: 読んだ 1 件" in out

    settings.SEIZOU = {}
    with pytest.raises(CommandError, match="見張るフォルダがありません。命令の引数か"):
        run_command("--once", "--user", "tester")
    with pytest.raises(CommandError, match="見張るフォルダがありません: "):
        run_command(str(scan / "nothing"), "--once", "--user", "tester")
    with pytest.raises(CommandError, match="利用者 nobody がいません"):
        run_command(str(scan), "--once", "--user", "nobody")
    with pytest.raises(CommandError, match="1 以上"):
        run_command(str(scan), "--once", "--interval", "0", "--user", "tester")


def test_loop_record_status_and_stop(monkeypatch, settings, scan, user):
    site = Path(settings.BASE_DIR)
    record_file = site / watch.RECORD
    stop_file = site / watch.STOP

    def stop_after_first_round(seconds):
        assert record_file.exists()
        assert watch.request_stop() is True

    monkeypatch.setattr(watch, "_sleep", stop_after_first_round)
    monkeypatch.setattr(watch, "close_old_connections", lambda: None)

    out, _err = run_command(str(scan), "--interval", "5", "--user", "tester")

    assert "見張りを始めました: " in out
    assert "見張りを止めました: " in out
    assert not record_file.exists()
    assert not stop_file.exists()
    assert run_command("--status")[0].strip() == "見張りは動いていません。"
    assert run_command("--stop")[0].strip() == "見張りは動いていません。"

    now = timezone.now()
    record = {"pid": 4242, "folder": str(scan), "interval": 10, "started_at": now.isoformat(), "seen_at": now.isoformat()}
    record_file.write_text(json.dumps(record), encoding="utf-8")
    assert run_command("--status")[0].startswith(f"動いている: {scan} (pid 4242・10 秒ごと")
    with pytest.raises(CommandError, match="既に見張っています"):
        run_command(str(scan), "--user", "tester")
    with pytest.raises(CommandError, match="既に見張っています"):
        run_command(str(scan), "--once", "--user", "tester")
    out, _err = run_command("--stop")
    assert out.startswith("止める合図を置きました。次の見回り (10 秒以内) で止まります。")
    assert stop_file.exists()

    record["seen_at"] = (now - timedelta(hours=1)).isoformat()
    record_file.write_text(json.dumps(record), encoding="utf-8")
    assert run_command("--status")[0].startswith("止まっている (記録が古い): ")
    out, _err = run_command(str(scan), "--user", "tester")  # 古い記録と残った合図は置き直して立てる
    assert "見張りを始めました: " in out
    assert not record_file.exists()
    assert not stop_file.exists()
