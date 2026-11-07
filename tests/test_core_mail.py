"""知らせのメールの送り方 (会社の設定 mail・seizou.core.mail・設定の画面の「メールの知らせ」と「試しに送る」) のテスト。

意図: パスワードの値を画面と知らせの字と記録に出さないことと、送り方の値の形と、「試しに送る」の門を、試験で崩れないようにするため。
パスワードは .env の EMAIL_HOST_PASSWORD だけに置き (ここでは monkeypatch で環境変数に置く作り物の字)、メールは pytest-django の
mailoutbox で読む (試験の中では Django の既定の送り方が locmem になる)。名前とアドレスは作り物。
"""

from __future__ import annotations

import logging
import smtplib
import socket
import ssl
from email.utils import formataddr

import pytest
from django.core import mail as django_mail
from django.urls import reverse

from seizou.core import conf, doctor, forms, mail, services, settings_screen
from seizou.core.models import SettingOverride
from tests import test_core_manual_auto, test_core_settings_edit
from tests.test_core_settings_edit import edit_url

# 設定の変更の試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# manager = システム管理者・plain = 権限の無い人。manager_client / plain_client = その人でログインした client。
manager = test_core_settings_edit.manager
plain = test_core_settings_edit.plain
manager_client = test_core_settings_edit.manager_client
plain_client = test_core_settings_edit.plain_client
# 使う画面の「?」の小窓の試験の fixture を借りる
client_logged_in = test_core_manual_auto.client_logged_in
folder = test_core_manual_auto.folder

#: 作り物のパスワード (環境変数にだけ置く。画面と知らせの字と記録に出ないことを見る)
PASSWORD = "tests-only-not-a-real-password"

#: 作り物の送り方 (7 つ)
FILLED = {
    "host": "smtp.example.test",
    "port": 587,
    "security": "starttls",
    "user": "notice-account@example.test",
    "from": "notice@example.test",
    "from_name": "",
    "site_url": "https://seizou.example.test",
}

STOP = 'data-notice="stop"'


def mail_section(body: str) -> str:
    """設定の画面の「メールの知らせ」の節の HTML。"""
    start = body.index('aria-labelledby="mail-title"')
    return body[start : body.index("</section>", start)]


def mail_row(body: str) -> str:
    """設定の画面の表の「メールの送り方」の行の HTML。"""
    start = body.index("<tr><td>メールの送り方")
    return body[start : body.index("</tr>", start)]


def toasts(body: str) -> str:
    """右上の知らせの枠の HTML (無ければ空の字)。"""
    if 'id="seizou-toasts"' not in body:
        return ""
    start = body.index('id="seizou-toasts"')
    return body[start:]


# ---------------------------------------------------------------- (a) 既定


@pytest.mark.django_db
def test_default_mail_setting_reads_and_passes_checks(settings):
    settings.SEIZOU = {}
    assert conf.setting("mail") == conf.DEFAULTS["mail"]
    assert list(conf.mail_settings()) == list(conf.MAIL_KEYS)
    assert conf.mail_settings() == conf.DEFAULTS["mail"]
    assert services.validate_setting("mail", conf.DEFAULTS["mail"]) == conf.DEFAULTS["mail"]
    assert [(row.ok, row.label) for row in doctor.check_settings()] == [(True, "会社の選べる設定")]
    # 既定では送らない (サーバーと差出人が空)
    assert mail.missing() == ["サーバー", "差出人"]
    assert not mail.ready()
    assert mail.link("/settings/") == ""


def test_mail_settings_fills_missing_and_wrong_shapes(settings):
    settings.SEIZOU = {"mail": {"host": " smtp.example.test ", "port": "x", "security": "tls", "from": 5}}
    values = conf.mail_settings()
    assert list(values) == list(conf.MAIL_KEYS)
    assert values["host"] == "smtp.example.test"
    assert values["port"] == 587 and values["security"] == "starttls" and values["from"] == ""
    settings.SEIZOU = {"mail": {"port": True}}
    assert conf.mail_settings()["port"] == 587
    settings.SEIZOU = {"mail": "smtp.example.test"}
    assert conf.mail_settings() == conf.DEFAULTS["mail"]


def test_password_is_read_from_environment_only(monkeypatch, settings):
    monkeypatch.delenv(mail.PASSWORD_ENV, raising=False)
    settings.EMAIL_HOST_PASSWORD = PASSWORD  # Django の settings の値は読まない
    assert mail.has_password() is False
    monkeypatch.setenv(mail.PASSWORD_ENV, "   ")
    assert mail.has_password() is False
    monkeypatch.setenv(mail.PASSWORD_ENV, PASSWORD)
    assert mail.has_password() is True


# ---------------------------------------------------------------- (b) 値の確かめ


def with_value(key, value):
    values = dict(conf.DEFAULTS["mail"])
    values[key] = value
    return values


@pytest.mark.parametrize(
    "value",
    [
        with_value("port", 0),
        with_value("port", 65536),
        with_value("port", True),
        with_value("port", "587"),
        with_value("security", "tls"),
        with_value("from", "not-an-address"),
        with_value("from", "a" * 250 + "@example.test"),
        with_value("from_name", "1 行目\n2 行目"),
        with_value("from_name", "名" * 101),
        with_value("site_url", "ftp://x"),
        with_value("site_url", "seizou.example.jp"),
        with_value("site_url", "https://"),
        with_value("site_url", "https://x:abc"),
        with_value("host", "smtp example"),
        with_value("host", "h" * 254),
        with_value("host", 5),
        with_value("user", "u" * 255),
        {**conf.DEFAULTS["mail"], "password": PASSWORD},
        "smtp.example.test",
    ],
)
def test_validate_mail_refuses_bad_values(value):
    with pytest.raises(ValueError) as caught:
        services.validate_setting("mail", value)
    message = str(caught.value)
    assert message.startswith("保存できません。")
    # 入れた値は断りの文に入れない
    assert PASSWORD not in message and "not-an-address" not in message and "ftp://x" not in message


def test_validate_mail_normalizes_values():
    result = services.validate_setting(
        "mail",
        {
            "host": "  smtp.example.test ",
            "user": " notice-account@example.test ",
            "from": " notice@example.test ",
            "from_name": " 作り物　知らせ ",
            "site_url": " https://seizou.example.test/ ",
        },
    )
    assert list(result) == list(conf.MAIL_KEYS)
    assert result == {
        "host": "smtp.example.test",
        "port": 587,
        "security": "starttls",
        "user": "notice-account@example.test",
        "from": "notice@example.test",
        "from_name": "作り物　知らせ",
        "site_url": "https://seizou.example.test",
    }
    # 欠けた中の鍵は既定で埋める
    assert services.validate_setting("mail", {"host": "smtp.example.test"}) == {**conf.DEFAULTS["mail"], "host": "smtp.example.test"}
    assert list(services.validate_setting("mail", {"site_url": "http://127.0.0.1:8001"})) == list(conf.MAIL_KEYS)
    assert services.validate_setting("mail", with_value("security", "ssl"))["security"] == "ssl"


# ---------------------------------------------------------------- (c) 設定の画面


@pytest.mark.django_db
def test_settings_screen_shows_mail_row_and_section(manager_client, settings, monkeypatch):
    settings.SEIZOU = {}
    monkeypatch.delenv(mail.PASSWORD_ENV, raising=False)
    body = manager_client.get(reverse("core:settings")).content.decode()

    row = mail_row(body)
    parts = ["サーバー: なし", "ポート: 587", "暗号化の方式: STARTTLS", "アカウント: なし", "差出人: なし", "差出人の名前: なし", "seizou の URL の頭: なし"]
    assert "・".join(f'<span class="sm:whitespace-nowrap">{part}</span>' for part in parts) in row
    rows = {item["key"]: item for item in settings_screen.company_settings()}
    assert rows["mail"]["value_parts"] == parts

    section = mail_section(body)
    assert "メールの知らせ" in section
    assert "パスワード" in section and "まだ" in section and "置いてある" not in section
    assert "「サーバー」「差出人」が空" in section
    assert edit_url("mail") in section and "試しに送る" in section
    assert f'action="{reverse("core:mail_test")}"' in section
    assert "btn-primary" not in section

    monkeypatch.setenv(mail.PASSWORD_ENV, PASSWORD)
    settings.SEIZOU = {"mail": {**FILLED, "from_name": "作り物の知らせ"}}
    body = manager_client.get(reverse("core:settings")).content.decode()
    section = mail_section(body)
    assert "置いてある" in section
    assert "入っている" in section
    assert "notice@example.test (作り物の知らせ)" in section
    assert PASSWORD not in body
    assert "<span class=\"sm:whitespace-nowrap\">サーバー: smtp.example.test</span>" in mail_row(body)


@pytest.mark.django_db
def test_settings_screen_hides_mail_buttons_from_plain_user(plain_client, settings):
    settings.SEIZOU = {}
    body = plain_client.get(reverse("core:settings")).content.decode()
    section = mail_section(body)
    assert "パスワード" in section
    assert edit_url("mail") not in body
    assert "試しに送る</button>" not in section
    assert reverse("core:mail_test") not in body


# ---------------------------------------------------------------- (d) 設定の変更の画面


@pytest.mark.django_db
def test_mail_edit_page_has_seven_fields_and_url_hint(manager_client, settings):
    settings.SEIZOU = {}
    body = manager_client.get(edit_url("mail")).content.decode()
    for name in ("mail_host", "mail_port", "mail_security", "mail_user", "mail_from", "mail_from_name", "mail_site_url"):
        assert f'name="{name}"' in body, name
    assert body.count('type="radio"') == 3
    for value, label in (("starttls", "STARTTLS"), ("ssl", "SSL/TLS"), ("none", "暗号化しない")):
        assert f'value="{value}"' in body, value
        assert label in body, label
    assert "mail" not in forms.CHOICE_NOTES
    assert "今この画面を開いている URL の頭は <code class=\"font-mono\">http://testserver</code> です。" in body
    assert '<code class="font-mono">mail</code>' not in body
    # パスワードの欄は無い
    assert 'type="password"' not in body
    assert "EMAIL_HOST_PASSWORD=" in body


@pytest.mark.django_db
def test_mail_edit_saves_seven_values(manager_client, settings):
    settings.SEIZOU = {}
    data = {
        "mail_host": " smtp.example.test ",
        "mail_port": "465",
        "mail_security": "ssl",
        "mail_user": "notice-account@example.test",
        "mail_from": "notice@example.test",
        "mail_from_name": "作り物の知らせ",
        "mail_site_url": "https://seizou.example.test/",
    }
    response = manager_client.post(edit_url("mail"), data)

    assert response.status_code == 302
    assert response["Location"] == reverse("core:settings")
    # 辞書が等しいかで見る (PostgreSQL の jsonb は鍵を並べ替えて返すので、並びは見ない)
    assert SettingOverride.objects.get(key="mail").value == {
        "host": "smtp.example.test",
        "port": 465,
        "security": "ssl",
        "user": "notice-account@example.test",
        "from": "notice@example.test",
        "from_name": "作り物の知らせ",
        "site_url": "https://seizou.example.test",
    }
    assert list(conf.mail_settings()) == list(conf.MAIL_KEYS)


@pytest.mark.django_db
def test_mail_edit_refuses_bad_port_and_site_url(manager_client, settings):
    settings.SEIZOU = {}
    data = {
        "mail_host": "smtp.example.test",
        "mail_port": "0",
        "mail_security": "starttls",
        "mail_user": "",
        "mail_from": "notice@example.test",
        "mail_from_name": "",
        "mail_site_url": "",
    }
    response = manager_client.post(edit_url("mail"), data)
    assert response.status_code == 200
    assert STOP in toasts(response.content.decode())
    assert not SettingOverride.objects.filter(key="mail").exists()

    # http で始まらない URL の頭は画面から起きる (services の断りが止まった知らせになる)
    response = manager_client.post(edit_url("mail"), {**data, "mail_port": "587", "mail_site_url": "seizou.example.test"})
    assert response.status_code == 200
    box = toasts(response.content.decode())
    assert STOP in box
    assert "保存できません。" in box and "seizou の URL の頭" in box
    assert not SettingOverride.objects.filter(key="mail").exists()


# ---------------------------------------------------------------- (e) 試しに送る


@pytest.mark.django_db
def test_mail_test_sends_to_sender_with_password_from_env(manager_client, settings, monkeypatch, mailoutbox):
    settings.SEIZOU = {"mail": FILLED}
    monkeypatch.setenv(mail.PASSWORD_ENV, PASSWORD)
    calls = []

    def recording_get_connection(*args, **kwargs):
        calls.append((args, kwargs))
        return django_mail.get_connection(*args, **kwargs)

    monkeypatch.setattr("seizou.core.mail.get_connection", recording_get_connection)

    response = manager_client.post(reverse("core:mail_test"))

    assert response.status_code == 302
    assert response["Location"] == reverse("core:settings")
    assert len(mailoutbox) == 1
    sent = mailoutbox[0]
    assert sent.to == ["notice@example.test"]
    assert sent.from_email == "notice@example.test"
    assert sent.subject == "seizou の試しのメール"
    assert sent.attachments == []
    assert "https://seizou.example.test" + reverse("core:settings") in sent.body
    assert PASSWORD not in sent.body
    args, kwargs = calls[0]
    assert args == ()
    assert "backend" not in kwargs
    assert kwargs["host"] == "smtp.example.test"
    assert kwargs["port"] == 587
    assert kwargs["username"] == "notice-account@example.test"
    assert kwargs["password"] == PASSWORD
    assert kwargs["use_tls"] is True and kwargs["use_ssl"] is False
    assert kwargs["timeout"] == mail.TIMEOUT_SECONDS == 5
    assert kwargs["fail_silently"] is False

    body = manager_client.get(reverse("core:settings")).content.decode()
    assert "試しのメールを notice@example.test へ送りました。" in toasts(body)
    assert PASSWORD not in body


@pytest.mark.django_db
def test_mail_test_uses_sender_name_and_ssl(manager_client, settings, mailoutbox):
    settings.SEIZOU = {"mail": {**FILLED, "security": "ssl", "port": 465, "from_name": "作り物の知らせ", "site_url": ""}}

    manager_client.post(reverse("core:mail_test"))

    assert len(mailoutbox) == 1
    assert mailoutbox[0].from_email == formataddr(("作り物の知らせ", "notice@example.test"))
    assert "seizou の URL の頭が入っていないので、知らせのメールにリンクが付きません。" in mailoutbox[0].body
    assert "http" not in mailoutbox[0].body


# ---------------------------------------------------------------- (f) 送れない


class RaisingConnection:
    """送る所で決めた例外を投げる偽の接続。"""

    def __init__(self, error):
        self.error = error

    def send_messages(self, messages):
        raise self.error


@pytest.mark.django_db
def test_mail_test_without_server_shows_red_toast(manager_client, settings, mailoutbox):
    settings.SEIZOU = {"mail": {**FILLED, "host": ""}}

    manager_client.post(reverse("core:mail_test"))

    body = manager_client.get(reverse("core:settings")).content.decode()
    box = toasts(body)
    assert "試しのメールを送れません。「サーバー」がまだ入っていません。" in box
    assert "失敗しました。" in box
    assert mailoutbox == []


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (smtplib.SMTPAuthenticationError(535, b"tests"), "サーバーがアカウントかパスワードを受け付けません。"),
        (smtplib.SMTPSenderRefused(550, b"tests", "notice@example.test"), "サーバーが差出人のアドレスを受け付けません。"),
        (smtplib.SMTPRecipientsRefused({}), "サーバーが送り先のアドレスを受け付けません。"),
        (smtplib.SMTPServerDisconnected(), "サーバーが送るのを断りました。"),
        (ConnectionRefusedError(), "サーバーにつながりません。"),
        (socket.timeout(), "サーバーにつながりません。"),
        (ssl.SSLError(), "暗号化の方式がサーバーと合いません。"),
        (ValueError(), "メールの見出しを作れません。"),
    ],
)
@pytest.mark.django_db
def test_mail_test_turns_errors_into_words(manager_client, settings, monkeypatch, caplog, error, expected):
    settings.SEIZOU = {"mail": FILLED}
    monkeypatch.setenv(mail.PASSWORD_ENV, PASSWORD)
    monkeypatch.setattr("seizou.core.mail.get_connection", lambda **kwargs: RaisingConnection(error))
    caplog.set_level(logging.DEBUG)

    response = manager_client.post(reverse("core:mail_test"))

    assert response.status_code == 302
    body = manager_client.get(reverse("core:settings")).content.decode()
    box = toasts(body)
    assert f"試しのメールを送れません。{expected}" in box
    assert PASSWORD not in body
    assert PASSWORD not in caplog.text
    records = [record for record in caplog.records if record.name == "seizou.mail"]
    assert [record.levelno for record in records] == [logging.WARNING]
    assert records[0].getMessage() == f"メールを送れなかった: {type(error).__name__}"


# ---------------------------------------------------------------- (g) 門


@pytest.mark.django_db
def test_mail_test_refuses_plain_user(plain_client, settings, mailoutbox):
    settings.SEIZOU = {"mail": FILLED}
    assert plain_client.post(reverse("core:mail_test")).status_code == 403
    assert mailoutbox == []


@pytest.mark.django_db
def test_mail_test_takes_post_only(manager_client, settings, mailoutbox):
    settings.SEIZOU = {"mail": FILLED}
    assert manager_client.get(reverse("core:mail_test")).status_code == 405
    assert mailoutbox == []


@pytest.mark.django_db
def test_mail_test_sends_anonymous_to_login(client, settings, mailoutbox):
    settings.SEIZOU = {"mail": FILLED}
    response = client.post(reverse("core:mail_test"))
    assert response.status_code == 302
    assert response["Location"].startswith(reverse("core:login"))
    assert mailoutbox == []


# ---------------------------------------------------------------- (h) 件名の改行


@pytest.mark.django_db
def test_send_turns_newlines_in_subject_into_spaces(settings, mailoutbox, caplog):
    settings.SEIZOU = {"mail": FILLED}
    caplog.set_level(logging.INFO)

    mail.send(["someone@example.test"], "1 行目\r\n2 行目\t" + "あ" * 300, "作り物の本文")

    assert len(mailoutbox) == 1
    subject = mailoutbox[0].subject
    assert "\n" not in subject and "\r" not in subject and "\t" not in subject
    assert subject.startswith("1 行目  2 行目 ")
    assert len(subject) == mail.SUBJECT_MAX
    records = [record.getMessage() for record in caplog.records if record.name == "seizou.mail"]
    assert records == ["メールを送った: 宛先 1 件"]


@pytest.mark.django_db
def test_send_refuses_empty_recipients(settings, mailoutbox):
    settings.SEIZOU = {"mail": FILLED}
    with pytest.raises(mail.MailNotSent, match="送り先のアドレスがありません。"):
        mail.send([], "件名", "本文")
    with pytest.raises(mail.MailNotSent, match="送り先のアドレスがありません。"):
        mail.send([""], "件名", "本文")
    assert mailoutbox == []


# ---------------------------------------------------------------- (i) 使い方の画面


def test_setting_update_help_shows_mail_refusals_from_screen_only(client_logged_in, folder):
    test_core_manual_auto.write_screen(folder, "core_setting_update")
    html = test_core_manual_auto.modal_html(client_logged_in, "core_setting_update")
    assert "保存できません。メールの送り方の「サーバー」は" in html
    assert "seizou の URL の頭" in html
    assert "host・port・security" not in html
    assert "starttls・ssl・none" not in html
    assert "「アカウント」は 254 字まで" not in html
