"""intake の画面 (注文書の読み取りの一覧・読み取る・詳細・受注にする) のテスト。core の土台の上。

本物の API は呼ばない (autouse の fixture が _ask_claude を「呼ばれたら AssertionError」に差し替える)。
返事が要るテストだけが作り物の返事に差し替える。鍵はテストごとに setenv か delenv で明示する。
品番と会社名は作り物。
"""

import re

import pytest
from django.contrib.auth import get_user_model
from django.template.loader import render_to_string
from django.urls import reverse

from seizou.intake import services
from seizou.intake.forms import AcceptForm, UploadForm
from seizou.intake.models import Extraction
from seizou.masters.services import create_item, create_partner
from seizou.orders.models import Order
from tests import test_examples_manual as seed_tests
from tests.test_core_manual_default import DEFAULT_DIR
from tests.test_intake_extract import FAKE_KEY, REPLY_PATH, refuse_api, upload


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
def fake_reply(no_real_api, monkeypatch):
    monkeypatch.setattr(services, "_ask_claude", lambda model, block: REPLY_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def manager(db):
    """アカウント管理の権限がある人 (システム管理者)。"""
    return get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return create_partner({"code": "IV01", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def extraction(with_key, fake_reply, customer, user, monkeypatch):
    record = services.extract_order(upload(), customer, user)
    monkeypatch.setattr(services, "_ask_claude", refuse_api)
    return record


def _post_upload(client, customer, data=None):
    file = upload() if data is None else upload(data, "order.bin", "application/octet-stream")
    return client.post(reverse("intake:extraction_create"), {"customer": customer.pk, "file": file})


@pytest.mark.django_db
def test_list_requires_login(client):
    response = client.get(reverse("intake:extraction_list"))

    assert response.status_code == 302
    assert reverse("core:login") in response["Location"]


def test_create_page_with_key(client_logged_in, with_key):
    response = client_logged_in.get(reverse("intake:extraction_create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "<fieldset disabled" not in body
    assert "鍵が無いので読み取りは使えません" not in body
    assert 'enctype="multipart/form-data"' in body


def test_create_page_without_key_is_disabled(client_logged_in, without_key):
    response = client_logged_in.get(reverse("intake:extraction_create"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "鍵が無いので読み取りは使えません" in body
    assert "alert-warning" in body
    assert "<fieldset disabled" not in body
    assert reverse("orders:order_create") in body


def _band(body):
    """鍵が無いときの黄の帯 (alert-warning の箱) の HTML。帯の中に div は無い (頭の印は svg、中は span と a)。"""
    match = re.search(r'<div class="alert alert-warning alert-soft mb-4" role="alert">.*?</div>', body, re.S)
    assert match, "鍵が無いときの黄の帯が無い"
    return match.group(0)


def test_create_page_without_key_has_no_card(client_logged_in, without_key):
    # 使えない欄とボタンは描かず、黄の帯の文と手入力への link だけで次の手を示す (C09)。
    body = client_logged_in.get(reverse("intake:extraction_create")).content.decode()

    assert "data-file-drop" not in body
    assert "<fieldset" not in body
    assert "「必須」の欄を埋めて" not in body
    assert 'name="customer"' not in body
    assert f'href="{reverse("orders:order_create")}"' in _band(body)


NO_KEY_FOR_ALL = "注文書の読み取りは、管理者が AI の鍵を設定すると使えます。"


@pytest.mark.parametrize("who", ["user", "manager"])
def test_create_page_without_key_band(request, client, without_key, who):
    # 帯は手入力だけを示し、受信した注文へは誘わない。鍵の置き場はアカウント管理の権限がある人にだけ出す (C09)。
    # 左のメニュー (受信した注文の入口)・上の帯の「設定」・AI チャットの窓 (鍵の字) が body に出るので、帯の中だけを見る。
    client.force_login(request.getfixturevalue(who))
    band = _band(client.get(reverse("intake:extraction_create")).content.decode())

    assert "AI の鍵が無いので読み取りは使えません。" in band
    assert NO_KEY_FOR_ALL in band
    assert f'href="{reverse("orders:order_create")}"' in band
    assert "受信した注文" not in band
    assert reverse("connect:inbox_list") not in band
    if who == "manager":
        assert services.MESSAGE_NO_KEY_MANAGER in band
        assert f'href="{reverse("core:settings")}"' in band
    else:
        assert "ANTHROPIC_API_KEY" not in band
        assert reverse("core:settings") not in band


def test_create_post_reads_and_moves_to_detail(client_logged_in, with_key, fake_reply, customer, user):
    response = _post_upload(client_logged_in, customer)

    assert Extraction.objects.count() == 1
    saved = Extraction.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("intake:extraction_detail", args=[saved.pk])
    assert saved.customer == customer and saved.read_by == user
    assert Order.objects.count() == 0


def test_create_post_without_key_is_red(client_logged_in, without_key, customer):
    response = _post_upload(client_logged_in, customer)

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "鍵が無いので読み取りは使えません" in body
    assert Extraction.objects.count() == 0


def test_create_post_unreadable_is_red(client_logged_in, with_key, customer):
    response = _post_upload(client_logged_in, customer, b"PK\x03\x04" + b"\x00" * 16)

    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-notice="stop"' in body and "読めない形式" in body
    assert Extraction.objects.count() == 0


def test_detail_shows_lines_and_notice(client_logged_in, extraction):
    response = client_logged_in.get(reverse("intake:extraction_detail", args=[extraction.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    assert "X-100" in body and "X-200" in body
    assert "AI が読み取った値です" in body
    assert reverse("intake:extraction_accept", args=[extraction.pk]) in body
    assert "TEST-PO-001" in body
    assert extraction.file.url in body and 'target="_blank"' in body


def _mapping_post(client, extraction, user):
    shaft = create_item({"part_no": "S-100", "name": "作り物のシャフト", "unit": "個"}, user)
    bracket = create_item({"part_no": "B-200", "name": "作り物のブラケット", "unit": "個", "standard_price": "240"}, user)
    url = reverse("intake:extraction_accept", args=[extraction.pk])
    return url, {"item_1": shaft.pk, "item_2": bracket.pk}


def test_accept_creates_order_and_moves(client_logged_in, extraction, user):
    url, data = _mapping_post(client_logged_in, extraction, user)
    page = client_logged_in.get(url)
    assert page.status_code == 200
    assert "得意先の品番 X-100 (作り物のシャフト)" in page.content.decode()

    response = client_logged_in.post(url, data)

    assert Order.objects.count() == 1
    order = Order.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:order_detail", args=[order.pk])
    assert order.source == Order.SOURCE_INTAKE
    detail = client_logged_in.get(reverse("intake:extraction_detail", args=[extraction.pk])).content.decode()
    assert url not in detail
    assert order.order_no in detail


def test_accept_twice_is_red(client_logged_in, extraction, user):
    url, data = _mapping_post(client_logged_in, extraction, user)
    client_logged_in.post(url, data)

    again = client_logged_in.post(url, data)

    assert again.status_code == 200
    body = again.content.decode()
    assert 'data-notice="stop"' in body and "受注" in body
    assert Order.objects.count() == 1


# ---------------------------------------------------------------- 受注にする画面の品番ごとの枠 (初見テスト R5 の候補 C91 の その 2)

DASH = '<span class="text-base-content/60">—</span>'


def _frame(body, name):
    """受注にする画面の data-accept-part="<name>" の枠の字 (その印から、次の枠の印か </form> まで)。"""
    start = body.index(f'data-accept-part="{name}"')
    ends = [at for at in (body.find("data-accept-part=", start + 1), body.find("</form>", start)) if at != -1]
    return body[start : min(ends)]


def test_accept_screen_puts_line_values_beside_each_part_field(client_logged_in, extraction):
    """受注にする画面で、欄 1 つの枠に、その品番の読み取った明細の数量・単位・納期・単価が並ぶ (受信した注文の受け入れと同じ形)。"""
    response = client_logged_in.get(reverse("intake:extraction_accept", args=[extraction.pk]))

    assert response.status_code == 200
    body = response.content.decode()
    first, second = _frame(body, "item_1"), _frame(body, "item_2")
    assert "X-100" in first and ">10 個<" in first and "2026-11-20" in first and ">¥120<" in first
    assert 'name="item_1"' in first
    assert "2026-11-27" not in first and 'name="item_2"' not in first
    # 単価が null の行は、単価の dd に薄い「—」
    assert "X-200" in second and ">5 個<" in second and "2026-11-27" in second and 'name="item_2"' in second
    assert f'単価</dt><dd class="tabular-nums">{DASH}</dd>' in second
    assert "None" not in second
    # 上の AI の知らせと読み取った明細の表は今のまま、枠より上に出る
    assert body.index("AI が読み取った値です") < body.index("読み取った明細 (") < body.index('data-accept-part="item_1"')


def test_accept_card_hides_unit_price_without_money(extraction):
    """金額の閲覧の無い人 (seizou.hide_money) には、枠に単価の行を dt も dd も出さない (上の表の単価の列と同じ)。"""

    def render(hide):
        return render_to_string(
            "intake/_extraction_accept_card.html", {"form": AcceptForm(instance=extraction), "seizou": {"hide_money": hide}}
        )

    hidden, shown = render(True), render(False)
    assert "単価</dt>" not in hidden and "¥120" not in hidden
    assert "単価</dt>" in shown and "¥120" in shown


def test_list_shows_record(client_logged_in, extraction):
    response = client_logged_in.get(reverse("intake:extraction_list"))

    assert response.status_code == 200
    body = response.content.decode()
    assert "作り物の得意先" in body
    assert "読み取った" in body
    assert "X-100 ×10 ほか 1 行" in body
    assert reverse("intake:extraction_create") in body


def test_menu_has_no_intake_entry_but_url_works(client_logged_in):
    # 左のメニューの入口は外した (受注の画面の「注文書を読み取る」から入る。02 の 8 節の決め 6)。URL と画面は残る。
    response = client_logged_in.get(reverse("core:home"))

    body = response.content.decode()
    assert "注文書の読み取り" not in body
    assert reverse("intake:extraction_list") not in body
    assert client_logged_in.get(reverse("intake:extraction_list")).status_code == 200


def test_manual_names_the_read_button():
    # 「?」の説明のボタンの字を、画面のボタンの字 (UploadForm.submit_label =「読み取る」) にそろえる (C09 の V121)。
    raw = (DEFAULT_DIR / "intake_extraction_create.html").read_text(encoding="utf-8")
    quoted = seed_tests.QUOTED.findall(seed_tests.plain(raw))

    assert UploadForm.submit_label in quoted
    assert "保存" not in quoted
