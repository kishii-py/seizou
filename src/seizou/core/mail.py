"""知らせのメールを送る口。サーバー・ポート・暗号化の方式は .env の EMAIL_HOST・EMAIL_PORT・EMAIL_SECURITY だけ (core.conf.MAIL_ENV。画面では読むだけ)、アカウント・差出人などは会社の設定 mail (設定の画面の「メールの知らせ」の「編集」で入れる)、パスワードは .env の EMAIL_HOST_PASSWORD だけ (画面と表と記録に値を出さない)。送るのはこの module だけ。

- has_password(): パスワードが .env に置いてあるか (値は返さない)。
- missing() / ready(): 送り方のうち空の要る物 (サーバー・差出人) の見出しと、送れる形か。
- link(path): seizou の URL の頭 (site_url) を付けたリンク。頭が空なら空の字。
- send(to, subject, body): 字だけのメールを送る。送れなければ MailNotSent (字は設定の画面で直せる日本語)。
- send_test(): 設定の画面の「試しに送る」。差出人のアドレスへ試しのメールを送る。
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.utils import formataddr

from django.core.mail import EmailMessage, get_connection
from django.urls import reverse

from seizou.core import conf, services

#: パスワードを置く環境変数の名前 (.env の行。値は画面と表と記録に出さない)
PASSWORD_ENV = "EMAIL_HOST_PASSWORD"
#: サーバーを待つ秒 (会話の受け口の中で送るとき、送り手が待つのをやめる 10 秒より前に返すため)
TIMEOUT_SECONDS = 5
#: 件名の字数の上限
SUBJECT_MAX = 200

#: 送り方のうち、空だと送れない物の見出し (設定の変更の画面の欄の名前と同じ字)
_REQUIRED = (("host", "サーバー"), ("from", "差出人"))
#: 空のときの入れ方 (サーバーは .env だけで決め、差出人は画面で入れる)
_REQUIRED_HINTS = {
    "サーバー": "「サーバー」は置き場の .env に EMAIL_HOST= の行で置き、起動し直してください。",
    "差出人": "「差出人」は設定の画面の「メールの知らせ」の「編集」で入れてください。",
}

logger = logging.getLogger("seizou.mail")


class MailNotSent(Exception):
    """メールを送れなかった。字は人が直せる日本語 (パスワードの値と本文を入れない)。"""


def has_password() -> bool:
    """パスワード (環境変数 EMAIL_HOST_PASSWORD) が置いてあるか。値は返さない。

    Django の settings の EMAIL_HOST_PASSWORD は読まない。今ある会社の settings.py (seizou new が作った写し) に EMAIL_ の行が無く、
    雛形に行を足しても今ある会社には入らないため。AI の鍵 (core.ai) と同じく、.env の行が起動の時に入った環境変数を直に読む。
    """
    return bool(os.environ.get(PASSWORD_ENV, "").strip())


def missing() -> list[str]:
    """送り方のうち空の要る物の見出し (「サーバー」「差出人」の順)。空の並びなら送れる形。"""
    values = conf.mail_settings()
    return [label for key, label in _REQUIRED if not values[key]]


def ready() -> bool:
    """送り方のサーバーと差出人が入っているか (届くかは「試しに送る」で確かめる)。"""
    return not missing()


def link(path: str) -> str:
    """seizou の URL の頭を付けたリンク。頭が空なら空の字 (リンクを付けない)。"""
    head = conf.mail_settings()["site_url"]
    return f"{head}{path}" if head else ""


def _clean_subject(subject: str) -> str:
    """件名の改行と制御の字を空白にし、SUBJECT_MAX 字で切る。"""
    text = "".join(" " if ord(char) < 32 or ord(char) == 127 else char for char in str(subject))
    return text[:SUBJECT_MAX]


def send(to, subject: str, body: str) -> None:
    """字だけのメールを送る (HTML にしない・添付を付けない)。送れなければ MailNotSent。

    送り方は conf.mail_settings() (サーバー・ポート・暗号化の方式は環境変数だけから読む)、パスワードは環境変数 EMAIL_HOST_PASSWORD。django.core.mail の get_connection に渡す
    (backend は渡さない。会社では Django の既定の SMTP で送り、試験では pytest-django が既定を locmem に替える)。
    件名の改行と制御の字は空白にする (Django は改行のある件名を断る。件名に入る相手の注文番号は相手の字なので、ここで消す)。
    記録には宛先の数と例外の class の名前だけを書き、宛先のアドレス・件名・本文・パスワードは書かない。
    訳: 設定の欠けたメールを送らず、送れなかった訳を設定の画面で直せる字で返すため (パスワードの値は字にも記録にも出さない)。待ちを 5 秒にしたのは、会話の受け口の中で送るとき、送り手が 10 秒で待つのをやめるより前に返すため。出所 = 作者の決め (送り方は設定の画面で入れ、パスワードは .env に置く) と AI の案 (字と 5 秒)。パスワードを渡すサーバー・ポート・暗号化の方式を .env だけにしたのは Claude Security の走査の候補 11-07 の 11・23・25・26。変えてよい条件 = 後で送る仕組み (待ち行列など) を足す時。
    """
    empty = missing()
    if empty:
        names = "".join(f"「{label}」" for label in empty)
        hints = "".join(_REQUIRED_HINTS[label] for label in empty)
        raise MailNotSent(f"{names}がまだ入っていません。{hints}")
    recipients = [address for address in (to or ()) if address]
    if not recipients:
        raise MailNotSent("送り先のアドレスがありません。")
    values = conf.mail_settings()
    sender = formataddr((values["from_name"], values["from"])) if values["from_name"] else values["from"]
    try:
        connection = get_connection(
            host=values["host"],
            port=values["port"],
            username=values["user"],
            password=os.environ.get(PASSWORD_ENV, "").strip(),
            use_tls=values["security"] == "starttls",
            use_ssl=values["security"] == "ssl",
            timeout=TIMEOUT_SECONDS,
            fail_silently=False,
        )
        EmailMessage(_clean_subject(subject), body, from_email=sender, to=recipients, connection=connection).send()
    except smtplib.SMTPAuthenticationError as exc:
        _not_sent(exc)
        raise MailNotSent("サーバーがアカウントかパスワードを受け付けません。「アカウント」と .env の EMAIL_HOST_PASSWORD を確かめてください。") from None
    except smtplib.SMTPSenderRefused as exc:
        _not_sent(exc)
        raise MailNotSent("サーバーが差出人のアドレスを受け付けません。「差出人」を確かめてください。") from None
    except smtplib.SMTPRecipientsRefused as exc:
        _not_sent(exc)
        raise MailNotSent("サーバーが送り先のアドレスを受け付けません。送り先を確かめてください。") from None
    except smtplib.SMTPException as exc:
        _not_sent(exc)
        raise MailNotSent("サーバーが送るのを断りました。「暗号化の方式」と「ポート」を確かめてください。") from None
    except ssl.SSLError as exc:
        _not_sent(exc)
        raise MailNotSent("暗号化の方式がサーバーと合いません。「暗号化の方式」と「ポート」を確かめてください。") from None
    except OSError as exc:
        # smtplib の例外も ssl.SSLError も OSError の子なので、この枝は上の枝の後に置く (つながらない・名前を引けない・待ちを超えた)
        _not_sent(exc)
        raise MailNotSent("サーバーにつながりません。「サーバー」と「ポート」を確かめてください。") from None
    except ValueError as exc:
        # Django の BadHeaderError ほか (答えの YAML の値は画面の確かめを通らないので、改行の入った差出人などで 500 にしない)
        _not_sent(exc)
        raise MailNotSent("メールの見出しを作れません。「差出人」「差出人の名前」と送り先のアドレスを確かめてください。") from None
    logger.info("メールを送った: 宛先 %d 件", len(recipients))


def _not_sent(exc: Exception) -> None:
    """送れなかった記録 (warning)。例外の class の名前だけを書く (相手のサーバーの返事の字とパスワードを書かない)。"""
    logger.warning("メールを送れなかった: %s", type(exc).__name__)


def send_test() -> str:
    """設定の画面の「試しに送る」。差出人のアドレスへ試しのメールを送り、送った宛先を返す。断りは send と同じ MailNotSent。"""
    address = conf.mail_settings()["from"]
    company = services.company_or_none()
    name = (company.name if company is not None else "") or "会社"
    url = link(reverse("core:settings"))
    body = "\n".join(
        [
            f"{name} の seizou から、設定の画面の「メールの送り方」で送りました。",
            "このメールが届いていれば、取引先から会話が届いたときの知らせも、同じ送り方で送ります。",
            "",
            url or "seizou の URL の頭が入っていないので、知らせのメールにリンクが付きません。",
        ]
    )
    send([address] if address else [], "seizou の試しのメール", body + "\n")
    return address
