"""受信した注文・出荷・受入の一覧の絞り「相手に届いていない物だけ」と、今日やることの 3 行の移る先 (N434)。

意図: 今日やることの「相手に届いていない注文回答」「相手に届いていない出荷の知らせ」「相手に届いていない検収」の行は、
受信した注文・出荷・受入の素の一覧へ移っていたので、移った先で届いていない物を人が探していた (N416 の記録の要望)。
本体の決め 11-12 13:4x で、数える関数を部品の services に移し (connect.services.replies_undelivered・
shipping.services.notices_undelivered・purchasing.services.acceptances_undelivered)、一覧の絞りと今日やることの行が
同じ関数を使う (N406c の指示書の一覧の絞りと同じ形)。今日やることの 3 行は、その絞りを付けた一覧を開く。

作り物の取引先と品番だけを使う。届いていない物の作り方は tests/test_core_today_undelivered.py の helper と fixture を
import して使い、そのファイルの helper は変えない。返事の欄と送った日時は update で入れる (送る口を通さずに状態だけを作る)。
HTTP は呼ばない (呼んだら落とす)。Django の test client で HTML を見る (本物のブラウザは使わない)。
"""

from dataclasses import dataclass

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from seizou.connect import services as connect_services
from seizou.connect.models import Inbox
from seizou.core import dashboard
from seizou.purchasing import services as purchasing_services
from seizou.purchasing.models import Receipt
from seizou.shipping import services as shipping_services
from seizou.shipping.models import Shipment
from tests.test_core_today_undelivered import REFUSED, UNREACHABLE, answered_inbox, edi_shipment, receipt_of, row_of
from tests.test_core_today_undelivered import customer, item, no_http, subcontractor  # noqa: F401 (fixture)

pytestmark = pytest.mark.django_db


@dataclass(frozen=True)
class Kind:
    """届いていない物の 1 種類 (今日やることの行の key ごと)。"""

    model: type
    #: 返事の欄と送った日時の列の名前
    response_field: str
    sent_field: str
    #: 移る先の一覧の URL の名前と、絞りの印の字
    url_name: str
    label: str
    #: 絞りの前に並ぶ、今ある絞りの名前 (並びを変えない)
    filters_before: tuple
    #: 送る物でない字 (作り物の種の読み込みの字。受入には種の字が無いので、seizou どうしで送った発注でない受入の字)
    not_sent: str
    #: 部品の services の関数の名前 (直す前に試験を集められるよう、呼ぶ時に引く)
    part_module: object
    part_name: str
    #: core の関数の名前 (core.dashboard の中)
    core_name: str

    def part(self):
        return getattr(self.part_module, self.part_name)()

    def core(self):
        return getattr(dashboard, self.core_name)()


KINDS = {
    "reply_undelivered": Kind(
        model=Inbox,
        response_field="reply_response",
        sent_field="reply_sent_at",
        url_name="connect:inbox_list",
        label="注文回答が届いていない物だけ",
        filters_before=("status", "partner", "received"),
        not_sent=connect_services.MESSAGE_REPLY_SEED,
        part_module=connect_services,
        part_name="replies_undelivered",
        core_name="_replies_undelivered",
    ),
    "notice_undelivered": Kind(
        model=Shipment,
        response_field="notice_response",
        sent_field="notice_sent_at",
        url_name="shipping:shipment_list",
        label="出荷の知らせが届いていない物だけ",
        filters_before=("customer", "shipped"),
        not_sent=shipping_services.MESSAGE_NOTICE_SEED,
        part_module=shipping_services,
        part_name="notices_undelivered",
        core_name="_notices_undelivered",
    ),
    "acceptance_undelivered": Kind(
        model=Receipt,
        response_field="acceptance_response",
        sent_field="acceptance_sent_at",
        url_name="purchasing:receipt_list",
        label="検収が届いていない物だけ",
        filters_before=(),
        not_sent=connect_services.MESSAGE_ACCEPTANCE_NOT_SENT_ORDER,
        part_module=purchasing_services,
        part_name="acceptances_undelivered",
        core_name="_acceptances_undelivered",
    ),
}
KEYS = tuple(KINDS)


@pytest.fixture
def make(user, customer, subcontractor, item, no_http):  # noqa: F811
    """key の種類の物を 1 つ作り、返事の欄を response にして (sent が真なら送った日時も入れて) pk を返す関数。"""

    def make_one(key, response, *, sent=False):
        if key == "reply_undelivered":
            made = answered_inbox(user, customer, item)
        elif key == "notice_undelivered":
            made = edi_shipment(user, customer, item)
        else:
            made = receipt_of(user, subcontractor, item)
        kind = KINDS[key]
        kind.model.objects.filter(pk=made.pk).update(
            **{kind.response_field: response, kind.sent_field: timezone.now() if sent else None}
        )
        return made.pk

    return make_one


def four_of(make, key):
    """届いていない物 2 つ (届かない・断られた)・届いた物 1 つ・送る物でない物 1 つを作り、(届いていない物の pk, 全部の pk) を返す。"""
    undelivered = {make(key, UNREACHABLE), make(key, REFUSED)}
    others = {make(key, UNREACHABLE, sent=True), make(key, KINDS[key].not_sent)}
    return undelivered, undelivered | others


def listed(client, url):
    """一覧を開き、並んだ行の pk の集まりを返す。"""
    response = client.get(url)
    assert response.status_code == 200
    return {row["pk"] for row in response.context["rows"]}


def filtered_url(key):
    return reverse(KINDS[key].url_name) + f"?f_{key}=1"


@pytest.mark.parametrize("key", KEYS)
def test_today_row_opens_the_list_of_only_undelivered(key, make, logged_in):
    # (a) 今日やることの行の件数が 2 で、行の URL を開いた一覧にその 2 つだけが並ぶ (件数と一覧が同じ)
    undelivered, _everything = four_of(make, key)
    row = row_of(key)
    assert row["count"] == 2
    assert row["url"] == filtered_url(key)
    assert listed(logged_in, row["url"]) == undelivered
    assert len(undelivered) == row["count"]


@pytest.mark.parametrize("key", KEYS)
def test_plain_list_is_unchanged_and_shows_the_flag(key, make, logged_in):
    # (b) 絞りを付けない一覧は今までどおり全部が並び、絞りの枠の最後に印の字が出る (ほかの絞りと並びは変えない)
    _undelivered, everything = four_of(make, key)
    kind = KINDS[key]
    response = logged_in.get(reverse(kind.url_name))
    assert response.status_code == 200
    assert {row["pk"] for row in response.context["rows"]} == everything
    assert [spec["name"] for spec in response.context["filters"]] == [*kind.filters_before, key]
    body = response.content.decode()
    assert f'name="f_{key}" value="1" class="checkbox checkbox-sm" data-list-filter>{kind.label}</label>' in body
    # 印を入れると、印は入の形で描かれる
    body = logged_in.get(filtered_url(key)).content.decode()
    assert f'name="f_{key}" value="1" class="checkbox checkbox-sm" checked data-list-filter>{kind.label}</label>' in body


def test_revision_reply_without_type_is_listed(make, logged_in):
    # (c) 注文回答のタイプが空で、送ろうとして届かなかった受信した注文 (変更と取消の版を受け入れた・断った時の注文回答は、
    # タイプを書かずに送る) も絞りの一覧に並ぶ。保存した後の黄の知らせ (connect.services の _reply_undelivered) と同じ見方
    pk = make("reply_undelivered", UNREACHABLE)
    Inbox.objects.filter(pk=pk).update(reply_type="")
    assert connect_services._reply_undelivered(Inbox.objects.get(pk=pk))
    assert row_of("reply_undelivered")["count"] == 1
    assert listed(logged_in, filtered_url("reply_undelivered")) == {pk}
    # まだ答えていない行 (返事の欄が空) は並ばない
    Inbox.objects.filter(pk=pk).update(reply_response="")
    assert not connect_services._reply_undelivered(Inbox.objects.get(pk=pk))
    assert listed(logged_in, filtered_url("reply_undelivered")) == set()


def test_inbox_poll_keeps_the_undelivered_flag(make, logged_in):
    # 受信した注文の一覧は 2 秒ごとに新しい行を取りに行き、絞りの欄 (data-list-filter) の今の値も送る (connect/_inbox_poll.html)。
    # 印を入れた一覧の取りに行きは、届いていない注文回答の行だけを足す (まだ答えていない新しい行は足さない)
    first = make("reply_undelivered", UNREACHABLE)
    body = logged_in.get(filtered_url("reply_undelivered")).content.decode()
    assert 'hx-include="[data-list-filter]"' in body
    newer = make("reply_undelivered", REFUSED)
    make("reply_undelivered", "")  # まだ答えていない行 (返事の欄が空)
    poll = logged_in.get(
        reverse("connect:inbox_list"), {"after": str(first), "f_reply_undelivered": "1"}, HTTP_HX_REQUEST="true"
    ).content.decode()
    assert poll.count('id="row-') == 1 and f'id="row-{newer}"' in poll


@pytest.mark.parametrize("key", KEYS)
def test_part_function_and_core_function_agree(key, make):
    # (d) 部品の関数と core の関数が同じ pk の集まりを返す
    undelivered, _everything = four_of(make, key)
    kind = KINDS[key]
    part = set(kind.part().values_list("pk", flat=True))
    core = set(kind.core().values_list("pk", flat=True))
    assert part == core == undelivered


def count_queries(client, url):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
    assert response.status_code == 200
    return len(queries), {row["pk"] for row in response.context["rows"]}


@pytest.mark.parametrize("key", KEYS)
def test_filtered_list_queries_do_not_grow(key, make, logged_in):
    # (e) 絞りを付けた一覧の問い合わせの数は、届いていない物の数で増えない (1 つのときと 4 つのときで同じ)
    first = make(key, UNREACHABLE)
    url = filtered_url(key)
    logged_in.get(url)  # 最初の 1 回の控え (session など) を外す
    one, rows = count_queries(logged_in, url)
    assert rows == {first}

    more = {make(key, UNREACHABLE), make(key, REFUSED), make(key, UNREACHABLE)}
    four, rows = count_queries(logged_in, url)
    assert rows == {first, *more}
    assert four == one
