"""誤りの画面に右下の「AI に質問」の丸を出さない (作者の決め 11-07 23:4x「置かないでいいかな」。UI の会話の指示書)。

base.html は丸を block assistant_float で読み込み、core/error.html がその block を空にする。
見る所: ログインした人が開く 404 と、部署の判定の 403 (core/access_denied.html) の応答に `assistant-root` が無い。
ふつうの画面 (最初の画面) には今までどおりある。人と部署と部品は作り物。丸は鍵の有無で変わらない (test_assistant_window と同じ) ので、鍵は入れない。
"""

from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from seizou.accounts import services as accounts_services
from seizou.masters import services as masters_services

User = get_user_model()

PASSWORD = "kumo-tests-2611"
ALL_ON = {"department": "on", "write": "on", "money": "on"}


@pytest.fixture
def admin(db):
    return User.objects.create_superuser("admin", password=PASSWORD)


def test_home_keeps_the_ai_float(client, admin):
    client.force_login(admin)

    html = client.get(reverse("core:home")).content.decode()

    assert 'id="assistant-root"' in html


def test_404_page_has_no_ai_float(client, admin):
    client.force_login(admin)

    response = client.get("/no-such-page-for-the-test/")

    assert response.status_code == 404
    html = response.content.decode()
    assert "最初の画面へ" in html
    assert "assistant-root" not in html


def test_department_403_page_has_no_ai_float(settings, client, admin):
    settings.SEIZOU = {"access_control": dict(ALL_ON)}
    shaft = masters_services.create_item(
        {"part_no": "T-100", "name": "作り物の軸", "unit": "個", "standard_price": Decimal("1234")}, admin
    )
    sales = accounts_services.create_department("受注と出荷", ["orders", "shipping"], ["orders"], None)
    accounts_services.issue_account("jimu", PASSWORD, [sales], ["master_edit"], {}, None)
    client.force_login(User.objects.get(username="jimu"))

    response = client.get(reverse("masters:item_update", args=[shaft.pk]))

    assert response.status_code == 403
    html = response.content.decode()
    assert "最初の画面へ" in html
    assert "assistant-root" not in html
