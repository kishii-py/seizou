"""添付ファイルの枠と画面 (02 の 5 節の N30 の 2 段目)。core の詳細の画面の下の枠・付ける・削除・一覧。"""

import re
from datetime import date
from decimal import Decimal

import pytest
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.attachments import services
from seizou.attachments.models import Attachment
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services

ORDER_DATE = date(2026, 11, 2)
PDF_BYTES = b"%PDF-1.4\n% made for tests\n"


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user("tester", password="pass-for-tests-only")


@pytest.fixture
def client_logged_in(client, user):
    client.force_login(user)
    return client


@pytest.fixture
def customer(user):
    return masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)


@pytest.fixture
def item(user):
    return masters_services.create_item({"part_no": "T-100", "name": "作り物の軸", "unit": "個"}, user)


@pytest.fixture
def order(user, customer, item):
    return orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=ORDER_DATE,
    )


@pytest.fixture
def line(order):
    return order.lines.get()


@pytest.fixture
def cut(db):
    return Process.objects.create(code="CUT", name="切断", sort_order=1)


@pytest.fixture
def workorder(user, line, cut):
    return workorders_services.issue_workorder(line, Decimal("4"), [{"process": cut, "planned_qty": Decimal("4")}], user)


def ct_of(obj) -> int:
    return ContentType.objects.get_for_model(obj).pk


def add_url_for(obj) -> str:
    return f"{reverse('attachments:attachment_add')}?content_type={ct_of(obj)}&amp;object_id={obj.pk}"


def html_of(response) -> str:
    return response.content.decode()


@pytest.mark.django_db
def test_panel_on_order_item_workorder_detail(client_logged_in, order, item, workorder):
    pages = [
        (reverse("orders:order_detail", args=[order.pk]), order),
        (reverse("masters:item_detail", args=[item.pk]), item),
        (reverse("workorders:workorder_detail", args=[workorder.pk]), workorder),
    ]
    for url, obj in pages:
        response = client_logged_in.get(url)
        html = html_of(response)
        assert response.status_code == 200, url
        assert 'id="attachments"' in html, url
        assert "添付ファイル" in html
        assert add_url_for(obj) in html, url
        assert "添付ファイルを付ける" in html
        assert "添付ファイルは、まだ付いていません。" in html
        assert "写真・検査成績書・作業手順などを付けると、ここに並びます。" in html


@pytest.mark.django_db
def test_add_from_panel_returns_to_record(client_logged_in, order):
    add_url = reverse("attachments:attachment_add")
    response = client_logged_in.get(add_url, {"content_type": ct_of(order), "object_id": order.pk})
    html = html_of(response)
    assert response.status_code == 200
    assert re.search(rf'<input type="hidden" name="content_type" value="{ct_of(order)}"', html)
    assert re.search(rf'<input type="hidden" name="object_id" value="{order.pk}"', html)
    assert 'enctype="multipart/form-data"' in html

    response = client_logged_in.post(
        add_url,
        {
            "content_type": ct_of(order),
            "object_id": order.pk,
            "file": SimpleUploadedFile("検査成績書.pdf", PDF_BYTES),
            "kind": "inspection",
            "note": "出荷前の検査",
        },
    )
    attachment = Attachment.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("attachments:attachment_detail", args=[attachment.pk])
    assert attachment.target == order
    assert attachment.kind == "inspection"
    assert attachment.original_name == "検査成績書.pdf"

    response = client_logged_in.get(response["Location"])
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:order_detail", args=[order.pk])

    html = html_of(client_logged_in.get(response["Location"]))
    assert "検査成績書.pdf" in html
    assert "出荷前の検査" in html
    assert f'href="{reverse("attachments:attachment_download", args=[attachment.pk])}"' in html
    assert "開く" in html
    assert reverse("attachments:attachment_delete", args=[attachment.pk]) in html


@pytest.mark.django_db
def test_panel_has_no_media_url(client_logged_in, order, workorder, user):
    attachments = [
        services.add_attachment(order, SimpleUploadedFile("受注の写真.png", b"png"), user, kind="photo"),
        services.add_attachment(workorder, SimpleUploadedFile("作業手順.pdf", PDF_BYTES), user, kind="procedure"),
    ]
    for url, attachment in zip(
        [reverse("orders:order_detail", args=[order.pk]), reverse("workorders:workorder_detail", args=[workorder.pk])],
        attachments,
        strict=True,
    ):
        html = html_of(client_logged_in.get(url))
        assert attachment.original_name in html
        assert "/media/" not in html
        assert attachment.file.name not in html
        assert attachment.file.name.rsplit("/", 1)[-1] not in html


@pytest.mark.django_db
def test_add_errors_are_red(client_logged_in, order, user):
    add_url = reverse("attachments:attachment_add")

    response = client_logged_in.post(
        add_url,
        {"content_type": ct_of(order), "object_id": order.pk, "file": SimpleUploadedFile("tool.exe", b"x"), "kind": "other"},
    )
    html = html_of(response)
    assert response.status_code == 200
    assert 'data-notice="stop"' in html
    assert "ファイルの形式が違います。" in html

    for content_type, object_id in [(ct_of(order), 9999), (ct_of(user), user.pk)]:
        response = client_logged_in.post(
            add_url,
            {
                "content_type": content_type,
                "object_id": object_id,
                "file": SimpleUploadedFile("a.png", b"png"),
                "kind": "other",
            },
        )
        html = html_of(response)
        assert response.status_code == 200
        assert 'data-notice="stop"' in html
        assert "付ける先の記録が見つかりません" in html

    assert Attachment.objects.count() == 0


@pytest.mark.django_db
def test_delete_confirm_then_delete(client_logged_in, order, user):
    attachment = services.add_attachment(order, SimpleUploadedFile("受注の写真.png", b"png"), user, kind="photo")
    storage, name = attachment.file.storage, attachment.file.name
    delete_url = reverse("attachments:attachment_delete", args=[attachment.pk])

    response = client_logged_in.get(delete_url)
    html = html_of(response)
    assert response.status_code == 200
    assert "受注の写真.png" in html
    assert str(order) in html
    assert "削除したファイルは元に戻せません。" in html
    assert "btn btn-error" in html
    assert 'name="csrfmiddlewaretoken"' in html
    assert reverse("attachments:attachment_detail", args=[attachment.pk]) in html

    response = client_logged_in.post(delete_url)
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:order_detail", args=[order.pk])
    assert Attachment.objects.count() == 0
    assert not storage.exists(name)


@pytest.mark.django_db
def test_list_menu_and_row_link(client_logged_in, order, user):
    attachment = services.add_attachment(order, SimpleUploadedFile("受注の写真.png", b"png"), user, kind="photo")
    list_url = reverse("attachments:attachment_list")

    response = client_logged_in.get(list_url)
    html = html_of(response)
    assert response.status_code == 200
    assert "受注の写真.png" in html
    assert str(order) in html
    assert "新規登録" not in html

    # 一覧は左のメニューに出さず、設定の画面 (普段は触らない物) から開く (11-03 の本人の指摘)。
    home = html_of(client_logged_in.get(reverse("core:home")))
    assert list_url not in home
    assert list_url in html_of(client_logged_in.get(reverse("core:settings")))

    assert client_logged_in.get(list_url, {"sort": "target_label"}).status_code == 200

    response = client_logged_in.get(reverse("attachments:attachment_detail", args=[attachment.pk]))
    assert response.status_code == 302
    assert response["Location"] == reverse("orders:order_detail", args=[order.pk])


@pytest.mark.django_db
def test_detail_of_removed_target_goes_to_list(client_logged_in, order, user):
    attachment = services.add_attachment(order, SimpleUploadedFile("a.png", b"png"), user, kind="photo")
    Attachment.objects.filter(pk=attachment.pk).update(object_id=9999)

    response = client_logged_in.get(reverse("attachments:attachment_detail", args=[attachment.pk]))
    assert response.status_code == 302
    assert response["Location"] == reverse("attachments:attachment_list")
    assert "(削除された記録)" in html_of(client_logged_in.get(reverse("attachments:attachment_list")))


# --- 添付ファイルを付けるの頭の 1 行 (初見テスト R5・候補 C66 の 2) ---


@pytest.mark.django_db
def test_add_form_shows_target_line_above_fields(client_logged_in, order, item):
    """開いたとき、入力の上に「付ける先: <種類の名前> <記録の字>」の 1 行が出る (種類の欄より前)。

    トップの帯のログアウトの form が先にあるので、<form method="post" の字では比べない。
    """
    add_url = reverse("attachments:attachment_add")
    for target, text in [(order, f"付ける先: 受注 {order.order_no}</p>"), (item, "付ける先: 部品 T-100 作り物の軸</p>")]:
        response = client_logged_in.get(add_url, {"content_type": ct_of(target), "object_id": target.pk})

        assert response.status_code == 200
        html = html_of(response)
        assert text in html
        assert html.index(text) < html.index('name="kind"')
        assert "attachments/attachment_form.html" in [t.name for t in response.templates]


@pytest.mark.django_db
def test_add_form_keeps_target_line_when_stopped(client_logged_in, order):
    """ファイルを付けずに保存して止まった画面でも、送られた値から引いた 1 行が出ている。誤りの字は見ない (誤りの id で見る)。"""
    response = client_logged_in.post(
        reverse("attachments:attachment_add"),
        {"content_type": ct_of(order), "object_id": order.pk, "kind": "other"},
    )

    assert response.status_code == 200
    html = html_of(response)
    assert 'id="id_file_error"' in html
    assert f"付ける先: 受注 {order.order_no}" in html
    assert Attachment.objects.count() == 0


@pytest.mark.django_db
def test_add_form_has_no_target_line_when_not_found(client_logged_in, order, user):
    """付ける先が引けないとき (無い番号・付けられない種類・数でない字) は、画面は開き、1 行は出ない。"""
    add_url = reverse("attachments:attachment_add")
    for content_type, object_id in [(ct_of(order), 9999), (ct_of(user), user.pk), ("abc", order.pk)]:
        response = client_logged_in.get(add_url, {"content_type": content_type, "object_id": object_id})

        assert response.status_code == 200
        assert "付ける先: " not in html_of(response)


@pytest.mark.django_db
def test_add_form_in_modal_has_no_target_line(client_logged_in, order):
    """詳細の枠から小窓で開くときは、今どおり土台の小窓の中身で、1 行は出ない (後ろに詳細が見えるため)。"""
    response = client_logged_in.get(
        reverse("attachments:attachment_add"),
        {"content_type": ct_of(order), "object_id": order.pk},
        HTTP_HX_REQUEST="true",
        HTTP_HX_TARGET="seizou-modal-box",
    )

    assert response.status_code == 200
    html = html_of(response)
    assert "seizou-modal-wide" in html
    assert "付ける先: " not in html
