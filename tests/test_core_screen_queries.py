"""全部の画面を 2 社の種で開き、画面ごとの問い合わせの数の上限を見る (精査 A4 の 20)。

画面の目録 (seizou.core.screens.all_screens) の全部を、管理者で Django の test client から GET で開く。
引数の無い画面はそのまま開き、引数が <int:pk> の 1 つだけの画面は view_class.model の最初の行の pk で開く。
ほかの引数の画面と、model の行の無い画面は飛ばし、名前を下の一覧に書いて数を固定する (黙って減らない)。
応答は 200・302・404 のどれかで、SQL の数は上限の表 (無ければ共通の上限) 以下であること。上限だけを見るので、数を下げる直しは通る。
上限の表は 11-06 に実測した数 (2 社で大きい方) に 5 を足した値。共通の上限は実測の最大に 10 を足した値。
設定と休日の控え (conf と calendar) は画面ごとに捨てる。どの画面も控えの読みを 1 回ずつ含むので、数は開く順と所要の時間に依らない。
名前は作り物だけ (examples/companies の種)。
"""

import re
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import resolve, reverse

from seizou.core import calendar, conf, demo
from seizou.core import fields as company_fields
from seizou.core.screens import all_screens

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
COMPANIES = ("a_machining", "b_plating")
OK_STATUS = {200, 302, 404}
PK_ONLY = ["<int:pk>"]

# 引数が <int:pk> の 1 つだけでない画面 (2 社で同じ)。目録に引数の作り方が無いので飛ばす
SKIPPED_OTHER_ARGS = {
    "core:setting_update",  # <str:key>
    "workorders:plan_board",  # <int:process_pk>
}
# 引数が <int:pk> の 1 つだけで、model の行が種に無いので飛ばす画面 (会社ごと)
SKIPPED_NO_ROW = {
    "a_machining": {
        "attachments:attachment_delete",
        "connect:inbox_accept",
        "connect:inbox_accept_conditional",
        "connect:inbox_detail",
        "connect:inbox_hold",
        "connect:inbox_reject",
        "connect:exchange_detail",  # 種は送り合いをしない (発注・出荷・受け入れは send=False) ので送り合った記録の行が無い (C106)
        "feedback:feedback_detail",
        "feedback:feedback_update",
        "intake:extraction_accept",
        "intake:extraction_detail",
        "masters:itemprice_delete",  # 種に単価の行が無い (C98。種を足すなら外す)
        "masters:itemprice_update",
        "progress:progress_delete",  # 種に実績の行が無い (C103)
        "progress:progress_update",
        "purchasing:selfinvoice_detail",  # 種に仕入明細の行が無い (C111。種を足すなら外す)
        "connect:receivedselfinvoice_detail",  # 種に受けた仕入明細の行が無い (C111。種を足すなら外す)
        "connect:receivedselfinvoice_answer",
        "purchasing:selfinvoice_cancel",
        "purchasing:selfinvoice_accept",
    },
    "b_plating": {
        "attachments:attachment_delete",
        "connect:exchange_detail",  # 種は送り合いをしない (発注・出荷・受け入れは send=False) ので送り合った記録の行が無い (C106)
        "feedback:feedback_detail",
        "feedback:feedback_update",
        "intake:extraction_accept",
        "intake:extraction_detail",
        "manuals:manualversion_detail",
        "manuals:manualversion_restore",
        "masters:itemprice_delete",  # 種に単価の行が無い (C98。種を足すなら外す)
        "masters:itemprice_update",
        "progress:progress_delete",  # 種に実績の行が無い (C103)
        "progress:progress_update",
        "purchasing:purchaseorder_cancel",
        "purchasing:purchaseorder_change",
        "purchasing:purchaseorder_detail",
        "purchasing:purchaseorder_send",
        "purchasing:receipt_detail",
        "purchasing:selfinvoice_detail",  # 種に仕入明細の行が無い (C111。種を足すなら外す)
        "connect:receivedselfinvoice_detail",  # 種に受けた仕入明細の行が無い (C111。種を足すなら外す)
        "connect:receivedselfinvoice_answer",
        "purchasing:selfinvoice_cancel",
        "purchasing:selfinvoice_accept",
    },
}

# url_name → SQL の数の上限 (11-06 の実測の 2 社で大きい方 + 5。並びは目録の順)
LIMITS = {
    "core:home": 54,
    "core:search": 20,
    "core:manual": 19,
    "core:settings": 20,
    "core:company_update": 20,
    "core:display_settings": 20,
    "masters:item_list": 21,
    "masters:item_create": 19,
    "masters:item_detail": 31,
    "masters:item_update": 20,
    "masters:item_modal": 8,
    "masters:partner_list": 21,
    "masters:partner_create": 19,
    "masters:partner_detail": 39,
    "masters:partner_update": 20,
    "masters:partner_modal": 8,
    "masters:process_list": 21,
    "masters:process_create": 19,
    "masters:process_detail": 22,
    "masters:process_update": 20,
    "masters:item_import": 19,
    "masters:partner_import": 19,
    "masters:item_drawing_viewer": 24,
    "masters:item_drawing_restore": 22,
    "masters:holiday_list": 48,
    "masters:holiday_create": 19,
    "masters:holiday_detail": 23,
    "masters:holiday_delete": 20,
    "orders:order_list": 27,
    "orders:order_create": 19,
    "orders:order_detail": 82,  # 会話のタブ (C104。タブの件数と行・会話の枠の判定と言葉・部署の判定) と、流れの枠が一式の行を受注の明細ごとに 1 回読む (C107。A 社の種は明細 3) を合わせた後の 11-07 の実測 75。仕入明細のタブ (C111。件数と行の 2 回) を足した後の実測 77 + 5
    "orders:order_update": 24,
    "orders:order_cancel": 20,
    "orders:orderline_list": 32,
    "connect:inbox_list": 24,
    "connect:inbox_detail": 26,  # 会話の節 (C104) (11-07 の実測 21 + 5。種に行があるのは b_plating だけ)
    "connect:inbox_accept": 28,
    "connect:inbox_reject": 20,
    "connect:connection_list": 23,
    "connect:connection_create": 20,
    "connect:connection_detail": 23,
    "connect:connection_update": 21,
    "shipping:shipment_list": 22,
    "shipping:shipment_create": 19,
    "shipping:shipment_bulk_create": 19,
    "shipping:shipment_detail": 26,
    "shipping:shipment_update": 20,
    "shipping:deliverynote_list": 22,
    "shipping:deliverynote_create": 20,
    "shipping:deliverynote_detail": 25,
    "shipping:deliverynote_cancel": 20,
    "purchasing:purchaseorder_list": 27,
    "purchasing:purchaseorder_create": 22,
    # 11-07 の実測 45 + 5。送った発注の変更 (C96) の「変更」のボタンを出すか (can_change_order) に 1 回 (取り消していない明細の受入・出荷の知らせ・つながりを 1 つの問い合わせで読む)。
    # 会話のタブとメモの枠 (C104) で +6 (11-07 の実測 51 + 5)。
    "purchasing:purchaseorder_detail": 56,
    "purchasing:purchaseorder_send": 20,
    "purchasing:purchaseorder_cancel": 20,
    "purchasing:receipt_list": 25,
    "purchasing:receipt_create": 19,
    "purchasing:receipt_detail": 25,
    "workorders:workorder_list": 23,
    "workorders:workorder_create": 21,
    "workorders:workorder_detail": 33,
    "workorders:workorder_modal": 8,
    "workorders:workorder_cancel": 20,
    "workorders:plan_index": 29,
    "workorders:plan_detail": 20,
    "progress:progress_list": 23,
    "progress:record_list": 23,
    "progress:progress_create": 20,
    "billing:invoice_list": 23,
    "billing:invoice_create": 23,  # 一式の節 (出し切らずに終えた一式の受注の明細を 1 回で読む問い合わせ) で +1 (11-07 の実測 21 → 22)。一式の行の後に出荷が続いた明細 (発行の請求書の一式の行を 1 回で読む。作者の決め C107-7) で +1 (11-07 の実測 22 → 23。一式の行がある会社では出荷の合計を読む 1 回がさらに足る)
    "billing:invoice_detail": 31,
    "billing:invoice_cancel": 21,
    "billing:payment_create": 23,
    "billing:payment_cancel": 21,
    "intake:extraction_list": 20,
    "intake:extraction_create": 20,
    "attachments:attachment_list": 20,
    "attachments:attachment_add": 19,
    "manuals:manual_list": 23,
    "manuals:manual_create": 20,
    "manuals:manual_detail": 23,
    "manuals:manual_update": 22,
    "manuals:manual_version_create": 22,
    "manuals:manualversion_list": 24,
    "manuals:manual_move": 21,
    "manuals:manual_protect": 20,
    "manuals:manual_delete": 20,
    "manuals:manualversion_detail": 21,
    "manuals:manualversion_restore": 20,
    "manuals:manualfolder_create": 19,
    "manuals:manualfolder_update": 20,
    "manuals:manualfolder_move": 22,
    "manuals:manualfolder_protect": 20,
    "manuals:manualfolder_delete": 20,
    "accounts:access_list": 22,
    "accounts:access_create": 21,
    "accounts:access_update": 24,
    "accounts:access_delete": 21,
    "accounts:department_list": 25,
    "accounts:department_create": 19,
    "accounts:department_detail": 24,
    "accounts:department_update": 20,
    "accounts:department_delete": 22,
    "stock:stock_list": 21,
    "stock:stockmove_list": 30,
    "stock:stockmove_detail": 24,
    "stock:finished_create": 20,
    "stock:issue_create": 19,
    "stock:adjust_create": 19,
    "feedback:feedback_list": 21,  # タブの帯の件数で +1 (依頼書 N321。11-07 の実測 15 → 16)
    "feedback:feedback_create": 20,  # タブの帯の件数で +1 (依頼書 N321。11-07 の実測 14 → 15)
    "notes:note_edit": 19,
}
# 上限の表に無い画面 (後から足した画面) の上限 (11-06 の実測の最大 63 + 10)
DEFAULT_LIMIT = 73
# 上の帯の知らせの鈴 (core/_bell.html。受け渡しの seizou-部品の6案-20261107/指示書.md の 4) がログインした人の全画面で数える分
# (鈴を開いた日時 1 + 受信した注文の件数 1 + フィードバックの直した・見送った・返事の集計 1)。11-06 の実測の後に足した物なので、上限に足す。
BELL_QUERIES = 3


@pytest.fixture
def no_registration(monkeypatch):
    """試験用の settings には会社ごとの項目の登録が無い。ほかの試験の登録が残っていても空にする (test_core_demo.py と同じ)。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def _url(screen):
    """画面を開く URL と、飛ばすときの理由。開けるときは (URL, "")、飛ばすときは ("", 理由)。"""
    args = re.findall(r"<[^>]+>", screen.path)
    if not args:
        return reverse(screen.url_name), ""
    if args != PK_ONLY:
        return "", "引数"
    # 目録の Screen は view の class を持たないので、仮の pk で URL を作って引き直す
    view_class = resolve(reverse(screen.url_name, kwargs={"pk": 1})).func.view_class
    model = getattr(view_class, "model", None)
    row = model._default_manager.order_by("pk").first() if model is not None else None
    if row is None:
        return "", "行が無い"
    return reverse(screen.url_name, kwargs={"pk": row.pk}), ""


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", COMPANIES)
def test_every_screen_stays_under_query_limit(company_id, client, no_registration, media):
    admin = get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")
    demo.load(COMPANIES_DIR / company_id)
    client.force_login(admin)

    screens = all_screens()
    url_names = [screen.url_name for screen in screens]
    assert len(url_names) == len(set(url_names))  # url_name で上限を引くので重ならない

    skipped_args, skipped_no_row, opened, failures = set(), set(), {}, []
    for screen in screens:
        url, reason = _url(screen)
        if reason == "引数":
            skipped_args.add(screen.url_name)
            continue
        if reason:
            skipped_no_row.add(screen.url_name)
            continue
        conf.reset_cache()
        calendar.reset_cache()
        with CaptureQueriesContext(connection) as queries:
            response = client.get(url)
        count = len(queries)
        opened[screen.url_name] = count
        limit = LIMITS.get(screen.url_name, DEFAULT_LIMIT) + BELL_QUERIES
        if response.status_code not in OK_STATUS or count > limit:
            failures.append((screen.url_name, url, response.status_code, count, limit))

    assert skipped_args == SKIPPED_OTHER_ARGS
    assert len(skipped_args) == 2
    assert skipped_no_row == SKIPPED_NO_ROW[company_id]
    assert len(opened) == len(screens) - len(skipped_args) - len(skipped_no_row)
    assert failures == [], "(url_name, URL, 応答, SQL の数, 上限) " + repr(failures)
