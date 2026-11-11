"""詳細と入力の画面の明細の表が、幅 1280 の画面 (左のメニューを開いた形) で横にはみ出さないための形を固める。

本体が 2026-11-11 に、手元の一時の置き場をブラウザ (幅 1280) で開いて確かめた。直す前は、受注の詳細の明細の表が枠 924px に対して
中身 1072px で、右端の「残」の列と「完了」の印が枠の外に隠れた。発注の詳細の明細の表と、請求書の新規登録の出荷の表も右端が切れた。
直した形: 表の枡の左右の余白を詰める (`[&_td]:px-2 [&_th]:px-2`)・部品の名前は表が枠に収まらない時だけ折り返す (品番は折らない)・
「完了」の印は残の数の下の行に置く・単価と金額は折らない。幅そのものは試験で測れないので、ここでは HTML の形だけを見る。
"""

from pathlib import Path

from seizou.core.templatetags.seizou_icons import menu_icon_name

SRC = Path(__file__).resolve().parents[1] / "src" / "seizou"
NARROW = "[&_td]:px-2 [&_th]:px-2"


def _read(path):
    return (SRC / path).read_text(encoding="utf-8")


def test_order_detail_lines_table_fits_narrow_screen():
    html = _read("orders/templates/orders/order_detail.html")
    assert f'<table class="table table-sm table-zebra {NARROW}">' in html
    # 部品の枡は折り返せる (品番だけ折らない)
    assert '<td><span class="whitespace-nowrap font-mono">{% master_link line.item line.item.part_no %}</span>' in html
    # 「完了」の印は、残の数の下の行
    assert '<span class="mt-1 block"><span class="badge badge-soft badge-ghost">{{ completed_label }}</span></span>' in html


def test_purchase_order_detail_lines_table_fits_narrow_screen():
    html = _read("purchasing/templates/purchasing/purchaseorder_detail.html")
    assert f'<table class="table table-sm {NARROW}">' in html
    assert "<td>{% master_link line.item %}" in html
    assert '<td class="whitespace-nowrap text-right tabular-nums">{{ line.unit_price|yen }}' in html
    assert '<span class="mt-1 block"><span class="badge badge-soft badge-ghost">{{ completed_label }}</span></span>' in html


def test_invoice_create_shipments_table_fits_narrow_screen():
    html = _read("billing/templates/billing/_invoice_create_card.html")
    assert f'class="table table-sm {NARROW}">' in html
    assert "<td>{{ row.shipment.order_line.item }}</td>" in html


def test_staff_menu_uses_person_icon():
    """左のメニューの「社員」は人の絵 (取引先は複数の人の絵)。"""
    assert menu_icon_name("masters:staff_list", "masters") == "user"
    assert menu_icon_name("masters:partner_list", "masters") == "users"
