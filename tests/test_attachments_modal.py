"""添付ファイルを詳細の小窓で付ける (決め 11-05 11:4x。N157)。

各詳細の「添付ファイル」の枠の「添付ファイルを付ける」は、入力の土台の小窓 (BaseFormView の小窓の形) で開く
(a の data-modal-form。href は同じ URL のまま)。小窓で保存すると、土台が本文の無い 204 に HX-Trigger の seizou:saved を付けて返し、
core の js/seizou-modal.js が body に seizou:refresh を出す。枠の section はその合図で同じ詳細の画面を htmx で取り直し、
#attachments だけを入れ替える (hx-select)。全画面の入力・開く・削除は今のまま。
core は変えずに使うので、小窓で付ける形が頼る core の字 (multipart の印・ドラッグの枠の委譲・開く印・取り直しの合図) も見張る。
断りの文と知らせの字は比べない (知らせは土台の success_message の値で比べる。断りは alert-error と欄の誤りの id と件数で見る)。
"""

import json
import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.contrib.contenttypes.models import ContentType
from django.contrib.messages import get_messages
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse

from seizou.attachments import services
from seizou.attachments.models import Attachment
from seizou.core.views import BaseFormView
from seizou.masters import services as masters_services
from seizou.masters.models import Process
from seizou.orders import services as orders_services
from seizou.workorders import services as workorders_services

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
# 枠の取り直し (section の hx-get。差し替える先は枠の id) を真似る見出し
REFRESH = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "attachments"}
ORDER_DATE = date(2026, 11, 2)
PDF_BYTES = b"%PDF-1.4\n% made for tests\n"
CORE = Path(__file__).resolve().parent.parent / "src" / "seizou" / "core"
REFRESH_ATTRS = (
    'hx-trigger="seizou:refresh from:body"',
    'hx-select="#attachments"',
    'hx-target="#attachments"',
    'hx-swap="outerHTML ignoreTitle:true"',
)


@pytest.fixture(autouse=True)
def media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


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
    """枠の a の href の頭 (テンプレートの字のまま。& は &amp;)。"""
    return f"{reverse('attachments:attachment_add')}?content_type={ct_of(obj)}&amp;object_id={obj.pk}"


def add_path_for(obj) -> str:
    """入力の URL (要求に使う形)。"""
    return f"{reverse('attachments:attachment_add')}?content_type={ct_of(obj)}&object_id={obj.pk}"


def html_of(response) -> str:
    return response.content.decode()


def panel_of(html: str) -> str:
    """詳細の画面の「添付ファイル」の枠 (section の開きから最初の </section> の前まで)。"""
    start = html.index('<section id="attachments"')
    return html[start : html.index("</section>", start)]


def panel_tag_of(html: str) -> str:
    """枠の section の開きの tag。"""
    return re.search(r'<section id="attachments"[^>]*>', html).group(0)


def details(order, item, workorder) -> list[tuple[str, object]]:
    return [
        (reverse("orders:order_detail", args=[order.pk]), order),
        (reverse("masters:item_detail", args=[item.pk]), item),
        (reverse("workorders:workorder_detail", args=[workorder.pk]), workorder),
    ]


def post_data(obj, **extra) -> dict:
    data = {"content_type": ct_of(obj), "object_id": obj.pk, "kind": "inspection", "note": "作り物の備考"}
    data.update(extra)
    return data


@pytest.mark.django_db
def test_panel_opens_add_form_in_modal_on_three_details(client_logged_in, order, item, workorder):
    for url, obj in details(order, item, workorder):
        response = client_logged_in.get(url)
        html = html_of(response)
        assert response.status_code == 200, url
        tag = panel_tag_of(html)
        assert f'hx-get="{url}"' in tag, url
        for attr in REFRESH_ATTRS:
            assert attr in tag, (url, attr)
        section = panel_of(html)
        pairs = re.findall(r'href="([^"]*)" data-modal-form="([^"]*)"', section)
        assert len(pairs) == 1, url
        href, modal = pairs[0]
        assert href == modal, url
        assert modal.startswith(add_url_for(obj)), url
        assert "添付ファイルを付ける" in section, url


@pytest.mark.django_db
def test_panel_rows_keep_plain_links(client_logged_in, order, user):
    attachment = services.add_attachment(order, SimpleUploadedFile("作り物の写真.png", b"png"), user, kind="photo")
    section = panel_of(html_of(client_logged_in.get(reverse("orders:order_detail", args=[order.pk]))))

    assert section.count("data-modal-form=") == 1  # 付けるだけ
    for name in ("attachments:attachment_delete", "attachments:attachment_download"):
        found = re.search(rf'<a [^>]*href="{re.escape(reverse(name, args=[attachment.pk]))}"[^>]*>', section)
        assert found is not None, name
        assert "data-modal-form" not in found.group(0), name


@pytest.mark.django_db
def test_modal_get_is_fragment_with_file_drop(client_logged_in, order):
    response = client_logged_in.get(add_path_for(order), **MODAL)
    body = html_of(response)
    assert response.status_code == 200
    assert "<html" not in body
    assert 'class="seizou-modal-wide"' in body
    assert "添付ファイルを付ける" in body
    assert f'hx-post="{reverse("attachments:attachment_add")}?content_type=' in body
    assert 'hx-target="#seizou-modal-box"' in body
    assert 'hx-encoding="multipart/form-data"' in body
    assert 'enctype="multipart/form-data"' in body
    assert "data-file-field" in body
    assert "data-file-drop" in body
    assert 'name="file"' in body
    assert re.search(rf'<input type="hidden" name="content_type" value="{ct_of(order)}"', body)
    assert re.search(rf'<input type="hidden" name="object_id" value="{order.pk}"', body)
    assert "キャンセル" not in body
    assert "HX-Target" in response["Vary"]


@pytest.mark.django_db
def test_modal_post_saves_and_asks_refresh(client_logged_in, order):
    response = client_logged_in.post(
        add_path_for(order),
        post_data(order, file=SimpleUploadedFile("作り物の検査成績書.pdf", PDF_BYTES)),
        **MODAL,
    )
    assert response.status_code == 204
    assert response.content == b""
    assert json.loads(response["HX-Trigger"]) == {
        "seizou:saved": {"message": BaseFormView.success_message, "level": "success"}
    }
    attachment = Attachment.objects.get()
    assert attachment.target == order
    assert attachment.kind == "inspection"
    assert attachment.note == "作り物の備考"
    assert attachment.original_name == "作り物の検査成績書.pdf"
    assert list(get_messages(response.wsgi_request)) == []


@pytest.mark.django_db
def test_modal_post_without_file_stays_in_modal(client_logged_in, order):
    response = client_logged_in.post(add_path_for(order), post_data(order), **MODAL)
    body = html_of(response)
    assert response.status_code == 200
    assert "<html" not in body
    assert 'id="id_file_error"' in body
    assert "hx-post" in body
    assert Attachment.objects.count() == 0


@pytest.mark.django_db
def test_modal_post_refused_stays_in_modal_red(client_logged_in, order):
    bad_extension = post_data(order, file=SimpleUploadedFile("tool.exe", b"x"), kind="other")
    no_target = post_data(order, file=SimpleUploadedFile("a.png", b"png"), kind="other", object_id=9999)
    for data in (bad_extension, no_target):
        response = client_logged_in.post(add_path_for(order), data, **MODAL)
        body = html_of(response)
        assert response.status_code == 200
        assert "<html" not in body
        assert "alert-error" in body
        assert "hx-post" in body
    assert Attachment.objects.count() == 0


@pytest.mark.django_db
def test_refresh_get_returns_panel_with_new_file(client_logged_in, order, item, workorder):
    for url, obj in details(order, item, workorder):
        name = f"作り物の{obj._meta.model_name}.pdf"
        saved = client_logged_in.post(
            add_path_for(obj), post_data(obj, file=SimpleUploadedFile(name, PDF_BYTES)), **MODAL
        )
        assert saved.status_code == 204, url

        response = client_logged_in.get(url, **REFRESH)
        html = html_of(response)
        assert response.status_code == 200, url
        assert html.count('id="attachments"') == 1, url
        section = panel_of(html)
        assert name in section, url
        assert '<span class="badge badge-sm tabular-nums">1</span>' in section, url
        # 入れ替えた新しい枠も、次の保存の合図で取り直す
        assert 'hx-trigger="seizou:refresh from:body"' in panel_tag_of(html), url


@pytest.mark.django_db
def test_full_page_add_is_unchanged(client_logged_in, order):
    response = client_logged_in.get(add_path_for(order))
    body = html_of(response)
    assert response.status_code == 200
    assert "<html" in body
    assert "キャンセル" in body
    assert 'enctype="multipart/form-data"' in body
    assert 'hx-encoding="multipart/form-data"' not in body

    response = client_logged_in.post(
        add_path_for(order), post_data(order, file=SimpleUploadedFile("作り物の検査成績書.pdf", PDF_BYTES))
    )
    attachment = Attachment.objects.get()
    assert response.status_code == 302
    assert response["Location"] == reverse("attachments:attachment_detail", args=[attachment.pk])


def test_core_parts_the_panel_relies_on():
    drop = (CORE / "static" / "js" / "seizou-file-drop.js").read_text(encoding="utf-8")
    assert 'document.addEventListener("drop", onDrop);' in drop
    assert 'document.addEventListener("change", onChange);' in drop

    modal = (CORE / "static" / "js" / "seizou-modal.js").read_text(encoding="utf-8")
    assert 'var OPENER = "[data-modal-form]";' in modal
    assert 'new CustomEvent("seizou:refresh")' in modal

    card = (CORE / "templates" / "core" / "_form_card.html").read_text(encoding="utf-8")
    assert '{% if form.is_multipart %} hx-encoding="multipart/form-data"{% endif %}' in card
