"""AI チャットの同じ提案を二度承認しても、受注や出荷を 1 度しか保存しないことの試験 (N351。エンタープライズ化の調べ 第一弾の指摘 ai-1)。

提案は assistant の表 (AssistantProposal) に持ち、session には提案の番号だけを置く。承認は承認待ちを短い atomic で
処理中に移してから書く道具を呼ぶ。session を承認の前の写しに戻しても、2 つ目の承認は「保存済み」になることを見る。

作り物の得意先・部品 (tests/test_assistant_services.py の fixture) と作り物の AI の返事だけを使い、本物の API は呼ばない
(autouse の fixture が _ask_claude を「呼ばれたら AssertionError」に差し替える)。thread は使わない
(SQLite の試験で順が決まらないため。1 つ目の処理中の 2 つ目は、書く道具の偽物の中から呼んで作る)。
"""

import copy

import pytest
from django.contrib.auth import get_user_model

from seizou.assistant import services
from seizou.assistant.models import AssistantProposal
from seizou.core import mcp_tools
from tests import test_assistant_services as base
from tests.test_assistant_services import _session_with, _ship_arguments, _shipments, refuse_api

# fixture を借りる (pytest は module の中の名前から fixture を拾う。そのファイルの fixture は変えない)
user = base.user
order = base.order


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


def _orders():
    from seizou.orders.models import Order

    return Order.objects.count()


def _order_arguments():
    """create_order の作り物の引数 (得意先 AV01 と部品 AV-100 は order の fixture が作る)。"""
    return {"customer_code": "AV01", "order_date": "2026-11-05", "lines": [{"part_no": "AV-100", "qty": "2", "unit_price": "100"}]}


def _row(proposal):
    return AssistantProposal.objects.get(proposal_id=proposal["id"])


def _restore(session, copied):
    """session の中身を、承認の前に取った写しで上書きする (session の書き戻しの代わり)。"""
    session.clear()
    session.update(copy.deepcopy(copied))


# ---- (a) 提案は表に 1 行 ----


@pytest.mark.django_db
def test_propose_makes_one_pending_row(user, order):
    proposal = services.propose("ship", _ship_arguments(order), user)

    row = AssistantProposal.objects.get()
    assert row.state == "pending"
    assert row.proposal_id == proposal["id"]
    assert row.user == user
    assert (row.tool, row.title, row.arguments, row.summary) == ("ship", "出荷", proposal["arguments"], proposal["summary"])
    assert row.created_at.isoformat(timespec="seconds") == proposal["created_at"]
    assert (row.decided_by, row.decided_at) == (None, None)

    # 利用者を渡さない提案も作れる (既にある試験の形)
    anonymous = services.propose("ship", _ship_arguments(order))
    assert _row(anonymous).user is None


@pytest.mark.django_db
def test_session_keeps_only_numbers(user, order):
    proposal = services.propose("ship", _ship_arguments(order), user)

    session = _session_with(proposal)

    assert session[services.HISTORY_KEY][0]["proposals"] == [{"id": proposal["id"]}]


# ---- (b) 承認の前の写しに戻しても 1 件 ----


@pytest.mark.django_db
def test_create_order_with_restored_session_saves_once(user, order):
    proposal = services.propose("create_order", _order_arguments(), user)
    session = _session_with(proposal)
    copied = copy.deepcopy(session)

    first = services.approve(session, proposal["id"], user)
    assert first["state"] == "saved"
    assert _orders() == 2

    _restore(session, copied)
    again = services.approve(session, proposal["id"], user)

    assert again["notice"] == services.MESSAGE_PROPOSAL_SAVED
    assert _orders() == 2


@pytest.mark.django_db
def test_ship_with_restored_session_saves_once(user, order):
    proposal = services.propose("ship", _ship_arguments(order), user)
    session = _session_with(proposal)
    copied = copy.deepcopy(session)

    assert services.approve(session, proposal["id"], user)["state"] == "saved"
    assert _shipments() == 1

    _restore(session, copied)
    again = services.approve(session, proposal["id"], user)

    assert again["notice"] == services.MESSAGE_PROPOSAL_SAVED
    assert _shipments() == 1


@pytest.mark.django_db
def test_question_written_back_after_approve_does_not_reopen(user, order):
    """1 つのタブ: 質問の答えを待つ間に前の提案を承認し、質問の終わりに古い session へ 1 往復を足して書き戻しても、もう一度は保存しない。"""
    proposal = services.propose("ship", _ship_arguments(order), user)
    session = _session_with(proposal)
    copied = copy.deepcopy(session)

    assert services.approve(session, proposal["id"], user)["state"] == "saved"

    _restore(session, copied)
    services.remember(session, {"question": "作り物の質問", "answer": "作り物の答え", "links": [], "tools": [], "proposals": []})
    assert services.history(session)[0]["proposals"][0]["state"] == "saved"
    again = services.approve(session, proposal["id"], user)

    assert again["notice"] == services.MESSAGE_PROPOSAL_SAVED
    assert _shipments() == 1


# ---- (c) 1 つ目が処理中の間の 2 つ目 ----


@pytest.mark.django_db
def test_second_approve_while_first_is_running(monkeypatch, user, order):
    proposal = services.propose("ship", _ship_arguments(order), user)
    session = _session_with(proposal)
    real_write_tools = mcp_tools.write_tools
    inner = []
    called = []

    def fake_write_tools(who):
        real = {fn.__name__: fn for fn in real_write_tools(who)}

        def ship(**kwargs):
            # 本物の道具を呼ぶ前に、同じ提案の承認をもう一度送る (2 つ目のタブ)
            inner.append(services.approve(session, proposal["id"], user))
            called.append(kwargs)
            return real["ship"](**kwargs)

        return (ship,)

    monkeypatch.setattr(mcp_tools, "write_tools", fake_write_tools)

    first = services.approve(session, proposal["id"], user)

    assert first["state"] == "saved"
    (second,) = inner
    assert second["state"] == "running"
    assert second["notice"] == services.MESSAGE_PROPOSAL_SAVED
    assert len(called) == 1
    assert _shipments() == 1
    assert _row(proposal).state == "saved"


# ---- (d) 処理中の行は書く道具を呼ばない ----


@pytest.mark.django_db
def test_running_row_is_not_carried_out(monkeypatch, user, order):
    proposal = services.propose("ship", _ship_arguments(order), user)
    AssistantProposal.objects.filter(proposal_id=proposal["id"]).update(state="running")

    def refuse_write_tools(who):
        raise AssertionError("処理中の提案で書く道具を呼んだ")

    monkeypatch.setattr(mcp_tools, "write_tools", refuse_write_tools)

    result = services.approve(_session_with(proposal), proposal["id"], user)

    assert result["notice"] == services.MESSAGE_PROPOSAL_SAVED
    assert _shipments() == 0
    assert _row(proposal).state == "running"


# ---- (e) session に無い番号 ----


@pytest.mark.django_db
def test_number_not_in_session_is_missing(user, order):
    mine = services.propose("ship", _ship_arguments(order), user)
    other = services.propose("ship", _ship_arguments(order), user)

    result = services.approve(_session_with(mine), other["id"], user)

    assert result["state"] == "missing"
    assert result["notice"] == services.MESSAGE_PROPOSAL_MISSING
    assert _shipments() == 0
    assert _row(other).state == "pending"


# ---- (f) 窓の履歴は表から描く ----


@pytest.mark.django_db
def test_history_reads_numbers_from_table(user, order, django_assert_num_queries):
    proposal = services.propose("ship", _ship_arguments(order), user)
    session = _session_with(proposal)

    with django_assert_num_queries(1):
        (exchange,) = services.history(session)
    assert exchange["proposals"] == [proposal]

    AssistantProposal.objects.filter(proposal_id=proposal["id"]).update(state="saved", notice="保存しました。作り物の知らせ")
    (changed,) = services.history(session)[0]["proposals"]
    assert (changed["state"], changed["notice"]) == ("saved", "保存しました。作り物の知らせ")

    # 表に無い番号は飛ばす
    session[services.HISTORY_KEY] = [
        {**session[services.HISTORY_KEY][0], "proposals": [{"id": "0123456789abcdef"}, {"id": proposal["id"]}]}
    ]
    assert [item["id"] for item in services.history(session)[0]["proposals"]] == [proposal["id"]]


@pytest.mark.django_db
def test_history_without_numbers_reads_no_table(django_assert_num_queries):
    session = {}
    services.remember(session, {"question": "作り物の質問", "answer": "作り物の答え", "links": [], "tools": [], "proposals": []})

    with django_assert_num_queries(0):
        (exchange,) = services.history(session)
    assert exchange["proposals"] == []


# ---- (g) 権限で断られた提案を管理者が承認できる ----


@pytest.mark.django_db
def test_refused_by_permission_can_be_saved_by_admin(settings, user, order):
    settings.SEIZOU = {"access_control": {"department": "log", "write": "on", "money": "log"}}
    proposal = services.propose("ship", _ship_arguments(order), user)
    session = _session_with(proposal)

    refused = services.approve(session, proposal["id"], user)
    assert (refused["state"], refused["code"]) == ("refused", "権限")
    row = _row(proposal)
    assert (row.state, row.code, row.decided_by) == ("refused", "権限", user)

    admin = get_user_model().objects.create_superuser("admin-for-tests", password="pass-for-tests-only")
    saved = services.approve(session, proposal["id"], admin)

    assert saved["state"] == "saved"
    row.refresh_from_db()
    assert (row.state, row.code, row.decided_by) == ("saved", "", admin)
    assert row.decided_at is not None
    assert _shipments() == 1


# ---- 作る物 4: 書く道具が保存の後に例外を出したとき ----


@pytest.mark.django_db
def test_tool_error_after_save_stays_running(monkeypatch, user, order):
    """書く道具の中で保存が済んだ後に例外が出ても、承認待ちに戻さない (処理中のまま。二度目は「保存済み」で断る)。"""
    from django.template.loader import render_to_string

    proposal = services.propose("ship", _ship_arguments(order), user)
    session = _session_with(proposal)
    real_write_tools = mcp_tools.write_tools

    def fake_write_tools(who):
        real = {fn.__name__: fn for fn in real_write_tools(who)}

        def ship(**kwargs):
            real["ship"](**kwargs)
            raise RuntimeError("作り物の、保存の後の誤り")

        return (ship,)

    monkeypatch.setattr(mcp_tools, "write_tools", fake_write_tools)

    failed = services.approve(session, proposal["id"], user)

    assert (failed["state"], failed["notice"]) == ("running", services.MESSAGE_PROPOSAL_FAILED)
    assert _shipments() == 1
    row = _row(proposal)
    assert (row.state, row.notice, row.decided_by) == ("running", services.MESSAGE_PROPOSAL_FAILED, user)
    # カードの字は今の「保存できませんでした」のまま (承認のボタンは出さない)
    card = render_to_string("assistant/_proposal.html", {"proposal": failed})
    assert services.MESSAGE_PROPOSAL_FAILED in card
    assert ">承認<" not in card

    again = services.approve(session, proposal["id"], user)

    assert again["notice"] == services.MESSAGE_PROPOSAL_SAVED
    assert _shipments() == 1


# ---- 作る物 5: 画面の口で承認を 2 回送る (2 回目の前に session を承認の前の写しに戻す) ----

APPROVE_URL = "/assistant/approve/"
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.mark.django_db
def test_view_double_post_with_restored_session_ships_once(monkeypatch, client, user, order):
    from tests.test_assistant_services import FAKE_KEY, script, text_reply, tool_reply

    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    client.force_login(user)
    # tests/test_assistant_views.py の _ask_ship と同じ形: ship の偽の往復で質問する
    script(monkeypatch, [tool_reply("ship", _ship_arguments(order)), text_reply("出荷の提案を出しました。")])
    question = {"question": f"{order.order_no} の 1 行目を 3 個出荷して", "page_url": "/", "screen_name": "ダッシュボード"}
    client.post("/assistant/ask/", question, **HTMX)
    (proposal,) = services.history(client.session)[-1]["proposals"]
    copied = copy.deepcopy(client.session[services.HISTORY_KEY])

    first = client.post(APPROVE_URL, {"proposal_id": proposal["id"]}, **HTMX).content.decode()
    assert "保存しました" in first
    assert _shipments() == 1

    session = client.session
    session[services.HISTORY_KEY] = copied
    session.save()
    second = client.post(APPROVE_URL, {"proposal_id": proposal["id"]}, **HTMX).content.decode()

    assert "保存済み" in second
    assert _shipments() == 1
