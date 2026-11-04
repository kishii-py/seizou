"""部署の判定が log のときの記録の行に画面の名前を出す (決め 11-04 14:4x の 3。精査 A7 の 4 節の 2。依頼書 N104)。

部署の門 (access.screen_denied) が、403 の文と同じ画面の名前を access.can_open に screen= で渡す。
log の会社で部署の外の画面を開くと、記録の行は「「<画面>」の画面 (「<部品>」の部品) を開いた」になる。
画面の名前が無いとき (screen を渡さない呼び方・空の字) と、部品の名前と同じときは、今の字のまま。
部署と人は作り物 (精査 A7 の手順の keiri = 経理)。
"""

import logging

import pytest
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.core import access
from tests.test_core_access import access_records, account

#: 部署の判定を log にする (編集の権限と金額の閲覧は鍵が無いので既定の log)
DEPARTMENT_LOG = {"access_control": {"department": "log"}}


def _department_lines(caplog):
    """記録の行のうち部署の物だけ (log の会社では、編集の権限と金額の閲覧の行も混ざる)。"""
    return [r.getMessage() for r in access_records(caplog) if "(部署)" in r.getMessage()]


def _keiri():
    """精査 A7 の手順の経理の人 (部署は請求だけ)。"""
    keiri_dept = accounts_services.create_department("経理", ["billing"], [], None)
    return account("keiri", departments=[keiri_dept], see_money=True)


@pytest.mark.django_db
def test_log_line_names_screen_and_part(settings, client, caplog):
    settings.SEIZOU = DEPARTMENT_LOG
    caplog.set_level(logging.WARNING, logger="seizou.access")
    client.force_login(_keiri())

    # 精査 A7 の 4 節の 2 の手順: 経理の人が受信した注文の一覧 (connect = 部署の外) を開く。log なので開ける。
    assert client.get(reverse("connect:inbox_list")).status_code == 200
    assert _department_lines(caplog) == [
        "記録だけ (部署): keiri が 「受信した注文」の画面 (「受注管理」の部品) を開いた (「受注管理」を開ける部署)"
    ]

    # 画面の名前と部品の名前が同じときは括弧を付けない (403 の文と同じ選び方。今の字のまま)。
    caplog.clear()
    assert client.get(reverse("orders:order_list")).status_code == 200
    assert _department_lines(caplog) == ["記録だけ (部署): keiri が 「受注」の画面を開いた (「受注」を開ける部署)"]


@pytest.mark.django_db
def test_can_open_without_screen_keeps_current_line(settings, caplog):
    settings.SEIZOU = DEPARTMENT_LOG
    caplog.set_level(logging.WARNING, logger="seizou.access")
    keiri = _keiri()

    # screen を渡さない呼び方と、空の字 (画面の名前を持たない画面) は、今の字のまま。
    assert access.can_open(keiri, "connect") is True
    assert access.can_open(keiri, "shipping", screen="") is True
    assert _department_lines(caplog) == [
        "記録だけ (部署): keiri が 「受注管理」の画面を開いた (「受注管理」を開ける部署)",
        "記録だけ (部署): keiri が 「出荷」の画面を開いた (「出荷」を開ける部署)",
    ]
