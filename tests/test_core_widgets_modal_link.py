"""字を打って探す欄 (RemoteSelect) の、選んだ物を小窓で開く口の印 (data-modal-url)。

初見テスト 2 回目の E-05: 受注と出荷の入力で、選んだ部品の名前から小窓が開かなかった。
widget が小窓の URL の型を欄に付け、static/js/seizou-select.js が選んだ物の下に「詳細を見る」の link を出す。
"""

from django.urls import reverse

from seizou.core.widgets import RemoteSelect


def test_item_select_carries_modal_url_template():
    html = RemoteSelect("masters:item_select").render("item", None)

    assert f'data-select-url="{reverse("masters:item_select")}"' in html
    assert f'data-modal-url="{reverse("masters:item_modal", args=[0])}"' in html


def test_partner_select_carries_modal_url_template():
    html = RemoteSelect("masters:partner_select").render("customer", None)

    assert f'data-modal-url="{reverse("masters:partner_modal", args=[0])}"' in html


def test_select_without_modal_has_no_template():
    # 受注の明細には小窓が無いので、印を付けない (js は link を出さない)
    html = RemoteSelect("orders:orderline_select").render("line", None)

    assert "data-modal-url" not in html
