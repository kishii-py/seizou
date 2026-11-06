"""2 社の入荷の種 (receiving.demo_seed。core の demo.load の PART_SEEDS が受注の後に呼ぶ)。

例の種 (examples/companies/<id>/receivings.yaml) を demo.load で入れ、数・引当・2 回目は飛ばすことを見る。
種の指す受注の明細が受注の種にあることも見る。HTTP は呼ばない (呼んだら落とす)。作り物のデータだけを使う。
"""

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from django.contrib.auth import get_user_model

from seizou.core import demo
from seizou.core import fields as company_fields
from seizou.masters import services as masters_services
from seizou.orders import services as orders_services
from seizou.receiving import demo_seed, services
from seizou.receiving.models import SOURCE_CONSIGNED, SOURCE_FREE, SOURCE_PAID, Allocation, Receiving

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
#: 種の入荷の件数と出所 (A 社 = 無償支給 1・有償支給 1、B 社 = 預かり 3)
EXPECTED = {
    "a_machining": {SOURCE_FREE: 1, SOURCE_PAID: 1},
    "b_plating": {SOURCE_CONSIGNED: 3},
}


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


def _seed(company_id):
    return yaml.safe_load((COMPANIES_DIR / company_id / "receivings.yaml").read_text(encoding="utf-8"))


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_examples_load_receiving_seeds_twice(company_id, no_registration, media, no_http, capsys):
    made = sum(EXPECTED[company_id].values())

    summary = demo.load(COMPANIES_DIR / company_id)

    assert summary["入荷"] == {"入れた": made, "飛ばした": 0}
    assert f"入荷: 入れた {made} 件・飛ばした 0 件" in capsys.readouterr().out
    sources = {}
    for receiving in Receiving.objects.all():
        sources[receiving.source] = sources.get(receiving.source, 0) + 1
    assert sources == EXPECTED[company_id]
    # 入荷は 1 件ずつ受注の明細に引き当て、部品は明細の部品・支給元は受注の得意先
    assert Allocation.objects.count() == made
    for allocation in Allocation.objects.select_related("receiving", "order_line__order"):
        assert allocation.receiving.item_id == allocation.order_line.item_id
        assert allocation.receiving.partner_id == allocation.order_line.order.customer_id
        assert allocation.qty == allocation.receiving.qty

    again = demo.load(COMPANIES_DIR / company_id)

    assert again["入荷"] == {"入れた": 0, "飛ばした": made}
    assert Receiving.objects.count() == made
    assert Allocation.objects.count() == made


@pytest.mark.django_db
def test_b_plating_has_two_full_lines_and_one_partial(no_registration, media, no_http):
    demo.load(COMPANIES_DIR / "b_plating")

    states = sorted(services.receiving_state(allocation.order_line) for allocation in Allocation.objects.all())

    assert states == [services.STATE_FULL, services.STATE_FULL, services.STATE_PARTIAL]


@pytest.mark.django_db
def test_seed_without_file_is_silent(tmp_path):
    assert demo_seed.load_after_orders(tmp_path, None) == {"入れた": 0, "飛ばした": 0}
    assert demo_seed.load(tmp_path, None) == {"入れた": 0, "飛ばした": 0}
    assert Receiving.objects.count() == 0


@pytest.mark.django_db
def test_bad_entry_names_file_and_position(tmp_path):
    user = get_user_model().objects.create_user("tester", password="pass-for-tests-only")
    customer = masters_services.create_partner({"code": "C001", "name": "作り物の得意先", "role": "customer"}, user)
    item = masters_services.create_item({"part_no": "W-100", "name": "作り物のワーク", "unit": "個"}, user)
    orders_services.create_order(
        customer,
        [{"item": item, "qty": Decimal("10"), "unit_price": Decimal("100")}],
        user,
        order_date=date(2026, 11, 2),
        customer_order_no="PO-1",
    )
    entry = {"得意先の取引先コード": "C001", "得意先の注文番号": "PO-1", "行番号": 1, "出所": "預かり", "数量": 5, "入荷日": "2026-11-03"}
    bad = {**entry, "出所": "購入"}
    (tmp_path / "receivings.yaml").write_text(yaml.safe_dump({"入荷": [entry, bad]}, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ValueError, match=r"receivings\.yaml の 2 件目: 出所は"):
        demo_seed.load_after_orders(tmp_path, user)


@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_seed_references_exist(company_id):
    lines = {}
    for order in yaml.safe_load((COMPANIES_DIR / company_id / "orders.yaml").read_text(encoding="utf-8"))["受注"]:
        for line_no, line in enumerate(order["明細"], start=1):
            lines[(order["得意先の取引先コード"], order["得意先の注文番号"], line_no)] = Decimal(str(line["数量"]))
    entries = _seed(company_id)["入荷"]
    assert set(entries[0]) == set(demo_seed.RECEIVING_KEYS)
    for entry in entries:
        key = (entry["得意先の取引先コード"], entry["得意先の注文番号"], entry["行番号"])
        assert key in lines, key
        assert entry["出所"] in demo_seed.SOURCE_WORDS
        assert 0 < Decimal(str(entry["数量"])) <= lines[key], key
