"""2 社の在庫と受入の種 (stock.demo_seed。core の demo.load の PART_SEEDS が呼ぶ)。

例の種 (examples/companies/<id>/) を demo.load で入れ、2 回目は飛ばすこと、在庫がマイナスにならないこと、
種の指す受注・指示書・取引先・品目が他の種にあることを見る。HTTP は呼ばない (呼んだら落とす)。
"""

import csv
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from seizou.connect.models import Outbox
from seizou.core import demo
from seizou.core import fields as company_fields
from seizou.masters.models import Item
from seizou.purchasing.models import PurchaseOrder, Receipt
from seizou.stock import demo_seed, services
from seizou.stock.models import StockMove

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
#: 種の在庫の動き・発注・受入の件数 (A 社 = 完成 3・払い出し 1・発注 1・受入 2、B 社 = 完成 3・払い出し 1)
EXPECTED = {"a_machining": 7, "b_plating": 4}


@pytest.fixture
def no_registration(monkeypatch):
    """試験用の settings には会社ごとの項目の登録が無い。ほかの試験の登録が残っていても空にする。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


@pytest.fixture
def no_http(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("種の読み込みで HTTP を呼んだ")

    monkeypatch.setattr("requests.post", fail)


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_examples_load_stock_seeds_twice(company_id, no_registration, media, no_http, capsys):
    made = EXPECTED[company_id]

    summary = demo.load(COMPANIES_DIR / company_id)

    assert summary["在庫"] == {"入れた": made, "飛ばした": 0}
    assert f"在庫: 入れた {made} 件・飛ばした 0 件" in capsys.readouterr().out
    assert len(summary["注意"]) == 1  # 例の種では「注意」の文を増やさない
    if company_id == "a_machining":
        order = PurchaseOrder.objects.get()
        assert order.status == PurchaseOrder.STATUS_DRAFT  # 2 行目が一部なので「納品された」にならない
        assert Outbox.objects.count() == 0  # 相手に送っていない
        assert Receipt.objects.count() == 2
    else:
        assert PurchaseOrder.objects.count() == 0
    moves = StockMove.objects.count()

    again = demo.load(COMPANIES_DIR / company_id)

    assert again["在庫"] == {"入れた": 0, "飛ばした": made}
    assert StockMove.objects.count() == moves
    assert Receipt.objects.count() == (2 if company_id == "a_machining" else 0)


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_seed_balances_are_not_negative(company_id, no_registration, media, no_http):
    demo.load(COMPANIES_DIR / company_id)

    values = services.balances(Item.objects.values_list("pk", flat=True))
    assert all(value is None or value >= 0 for value in values.values())

    by_part_no = {item.part_no: values[item.pk] for item in Item.objects.all()}
    if company_id == "a_machining":
        assert by_part_no["WK-1030"] == Decimal("300")
        assert by_part_no["WK-1029"] == Decimal("190")
        assert by_part_no["WK-1014"] == Decimal("500")
        assert by_part_no["WK-1001"] == Decimal("200")
        assert by_part_no["WK-1004"] == Decimal("40")
    else:
        assert by_part_no["SB-2009"] == Decimal("500")
        assert by_part_no["SB-2015"] == Decimal("10")
        assert by_part_no["SB-2013"] == Decimal("45")
    assert len([value for value in values.values() if value is not None]) == (5 if company_id == "a_machining" else 3)


@pytest.mark.django_db
def test_seed_without_files_is_silent(tmp_path):
    assert demo_seed.load(tmp_path, None) == {"入れた": 0, "飛ばした": 0}
    assert StockMove.objects.count() == 0

    # 在庫の動きの種は取引先と受注と指示書の種を指すので、それらの無い置き場では ValueError で止まる (黙って飛ばさない)
    (tmp_path / "stock_moves.yaml").write_text(
        (COMPANIES_DIR / "a_machining" / "stock_moves.yaml").read_text(encoding="utf-8"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="stock_moves.yaml の 1 件目"):
        demo_seed.load(tmp_path, None)
    assert StockMove.objects.count() == 0


def _yaml(path, root):
    return yaml.safe_load(path.read_text(encoding="utf-8"))[root]


def _csv(path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_seed_references_exist(company_id):
    folder = COMPANIES_DIR / company_id
    order_lines = {
        (order["得意先の取引先コード"], order["得意先の注文番号"], number): line["自社品番"]
        for order in _yaml(folder / "orders.yaml", "受注")
        for number, line in enumerate(order["明細"], start=1)
    }
    workorder_lines = {
        (entry["得意先の取引先コード"], entry["得意先の注文番号"], entry["行番号"])
        for entry in _yaml(folder / "workorders.yaml", "指示書")
    }
    part_nos = {row["自社品番"] for row in _csv(folder / "items.csv")}

    for entry in _yaml(folder / "stock_moves.yaml", "在庫の動き"):
        if entry["種類"] == "製造の完了":
            key = (entry["得意先の取引先コード"], entry["得意先の注文番号"], entry["行番号"])
            assert key in order_lines, key
            assert key in workorder_lines, key
        else:
            assert entry["種類"] == "払い出し"
            assert entry["自社品番"] in part_nos

    receipts = folder / "receipts.yaml"
    if company_id == "b_plating":
        assert not receipts.exists()
        return
    subcontractors = {row["取引先コード"] for row in _csv(folder / "partners.csv") if row["取引先の種類"] == "協力企業"}
    for order in _yaml(receipts, "発注"):
        assert order["取引先コード"] in subcontractors
        for line in order["明細"]:
            assert line["自社品番"] in part_nos
            assert line["自社品番"] not in order_lines.values()  # 受注の種に無い部品
        for receipt in order["受入"]:
            assert 1 <= receipt["行番号"] <= len(order["明細"])
