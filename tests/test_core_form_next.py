"""入力の土台 (BaseFormView) の、開いた元の画面へ戻す口 (?next=)。

背景 (11-05): 部品の詳細の「添付ファイルを付ける」で開いた入力の「キャンセル」が、詳細でなく整理用の添付ファイルの一覧へ戻った
(本人の指摘)。初見テスト R4 の参考にも「入力のキャンセルが開いた元の画面でなく、いつも一覧へ戻る」(在庫の一覧から開いた入庫・
請求書の詳細から開いた入金) がある。
約束: ?next=<同じサイトの道> があれば「キャンセル」の先と、一覧へ戻る形の保存の後の移り先をその道にする。保存した物の詳細へ移る形は
今のまま。外の URL・絶対 URL は捨てて今までどおり。form の hidden の next で POST にも引き継ぐ。
開く側: 詳細の「編集」・詳細の「添付ファイルを付ける」・請求書の詳細の「入金を記録する」・発注の詳細の「受入を登録」などが ?next= を付ける。
"""

from datetime import date
from decimal import Decimal

import pytest
from django import forms
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages.storage.fallback import FallbackStorage
from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory
from django.urls import reverse

from seizou.billing import services as billing_services
from seizou.core.views import BaseFormView
from seizou.masters import services as masters_services
from seizou.masters.models import Item
from seizou.orders import services as orders_services
from seizou.purchasing import services as purchasing_services
from seizou.shipping import services as shipping_services
from tests.test_input_screens import _update_post

ORDER_DATE = date(2026, 11, 2)
SHIPPED_AT = date(2026, 11, 5)
ISSUED_ON = date(2026, 11, 30)
# 開いた元の画面の道 (一覧の絞りの付いた形。字のまま戻る)
CAME_FROM = "/masters/items/?q=T-100"
RETURNS: list = []


def _service(data, user, *, ext=None):
    """保存した物を RETURNS の頭から返す (None = 保存した物の詳細が無い形)。"""
    return RETURNS.pop(0) if RETURNS else None


class NoteForm(forms.Form):
    note = forms.CharField(label="備考", required=False)


class NoteView(BaseFormView):
    """試験の入力。

    使う人: 試験 / いつ: 試験のとき / 何のために: 開いた元の画面へ戻す口を見る / 次にすること: 無し
    """

    model = Item
    form_class = NoteForm
    service = staticmethod(_service)
    screen_name = "試験の入力"
    template_name = "core/form.html"


def body(response) -> str:
    if hasattr(response, "render"):
        response.render()
    return response.content.decode()


@pytest.fixture(autouse=True)
def _clean():
    RETURNS.clear()
    yield
    RETURNS.clear()


@pytest.fixture
def call(user):
    """NoteView を RequestFactory で呼ぶ。call("get" か "post", 送る値, 見出し)。"""
    factory = RequestFactory()

    def make(method, data=None, headers=None, path="/x/"):
        request = getattr(factory, method)(path, data or {}, **(headers or {}))
        SessionMiddleware(lambda r: None).process_request(request)
        request.session.save()
        request._messages = FallbackStorage(request)
        request.user = user
        return NoteView.as_view()(request)

    return make


def _hidden_next(value: str) -> str:
    return f'<input type="hidden" name="next" value="{value}">'


# ---------------------------------------------------------------- 土台の口


@pytest.mark.django_db
def test_next_sets_cancel_link_and_hidden_input(call):
    response = call("get", {"next": CAME_FROM})
    html = body(response)

    assert response.context_data["cancel_url"] == CAME_FROM
    assert f'href="{CAME_FROM}"' in html and "キャンセル" in html
    assert _hidden_next(CAME_FROM) in html
    # next は入力の値でないので、form の初期値に写さない
    assert "next" not in response.context_data["form"].initial


@pytest.mark.django_db
def test_save_returns_to_next_when_no_detail(call):
    # 保存した物の詳細が無い形 (一覧へ戻る形) は、form の hidden の next (POST) へ移る
    response = call("post", {"note": "作り物", "next": CAME_FROM})
    assert response.status_code == 302
    assert response["Location"] == CAME_FROM
    # htmx の要求でも同じ先 (HX-Redirect)
    response = call("post", {"note": "作り物", "next": CAME_FROM}, {"HTTP_HX_REQUEST": "true"})
    assert response["HX-Redirect"] == CAME_FROM
    # form の action の無い送り (GET の ?next= の付いた同じ URL へ POST) でも同じ
    response = call("post", {"note": "作り物"}, path=f"/x/?next={CAME_FROM.replace('?', '%3F').replace('=', '%3D')}")
    assert response["Location"] == CAME_FROM


@pytest.mark.django_db
def test_save_to_saved_detail_ignores_next(call, user):
    # 保存した物の詳細へ移る形 (受注の登録など) は今のまま。next は効かない
    item = masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)
    RETURNS.append(item)
    response = call("post", {"note": "作り物", "next": CAME_FROM})
    assert response["Location"] == reverse("masters:item_detail", args=[item.pk])


@pytest.mark.django_db
def test_without_next_keeps_list(call):
    list_url = reverse("masters:item_list")
    response = call("get")
    assert response.context_data["cancel_url"] == list_url
    assert 'name="next"' not in body(response)
    assert call("post", {"note": "作り物"})["Location"] == list_url


@pytest.mark.django_db
@pytest.mark.parametrize(
    "bad",
    [
        "https://evil.example/x",
        "http://testserver/masters/items/",  # 同じ host でも絶対 URL は捨てる
        "//evil.example/x",
        "/\\evil.example/x",
        "javascript:alert(1)",
        "masters/items/",
    ],
)
def test_outside_or_absolute_next_is_dropped(call, bad):
    list_url = reverse("masters:item_list")
    response = call("get", {"next": bad})
    assert response.context_data["cancel_url"] == list_url
    assert 'name="next"' not in body(response)
    assert call("post", {"note": "作り物", "next": bad})["Location"] == list_url


# ---------------------------------------------------------------- 開く側の link


def _customer(user):
    return masters_services.create_partner(
        {"code": "C101", "name": "作り物の得意先", "role": "customer", "closing_day": 20, "payment_month": "next", "payment_day": 25},
        user,
    )


def _invoice(user, item):
    customer = _customer(user)
    order = orders_services.create_order(
        customer, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], user, order_date=ORDER_DATE
    )
    shipment = shipping_services.ship(order.lines.get(), Decimal("4"), SHIPPED_AT, user)
    shipping_services.create_delivery_note(customer, [shipment], user)
    shipment.refresh_from_db()
    return billing_services.create_invoice(customer, [shipment], user, issued_on=ISSUED_ON)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.mark.django_db
def test_attachment_button_passes_detail_and_cancel_returns_there(client_logged_in, item):
    detail_url = reverse("masters:item_detail", args=[item.pk])
    ct = ContentType.objects.get_for_model(item).pk
    add_url = reverse("attachments:attachment_add")

    html = client_logged_in.get(detail_url).content.decode()
    assert f'href="{add_url}?content_type={ct}&amp;object_id={item.pk}&amp;next={detail_url}"' in html

    # 開いた入力の「キャンセル」は、整理用の添付ファイルの一覧でなく部品の詳細へ戻る
    form = client_logged_in.get(add_url, {"content_type": ct, "object_id": item.pk, "next": detail_url})
    assert form.context["cancel_url"] == detail_url
    assert _hidden_next(detail_url) in form.content.decode()


@pytest.mark.django_db
def test_payment_link_on_invoice_detail_passes_next(client_logged_in, user, item):
    # 初見テスト R4: 請求書の詳細から開いた入金の「キャンセル」は請求書の一覧でなく詳細へ
    invoice = _invoice(user, item)
    detail_url = reverse("billing:invoice_detail", args=[invoice.pk])
    payment_url = reverse("billing:payment_create", args=[invoice.pk])

    html = client_logged_in.get(detail_url).content.decode()
    assert f'href="{payment_url}?next={detail_url}"' in html
    assert client_logged_in.get(payment_url, {"next": detail_url}).context["cancel_url"] == detail_url


@pytest.mark.django_db
def test_receipt_links_on_purchase_order_detail_pass_next(client_logged_in, user, item, monkeypatch):
    # 初見テスト R4: 発注の詳細の「受入を登録」から開いた受入の「キャンセル」は受入の一覧でなく発注の詳細へ
    monkeypatch.setattr("requests.post", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("HTTP を呼んだ")))
    partner = masters_services.create_partner({"code": "S001", "name": "作り物の協力企業", "role": "subcontractor"}, user)
    po = purchasing_services.create_purchase_order(
        partner, [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("25")}], user, order_date=ORDER_DATE
    )
    line = po.lines.get()
    detail_url = reverse("purchasing:purchaseorder_detail", args=[po.pk])
    receipt_url = reverse("purchasing:receipt_create")

    html = client_logged_in.get(detail_url).content.decode()
    assert f'href="{receipt_url}?line={line.pk}&amp;next={detail_url}"' in html
    response = client_logged_in.get(receipt_url, {"line": line.pk, "next": detail_url})
    assert response.context["cancel_url"] == detail_url
    # 明細の初めの選択 (?line=) は今までどおり form の初期値に入る
    assert str(response.context["form"].initial["line"]) == str(line.pk)


def _order(user, item):
    return orders_services.create_order(
        _customer(user), [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("1500")}], user, order_date=ORDER_DATE
    )


@pytest.mark.django_db
def test_edit_link_on_order_detail_passes_next_and_cancel_returns_there(client_logged_in, user, item):
    # 初見テスト R5 (候補 C63): 受注の詳細の「編集」から開いた編集の「キャンセル」は受注の一覧でなく詳細へ
    order = _order(user, item)
    detail_url = reverse("orders:order_detail", args=[order.pk])
    edit_url = reverse("orders:order_update", args=[order.pk])

    html = client_logged_in.get(detail_url).content.decode()
    assert f'href="{edit_url}?next={detail_url}"' in html
    assert client_logged_in.get(edit_url, {"next": detail_url}).context["cancel_url"] == detail_url


@pytest.mark.django_db
def test_order_update_with_next_saves_to_detail_and_keeps_cancel_after_error(client_logged_in, user, item):
    # 保存の後は今までどおり受注の詳細へ。数量が空で止まって描き直しても「キャンセル」は詳細のまま
    # (受注の編集の form は hidden の next を持たず、action の無い form が ?next= の付いた同じ URL へ送る形の見張り)
    order = _order(user, item)
    detail_url = reverse("orders:order_detail", args=[order.pk])
    url = f'{reverse("orders:order_update", args=[order.pk])}?next={detail_url}'

    response = client_logged_in.post(url, _update_post(order))
    assert response.status_code == 302
    assert response["Location"] == detail_url

    response = client_logged_in.post(url, _update_post(order, qty=""))
    assert response.status_code == 200
    assert response.context["cancel_url"] == detail_url


@pytest.mark.django_db
def test_edit_link_on_item_detail_passes_next(client_logged_in, item):
    # 土台の「編集」をそのまま使う詳細 (block edit_button を上書きしない) の代表 = 部品の詳細
    detail_url = reverse("masters:item_detail", args=[item.pk])
    edit_url = reverse("masters:item_update", args=[item.pk])

    html = client_logged_in.get(detail_url).content.decode()
    assert f'href="{edit_url}?next={detail_url}"' in html
