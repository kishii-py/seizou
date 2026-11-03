"""初見テスト 3 回目 (R3) の言葉と小さな動きの直し。Django の test client で HTML と表の状態を見る。

既にある試験のファイルから import せず、fixture は手本の試験の形を写す。品番と会社名は作り物。
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.masters.forms import PartnerForm

ROUNDING_HELP = "納品書と請求書の金額の端数 (四捨五入・切上・切捨)。空なら会社の既定に従います。"
TAX_HELP = "消費税の端数 (トータル・各行四捨五入・各行切捨)。空なら会社の既定に従います。"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


def text(response):
    return response.content.decode()


# --- 取引先の入力の端数の説明 ---


@pytest.mark.django_db
def test_partner_form_help_texts_for_rounding():
    fields = PartnerForm().fields
    assert fields["rounding"].help_text == ROUNDING_HELP
    assert fields["tax_mode"].help_text == TAX_HELP
    # 今の 2 行は字のまま
    assert fields["closing_day"].help_text == "1〜31。月末締めは 31"
    assert fields["payment_day"].help_text == "1〜31。月末は 31"


@pytest.mark.django_db
def test_partner_create_shows_rounding_help(client_logged_in):
    response = client_logged_in.get(reverse("masters:partner_create"))
    assert response.status_code == 200
    body = text(response)
    assert ROUNDING_HELP in body and TAX_HELP in body
    # 取引先の種類の字は「協力企業」(古い呼び名は tests/test_wording.py が全部のファイルで見るので、ここには書かない)
    assert "協力企業" in body
