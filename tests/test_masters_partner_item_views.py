"""部品の取引先の画面 (C98 の その 1)。部品の詳細の「取引先」のタブと取引先の詳細の「部品」のタブから、足す・直す・消す。

小窓の形は htmx の見出し (HX-Request と HX-Target = seizou-modal-box) で開く。保存と削除は 204 と HX-Trigger の seizou:saved。
画面の全体の保存は移る (保存した物の詳細 = partneritem_detail → 部品の詳細。削除は ?next=)。
"""

import json

import pytest
from django.urls import reverse

from seizou.masters import services
from seizou.masters.models import Item, Partner, PartnerItem

MODAL = {"HTTP_HX_REQUEST": "true", "HTTP_HX_TARGET": "seizou-modal-box"}
WRITE_ON = {"access_control": {"department": "off", "write": "on", "money": "off"}}
#: 空の部品のタブの、足す入口の 1 行 (部品の詳細)
ADD_HINT = "「取引先を足す」で、得意先や協力企業を何社でも足せます。"
#: 空の標準の工程のタブの意味の 1 行 (C19)
PROCESSES_MEANING = "指示書の発行で工程を選ばないときに写す、この部品の工程の並びです。"


@pytest.fixture
def item(db):
    return Item.objects.create(part_no="T-100", name="作り物の軸", unit="個")


@pytest.fixture
def partner(db):
    return Partner.objects.create(code="C001", name="作り物の得意先", short_name="作り得", role="customer")


@pytest.fixture
def supplier(db):
    return Partner.objects.create(code="S001", name="作り物の協力企業", role="subcontractor")


def _saved_message(response):
    assert response.status_code == 204
    return json.loads(response["HX-Trigger"])["seizou:saved"]["message"]


def _post_add(client, partner, item, part_no="", name="", **extra):
    data = {"item": item.pk, "partner": partner.pk, "partner_part_no": part_no, "partner_item_name": name}
    return client.post(reverse("masters:partneritem_add"), data, **extra)


def test_item_detail_partner_tab(client_logged_in, user, item, partner, supplier):
    # (g) タブの名前は「取引先」。「取引先を足す」は小窓で開く。品番の空の行は「—」。行に「直す」と「⋮」の赤の「削除」
    services.map_partner_item(partner, item, "X-1", user)
    blank = services.add_partner_item({"partner": supplier, "item": item, "partner_part_no": "", "partner_item_name": ""}, user)
    html = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()

    assert 'aria-label="取引先"' in html
    add = f'{reverse("masters:partneritem_add")}?item={item.pk}&amp;next='
    assert f'href="{add}' in html and f'data-modal-form="{add}' in html
    assert '<td class="whitespace-nowrap font-mono"><span class="text-base-content/50">—</span></td>' in html
    assert f'{reverse("masters:partneritem_update", args=[blank.pk])}?next=' in html
    assert 'aria-label="行の操作"' in html
    delete = reverse("masters:partneritem_delete", args=[blank.pk])
    assert f'<a class="flex items-center gap-2 text-error" href="{delete}?next=' in html
    assert 'id="item-partners"' in html and 'id="item-partners-refresh"' in html


def test_partner_detail_items_tab(client_logged_in, user, item, partner):
    # (h) 取引先の詳細の側は「部品」のタブ。「部品を足す」は ?partner=<pk>。画面の全体の保存は部品の詳細へ移る
    services.map_partner_item(partner, item, "X-1", user)
    html = client_logged_in.get(reverse("masters:partner_detail", args=[partner.pk])).content.decode()
    assert 'aria-label="部品"' in html
    assert f'{reverse("masters:partneritem_add")}?partner={partner.pk}&amp;next=' in html
    assert 'id="partner-items"' in html and 'id="partner-items-refresh"' in html

    other = Item.objects.create(part_no="T-200", name="作り物の板")
    next_url = reverse("masters:partner_detail", args=[partner.pk])
    response = client_logged_in.post(
        f'{reverse("masters:partneritem_add")}?next={next_url}',
        {"item": other.pk, "partner": partner.pk, "partner_part_no": "", "partner_item_name": "", "next": next_url},
    )
    row = PartnerItem.objects.get(item=other)
    assert response.status_code == 302
    assert response.url == reverse("masters:partneritem_detail", args=[row.pk])
    assert client_logged_in.get(response.url).url == reverse("masters:item_detail", args=[other.pk])


def test_add_screen_and_saves(client_logged_in, user, item, partner, supplier):
    # (i) ?item=<pk> で開くと部品が選んである。画面の全体の保存は行を作って部品の詳細へ (partneritem_detail を通る)
    html = client_logged_in.get(f'{reverse("masters:partneritem_add")}?item={item.pk}').content.decode()
    assert f'<option value="{item.pk}" selected>' in html

    response = _post_add(client_logged_in, supplier, item)
    row = PartnerItem.objects.get(partner=supplier)
    assert row.partner_part_no == ""
    assert response.url == reverse("masters:partneritem_detail", args=[row.pk])

    # 小窓の保存は 204 と seizou:saved
    message = _saved_message(_post_add(client_logged_in, partner, item, part_no="X-1", **MODAL))
    assert message == "保存しました。"

    # 同じ取引先と部品を品番の空のままもう一度 → 行は増えず、緑の知らせの字 (表の制約の赤にならない)
    message = _saved_message(_post_add(client_logged_in, supplier, item, **MODAL))
    assert message == "この取引先は、もうこの部品に登録してあります。"
    assert PartnerItem.objects.filter(partner=supplier, item=item).count() == 1

    # 同じ取引先のほかの部品の行にある品番 → services の字の赤 (Django の既定の字でない)
    other = Item.objects.create(part_no="T-200", name="作り物の板")
    response = _post_add(client_logged_in, partner, other, part_no="X-1", **MODAL)
    body = response.content.decode()
    assert response.status_code == 200
    # 赤の帯は頭の「保存できません。」を太字で分けて出す
    assert "保存できません。" in body
    assert "取引先 作り物の得意先 の品番 X-1 は、既に T-100 作り物の軸 に対応しています。" in body
    assert "制約" not in body
    assert not PartnerItem.objects.filter(item=other).exists()


def test_update_screen(client_logged_in, user, item, partner):
    # (j) 品番と品名を直す。品番の空の行がもうある部品の、ほかの行の品番を空にする保存は小窓の赤い帯で止まる
    first = services.map_partner_item(partner, item, "X-1", user)
    second = services.map_partner_item(partner, item, "X-2", user)
    url = reverse("masters:partneritem_update", args=[first.pk])
    html = client_logged_in.get(url, **MODAL).content.decode()
    assert "T-100 作り物の軸" in html and "作り物の得意先" in html

    message = _saved_message(client_logged_in.post(url, {"partner_part_no": "", "partner_item_name": "相手の軸"}, **MODAL))
    assert message == "保存しました。"
    first.refresh_from_db()
    assert (first.partner_part_no, first.partner_item_name) == ("", "相手の軸")

    response = client_logged_in.post(
        reverse("masters:partneritem_update", args=[second.pk]), {"partner_part_no": "", "partner_item_name": ""}, **MODAL
    )
    assert response.status_code == 200
    assert "この取引先には、品番が空の行がもうあります。単価はその行に足してください。" in response.content.decode()
    second.refresh_from_db()
    assert second.partner_part_no == "X-2"


def test_delete_screen(client_logged_in, user, item, partner):
    # (k) 確かめの小窓に赤の「削除」と「消すと戻せません。」と「(削除された記録)」。小窓の削除は 204 の「削除しました。」
    row = services.map_partner_item(partner, item, "X-1", user)
    url = reverse("masters:partneritem_delete", args=[row.pk])
    html = client_logged_in.get(url, **MODAL).content.decode()
    assert 'class="btn btn-error"' in html
    assert "消すと戻せません。" in html and "(削除された記録)" in html
    assert _saved_message(client_logged_in.post(url, {}, **MODAL)) == "削除しました。"
    assert not PartnerItem.objects.filter(pk=row.pk).exists()

    # 画面の全体は ?next= (開いた部品の詳細) へ戻る
    again = services.map_partner_item(partner, item, "X-1", user)
    detail = reverse("masters:item_detail", args=[item.pk])
    page = client_logged_in.get(f'{reverse("masters:partneritem_delete", args=[again.pk])}?next={detail}')
    assert page.status_code == 200 and "消すと戻せません。" in page.content.decode()
    response = client_logged_in.post(f'{reverse("masters:partneritem_delete", args=[again.pk])}?next={detail}', {"next": detail})
    assert response.status_code == 302 and response.url == detail
    assert not PartnerItem.objects.filter(pk=again.pk).exists()


def test_partneritem_detail_redirects_to_item(client_logged_in, user, item, partner):
    # (l) 部品の取引先の行の口は部品の詳細へ移すだけ
    row = services.map_partner_item(partner, item, "X-1", user)
    response = client_logged_in.get(reverse("masters:partneritem_detail", args=[row.pk]))
    assert response.status_code == 302 and response.url == reverse("masters:item_detail", args=[item.pk])
    assert client_logged_in.get(reverse("masters:partneritem_detail", args=[row.pk + 100])).status_code == 404


def test_without_items_permission(client_logged_in, user, item, partner, settings):
    # (m) 部品マスターの編集の権限の無い人には入口が出ず、保存と削除は赤の断り
    settings.SEIZOU = WRITE_ON
    row = services.map_partner_item(partner, item, "X-1", user)
    html = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
    assert "取引先を足す" not in html
    assert reverse("masters:partneritem_update", args=[row.pk]) not in html
    assert reverse("masters:partneritem_delete", args=[row.pk]) not in html

    refusal = "部品マスターの編集の権限がありません。"
    response = _post_add(client_logged_in, partner, item, part_no="X-9", **MODAL)
    assert response.status_code == 200 and refusal in response.content.decode()
    response = client_logged_in.post(
        reverse("masters:partneritem_update", args=[row.pk]), {"partner_part_no": "X-8", "partner_item_name": ""}, **MODAL
    )
    assert response.status_code == 200 and refusal in response.content.decode()
    response = client_logged_in.post(reverse("masters:partneritem_delete", args=[row.pk]), {}, **MODAL)
    assert response.status_code == 200 and refusal in response.content.decode()
    row.refresh_from_db()
    assert row.partner_part_no == "X-1"
    assert PartnerItem.objects.count() == 1


def test_empty_partner_tab(client_logged_in, item):
    # (n) 取引先が 0 件の部品の詳細: 空の字と足す入口の 1 行が 1 回ずつ。標準の工程の意味の 1 行も 1 回のまま
    html = client_logged_in.get(reverse("masters:item_detail", args=[item.pk])).content.decode()
    assert html.count("取引先が入ると、ここに並びます。") == 1
    assert html.count(ADD_HINT) == 1
    assert html.count(PROCESSES_MEANING) == 1
