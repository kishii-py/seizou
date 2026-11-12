"""ダッシュボードの「今日やること」の、相手に届いていない注文回答・出荷の知らせ・検収の 3 行 (core.dashboard の _UNDELIVERED_TASKS)。

意図: 届かなかった物が、それぞれの詳細の「送り直す」の横にしか出ず、詳細を開かない人は気づかなかった (精査 Q3 の T8・M4)。
本体の決め 11-11 14:2x で、今日やることに「相手に届いていない物」の行を足す。行は受信した注文・出荷・受入の素の一覧へ移る。

作り物の取引先と品番だけを使う。受信した注文・出荷・受入の作り方は、既にある試験の helper を import して使い、そのファイルは変えない
(受信した注文で作った受注 = tests/test_shipping_notice.py の _inbox_order、発注 = tests/test_purchasing_acceptance.py の _order)。
返事の欄 (reply_response・notice_response・acceptance_response) と送った日時と注文回答のタイプは、試験の中で update で入れる
(送る口を通さずに状態だけを作るため)。HTTP は呼ばない (呼んだら落とす)。Django の test client で HTML を見る (本物のブラウザは使わない)。
2 社の作り物の種の読み方は tests/test_core_demo.py の見本 (no_registration と media の fixture) に合わせる。
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse
from django.utils import timezone

from seizou.accounts import services as accounts_services
from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import dashboard, demo, edi_codes
from seizou.masters import services as masters_services
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import Receipt
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from tests.test_core_demo import media, no_registration  # noqa: F401
from tests.test_purchasing_acceptance import _order as purchase_order_of
from tests.test_shipping_notice import _inbox_order

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
SHIPPED_AT = date(2026, 11, 5)
RECEIVED_ON = date(2026, 11, 6)
PASSWORD = "kumo-tests-2611"
SECTION = re.compile(r'<section[^>]*aria-labelledby="flow-guide-title".*?</section>', re.S)
#: 届いていない物の 3 行の key
UNDELIVERED_KEYS = ("reply_undelivered", "notice_undelivered", "acceptance_undelivered")
#: 相手の受け口が応えなかった・断ったときの返事の欄の字 (作り物。送る口が書く字の形)
UNREACHABLE = "届きません: 相手の受け口が応えません。"
REFUSED = "断られた: 署名が合いません。"


@pytest.fixture
def no_http(monkeypatch):
    """HTTP を呼んだら落とす (この試験は送る口を通さない)。"""

    def fail(*args, **kwargs):
        raise AssertionError("今日やることの試験で HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def subcontractor(user):
    return masters_services.create_partner(
        {"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user
    )


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


def answered_inbox(user, customer, item):
    """受け入れた受信した注文 (入口 = 直接)。注文回答のタイプを受諾にし、返事の欄は空のまま。"""
    order = _inbox_order(customer, item, user)
    inbox = Inbox.objects.get(order=order)
    Inbox.objects.filter(pk=inbox.pk).update(reply_type=edi_codes.REPLY_ACCEPTED)
    return inbox


def edi_shipment(user, customer, item):
    """受信した注文で作った受注の出荷 (send=False なので、返事の欄は作り物の種の字)。"""
    order = _inbox_order(customer, item, user)
    shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user, send=False)
    return Shipment.objects.get(order_line__order=order)


def receipt_of(user, subcontractor, item):
    """つながりの無い協力企業への発注の受入 (検収を送らないので、返事の欄は空)。"""
    purchase_order = purchase_order_of(subcontractor, item, user)
    return purchasing_services.receive(purchase_order.lines.get(), Decimal("4"), RECEIVED_ON, user)


def rows_of(user=None):
    today = dashboard.today_tasks(user)
    return [] if today is None else today["rows"]


def row_of(key, user=None):
    """key の行 (無ければ None)。"""
    return next((row for row in rows_of(user) if row["key"] == key), None)


def keys_of(login_id):
    """ログイン ID の人の行の key の並び。利用者の物は控えを持たないよう取り直す。"""
    return [row["key"] for row in rows_of(get_user_model().objects.get(username=login_id))]


def shown(row):
    """行の画面に出る物 (字・件数・単位・ボタンの字・移る先・流れ)。"""
    return (row["label"], row["count"], row["unit"], row["button"], row["url"], row["flow"])


@pytest.mark.django_db
def test_reply_undelivered_row_appears_until_sent(user, customer, item, no_http):
    # (a) 入口が直接で答えた受信した注文の返事が「届きません: …」で、送った日時が空なら 1 件の行が出る
    inbox = answered_inbox(user, customer, item)
    assert row_of("reply_undelivered") is None  # 返事の欄が空 = まだ送っていない

    Inbox.objects.filter(pk=inbox.pk).update(reply_response=UNREACHABLE)
    row = row_of("reply_undelivered")
    assert shown(row) == (
        "相手に届いていない注文回答", 1, "件", "受信した注文", reverse("connect:inbox_list") + "?f_reply_undelivered=1", "sales"
    )
    assert row["url_name"] == "connect:inbox_list"

    # 相手が受け取った (送った日時が入った) ら行が消える
    Inbox.objects.filter(pk=inbox.pk).update(reply_sent_at=timezone.now())
    assert row_of("reply_undelivered") is None

    # 作り物の種の読み込みの字と、注文書の読み取りの字は送る物でないので数えない
    for response in (connect_services.MESSAGE_REPLY_SEED, connect_services.MESSAGE_REPLY_INTAKE):
        Inbox.objects.filter(pk=inbox.pk).update(reply_sent_at=None, reply_response=response)
        assert row_of("reply_undelivered") is None, response

    # 入口が読み取りの行は相手の seizou とつながっていないので数えない
    Inbox.objects.filter(pk=inbox.pk).update(reply_response=UNREACHABLE, source=Inbox.SOURCE_INTAKE)
    assert row_of("reply_undelivered") is None

    # 注文回答のタイプが空でも、送ろうとして届かなかった行は数える (変更と取消の版を受け入れた・断った時の注文回答は、
    # タイプを書かずに送る。保存した後の黄の知らせ = connect.services の _reply_undelivered と同じ見方。506 回目の本体の直し)
    Inbox.objects.filter(pk=inbox.pk).update(source=Inbox.SOURCE_DIRECT, reply_type="")
    assert row_of("reply_undelivered")["count"] == 1
    # まだ答えていない行 (返事の欄が空) は数えない
    Inbox.objects.filter(pk=inbox.pk).update(reply_response="")
    assert row_of("reply_undelivered") is None


@pytest.mark.django_db
def test_reply_row_and_saved_notice_agree(user, customer, item, no_http):
    """今日やることの行と、保存した後の黄の知らせ (connect.services の _reply_undelivered) は、同じ受信した注文を「届いていない」と見る。

    訳: 2 つの見方がずれると、黄の知らせは出たのに行が出ない (か、その逆の) 事が起きる。変更と取消の版の注文回答は注文回答のタイプを
    書かずに送るので、行の側だけがタイプで絞ると、その注文回答を数えなかった (506 回目の本体の直し)。
    """
    inbox = answered_inbox(user, customer, item)
    accepted = edi_codes.REPLY_ACCEPTED
    states = [
        {"reply_response": "", "reply_sent_at": None, "reply_type": accepted},  # まだ送っていない
        {"reply_response": UNREACHABLE, "reply_sent_at": None, "reply_type": accepted},  # 届かなかった
        {"reply_response": UNREACHABLE, "reply_sent_at": None, "reply_type": ""},  # 版の注文回答が届かなかった
        {"reply_response": UNREACHABLE, "reply_sent_at": timezone.now(), "reply_type": accepted},  # 送り直して届いた
        {"reply_response": connect_services.MESSAGE_REPLY_SEED, "reply_sent_at": None, "reply_type": accepted},  # 作り物の種
    ]
    seen = []
    for state in states:
        Inbox.objects.filter(pk=inbox.pk).update(**state)
        noticed = connect_services._reply_undelivered(Inbox.objects.get(pk=inbox.pk))
        assert (row_of("reply_undelivered") is not None) == noticed, state
        seen.append(noticed)
    assert seen == [False, True, True, False, False]


@pytest.mark.django_db
def test_notice_undelivered_row_counts_refused_notices(user, customer, item, no_http):
    # (b) 出荷の知らせの返事が「断られた: …」で、送った日時が空なら 1 件の行が出る (断られた物も数える)
    shipment = edi_shipment(user, customer, item)
    assert shipment.notice_response == shipping_services.MESSAGE_NOTICE_SEED
    assert row_of("notice_undelivered") is None  # 作り物の種の字は数えない

    Shipment.objects.filter(pk=shipment.pk).update(notice_response=REFUSED)
    row = row_of("notice_undelivered")
    assert shown(row) == (
        "相手に届いていない出荷の知らせ", 1, "件", "出荷", reverse("shipping:shipment_list") + "?f_notice_undelivered=1", "sales"
    )

    Shipment.objects.filter(pk=shipment.pk).update(notice_sent_at=timezone.now())
    assert row_of("notice_undelivered") is None

    # 受信した注文で作った受注でない出荷の字は、送る物でないので数えない
    Shipment.objects.filter(pk=shipment.pk).update(
        notice_sent_at=None, notice_response=connect_services.MESSAGE_SHIPPING_NOT_EDI_ORDER
    )
    assert row_of("notice_undelivered") is None


@pytest.mark.django_db
def test_acceptance_undelivered_row_appears_until_sent(user, subcontractor, item, no_http):
    # (c) 受入の検収の返事が「届きません: …」で、送った日時が空なら 1 件の行が出る
    receipt = receipt_of(user, subcontractor, item)
    assert receipt.acceptance_response == ""
    assert row_of("acceptance_undelivered") is None

    Receipt.objects.filter(pk=receipt.pk).update(acceptance_response=UNREACHABLE)
    row = row_of("acceptance_undelivered")
    assert shown(row) == (
        "相手に届いていない検収", 1, "件", "受入", reverse("purchasing:receipt_list") + "?f_acceptance_undelivered=1", "purchase"
    )

    Receipt.objects.filter(pk=receipt.pk).update(acceptance_sent_at=timezone.now())
    assert row_of("acceptance_undelivered") is None

    # seizou どうしで送った発注でない受入の字は、送る物でないので数えない
    Receipt.objects.filter(pk=receipt.pk).update(
        acceptance_sent_at=None, acceptance_response=connect_services.MESSAGE_ACCEPTANCE_NOT_SENT_ORDER
    )
    assert row_of("acceptance_undelivered") is None


def make_all_undelivered(user, customer, subcontractor, item):
    """3 つの届いていない物を 1 件ずつ作る (注文回答・出荷の知らせ・検収)。流れの行 (出荷の残り・返事を待つ発注など) も一緒にできる。"""
    inbox = answered_inbox(user, customer, item)
    Inbox.objects.filter(pk=inbox.pk).update(reply_response=UNREACHABLE)
    shipment = edi_shipment(user, customer, item)
    Shipment.objects.filter(pk=shipment.pk).update(notice_response=REFUSED)
    receipt = receipt_of(user, subcontractor, item)
    Receipt.objects.filter(pk=receipt.pk).update(acceptance_response=UNREACHABLE)


@pytest.mark.django_db
def test_rows_follow_their_flow(user, customer, subcontractor, item, no_http):
    # (d) 受注の流れの行の後に注文回答と出荷の知らせ、発注の流れの行の後に検収
    make_all_undelivered(user, customer, subcontractor, item)
    rows = rows_of()
    keys = [row["key"] for row in rows]
    sales = [row["key"] for row in rows if row["flow"] == "sales"]
    purchase = [row["key"] for row in rows if row["flow"] == "purchase"]
    assert keys == sales + purchase
    assert sales[-2:] == ["reply_undelivered", "notice_undelivered"]
    assert purchase[-1] == "acceptance_undelivered"
    # どちらの流れにも、届いていない物の行の前に流れの段の行がある
    assert len(sales) > 2
    assert len(purchase) > 1
    assert all(row["count"] == 1 for row in rows if row["key"] in UNDELIVERED_KEYS)


@pytest.mark.django_db
def test_rows_follow_department_of_the_target_list(settings, user, customer, subcontractor, item, no_http):
    # (d) 部署で移る先の部品を開けない人の行は外す。注文回答の行は受信した注文 (connect) を開ける人だけ。
    # 出荷の知らせの行は、connect を開けなくても出荷を開けて送り直せる人には出る (送り直す口は出荷の詳細にあり、action の種類は出荷)
    settings.SEIZOU = {"access_control": {"department": "on"}}
    make_all_undelivered(user, customer, subcontractor, item)
    shipping_only = accounts_services.create_department("作り物の受注と出荷の部署", ["orders", "shipping"], ["orders"], None)
    receiving = accounts_services.create_department("作り物の受信した注文の部署", ["orders", "connect"], ["orders"], None)
    buying = accounts_services.create_department("作り物の購買の部署", ["purchasing"], ["purchasing"], None)
    accounts_services.issue_account("shukka", PASSWORD, [shipping_only], [], {}, None)
    accounts_services.issue_account("jushin", PASSWORD, [receiving], [], {}, None)
    accounts_services.issue_account("kobai", PASSWORD, [buying], [], {}, None)

    assert [key for key in keys_of("shukka") if key in UNDELIVERED_KEYS] == ["notice_undelivered"]
    assert [key for key in keys_of("jushin") if key in UNDELIVERED_KEYS] == ["reply_undelivered"]
    assert [key for key in keys_of("kobai") if key in UNDELIVERED_KEYS] == ["acceptance_undelivered"]
    # 部署の判定を通さない呼び方 (user なし) は 3 行とも出す
    assert [row["key"] for row in rows_of() if row["key"] in UNDELIVERED_KEYS] == list(UNDELIVERED_KEYS)


@pytest.mark.django_db
def test_rows_follow_write_permission_of_the_resend_action(settings, user, customer, subcontractor, item, no_http):
    # (d) 行の action (送り直す口と同じ connect.send_shipping_notice) を行えない人の行は外す。編集の判定が on で伝票編集の権限が無い人には出ない
    settings.SEIZOU = {"access_control": {"department": "on", "write": "on"}}
    make_all_undelivered(user, customer, subcontractor, item)
    shipping_only = accounts_services.create_department("作り物の受注と出荷の部署", ["orders", "shipping"], ["orders"], None)
    accounts_services.issue_account("miru", PASSWORD, [shipping_only], [], {}, None)
    accounts_services.issue_account("kaku", PASSWORD, [shipping_only], ["slip_edit"], {}, None)

    assert "notice_undelivered" not in keys_of("miru")
    assert "notice_undelivered" in keys_of("kaku")


@pytest.mark.django_db
def test_home_shows_undelivered_row_with_count_and_button(logged_in, user, customer, item, no_http):
    # (e) ダッシュボードを開くと、行の字と件数と、移る先のボタンの字が出る
    inbox = answered_inbox(user, customer, item)
    Inbox.objects.filter(pk=inbox.pk).update(reply_response=UNREACHABLE)

    response = logged_in.get(reverse("core:home"))
    assert response.status_code == 200
    section = SECTION.search(response.content.decode()).group(0)
    assert '相手に届いていない注文回答 <span class="font-bold tabular-nums">1</span> 件' in section
    assert f'href="{reverse("connect:inbox_list")}?f_reply_undelivered=1">受信した注文<' in section


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", ("a_machining", "b_plating"))
def test_seeds_show_no_undelivered_rows(company_id, db, no_registration, media):  # noqa: F811
    # (f) 2 社の作り物の種を読んだ置き場では 3 行が出ない (種の読み込みは送らないので、数えない字だけが入る)
    demo.load(COMPANIES_DIR / company_id)
    assert not [row["key"] for row in rows_of() if row["key"] in UNDELIVERED_KEYS]
    # 種が返事の欄に書いた字は、数えない字だけ (11-12 に見た時は、B 社に注文回答と出荷の知らせの種の字があり、A 社は空)
    written = {
        *Inbox.objects.exclude(reply_response="").values_list("reply_response", flat=True),
        *Shipment.objects.exclude(notice_response="").values_list("notice_response", flat=True),
        *Receipt.objects.exclude(acceptance_response="").values_list("acceptance_response", flat=True),
    }
    assert written <= {
        connect_services.MESSAGE_REPLY_SEED,
        connect_services.MESSAGE_REPLY_INTAKE,
        connect_services.MESSAGE_SHIPPING_NOT_EDI_ORDER,
        connect_services.MESSAGE_ACCEPTANCE_NOT_SENT_ORDER,
        shipping_services.MESSAGE_NOTICE_SEED,
    }
