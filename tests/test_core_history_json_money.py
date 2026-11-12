"""変更の履歴で、JSON の値の中の金額の鍵を、金額の閲覧の無い人に出さない (515 回目の本体の直し)。

訳: 受信した注文に「条件を直して受け入れる」で答えると、変更の履歴に「注文回答の明細」(Inbox.reply_lines。JSON) の前と後が残り、
その中に回答単価 (reply_unit_price) が入っている。履歴は項目の名前で金額かどうかを見分けるので、JSON の項目は金額の項目と読まれず、
金額の閲覧の無い人の履歴に、回答単価の数字がそのまま出ていた (詳細の「返した条件」の行では、単価は前から隠れている)。
514 回目 (注文回答の「送り直す」を、金額の閲覧の無い人にも押せるようにした) の確かめで見つけた。前からの作りで、514 回目が原因ではない。
直し: 金額の閲覧の無い人には、JSON の値から金額の名前の鍵を鍵ごと外して出す。外した後に前と後が同じ項目 (金額だけが変わった) は出さない。
2 社の役とつながりは tests/test_connect_reply_resend.py の fixture と helper を使う (そのファイルは変えない)。
"""

import json
from decimal import Decimal

import pytest
from django.urls import reverse

from seizou.connect import services
from seizou.connect.models import Inbox
from seizou.core import access, history
from tests.test_connect_reply_resend import (  # noqa: F401 (fixture)
    _received,
    connections,
    customer,
    item,
    order_secret,
    own_item,
    reply_secret,
    subcontractor,
    user,
)
from tests.test_core_access import account

#: 作り物の回答単価 (ほかの数と紛れない値)
ANSWERED_PRICE = "31.75"


@pytest.fixture
def answered(settings, monkeypatch, user, subcontractor, customer, item, own_item, connections):  # noqa: F811
    """金額の判定が有効の会社で、単価を 31.75 に直して条件付で受け入れた受信した注文。"""
    settings.SEIZOU = {"access_control": {"department": "off", "write": "on", "money": "on"}}
    _po, inbox = _received(monkeypatch, subcontractor, item, user)
    answers = {1: {"unit_price": Decimal(ANSWERED_PRICE), "qty": Decimal("10"), "due_date": None}}
    services.accept_with_conditions(inbox, {"T-100": own_item}, answers, user)
    inbox = Inbox.objects.get(pk=inbox.pk)
    assert inbox.reply_lines[0]["reply_unit_price"] == ANSWERED_PRICE
    return inbox


def _detail(client, inbox):
    return client.get(reverse("connect:inbox_detail", args=[inbox.pk])).content.decode()


def _reply_lines_item(inbox, viewer):
    """その人の履歴の行のうち、「注文回答の明細」の項目 (無ければ None)。"""
    for row in history.timeline(inbox, viewer).rows:
        for change in row["items"]:
            if change["label"] == "注文回答の明細":
                return change
    return None


@pytest.mark.django_db
def test_history_hides_money_keys_inside_json_from_people_without_money(answered, client):
    client.force_login(account("jimu", permissions=["orders"]))

    body = _detail(client, answered)

    assert ANSWERED_PRICE not in body  # 履歴にも、どこにも回答単価が出ない
    assert "reply_unit_price" not in body
    # 金額でない答え (数量と納期) は、履歴に残る
    shown = _reply_lines_item(answered, account("jimu2", permissions=["orders"]))
    assert shown is not None
    after = json.loads(shown["after"])
    assert after == [{"line_no": 1, "reply_due_date": "2026-11-20", "reply_qty": "10", "reply_type": "CA"}]


@pytest.mark.django_db
def test_history_keeps_money_keys_for_people_with_money(answered, client):
    client.force_login(account("keiri", permissions=["orders"], see_money=True))

    body = _detail(client, answered)

    assert ANSWERED_PRICE in body
    shown = _reply_lines_item(answered, account("keiri2", permissions=["orders"], see_money=True))
    assert json.loads(shown["after"])[0]["reply_unit_price"] == ANSWERED_PRICE


@pytest.mark.django_db
def test_item_with_only_money_change_inside_json_is_dropped(answered):
    """JSON の中の金額だけが変わった項目は、金額の閲覧の無い人には項目ごと出さない (ほかの金額の項目と同じ扱い)。"""
    from seizou.core.models import ChangeLog

    viewer = account("jimu", permissions=["orders"])
    entry = ChangeLog.objects.filter(owner_model="connect.inbox", owner_pk=answered.pk).order_by("-pk").first()
    lines = [{"line_no": 1, "reply_qty": "10", "reply_unit_price": "31.75"}]
    changed = [{"line_no": 1, "reply_qty": "10", "reply_unit_price": "40"}]
    entry.changes = [
        {
            "field": "reply_lines",
            "label": "注文回答の明細",
            "type": "text",
            "before": json.dumps(lines, ensure_ascii=False, sort_keys=True),
            "after": json.dumps(changed, ensure_ascii=False, sort_keys=True),
        }
    ]
    entry.save(update_fields=["changes"])
    # この 1 行だけを見る (ほかの行にも「注文回答の明細」の項目があるため)
    ChangeLog.objects.filter(owner_model="connect.inbox", owner_pk=answered.pk).exclude(pk=entry.pk).delete()

    assert _reply_lines_item(answered, viewer) is None
    assert history.timeline(answered, viewer).rows == []  # 項目が残らない行は、行ごと出さない
    seen = _reply_lines_item(answered, account("keiri", permissions=["orders"], see_money=True))
    assert seen is not None and '"reply_unit_price": "40"' in seen["after"]


def test_without_money_keys_strips_nested_keys():
    value = {"lines": [{"qty": "3", "unit_price": "100", "more": {"amount": "300", "note": "作り物"}}], "total": "300", "order_no": "A-1"}

    assert access.without_money_keys(value) == {"lines": [{"qty": "3", "more": {"note": "作り物"}}], "order_no": "A-1"}
    assert access.without_money_keys("字のまま") == "字のまま"
    assert access.without_money_keys([1, {"tax": 1, "x": 2}]) == [1, {"x": 2}]
