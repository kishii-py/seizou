"""AI チャットの窓 (全部の画面の右下) と、質問を受ける口 (assistant:ask・assistant:clear) のテスト。

本物の API は呼ばない (autouse の fixture が _ask_claude を「呼ばれたら AssertionError」に差し替える)。
返事が要るテストだけが作り物の返事に差し替える。鍵の有無で窓の中身が変わるので、鍵はテストごとに
setenv か delenv で明示する。品番と会社名は作り物。
"""

from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.assistant import services
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from tests.test_assistant_services import FAKE_KEY, refuse_api, script, text_reply, tool_reply

ASK_URL = "/assistant/ask/"
CLEAR_URL = "/assistant/clear/"
APPROVE_URL = "/assistant/approve/"
HTMX = {"HTTP_HX_REQUEST": "true"}


@pytest.fixture(autouse=True)
def no_real_api(monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", refuse_api)


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
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def order(user):
    customer = masters_services.create_partner({"code": "AV01", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "AV-100", "name": "作り物の軸", "unit": "個"}, user)
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
    )


def ask(client, question, **extra):
    data = {"question": question, "page_url": "/", "screen_name": "ダッシュボード"}
    return client.post(ASK_URL, data, **HTMX, **extra)


def saved_history(client):
    return client.session.get(services.HISTORY_KEY)


@pytest.mark.django_db
def test_panel_shown_after_login_with_key(with_key, client_logged_in):
    html = client_logged_in.get(reverse("core:home")).content.decode()

    for word in (
        'id="assistant-toggle"',
        'aria-expanded="false"',
        'hx-post="/assistant/ask/"',
        'name="question"',
        'name="page_url" value="/"',
        'hx-post="/assistant/clear/"',
        "print:hidden",
    ):
        assert word in html, word
    assert services.MESSAGE_NO_KEY not in html


@pytest.mark.django_db
def test_panel_without_key_shows_sentence(without_key, client_logged_in):
    html = client_logged_in.get(reverse("core:home")).content.decode()

    assert 'id="assistant-toggle"' in html
    assert services.MESSAGE_NO_KEY not in html
    assert "AI チャットは、管理者が設定すると使えます。" in html
    assert 'name="question"' not in html
    assert "/assistant/ask/" not in html
    assert 'id="assistant-messages"' not in html


@pytest.mark.django_db
def test_panel_not_on_login_page(with_key, client):
    html = client.get(reverse("core:login")).content.decode()

    assert "assistant-" not in html


@pytest.mark.django_db
def test_panel_keeps_primary_button_count(with_key, client_logged_in):
    html = client_logged_in.get(reverse("masters:item_list")).content.decode()

    assert 'id="assistant-toggle"' in html
    assert html.count("btn-primary") == 1


@pytest.mark.django_db
def test_ask_returns_partial_and_keeps_history(with_key, client_logged_in, monkeypatch):
    calls = script(monkeypatch, [text_reply("作り物の答えです。")])

    response = ask(client_logged_in, "作り物の質問")

    body = response.content.decode()
    assert response.status_code == 200
    assert "作り物の質問" in body
    assert "作り物の答えです。" in body
    assert "chat-start" in body and "chat-end" in body
    assert "<html" not in body
    assert "ダッシュボード" in calls[0]["system"]
    assert saved_history(client_logged_in) == [
        {"question": "作り物の質問", "answer": "作り物の答えです。", "links": [], "tools": [], "proposals": []}
    ]

    html = client_logged_in.get(reverse("core:home")).content.decode()
    assert "作り物の質問" in html
    assert "作り物の答えです。" in html


@pytest.mark.django_db
def test_answer_is_escaped(with_key, client_logged_in, monkeypatch):
    script(monkeypatch, [text_reply("<script>x</script>\n2 行目")])

    body = ask(client_logged_in, "<b>太字</b>").content.decode()

    assert "&lt;script&gt;" in body
    assert "<br>" in body
    assert "<script>x" not in body
    assert "&lt;b&gt;太字" in body


@pytest.mark.django_db
def test_links_under_answer(with_key, client_logged_in, monkeypatch, order):
    url = reverse("orders:order_detail", args=[order.pk])
    script(
        monkeypatch,
        [
            tool_reply("get_order", {"order_no": order.order_no}),
            text_reply(f"受注は {url} で見られます。/x-not-from-tools/ も見てください。"),
        ],
    )

    body = ask(client_logged_in, f"{order.order_no} の残は").content.decode()

    assert f'href="{url}"' in body
    assert 'href="/x-not-from-tools/"' not in body
    assert saved_history(client_logged_in)[0]["links"] == [url]


@pytest.mark.django_db
def test_history_keeps_last_ten(with_key, client_logged_in, monkeypatch):
    script(monkeypatch, [text_reply("答え")])

    for number in range(11):
        assert ask(client_logged_in, f"質問その{number}").status_code == 200

    questions = [entry["question"] for entry in saved_history(client_logged_in)]
    assert questions == [f"質問その{number}" for number in range(1, 11)]


@pytest.mark.django_db
def test_ask_requires_login_post_and_htmx(with_key, client, user):
    anonymous = client.post(ASK_URL, {"question": "質問"}, **HTMX)
    assert anonymous.status_code == 302
    assert anonymous["Location"].startswith(reverse("core:login"))

    client.force_login(user)
    assert client.get(ASK_URL, **HTMX).status_code == 405
    assert client.post(ASK_URL, {"question": "質問"}).status_code == 400
    assert saved_history(client) is None


@pytest.mark.django_db
def test_failure_is_returned_as_bubble(with_key, client_logged_in, monkeypatch):
    script(monkeypatch, [ValueError(services.MESSAGE_BUSY)])

    response = ask(client_logged_in, "質問")

    body = response.content.decode()
    assert response.status_code == 200
    assert services.MESSAGE_BUSY in body
    assert "chat-start" in body


@pytest.mark.django_db
def test_no_key_ask_does_not_call_api(without_key, client_logged_in):
    response = ask(client_logged_in, "質問")

    assert response.status_code == 200
    assert services.MESSAGE_NO_KEY in response.content.decode()


@pytest.mark.django_db
def test_clear_empties_history(with_key, client_logged_in, monkeypatch):
    script(monkeypatch, [text_reply("答え")])
    ask(client_logged_in, "質問")
    assert len(saved_history(client_logged_in)) == 1

    response = client_logged_in.post(CLEAR_URL, **HTMX)

    assert response.status_code == 200
    assert response.content == b""
    assert services.HISTORY_KEY not in client_logged_in.session
    assert client_logged_in.get(CLEAR_URL, **HTMX).status_code == 405
    assert client_logged_in.post(CLEAR_URL).status_code == 400


def test_urls():
    assert reverse("assistant:ask") == ASK_URL
    assert reverse("assistant:clear") == CLEAR_URL
    assert reverse("assistant:approve") == APPROVE_URL


@pytest.mark.django_db
def test_tool_labels_under_answer(with_key, client_logged_in, monkeypatch, order):
    script(
        monkeypatch,
        [tool_reply("search_items", {"query": "AV-100"}), text_reply("AV-100 は作り物の軸です。")],
    )

    body = ask(client_logged_in, "AV-100 は何").content.decode()

    assert '<span class="badge badge-ghost badge-sm">部品を探した</span>' in body
    assert saved_history(client_logged_in)[0]["tools"] == ["search_items"]

    html = client_logged_in.get(reverse("core:home")).content.decode()
    assert '<span class="badge badge-ghost badge-sm">部品を探した</span>' in html


# ---- 提案のカードと承認 (2 段目) ----


def _shipments():
    from seizou.shipping.models import Shipment

    return Shipment.objects.count()


def _ask_ship(client, monkeypatch, order, qty="3"):
    """ship の偽の往復で質問し、返事の本文と、session に残った提案を返す。"""
    arguments = {"order_no": order.order_no, "line_no": 1, "qty": qty, "shipped_at": "2026-11-05"}
    script(monkeypatch, [tool_reply("ship", arguments), text_reply("出荷の提案を出しました。")])
    body = ask(client, f"{order.order_no} の 1 行目を {qty} 個出荷して").content.decode()
    (proposal,) = saved_history(client)[-1]["proposals"]
    return body, proposal


def approve(client, proposal_id, **data):
    return client.post(APPROVE_URL, {"proposal_id": proposal_id, **data}, **HTMX)


@pytest.mark.django_db
def test_write_tool_shows_proposal_card(with_key, client_logged_in, monkeypatch, order):
    body, proposal = _ask_ship(client_logged_in, monkeypatch, order)

    assert f'id="assistant-proposal-{proposal["id"]}"' in body
    assert "提案: 出荷" in body
    assert ">承認<" in body
    assert 'hx-post="/assistant/approve/"' in body
    assert "出荷を提案した" in body
    assert "alert-" not in body
    assert "csrf" not in body.lower()
    assert proposal["state"] == "pending"
    assert _shipments() == 0


@pytest.mark.django_db
def test_approve_saves_and_repeat_is_refused(with_key, client_logged_in, monkeypatch, order):
    _, proposal = _ask_ship(client_logged_in, monkeypatch, order)

    response = approve(client_logged_in, proposal["id"])

    body = response.content.decode()
    assert response.status_code == 200
    assert "保存しました" in body
    assert f'href="{reverse("orders:order_detail", args=[order.pk])}"' in body
    assert "text-success" in body
    assert ">承認<" not in body
    assert _shipments() == 1

    again = approve(client_logged_in, proposal["id"]).content.decode()
    assert "保存済み" in again
    assert _shipments() == 1

    html = client_logged_in.get(reverse("core:home")).content.decode()
    assert f'id="assistant-proposal-{proposal["id"]}"' in html
    assert "保存しました" in html
    assert ">承認<" not in html


@pytest.mark.django_db
def test_approve_warn_shows_confirm_button(with_key, client_logged_in, monkeypatch, order, settings):
    settings.SEIZOU = {"over_shipment": "warn"}
    _, proposal = _ask_ship(client_logged_in, monkeypatch, order, qty="12")

    first = approve(client_logged_in, proposal["id"]).content.decode()

    assert "R01" in first
    assert "text-warning" in first
    assert "このまま保存" in first
    assert 'name="confirm" value="1"' in first
    assert f'name="proposal_id" value="{proposal["id"]}"' in first
    assert _shipments() == 0

    second = approve(client_logged_in, proposal["id"], confirm="1").content.decode()
    assert "保存しました" in second
    assert _shipments() == 1


@pytest.mark.django_db
def test_approve_stop_is_red(with_key, client_logged_in, monkeypatch, order, settings):
    settings.SEIZOU = {"over_shipment": "stop"}
    _, proposal = _ask_ship(client_logged_in, monkeypatch, order, qty="12")

    body = approve(client_logged_in, proposal["id"], confirm="1").content.decode()

    assert "R01" in body
    assert "text-error" in body
    assert "このまま保存" not in body
    assert ">承認<" not in body
    assert _shipments() == 0


@pytest.mark.django_db
def test_approve_refused_without_permission(with_key, client_logged_in, monkeypatch, order, settings):
    settings.SEIZOU = {"access_control": {"department": "log", "write": "on", "money": "log"}}
    _, proposal = _ask_ship(client_logged_in, monkeypatch, order)

    body = approve(client_logged_in, proposal["id"]).content.decode()

    assert "権限" in body
    assert "アカウント管理の担当者" in body
    assert "text-error" in body
    assert _shipments() == 0


@pytest.mark.django_db
def test_approve_guards(with_key, client, user, monkeypatch, order):
    anonymous = client.post(APPROVE_URL, {"proposal_id": "x"}, **HTMX)
    assert anonymous.status_code == 302
    assert anonymous["Location"].startswith(reverse("core:login"))

    client.force_login(user)
    missing = approve(client, "0000000000000000")
    assert missing.status_code == 200
    assert "見つかりません" in missing.content.decode()
    assert client.get(APPROVE_URL, **HTMX).status_code == 405

    _, proposal = _ask_ship(client, monkeypatch, order)
    assert client.post(APPROVE_URL, {"proposal_id": proposal["id"]}).status_code == 400
    assert _shipments() == 0

    client.post(CLEAR_URL, **HTMX)
    after_clear = approve(client, proposal["id"])
    assert after_clear.status_code == 200
    assert "見つかりません" in after_clear.content.decode()
    assert _shipments() == 0
