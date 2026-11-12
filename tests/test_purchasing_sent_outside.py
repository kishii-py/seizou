"""つながりの無い取引先への発注を「発注書を送った」で「送った」にする (依頼書 N439。本人の決め 11-12 14:3x と 18:2x)。

訳: つながりの無い取引先への発注は、発注書を刷って seizou の外で送る。送った後に押すボタンが無く、発注は「下書き」のまま残り、
ダッシュボードの「まだ送っていない発注」に数え続けた。押すと状態が「送った」になり、発注の表の「発注書を送った日時」(sent_outside_at) に
押した日時が残る。seizou では答えが届かないので「返事を待つ発注」には数えない。受入は今までどおり記録できる。
seizou の外で送った発注の取消は、相手へ送らずに取り消す (本体の決め 11-12)。
相手への HTTP は、つながりの無い試験では呼ばれたら落とす偽の物、つながりのある取引先では tests/test_connect_send.FakePost にする。
取引先と品目は作り物。
"""

import json
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.connect.models import Connection, Outbox
from seizou.core import access, dashboard, demo, edi_codes
from seizou.core import fields as company_fields
from seizou.core.models import ChangeLog
from seizou.masters import services as masters_services
from seizou.purchasing import services, timeline
from seizou.purchasing.models import PurchaseOrder
from tests.test_connect_reply import ORDER_ENV, SUPPLIER_URL
from tests.test_connect_send import FakePost
from tests.test_connect_signing import _new_secret
from tests.test_core_access import account

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
DAY = date(2026, 11, 12)
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
#: 編集の権限と金額の閲覧の判定が有効の会社 (部署の判定は切)
WRITE_AND_MONEY = {"department": "off", "write": "on", "money": "on"}

#: 押す前に出す説明の 1 文 (本人の決め 11-12 18:2x)
EXPLAIN = "発注書 (印刷用の画面か PDF) を、FAX・郵送・メールなどで取引先に送った後に押します。"
SAVED = "発注書を送った事を記録しました。状態を「送った」にしました。"
#: 発注の詳細の状態の 1 行 (知らせの段 5)。(a) つながりの無い「下書き」の発注、(b) seizou の外で送った発注
ALERT_DRAFT = (
    "この取引先とは seizou でつながっていません。発注書 (印刷用の画面か PDF) を FAX・郵送・メールなどで送り、"
    "送った後に「発注書を送った」を押してください。"
)
ALERT_SENT_HEAD = "発注書を seizou の外で送った発注です ("
# 受入の入口は、明細の行の ⋮ の「受入を登録」(緑の「受入を記録する」は出荷の知らせのある発注にだけ出るので、この発注には出ない。518 回目の本体の直し)
ALERT_SENT_TAIL = "相手の答えと出荷の知らせは、seizou には届きません。現物が戻ったら、明細の行の ⋮ の「受入を登録」で記録します。"
CANCEL_LINE = "この発注は、発注書を seizou の外で送っています。取消は、取引先に自分で伝えてください。"
TIMELINE_TITLE = "発注書を送った (seizou の外で)"
NOT_DRAFT = "記録できません。「発注書を送った」を記録できるのは、状態が「下書き」の発注だけです。"


@pytest.fixture(autouse=True)
def no_http(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("相手への HTTP を呼んだ")

    monkeypatch.setattr("requests.post", refuse)


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def partner(user):
    """seizou でつながっていない協力企業。"""
    return masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)


@pytest.fixture
def linked(user, monkeypatch):
    """取引先に向き「送る」のつながりのある協力企業 (届いたと答える偽の送り口)。"""
    monkeypatch.setenv(ORDER_ENV, _new_secret())
    monkeypatch.setattr("requests.post", FakePost())
    row = masters_services.create_partner({"code": "S002", "name": "作り物のつながる協力企業", "role": "subcontractor"}, user)
    connect_services.register_connection(row, SUPPLIER_URL, ORDER_ENV, "send", user)
    return row


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def judged(settings):
    settings.SEIZOU = {"access_control": dict(WRITE_AND_MONEY)}


@pytest.fixture
def no_registration(monkeypatch):
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def _draft(partner, user, item, qty="10"):
    """相手に送らずに入れた「下書き」の発注 (明細 1 行)。"""
    return services.create_purchase_order(
        partner,
        [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("45")}],
        user,
        order_date=DAY,
        send=False,
    )


def _url(po):
    return reverse("purchasing:purchaseorder_sent_outside", args=[po.pk])


def _send_url(po):
    return reverse("purchasing:purchaseorder_send", args=[po.pk])


def _cancel_url(po):
    return reverse("purchasing:purchaseorder_cancel", args=[po.pk])


def _detail(client, po):
    response = client.get(reverse("purchasing:purchaseorder_detail", args=[po.pk]))
    assert response.status_code == 200
    return response.content.decode()


def _logs(po):
    return list(ChangeLog.objects.filter(owner_model="purchasing.purchaseorder", owner_pk=po.pk).order_by("pk"))


def _shows(body, message):
    """赤の知らせ (1 文目を太字に分けて描く) に message が出ているか。1 文目と残りを分けて探す。"""
    head, _, rest = message.partition("。")
    return f"{head}。" in body and rest in body


def _row(user, key):
    today = dashboard.today_tasks(user)
    return None if today is None else next((row for row in today["rows"] if row["key"] == key), None)


def _count(user, key):
    row = _row(user, key)
    return 0 if row is None else row["count"]


def _listed(client, url):
    response = client.get(url)
    assert response.status_code == 200
    return [entry["obj"] for entry in response.context["rows"]]


# --- (a) 詳細の頭の緑と状態の 1 行 ---


@pytest.mark.django_db
def test_button_and_alert_only_for_unlinked_draft(logged_in, user, partner, linked, item):
    unlinked = _draft(partner, user, item)
    body = _detail(logged_in, unlinked)
    assert f'data-modal-form="{_url(unlinked)}"' in body
    assert "発注書を送った</a>" in body
    assert f'action="{_send_url(unlinked)}"' not in body  # 「再送」は出ない
    assert ALERT_DRAFT in body
    assert "発注書を送った日時" not in body  # 基本情報には出さない (レーンは土台の出し方のまま出していた。518 回目の本体の直しで隠した)

    linked_draft = _draft(linked, user, item)
    body = _detail(logged_in, linked_draft)
    assert f'action="{_send_url(linked_draft)}"' in body  # つながりのある取引先は今のまま「再送」
    assert _url(linked_draft) not in body
    assert ALERT_DRAFT not in body


# --- (b) 小窓で押す ---


@pytest.mark.django_db
def test_modal_records_sent_outside(logged_in, user, partner, item):
    po = _draft(partner, user, item)
    response = logged_in.get(_url(po), **MODAL)
    assert response.status_code == 200
    body = response.content.decode()
    assert EXPLAIN in body
    assert po.po_no in body and partner.name in body
    assert "発注書を送った</button>" in body

    page = logged_in.get(_url(po))  # URL を直に開いた時は画面の全体
    assert page.status_code == 200
    assert EXPLAIN in page.content.decode() and "<html" in page.content.decode()

    before = len(_logs(po))
    response = logged_in.post(_url(po), **MODAL)
    assert response.status_code == 204
    assert json.loads(response["HX-Trigger"]) == {"seizou:saved": {"message": SAVED, "level": "success"}}

    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT
    assert po.sent_outside_at is not None
    new = _logs(po)[before:]
    assert len(new) == 1  # 変更の履歴に 1 行 (状態と一緒に)
    assert [change["field"] for change in new[0].changes] == ["status", "sent_outside_at"]
    assert new[0].actor_id == user.pk

    body = _detail(logged_in, po)
    assert ALERT_SENT_HEAD in body and ALERT_SENT_TAIL in body
    assert ALERT_DRAFT not in body
    assert TIMELINE_TITLE in body
    assert _url(po) not in body  # 押した後は出ない
    assert f'action="{_send_url(po)}"' not in body

    events = [event for event in timeline.events_for(po) if event.title == TIMELINE_TITLE]
    assert [(event.kind, event.when, event.icon) for event in events] == [("sent", po.sent_outside_at, timeline.KIND_ICONS["sent"])]
    assert timeline.stage_band(po).current_name == "送付"


# --- (c) 断り ---


@pytest.mark.django_db
def test_refuses_not_draft_and_linked(logged_in, user, partner, linked, item):
    sent = services.mark_sent(_draft(partner, user, item), user)
    before = len(_logs(sent))
    response = logged_in.post(_url(sent), **MODAL)
    assert response.status_code == 200
    assert _shows(response.content.decode(), NOT_DRAFT)
    sent.refresh_from_db()
    assert (sent.status, sent.sent_outside_at) == (PurchaseOrder.STATUS_SENT, None)
    assert len(_logs(sent)) == before

    linked_draft = _draft(linked, user, item)
    before = len(_logs(linked_draft))
    response = logged_in.post(_url(linked_draft))
    assert response.status_code == 200
    body = response.content.decode()
    assert _shows(body, f"記録できません。取引先 {linked.name} とは seizou でつながっています。「再送」で注文を送ってください。")
    linked_draft.refresh_from_db()
    assert (linked_draft.status, linked_draft.sent_outside_at) == (PurchaseOrder.STATUS_DRAFT, None)
    assert len(_logs(linked_draft)) == before

    with pytest.raises(ValueError, match="^記録できません。"):
        services.mark_sent_outside(PurchaseOrder.objects.get(pk=sent.pk), user)


# --- (d) 権限 ---


def test_action_table():
    from seizou.purchasing.views import PurchaseOrderSentOutsideView

    action = "purchasing.mark_sent_outside"
    assert PurchaseOrderSentOutsideView().get_action() == action
    assert access.ACTION_KINDS[action] == "purchasing"
    assert action not in access.MONEY_ACTIONS


@pytest.mark.django_db
def test_buyer_without_money_can_press(judged, client, user, partner, item):
    po = _draft(partner, user, item)
    client.force_login(account("buyer", permissions=["purchasing"]))
    assert f'data-modal-form="{_url(po)}"' in _detail(client, po)
    response = client.post(_url(po), **MODAL)
    assert response.status_code == 204
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT and po.sent_outside_at is not None


@pytest.mark.django_db
def test_without_purchasing_write_no_button_and_refused(judged, client, user, partner, item):
    po = _draft(partner, user, item)
    other = account("eigyo", permissions=["orders"])
    client.force_login(other)
    assert _url(po) not in _detail(client, po)
    assert client.get(_url(po)).status_code == 403
    response = client.post(_url(po))
    assert response.status_code == 200
    assert _shows(response.content.decode(), access.write_refusal(other, "purchasing.mark_sent_outside"))
    po.refresh_from_db()
    assert (po.status, po.sent_outside_at) == (PurchaseOrder.STATUS_DRAFT, None)


# --- (e) 今日やること ---


@pytest.mark.django_db
def test_today_counts_and_waiting_list(logged_in, user, partner, linked, item):
    po = _draft(partner, user, item)
    assert _count(user, "purchase_draft") == 1
    services.mark_sent_outside(po, user)
    assert _count(user, "purchase_draft") == 0
    assert _count(user, "purchase_sent") == 0  # 押した発注は返事を待つ発注に数えない

    linked_po = services.create_purchase_order(
        linked, [{"item": item, "qty": Decimal("5"), "unit_price": Decimal("45")}], user, order_date=DAY
    )
    assert linked_po.status == PurchaseOrder.STATUS_SENT  # つながりで送って「送った」
    assert _count(user, "purchase_sent") == 1
    row = _row(user, "purchase_sent")
    assert row["url"] == reverse("purchasing:purchaseorder_list") + "?f_waiting_reply=1"
    assert _listed(logged_in, row["url"]) == [PurchaseOrder.objects.get(pk=linked_po.pk)]
    assert list(services.waiting_reply()) == [PurchaseOrder.objects.get(pk=linked_po.pk)]


@pytest.mark.django_db
def test_list_filter_label(logged_in):
    body = logged_in.get(reverse("purchasing:purchaseorder_list"), {"f_waiting_reply": "1"}).content.decode()
    assert 'name="f_waiting_reply" value="1" class="checkbox checkbox-sm" checked data-list-filter>返事を待っている物だけ</label>' in body


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", ("a_machining", "b_plating"))
def test_seed_waiting_count_is_unchanged(company_id, no_registration, media):
    demo.load(COMPANIES_DIR / company_id)
    assert not PurchaseOrder.objects.exclude(sent_outside_at=None).exists()
    # 種の「送った」の発注は送った記録の無い物も含めて、今までどおり全部が返事を待つ発注
    assert dashboard._flow_count("purchase_sent") == PurchaseOrder.objects.filter(status=PurchaseOrder.STATUS_SENT).count()


# --- (f) 受入 ---


@pytest.mark.django_db
def test_receipts_after_sent_outside(user, partner, item):
    po = services.mark_sent_outside(_draft(partner, user, item), user)
    line = po.lines.get()
    services.receive(line, Decimal("4"), DAY, user)
    po.refresh_from_db()
    # 今までどおり: 「送った」の発注は一部の受入では状態を変えない (「一部納品」に進むのは「相手が受け入れた」の発注だけ)
    assert po.status == PurchaseOrder.STATUS_SENT
    assert _count(user, "purchase_sent") == 0
    services.receive(line, Decimal("6"), DAY, user)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_DELIVERED
    assert not Outbox.objects.filter(purchase_order=po).exists()
    assert all(receipt.acceptance_sent_at is None for receipt in po.receipts)  # 検収は送らない (HTTP は no_http が止める)


# --- (g) 取消 ---


@pytest.mark.django_db
def test_cancel_sent_outside_without_sending(logged_in, user, partner, item):
    po = services.mark_sent_outside(_draft(partner, user, item), user)
    body = logged_in.get(_cancel_url(po), **MODAL).content.decode()
    assert CANCEL_LINE in body
    response = logged_in.post(_cancel_url(po), {"reason": "作り物の理由"}, **MODAL)
    assert response.status_code == 204
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_CANCELLED
    assert (po.revision_no, po.purpose_code) == (1, edi_codes.PURPOSE_NEW)
    assert [line.line_status_code for line in po.lines.all()] == [edi_codes.LINE_NEW]
    assert not Outbox.objects.filter(purchase_order=po).exists()

    draft = _draft(partner, user, item)
    assert CANCEL_LINE not in logged_in.get(_cancel_url(draft), **MODAL).content.decode()


@pytest.mark.django_db
def test_cancel_sent_outside_after_receipt_is_refused(user, partner, item):
    po = services.mark_sent_outside(_draft(partner, user, item), user)
    services.receive(po.lines.get(), Decimal("2"), DAY, user)
    with pytest.raises(ValueError) as refused:
        services.cancel_purchase_order(po, user)
    assert str(refused.value) == services._CANCEL_SHIPPED_MESSAGE
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT


@pytest.mark.django_db
def test_cancel_unmarked_sent_after_connection_removed_is_unchanged(user, linked, item):
    po = services.mark_sent(_draft(linked, user, item), user)  # 送った (発注書を送ったの印は無い)
    Connection.objects.filter(partner=linked).delete()
    with pytest.raises(ValueError, match="^取り消せません。取引先 .* へのつながりが無いので、取消を相手へ送れません。"):
        services.cancel_purchase_order(po, user)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_SENT


# --- (h) migration ---


@pytest.mark.django_db(transaction=True)
def test_migration_leaves_existing_orders_empty():
    executor = MigrationExecutor(connection)
    target = [("purchasing", "0015_n439_sent_outside_at")]
    before = [("purchasing", "0014_m01_subcontract_step")]
    executor.migrate(before)
    old = executor.loader.project_state(before).apps
    partner = old.get_model("masters", "Partner").objects.create(code="S901", name="作り物の前の協力企業", role="subcontractor")
    old_order = old.get_model("purchasing", "PurchaseOrder").objects.create(
        po_no="PO-900001", partner_id=partner.pk, order_date=DAY, status="sent"
    )

    executor = MigrationExecutor(connection)
    executor.migrate(target)
    new = executor.loader.project_state(target).apps
    assert new.get_model("purchasing", "PurchaseOrder").objects.get(pk=old_order.pk).sent_outside_at is None
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


# --- 518 回目の本体の直し (レーンの記録の迷った事 3・5 と要望 2) ---


@pytest.mark.django_db
def test_alert_names_the_real_receipt_entrance(logged_in, user, partner, item):
    """押した後の黄の 1 行が指す受入の入口は、その画面に在る (明細の行の ⋮ の「受入を登録」)。頭の右の緑の「受入を記録する」は出ない。"""
    po = services.mark_sent_outside(_draft(partner, user, item), user)

    body = _detail(logged_in, po)

    assert ALERT_SENT_TAIL in body
    assert "受入を登録</a>" in body
    assert "受入を記録する" not in body


@pytest.mark.django_db
def test_stage_band_counts_sent_outside_as_sent(user, partner, item):
    """段の帯の「送付」は、発注書を seizou の外で送った発注でも通った段に数える (全部を受け入れて「納品された」になった後も)。

    訳: 送った記録の行 (Outbox) が無いので、状態が「送った」でなくなると「送付」が通っていない印になっていた (レーンの要望 2)。
    """
    po = services.mark_sent_outside(_draft(partner, user, item), user)
    line = po.lines.get()
    assert 2 in timeline.reached_stages(line)

    services.receive(line, line.qty, DAY, user)
    po.refresh_from_db()
    assert po.status == PurchaseOrder.STATUS_DELIVERED
    assert 2 in timeline.reached_stages(po.lines.get())

    # 押していない「下書き」の発注は、今までどおり「送付」を通っていない
    assert 2 not in timeline.reached_stages(_draft(partner, user, item).lines.get())


@pytest.mark.django_db
def test_sent_outside_at_is_not_in_basic_info(logged_in, user, partner, item):
    """「発注書を送った日時」は、詳細の基本情報と小窓の項目に出さない (押していない発注に「—」の 1 行を増やさない)。

    押した日時は、黄の 1 行と流れの枠の出来事に出る (レーンの迷った事 5)。
    """
    label = PurchaseOrder._meta.get_field("sent_outside_at").verbose_name
    draft = _draft(partner, user, item)
    assert label not in _detail(logged_in, draft)

    po = services.mark_sent_outside(_draft(partner, user, item), user)
    body = _detail(logged_in, po)
    start = body.index('<aside id="detail-aside"')
    assert label not in body[start : body.index("</aside>", start)]  # 右の列 (基本情報のカード) に出ない。履歴のタブには列の名前が出る
    assert ALERT_SENT_HEAD in body and TIMELINE_TITLE in body
    assert "sent_outside_at" in PurchaseOrder.detail_hidden_fields

