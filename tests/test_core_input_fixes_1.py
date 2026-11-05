"""初見テストと精査 A8 の直し 1 (N134。精査 A8 の 3 節の 1〜3 と、本人 11-04 22:1x のノート PC の幅)。名前は作り物だけ。

1. 入力の小窓 (#seizou-modal-box) の中の「詳細を見る」は、小窓を差し替えず新しいタブで詳細の画面を開く
   (core/static/js/seizou-select.js)。出荷の新規登録の小窓は、受注の明細を選び直したときに「残り n」を写す JS を読む
   (shipping/_form_modal.html)。
2. 受注の一覧の htmx の返事 (行がある時) に「絞りを外す」の oob (#list-filter-clear) がある (orders/_rows.html)。
   注文残の盤の行の部分 (orders/_board_rows.html) にも同じ行がある (盤の絞りの宣言が入ると出る)。
3. ダッシュボードの「はじめての方へ」の 2 つの流れは、2xl の幅 (1536 以上) から 2 列。それより狭い幅は縦に並ぶ。
"""

import re
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from django.urls import reverse

from seizou.orders import services
from seizou.shipping import services as shipping_services
from tests.test_orders_views import customer, logged_in, shaft, user  # noqa: F401 - fixture を借りる

SRC = Path(__file__).resolve().parents[1] / "src" / "seizou"
MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
ORDER_DATE = date(2026, 11, 2)
FILTER_CLEAR_OOB = '<div id="list-filter-clear" hx-swap-oob="true">'
FILTER_CLEAR_LINE = '{% if filters %}{% include "core/_filter_clear.html" with oob=True %}{% endif %}'


def _order(partner, item, user, qty="10"):  # noqa: F811
    return services.create_order(
        partner, [{"item": item, "qty": Decimal(qty), "unit_price": Decimal("100")}], user, order_date=ORDER_DATE
    )


def _read(relative: str) -> str:
    return (SRC / relative).read_text(encoding="utf-8")


# ---------------------------------------------------------------- 1. 小窓の中の「詳細を見る」と「残り n」


def test_select_link_inside_modal_opens_a_new_tab():
    js = _read("core/static/js/seizou-select.js")
    body = js[js.index("var openModal = function") : js.index("var syncLink = function")]
    assert body.count('link.closest("#seizou-modal-box")') == 1
    assert body.count('window.open(link.href, "_blank", "noopener")') == 1
    # 新しいタブの分かれ道は、小窓に読む htmx の要求より前 (小窓の外は今までどおり小窓)
    assert body.index("window.open(") < body.index(".ajax(")
    assert body.count('target: "#seizou-modal-box"') == 1


@pytest.mark.django_db
def test_shipment_create_modal_reads_the_remaining_js(logged_in, customer, shaft, user):  # noqa: F811
    line = _order(customer, shaft, user).lines.get()
    url = reverse("shipping:shipment_create") + f"?order_line={line.pk}"

    modal = logged_in.get(url, **MODAL)
    assert modal.status_code == 200
    body = modal.content.decode()
    assert "<html" not in body
    assert 'id="id_qty_helptext"' in body
    assert body.count("shipping/shipment-form.js") == 1

    # 画面の全体は今までどおり 1 回 (shipping/shipment_form.html の block content)
    assert logged_in.get(url).content.decode().count("shipping/shipment-form.js") == 1
    # 一括出荷の小窓は読まない (明細の表のカードに受注の明細の選ぶ欄が無い)
    bulk = logged_in.get(reverse("shipping:shipment_bulk_create"), {"line": line.pk}, **MODAL).content.decode()
    assert "shipping/shipment-form.js" not in bulk


# ---------------------------------------------------------------- 2. 受注の一覧の「絞りを外す」の oob


@pytest.mark.django_db
def test_order_list_partial_has_the_filter_clear_oob(logged_in, customer, shaft, user):  # noqa: F811
    _order(customer, shaft, user)
    done = _order(customer, shaft, user, qty="2")
    shipping_services.ship(done.lines.get(), Decimal("2"), date(2026, 11, 5), user)
    url = reverse("orders:order_list")

    chosen = logged_in.get(url, {"f_status": "shipped"}, HTTP_HX_REQUEST="true").content.decode()
    assert done.order_no in chosen
    assert chosen.count(FILTER_CLEAR_OOB) == 1
    assert "絞りを外す" in chosen
    # 絞りの値が無い時も箱は返す (中は空 = 前に出ていた「絞りを外す」を消す)
    plain = logged_in.get(url, HTTP_HX_REQUEST="true").content.decode()
    assert plain.count(FILTER_CLEAR_OOB) == 1
    assert "絞りを外す" not in plain
    # 画面の全体では oob にしない (core/list.html が絞り込みの枠の下に置く 1 つだけ。N161)
    whole = logged_in.get(url, {"f_status": "shipped"}).content.decode()
    assert FILTER_CLEAR_OOB not in whole
    assert whole.count('id="list-filter-clear"') == 1


def test_board_rows_have_the_same_filter_clear_line():
    for name in ("orders/templates/orders/_rows.html", "orders/templates/orders/_board_rows.html"):
        assert _read(name).count(FILTER_CLEAR_LINE) == 1, name
    assert _read("core/templates/core/_rows.html").count(FILTER_CLEAR_LINE) == 1


# ---------------------------------------------------------------- 3. 流れの段の格子


@pytest.mark.django_db
def test_flow_guide_goes_two_columns_from_2xl(logged_in):  # noqa: F811
    body = logged_in.get(reverse("core:home")).content.decode()
    section = re.search(r'<section[^>]*aria-labelledby="flow-guide-title".*?</section>', body, re.S).group(0)
    assert '<div class="mt-4 grid gap-6 2xl:grid-cols-2">' in section
    assert "lg:grid-cols-2" not in section
    # 折り返しの形 (矢印を前の段の li に入れる。120 回目) はそのまま
    assert section.count('<ol class="mt-2 flex flex-wrap items-center gap-2"') == 2
