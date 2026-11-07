"""相手から会話が届いたときの知らせのメール (connect.services._notify_chat_received) と、つながりの自社の連絡先のメールアドレスのテスト。

意図: 「取引先との接続で登録したアドレスへ知らせる」「載せるのは会話の 1 行目とリンクだけで、添付は載せない」と、送れなくても会話を受けることと、
送り直しでメールが重ならないことを、試験で崩れないようにするため。

2 社の役とつながりと偽の requests.post は tests/test_connect_chat.py と同じ (1 つの DB に 2 社の役を置く)。
A 社 (発注した側) が書いた言葉は B 社の側で取引先 customer から受け (鍵 ORDER_ENV の受ける向きの行)、
B 社 (受けた側) が書いた言葉は A 社の側で取引先 subcontractor から受ける (鍵 REPLY_ENV の受ける向きの行)。
メールは pytest-django の mailoutbox で読む。名前とアドレスとパスワードは作り物。
"""

import logging

import pytest
from django.db import connection as db_connection
from django.urls import reverse

from seizou.connect import services
from seizou.connect.models import ChatMessage, Connection, Inbox
from seizou.core import conf, mail
from tests import test_core_manual_auto
from tests.test_connect_reply import (  # noqa: F401 (fixture)
    _into_receive,
    _send_order,
    connections,
    customer,
    item,
    order_secret,
    own_item,
    reply_secret,
    subcontractor,
    user,
)
from tests.test_connect_send import FakePost

# 使う画面の「?」の小窓の試験の fixture を借りる
client_logged_in = test_core_manual_auto.client_logged_in
folder = test_core_manual_auto.folder

SITE = "https://seizou.example.test"
MAIL = {
    "host": "smtp.example.test",
    "port": 587,
    "security": "starttls",
    "user": "",
    "from": "notice@example.test",
    "from_name": "",
    "site_url": SITE,
}
#: 作り物のパスワード (環境変数にだけ置く。記録に出ないことを見る)
PASSWORD = "tests-only-not-a-real-password"
B_ADDRESS = "b-contact@example.test"
A_ADDRESS = "a-contact@example.test"


@pytest.fixture
def mail_settings(settings, monkeypatch):
    # サーバー・ポート・暗号化の方式は .env (環境変数) だけで決める (core.conf.MAIL_ENV)。ほかの送り方は会社の設定
    settings.SEIZOU = {"mail": dict(MAIL)}
    for key, name in conf.MAIL_ENV.items():
        monkeypatch.setenv(name, str(MAIL[key]))
    return settings


def _round_trip(monkeypatch):
    fake = FakePost(inside=_into_receive)
    monkeypatch.setattr("requests.post", fake)
    return fake


def _sent_order(monkeypatch, subcontractor, item, user):  # noqa: F811
    """発注を送って、届いた受信した注文と一緒に返す。"""
    _round_trip(monkeypatch)
    po = _send_order(subcontractor, item, user)
    return po, Inbox.objects.get()


def _set_address(partner, direction, address):
    Connection.objects.filter(partner=partner, direction=direction).update(contact_email=address)


def _received(message_id):
    return ChatMessage.objects.get(message_id=message_id, direction=ChatMessage.DIRECTION_RECEIVED)


# ---------------------------------------------------------------- (a) A 社の発注から書く → B 社の連絡先へ


@pytest.mark.django_db
def test_message_from_purchase_order_notifies_customer_contact(
    monkeypatch, mail_settings, mailoutbox, user, subcontractor, customer, item, own_item, connections  # noqa: F811
):
    po, inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)

    services.post_chat_message(po, "作り物の 1 行目です。\n作り物の 2 行目です。", user)

    assert len(mailoutbox) == 1
    sent = mailoutbox[0]
    assert sent.to == [B_ADDRESS]
    assert sent.from_email == "notice@example.test"
    assert sent.subject == f"{customer.name} から会話が届きました (受信した注文 {po.po_no})"
    assert "作り物の 1 行目です。" in sent.body
    assert "作り物の 2 行目です。" not in sent.body
    assert SITE + reverse("connect:inbox_detail", args=[inbox.pk]) in sent.body
    assert f"このメールに返信しても {customer.name} には届きません。" in sent.body
    assert sent.attachments == []
    assert "user" not in sent.body and "tester" not in sent.body  # 書いた人の名前は載せない

    order = services.accept(inbox, {"T-100": own_item}, user)
    _round_trip(monkeypatch)
    services.post_chat_message(po, "受け入れた後の作り物の言葉", user)

    assert len(mailoutbox) == 2
    after = mailoutbox[1]
    assert after.subject == f"{customer.name} から会話が届きました (受注 {order.order_no})"
    assert SITE + reverse("orders:order_detail", args=[order.pk]) in after.body


# ---------------------------------------------------------------- (b) B 社から書く → A 社の連絡先へ


@pytest.mark.django_db
def test_message_from_inbox_notifies_subcontractor_contact(
    monkeypatch, mail_settings, mailoutbox, user, subcontractor, customer, item, connections  # noqa: F811
):
    po, inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(subcontractor, Connection.DIRECTION_RECEIVE, A_ADDRESS)
    _round_trip(monkeypatch)

    services.post_chat_message(inbox, "受けた側の作り物の言葉", user)

    assert len(mailoutbox) == 1
    sent = mailoutbox[0]
    assert sent.to == [A_ADDRESS]
    assert sent.subject == f"{subcontractor.name} から会話が届きました (発注 {po.po_no})"
    assert SITE + reverse("purchasing:purchaseorder_detail", args=[po.pk]) in sent.body
    assert "受けた側の作り物の言葉" in sent.body


# ---------------------------------------------------------------- (c) 同じ番号の 2 回目


@pytest.mark.django_db
def test_same_message_twice_sends_one_mail(
    monkeypatch, mail_settings, mailoutbox, user, subcontractor, customer, item, connections  # noqa: F811
):
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)
    fake = _round_trip(monkeypatch)

    saved = services.post_chat_message(po, "作り物の言葉", user)
    call = fake.calls[-1]
    again = services.receive(call["data"], call["headers"])

    assert again.chat_message == _received(saved.message_id)
    assert ChatMessage.objects.filter(direction=ChatMessage.DIRECTION_RECEIVED).count() == 1
    assert len(mailoutbox) == 1


# ---------------------------------------------------------------- (d) 宛先


@pytest.mark.django_db
def test_no_address_sends_nothing_but_keeps_message(
    monkeypatch, mail_settings, mailoutbox, caplog, user, subcontractor, customer, item, connections  # noqa: F811
):
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _round_trip(monkeypatch)
    caplog.set_level(logging.INFO)

    saved = services.post_chat_message(po, "作り物の言葉", user)

    assert mailoutbox == []
    assert _received(saved.message_id).body == "作り物の言葉"
    assert any("自社の連絡先のメールアドレスが無いので送らなかった" in record.getMessage() for record in caplog.records)


@pytest.mark.django_db
def test_address_on_send_row_only_is_used(
    monkeypatch, mail_settings, mailoutbox, user, subcontractor, customer, item, connections  # noqa: F811
):
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_SEND, B_ADDRESS)
    _round_trip(monkeypatch)

    services.post_chat_message(po, "作り物の言葉", user)

    assert [sent.to for sent in mailoutbox] == [[B_ADDRESS]]


@pytest.mark.django_db
def test_receive_row_address_wins_over_send_row(
    monkeypatch, mail_settings, mailoutbox, user, subcontractor, customer, item, connections  # noqa: F811
):
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_SEND, "send-row@example.test")
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)
    _round_trip(monkeypatch)

    services.post_chat_message(po, "作り物の言葉", user)

    assert [sent.to for sent in mailoutbox] == [[B_ADDRESS]]


# ---------------------------------------------------------------- (e) 送り方が無い


@pytest.mark.django_db
def test_no_mail_settings_sends_nothing_but_keeps_message(
    monkeypatch, settings, mailoutbox, caplog, user, subcontractor, customer, item, connections  # noqa: F811
):
    settings.SEIZOU = {"mail": {**MAIL, "host": ""}}
    monkeypatch.delenv(conf.MAIL_ENV["host"], raising=False)
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)
    _round_trip(monkeypatch)
    caplog.set_level(logging.INFO)

    saved = services.post_chat_message(po, "作り物の言葉", user)

    assert mailoutbox == []
    assert _received(saved.message_id).body == "作り物の言葉"
    assert any("メールの送り方がまだなので送らなかった" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------- (f) 送れない


class RaisingConnection:
    """送る所で決めた例外を投げる偽の接続。"""

    def __init__(self, error):
        self.error = error

    def send_messages(self, messages):
        raise self.error


@pytest.mark.django_db
def test_mail_failure_still_receives_message(
    monkeypatch, mail_settings, mailoutbox, caplog, user, subcontractor, customer, item, connections  # noqa: F811
):
    monkeypatch.setenv(mail.PASSWORD_ENV, PASSWORD)
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)
    _round_trip(monkeypatch)
    monkeypatch.setattr("seizou.core.mail.get_connection", lambda **kwargs: RaisingConnection(ConnectionRefusedError()))
    caplog.set_level(logging.INFO)

    saved = services.post_chat_message(po, "作り物の言葉", user)

    assert mailoutbox == []
    assert _received(saved.message_id).body == "作り物の言葉"
    # 受け口は 200 を返したので、送った側の行は届いた印 (sent_at) を持つ
    assert saved.sent_at is not None
    assert saved.delivery_state().startswith("届いた ")
    warnings = [record for record in caplog.records if record.name == "seizou.connect" and record.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "会話の知らせのメールを送れなかった" in warnings[0].getMessage()
    assert PASSWORD not in caplog.text
    assert B_ADDRESS not in caplog.text


@pytest.mark.django_db
def test_unexpected_error_is_logged_and_message_kept(
    monkeypatch, mail_settings, mailoutbox, caplog, user, subcontractor, customer, item, connections  # noqa: F811
):
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)
    _round_trip(monkeypatch)

    def broken(*args, **kwargs):
        raise RuntimeError("作り物の思わぬ誤り")

    monkeypatch.setattr("seizou.core.mail.send", broken)

    saved = services.post_chat_message(po, "作り物の言葉", user)

    assert _received(saved.message_id).body == "作り物の言葉"
    assert saved.sent_at is not None
    assert any("会話の知らせのメールで思わぬ誤り" in record.getMessage() for record in caplog.records)


# ---------------------------------------------------------------- (g) 1 行目と件名


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("1 行目\n2 行目", "1 行目"),
        ("\n   \n  字のある行  \n次の行", "字のある行"),
        ("あ" * 101, "あ" * 100 + "…"),
        ("あ" * 100, "あ" * 100),
        ("", ""),
    ],
)
def test_first_line(body, expected):
    assert services._chat_first_line(body) == expected


@pytest.mark.django_db
def test_mail_has_first_line_only_and_subject_without_newline(
    monkeypatch, mail_settings, mailoutbox, user, subcontractor, customer, item, connections  # noqa: F811
):
    po, inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)
    # 受信した注文の番号は相手の字 (改行を含んでも件名に改行を入れない)
    inbox.payload = {**inbox.payload, "order_no": "PO-1\r\nX"}
    inbox.save(update_fields=["payload"])
    _round_trip(monkeypatch)

    services.post_chat_message(po, "\n\n" + "い" * 101 + "\n2 行目の字", user)

    assert len(mailoutbox) == 1
    sent = mailoutbox[0]
    assert "\n" not in sent.subject and "\r" not in sent.subject
    assert "PO-1" in sent.subject
    assert "い" * 100 + "…" in sent.body
    assert "い" * 101 not in sent.body
    assert "2 行目の字" not in sent.body


# ---------------------------------------------------------------- (h) atomic の外


@pytest.mark.django_db(transaction=True)
def test_mail_is_sent_outside_atomic(
    monkeypatch, mail_settings, mailoutbox, user, subcontractor, customer, item, connections  # noqa: F811
):
    from django.core import mail as django_mail

    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)
    _round_trip(monkeypatch)
    seen = []

    def recording_get_connection(**kwargs):
        seen.append(db_connection.in_atomic_block)
        return django_mail.get_connection(**kwargs)

    monkeypatch.setattr("seizou.core.mail.get_connection", recording_get_connection)

    services.post_chat_message(po, "作り物の言葉", user)

    assert seen == [False]
    assert len(mailoutbox) == 1


# ---------------------------------------------------------------- (i) URL の頭が空


@pytest.mark.django_db
def test_mail_without_site_url_has_no_link(
    monkeypatch, settings, mailoutbox, user, subcontractor, customer, item, connections  # noqa: F811
):
    settings.SEIZOU = {"mail": {**MAIL, "site_url": ""}}
    monkeypatch.setenv(conf.MAIL_ENV["host"], MAIL["host"])
    po, _inbox = _sent_order(monkeypatch, subcontractor, item, user)
    _set_address(customer, Connection.DIRECTION_RECEIVE, B_ADDRESS)
    _round_trip(monkeypatch)

    services.post_chat_message(po, "作り物の言葉", user)

    body = mailoutbox[0].body
    assert "リンクを付けていません" in body
    assert "http" not in body


# ---------------------------------------------------------------- (j) つながりの欄


@pytest.mark.django_db
def test_connection_screens_save_and_show_contact_email(client, user, customer):  # noqa: F811
    client.force_login(user)
    client_logged_in = client
    data = {"partner": customer.pk, "direction": "receive", "url": "", "secret_env": "SEIZOU_PARTNER_MAILCO_SECRET"}
    response = client_logged_in.post(reverse("connect:connection_create"), {**data, "contact_email": " b-contact@example.test "})
    row = Connection.objects.get()
    assert response.status_code == 302
    assert row.contact_email == B_ADDRESS

    body = client_logged_in.get(reverse("connect:connection_detail", args=[row.pk])).content.decode()
    assert "自社の連絡先のメールアドレス" in body and B_ADDRESS in body

    body = client_logged_in.get(reverse("connect:connection_update", args=[row.pk])).content.decode()
    assert 'name="contact_email"' in body and B_ADDRESS in body
    response = client_logged_in.post(reverse("connect:connection_update", args=[row.pk]), {**data, "contact_email": A_ADDRESS})
    assert response.status_code == 302
    row.refresh_from_db()
    assert row.contact_email == A_ADDRESS

    response = client_logged_in.post(reverse("connect:connection_update", args=[row.pk]), {**data, "contact_email": "not-an-address"})
    assert response.status_code == 200
    page = response.content.decode()
    assert 'id="id_contact_email_error"' in page
    assert 'data-notice="stop"' in page
    row.refresh_from_db()
    assert row.contact_email == A_ADDRESS


@pytest.mark.django_db
def test_services_check_contact_email_and_keep_it_on_positional_update(user, customer):  # noqa: F811
    with pytest.raises(ValueError) as caught:
        services.register_connection(customer, "", "SEIZOU_PARTNER_MAILCO_SECRET", "receive", user, contact_email="not-an-address")
    assert str(caught.value) == "保存できません。自社の連絡先のメールアドレスが、メールアドレスの形ではありません。例 info@example.jp の形で入れてください。"
    with pytest.raises(ValueError):
        services.register_connection(customer, "", "SEIZOU_PARTNER_MAILCO_SECRET", "receive", user, contact_email="a" * 250 + "@example.test")
    assert not Connection.objects.exists()

    row = services.register_connection(customer, "", "SEIZOU_PARTNER_MAILCO_SECRET", "receive", user, contact_email=B_ADDRESS)
    # 位置の引数だけで呼ぶ今の呼び手は、値を消さない
    row = services.update_connection(row, customer, "", "SEIZOU_PARTNER_MAILCO_SECRET", "receive", user)
    assert row.contact_email == B_ADDRESS
    row = services.update_connection(row, customer, "", "SEIZOU_PARTNER_MAILCO_SECRET", "receive", user, contact_email="")
    assert row.contact_email == ""
    # 送る向きの行にも入れてよい
    sent = services.register_connection(
        customer, "https://buyer.example.test/connect/receive/order/", "SEIZOU_PARTNER_MAILCO_SECRET", "send", user, contact_email=A_ADDRESS
    )
    assert sent.contact_email == A_ADDRESS


# ---------------------------------------------------------------- (k) 使い方の画面


def test_connection_create_help_has_no_contact_email_refusal(client_logged_in, folder):
    test_core_manual_auto.write_screen(folder, "connect_connection_create")
    html = test_core_manual_auto.modal_html(client_logged_in, "connect_connection_create")
    assert "自社の連絡先のメールアドレスが、メールアドレスの形ではありません" not in html
