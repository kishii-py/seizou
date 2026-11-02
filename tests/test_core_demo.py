"""作り物のデータの読み込み (seizou.core.demo.load)。

1 段目は tmp_path に書いた小さな種で、services を通して入ることを確かめる。
"""

import csv
import os
from decimal import Decimal
from pathlib import Path

import pytest
import yaml
from django.contrib.auth import get_user_model

from seizou import cli
from seizou.connect.models import Connection
from seizou.core import demo
from seizou.core import fields as company_fields
from seizou.masters.models import Item, Partner, PartnerItem, Process
from seizou.orders.models import Order

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"

ITEM_HEADER = ["自社品番", "品名", "図面番号", "図面ファイル", "単位", "標準単価", "備考"]
PARTNER_HEADER = ["取引先コード", "名前", "略称", "区分", "締日", "支払日", "小計の計算方法", "消費税の計算方法", "備考"]
PARTNER_ITEM_HEADER = ["取引先コード", "自社品番", "取引先の品番", "取引先の品名"]

ITEM_ROWS = [
    ["A-100", "作り物の軸", "", "", "個", "120", ""],
    ["A-200", "作り物の板", "", "", "枚", "80.50", ""],
]
PARTNER_ROWS = [
    ["C001", "作り物の得意先", "得意", "得意先", "25", "末", "四捨五入", "トータル", ""],
    ["S001", "作り物の外注先", "", "外注先", "", "", "", "", ""],
]
PARTNER_ITEM_ROWS = [["C001", "A-100", "X-1", "相手の軸"]]
PROCESSES = [{"工程コード": "P10", "名前": "旋削", "外注か": False, "並び順": 10}]
ORDERS = [
    {
        "得意先の取引先コード": "C001",
        "受注日": "2026-10-05",
        "得意先の注文番号": "PO-1001",
        "備考": "",
        "明細": [
            # 単位を空にすると、品目の単位が写る
            {"自社品番": "A-100", "数量": 10, "単位": "", "単価": 120, "納期": "2026-10-20", "備考": ""},
            {"自社品番": "A-200", "数量": 3, "単位": "枚", "単価": 80.5, "納期": "2026-10-25", "備考": "急ぎ"},
        ],
    }
]
CONNECTIONS = [
    {
        "取引先コード": "S001",
        "受け口の URL": "http://127.0.0.1:8002/connect/receive/order/",
        "鍵の置き場の名前": "SEIZOU_PARTNER_B_PLATING_SECRET",
        "向き": "送る",
    }
]


def write_csv(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def write_yaml(path, key, entries):
    path.write_text(yaml.safe_dump({key: entries}, allow_unicode=True, sort_keys=False), encoding="utf-8")


def write_seeds(folder, *, item_header=None, item_rows=None, orders=None):
    folder.mkdir(parents=True, exist_ok=True)
    write_csv(folder / "items.csv", item_header or ITEM_HEADER, item_rows or ITEM_ROWS)
    write_csv(folder / "partners.csv", PARTNER_HEADER, PARTNER_ROWS)
    write_csv(folder / "partner_items.csv", PARTNER_ITEM_HEADER, PARTNER_ITEM_ROWS)
    write_yaml(folder / "processes.yaml", "工程", PROCESSES)
    write_yaml(folder / "orders.yaml", "受注", orders or ORDERS)
    write_yaml(folder / "connections.yaml", "つながり", CONNECTIONS)
    return folder


def counts():
    return {
        "品目": Item.objects.count(),
        "取引先": Partner.objects.count(),
        "品番の対応": PartnerItem.objects.count(),
        "工程": Process.objects.count(),
        "受注": Order.objects.count(),
        "つながり": Connection.objects.count(),
    }


@pytest.fixture
def no_registration(monkeypatch):
    """試験用の settings には会社ごとの項目の登録が無い。ほかの試験の登録が残っていても空にする。"""
    monkeypatch.setattr(company_fields, "_REGISTRY", {})


@pytest.mark.django_db
def test_load_small_seeds_through_services(tmp_path, capsys):
    get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")
    seeds = write_seeds(tmp_path / "seeds")

    summary = demo.load(seeds)

    assert counts() == {"品目": 2, "取引先": 2, "品番の対応": 1, "工程": 1, "受注": 1, "つながり": 1}
    assert {kind: summary[kind] for kind in demo.KINDS} == {
        kind: {"入れた": n, "飛ばした": 0}
        for kind, n in {"品目": 2, "取引先": 2, "品番の対応": 1, "工程": 1, "受注": 1, "つながり": 1}.items()
    }
    out = capsys.readouterr().out
    assert out.count("入れた") == len(demo.KINDS)

    order = Order.objects.get()
    assert order.order_no == "SO-000001"
    assert order.customer.code == "C001"
    first, second = list(order.lines.order_by("line_no"))
    assert first.unit == "個"  # 空の単位は品目の単位が写る
    assert first.amount == first.qty * first.unit_price == Decimal("1200")
    assert second.amount == Decimal("242")  # 3 × 80.50 = 241.50 を四捨五入
    assert second.note == "急ぎ"

    partner = Partner.objects.get(code="C001")
    assert (partner.closing_day, partner.payment_day) == (25, "末")
    connection = Connection.objects.get()
    assert connection.secret_env == "SEIZOU_PARTNER_B_PLATING_SECRET"
    assert PartnerItem.objects.get().item.part_no == "A-100"


@pytest.mark.django_db
def test_load_twice_does_not_duplicate(tmp_path):
    seeds = write_seeds(tmp_path / "seeds")
    demo.load(seeds)
    before = counts()

    second = demo.load(seeds)

    assert counts() == before
    for kind in demo.KINDS:
        assert second[kind]["入れた"] == 0, kind
        assert second[kind]["飛ばした"] == before[kind], kind


def test_missing_folder_is_japanese_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="種のフォルダ .* がありません。答えの YAML と同じ場所に <id>/ を置いてください。"):
        demo.load(tmp_path / "no_such_folder")


@pytest.mark.django_db
def test_bad_row_stops_with_file_and_line(tmp_path):
    bad_orders = [
        {**ORDERS[0], "明細": [{**ORDERS[0]["明細"][0]}, {**ORDERS[0]["明細"][1], "数量": 0}]},
    ]
    seeds = write_seeds(tmp_path / "seeds", orders=bad_orders)

    with pytest.raises(ValueError, match=r"orders\.yaml の 1 件目の受注 \(得意先の注文番号 PO-1001\).*R02"):
        demo.load(seeds)

    # 全体が 1 つのトランザクションなので、品目も取引先も入らない
    assert counts() == {"品目": 0, "取引先": 0, "品番の対応": 0, "工程": 0, "受注": 0, "つながり": 0}


@pytest.mark.django_db
def test_bad_csv_row_names_the_line(tmp_path):
    seeds = write_seeds(tmp_path / "seeds", item_rows=[ITEM_ROWS[0], ["A-300", "標準単価が字", "", "", "個", "高い", ""]])

    with pytest.raises(ValueError, match=r"items\.csv の 3 行目.*標準単価"):
        demo.load(seeds)
    assert Item.objects.count() == 0


@pytest.mark.django_db
def test_ext_columns_without_registration_are_reported(tmp_path, no_registration, capsys):
    seeds = write_seeds(
        tmp_path / "seeds",
        item_header=[*ITEM_HEADER, "材質", "図面の版"],
        item_rows=[[*row, "S45C", "A"] for row in ITEM_ROWS],
    )

    summary = demo.load(seeds)

    assert Item.objects.count() == 2
    assert summary["注意"] == [
        "会社ごとの項目が登録されていないので 2 列を入れませんでした (材質・図面の版)。"
    ]
    assert "会社ごとの項目が登録されていないので 2 列を入れませんでした" in capsys.readouterr().out


@pytest.mark.django_db
def test_labels_map_to_choices(tmp_path):
    rows = [
        ["C001", "得意先の社", "", "得意先", "", "", "切上", "各行四捨五入", ""],
        ["S001", "外注先の社", "", "外注先", "", "", "切捨", "各行切捨", ""],
        ["B001", "両方の社", "", "両方", "", "", "", "", ""],
    ]
    seeds = write_seeds(tmp_path / "seeds", orders=[])
    write_csv(seeds / "partners.csv", PARTNER_HEADER, rows)
    write_csv(seeds / "partner_items.csv", PARTNER_ITEM_HEADER, [])
    write_yaml(seeds / "connections.yaml", "つながり", [
        {**CONNECTIONS[0], "取引先コード": "S001", "向き": "送る"},
        {**CONNECTIONS[0], "取引先コード": "B001", "受け口の URL": "", "鍵の置き場の名前": "SEIZOU_PARTNER_A_MACHINING_SECRET", "向き": "受ける"},
    ])

    demo.load(seeds)

    roles = {p.code: (p.role, p.rounding, p.tax_mode) for p in Partner.objects.all()}
    assert roles == {
        "C001": ("customer", "ceil", "line_round"),
        "S001": ("subcontractor", "floor", "line_floor"),
        "B001": ("both", "", ""),
    }
    directions = {(c.partner.code, c.direction, c.url) for c in Connection.objects.all()}
    assert directions == {
        ("S001", "send", "http://127.0.0.1:8002/connect/receive/order/"),
        ("B001", "receive", ""),
    }


# ---------------------------------------------------------------- 2 段目: 2 社の種

# 会社の id: (品目, 取引先, 品番の対応, 工程, 受注, つながり)
EXPECTED_EXAMPLE_COUNTS = {
    "a_machining": (30, 5, 10, 5, 20, 1),
    "b_plating": (30, 5, 10, 5, 20, 1),
}


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", sorted(EXPECTED_EXAMPLE_COUNTS))
def test_examples_load_into_test_db(company_id, no_registration):
    summary = demo.load(COMPANIES_DIR / company_id)

    items, partners, partner_items, processes, orders, connections = EXPECTED_EXAMPLE_COUNTS[company_id]
    assert counts() == {
        "品目": items,
        "取引先": partners,
        "品番の対応": partner_items,
        "工程": processes,
        "受注": orders,
        "つながり": connections,
    }
    assert all(summary[kind]["飛ばした"] == 0 for kind in demo.KINDS)
    # 会社ごとの項目は登録が無いので入れず、入れなかった旨が要約に出る
    assert len(summary["注意"]) == 1
    assert "会社ごとの項目が登録されていないので 2 列を入れませんでした" in summary["注意"][0]
    # 受注の明細は 1〜3 行、金額は数量 × 単価 (端数は取引先の方法で丸める)
    for order in Order.objects.all():
        lines = list(order.lines.all())
        assert 1 <= len(lines) <= 3
        assert order.customer_order_no
        for line in lines:
            assert line.unit_price > 0
            assert abs(line.amount - line.qty * line.unit_price) <= Decimal("0.5")


def read_seed_csv(company_id, name):
    with open(COMPANIES_DIR / company_id / name, encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def test_b_partner_items_cover_a_part_numbers():
    a_part_numbers = {row["自社品番"] for row in read_seed_csv("a_machining", "items.csv")}
    b_part_numbers = {row["自社品番"] for row in read_seed_csv("b_plating", "items.csv")}
    rows = read_seed_csv("b_plating", "partner_items.csv")

    assert rows
    # 相手の品番のまま届き、自社の品番で受ける: 取引先の品番は A 社の自社品番、自社品番は B 社の品目
    assert {row["取引先の品番"] for row in rows} <= a_part_numbers
    assert {row["自社品番"] for row in rows} <= b_part_numbers
    # 取引先は B 社の取引先のうち A 社 (得意先) の行
    a_name = yaml.safe_load((COMPANIES_DIR / "a_machining.yaml").read_text(encoding="utf-8"))["name"]
    a_codes = {row["取引先コード"] for row in read_seed_csv("b_plating", "partners.csv") if row["名前"] == a_name}
    assert {row["取引先コード"] for row in rows} == a_codes
    # A 社の側の品番の対応は、A 社の得意先の品番 → A 社の自社品番
    a_rows = read_seed_csv("a_machining", "partner_items.csv")
    assert {row["自社品番"] for row in a_rows} <= a_part_numbers


def test_seizou_new_demo_end_to_end(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SEIZOU_SITES_DIR", str(tmp_path))

    code = cli.main(["new", str(COMPANIES_DIR / "a_machining.yaml"), "--demo"])

    out = capsys.readouterr().out
    assert code == 0, out
    assert "作り物のデータを入れました" in out
    site = tmp_path / "a_machining"
    count = (
        "import importlib; load = importlib.import_module('seizou.core.demo').load; "
        "import pathlib; seeds = pathlib.Path({seeds!r}); "
        "models = importlib.import_module('seizou.masters.models'); "
        "ext = importlib.import_module('fields.models'); "
        "print('COUNT', models.Item.objects.count(), ext.ItemExt.objects.count()); "
        "load(seeds); "
        "print('AGAIN', models.Item.objects.count(), ext.ItemExt.objects.count())"
    ).format(seeds=str(COMPANIES_DIR / "a_machining"))
    result = cli.run_manage(site, ["shell", "--no-imports", "-c", count])
    assert result.returncode == 0, result.stderr
    lines = {line.split()[0]: line.split()[1:] for line in result.stdout.splitlines() if line.startswith(("COUNT", "AGAIN"))}
    assert lines["COUNT"][0] == "30"
    assert int(lines["COUNT"][1]) >= 1
    # 2 回目の load で数が増えない
    assert lines["AGAIN"] == lines["COUNT"]
    assert os.path.exists(site / "db.sqlite3")
