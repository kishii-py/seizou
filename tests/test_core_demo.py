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
# 小計の計算方法・消費税の計算方法は、画面の字 (金額の端数の扱い・消費税の端数の扱い) を直す前の見出し。種はこの字のままでも読む。
PARTNER_HEADER = [
    "取引先コード", "名前", "略称", "取引先の種類", "締日", "支払の月", "支払日", "小計の計算方法", "消費税の計算方法", "備考",
]
PARTNER_ITEM_HEADER = ["取引先コード", "自社品番", "取引先の品番", "取引先の品名"]

ITEM_ROWS = [
    ["A-100", "作り物の軸", "", "", "個", "120", ""],
    ["A-200", "作り物の板", "", "", "枚", "80.50", ""],
]
PARTNER_ROWS = [
    ["C001", "作り物の得意先", "得意", "得意先", "25", "翌月", "31", "四捨五入", "トータル", ""],
    ["S001", "作り物の協力企業", "", "協力企業", "", "", "", "", "", ""],
]
PARTNER_ITEM_ROWS = [["C001", "A-100", "X-1", "相手の軸"]]
PROCESSES = [{"工程コード": "P10", "名前": "旋削", "協力企業に出すか": False, "並び順": 10}]
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
    # 支払の月は字 (翌月) から choices の値に、支払日は数に (31 は月末)。
    assert (partner.closing_day, partner.payment_month, partner.payment_day) == (25, "next", 31)
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
        ["C001", "得意先の社", "", "得意先", "", "", "", "切上", "トータル", ""],
        ["S001", "協力企業の社", "", "協力企業", "", "", "", "切捨", "トータル", ""],
        ["B001", "両方の社", "", "両方", "", "", "", "", "", ""],
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
        "C001": ("customer", "ceil", "total"),
        "S001": ("subcontractor", "floor", "total"),
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
    "a_machining": (30, 5, 10, 5, 20, 2),
    "b_plating": (30, 5, 11, 5, 21, 2),
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
    assert "作り物のデータを登録しました" in out
    assert "品目: 入れた 30 件・飛ばした 0 件" in out
    assert "出荷の知らせと検収: 入れた 3 件・飛ばした 0 件" in out
    assert out.index("品目: 入れた") < out.index("作り物のデータを登録しました")
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
    assert "説明書の断片を写しました" in out
    assert len(list((site / "manual").glob("*.html"))) == len(list((COMPANIES_DIR / "a_machining" / "manual").glob("*.html")))


# ---------------------------------------------------------------- 新しい 6 種類 (標準の工程・図面・指示書・出荷・納品書・請求書)

MORE_KIND_NAMES = ("標準の工程", "図面", "指示書", "出荷", "納品書", "請求書")


def more_imports():
    # 新しい種類の表は、試験の中で引く (上の import の行は変えない)。
    from seizou.billing.models import Invoice, InvoiceLine
    from seizou.masters.models import ItemProcess
    from seizou.shipping.models import DeliveryNote, Shipment
    from seizou.workorders.models import WorkOrder, WorkOrderStep

    return Invoice, InvoiceLine, ItemProcess, DeliveryNote, Shipment, WorkOrder, WorkOrderStep


def more_counts():
    Invoice, InvoiceLine, ItemProcess, DeliveryNote, Shipment, WorkOrder, WorkOrderStep = more_imports()
    return {
        "標準の工程": ItemProcess.objects.count(),
        "指示書": WorkOrder.objects.count(),
        "指示書の工程": WorkOrderStep.objects.count(),
        "出荷": Shipment.objects.count(),
        "納品書": DeliveryNote.objects.count(),
        "請求書": Invoice.objects.count(),
        "請求書の明細": InvoiceLine.objects.count(),
    }


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    return settings.MEDIA_ROOT


def drawing_image(fmt, **options):
    """試験用の小さな図面 (白黒に線 2 本)。作品のファイルは読まず、この中で作る。"""
    import io

    from PIL import Image, ImageDraw

    image = Image.new("1", (400, 300), 1)
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 390, 290), outline=0)
    draw.line((10, 10, 390, 290), fill=0)
    buffer = io.BytesIO()
    image.save(buffer, format=fmt, **options)
    return buffer.getvalue()


# 明細は A-200 (3 枚) の 1 行だけ。出荷は 1 枚と 2 枚の分納で、受注の状態は出荷済みになる。
MORE_ORDERS = [
    {
        "得意先の取引先コード": "C001",
        "受注日": "2026-10-05",
        "得意先の注文番号": "PO-2001",
        "備考": "",
        "明細": [{"自社品番": "A-200", "数量": 3, "単位": "枚", "単価": 80.5, "納期": "2026-10-25", "備考": ""}],
    }
]
LINE_REF = {"得意先の取引先コード": "C001", "得意先の注文番号": "PO-2001", "行番号": 1}
SHIPMENT_REFS = [
    {"得意先の注文番号": "PO-2001", "行番号": 1, "出荷日": "2026-10-10"},
    {"得意先の注文番号": "PO-2001", "行番号": 1, "出荷日": "2026-10-12"},
]


def write_more_seeds(folder, *, drawing_name="A-100.png", workorders=None, shipments=None):
    folder = write_seeds(
        folder,
        item_rows=[
            ["A-100", "作り物の軸", "", drawing_name, "個", "120", ""],
            ["A-200", "作り物の板", "", "A-200.tif", "枚", "80.50", ""],
        ],
        orders=MORE_ORDERS,
    )
    write_yaml(
        folder / "processes.yaml",
        "工程",
        [
            {"工程コード": "P10", "名前": "旋削", "協力企業に出すか": False, "並び順": 10, "計画に使う": True},
            {"工程コード": "P20", "名前": "検査", "協力企業に出すか": False, "並び順": 20, "計画に使う": False},
        ],
    )
    write_csv(
        folder / "item_processes.csv",
        ["自社品番", "工程コード", "順"],
        [["A-100", "P10", "1"], ["A-200", "P20", "2"], ["A-200", "P10", "1"]],
    )
    files = folder / "files"
    files.mkdir(exist_ok=True)
    (files / "A-100.png").write_bytes(drawing_image("PNG"))
    (files / "A-200.tif").write_bytes(drawing_image("TIFF", compression="group4"))
    write_yaml(
        folder / "workorders.yaml",
        "指示書",
        workorders
        or [
            {
                **LINE_REF,
                "数量": 3,
                "工程": [
                    {"工程コード": "P10", "予定の数": 3, "開始日": "2026-11-02", "終了日": "2026-11-03"},
                    {"工程コード": "P20", "予定の数": 3, "開始日": None, "終了日": None},
                ],
            }
        ],
    )
    write_yaml(
        folder / "shipments.yaml",
        "出荷",
        shipments
        or [
            {**LINE_REF, "数量": 1, "出荷日": "2026-10-10", "備考": "1 回目"},
            {**LINE_REF, "数量": 2, "出荷日": "2026-10-12", "備考": "2 回目"},
        ],
    )
    write_yaml(folder / "delivery_notes.yaml", "納品書", [{"得意先の取引先コード": "C001", "出荷": SHIPMENT_REFS}])
    write_yaml(
        folder / "invoices.yaml",
        "請求書",
        [{"得意先の取引先コード": "C001", "発行日": "2026-10-31", "出荷": SHIPMENT_REFS}],
    )
    return folder


@pytest.mark.django_db
def test_load_more_kinds_through_services(tmp_path, media, capsys):
    from seizou.workorders.models import WorkOrderStep

    get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")
    seeds = write_more_seeds(tmp_path / "seeds")

    summary = demo.load(seeds)

    assert {kind: summary[kind] for kind in MORE_KIND_NAMES} == {
        "標準の工程": {"入れた": 2, "飛ばした": 0},
        "図面": {"入れた": 2, "飛ばした": 0},
        "指示書": {"入れた": 1, "飛ばした": 0},
        "出荷": {"入れた": 2, "飛ばした": 0},
        "納品書": {"入れた": 1, "飛ばした": 0},
        "請求書": {"入れた": 1, "飛ばした": 0},
    }
    out = capsys.readouterr().out
    assert out.count("入れた") == len(demo.KINDS) + len(MORE_KIND_NAMES)
    assert "指示書: 入れた 1 件・飛ばした 0 件" in out
    assert more_counts() == {
        "標準の工程": 3, "指示書": 1, "指示書の工程": 2, "出荷": 2, "納品書": 1, "請求書": 1, "請求書の明細": 2,
    }

    # 標準の工程は、順の小さい順に並ぶ
    plate = Item.objects.get(part_no="A-200")
    assert [row.process.code for row in plate.item_processes.order_by("seq")] == ["P10", "P20"]
    # 指示書の工程は、日のある工程だけ予定が入る
    first, second = list(WorkOrderStep.objects.order_by("seq"))
    assert (str(first.planned_start), str(first.planned_end)) == ("2026-11-02", "2026-11-03")
    assert (second.planned_start, second.planned_end) == (None, None)
    # 分納の 2 回で受注は出荷済み、請求書の明細の数量は出荷の数量
    assert Order.objects.get().status == Order.STATUS_SHIPPED
    from seizou.billing.models import Invoice
    from seizou.shipping.models import Shipment

    invoice = Invoice.objects.get()
    assert sorted(line.qty for line in invoice.lines.all()) == [Decimal("1"), Decimal("2")]
    assert sorted(s.qty for s in Shipment.objects.all()) == [Decimal("1"), Decimal("2")]
    # 図面: PNG はそのまま、TIFF は表示用の PNG が付き、誤りの文は空
    png_item = Item.objects.get(part_no="A-100")
    assert png_item.drawing and not png_item.drawing_preview and png_item.drawing_preview_error == ""
    assert plate.drawing and plate.drawing_preview and plate.drawing_preview_error == ""
    assert plate.drawing.size > 0


@pytest.mark.django_db
def test_more_kinds_twice_does_not_duplicate(tmp_path, media, capsys):
    seeds = write_more_seeds(tmp_path / "seeds")
    demo.load(seeds)
    before = more_counts()
    capsys.readouterr()

    summary = demo.load(seeds)

    assert all(summary[kind]["入れた"] == 0 for kind in MORE_KIND_NAMES)
    assert summary["標準の工程"]["飛ばした"] == 2
    assert summary["出荷"]["飛ばした"] == 2
    assert more_counts() == before
    out = capsys.readouterr().out
    assert out.count("入れた 0 件") == len(demo.KINDS) + len(MORE_KIND_NAMES)


@pytest.mark.django_db
def test_old_seed_folder_still_loads(tmp_path, capsys):
    seeds = write_seeds(tmp_path / "seeds")

    summary = demo.load(seeds)

    assert all(summary[kind] == {"入れた": 0, "飛ばした": 0} for kind in MORE_KIND_NAMES)
    out = capsys.readouterr().out
    assert out.count("入れた") == len(demo.KINDS)
    assert not any(f"{kind}:" in out for kind in MORE_KIND_NAMES)
    assert more_counts() == {
        "標準の工程": 0, "指示書": 0, "指示書の工程": 0, "出荷": 0, "納品書": 0, "請求書": 0, "請求書の明細": 0,
    }


@pytest.mark.django_db
def test_unknown_reference_stops_with_file_and_position(tmp_path, media):
    seeds = write_more_seeds(
        tmp_path / "seeds",
        shipments=[{**LINE_REF, "得意先の注文番号": "PO-9999", "数量": 1, "出荷日": "2026-10-10", "備考": ""}],
    )

    with pytest.raises(ValueError) as caught:
        demo.load(seeds)

    assert "shipments.yaml" in str(caught.value)
    assert "1 件目" in str(caught.value)
    assert "PO-9999" in str(caught.value)
    # 全体が 1 つのトランザクション。受注も入らない
    assert Order.objects.count() == 0
    assert more_counts()["出荷"] == 0


@pytest.mark.django_db
def test_drawing_name_outside_files_is_refused(tmp_path, media):
    seeds = write_more_seeds(tmp_path / "seeds", drawing_name="../x.png")

    with pytest.raises(ValueError) as caught:
        demo.load(seeds)

    assert "items.csv" in str(caught.value)
    assert "2 行目" in str(caught.value)
    assert Item.objects.count() == 0


@pytest.mark.django_db
def test_plan_dates_go_through_plan_step(tmp_path, media):
    workorders = [
        {
            **LINE_REF,
            "数量": 3,
            "工程": [{"工程コード": "P10", "予定の数": 3, "開始日": "2026-11-05", "終了日": "2026-11-03"}],
        }
    ]
    seeds = write_more_seeds(tmp_path / "seeds", workorders=workorders)

    with pytest.raises(ValueError) as caught:
        demo.load(seeds)

    assert "workorders.yaml" in str(caught.value)
    assert "1 件目" in str(caught.value)
    assert "開始日が終了日より後です" in str(caught.value)
    assert more_counts()["指示書"] == 0


# 会社の id: 標準の工程の行・指示書・指示書の工程・出荷・納品書・請求書・請求書の明細・図面のある部品
EXPECTED_MORE_COUNTS = {
    "a_machining": {"標準の工程": 85, "指示書": 10, "指示書の工程": 26, "出荷": 21, "納品書": 6, "請求書": 3, "請求書の明細": 9, "図面": 4},
    "b_plating": {"標準の工程": 120, "指示書": 10, "指示書の工程": 40, "出荷": 24, "納品書": 6, "請求書": 2, "請求書の明細": 5, "図面": 0},
}
# 出荷済みの受注の数。B 社は受信した注文の写しの受注 (受注の 21 件目) も出荷済み
EXPECTED_SHIPPED_ORDERS = {"a_machining": 10, "b_plating": 11}


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", sorted(EXPECTED_MORE_COUNTS))
def test_examples_load_more_kinds(company_id, no_registration, media):
    from seizou.shipping.models import DeliveryNote, Shipment
    from seizou.workorders.models import WorkOrderStep

    expected = EXPECTED_MORE_COUNTS[company_id]
    summary = demo.load(COMPANIES_DIR / company_id)

    got = more_counts()
    assert got == {key: expected[key] for key in got}
    assert summary["指示書"]["入れた"] == 10
    assert summary["図面"]["入れた"] == expected["図面"]
    # 受注の 10 件 (B 社は受信した注文の写しの受注も入れて 11 件) は出荷済み。出荷は全部が発行の納品書に入っている
    assert Order.objects.filter(status=Order.STATUS_SHIPPED).count() == EXPECTED_SHIPPED_ORDERS[company_id]
    assert Shipment.objects.filter(delivery_note__isnull=True).count() == 0
    assert not Shipment.objects.exclude(delivery_note__status="issued").exists()
    # 納品書の発行日は、入れた出荷のいちばん遅い出荷日 (種を読んだ日ではない。精査 A9 の 3 節の 2)
    for note in DeliveryNote.objects.all():
        assert note.issued_on == max(s.shipped_at for s in note.shipments.all())
    # 工程の予定に日のある工程があり、計画に使う工程だけに置いてある
    dated = WorkOrderStep.objects.filter(planned_start__isnull=False)
    assert dated.exists()
    assert all(step.process.use_in_plan for step in dated)
    # 例の種では「注意」の文を増やさない (品目の項目の登録が無い 1 件だけ)
    assert len(summary["注意"]) == 1

    drawings = Item.objects.exclude(drawing="")
    assert drawings.count() == expected["図面"]
    if company_id == "a_machining":
        tiff = Item.objects.get(drawing__endswith=".tif")
        assert tiff.drawing_preview and tiff.drawing_preview_error == ""
        assert all(item.drawing_preview_error == "" for item in drawings)

    # 2 回目は新しい 6 種類が全部「入れた 0」
    again = demo.load(COMPANIES_DIR / company_id)
    assert all(again[kind]["入れた"] == 0 for kind in MORE_KIND_NAMES)
    assert more_counts() == got


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", sorted(EXPECTED_MORE_COUNTS))
def test_examples_new_kinds_go_into_an_existing_site(company_id, tmp_path, no_registration, media):
    """今ある置き場 (古い 6 種類だけ入っている) に load を打ち直すと、新しい 6 種類だけが入る。"""
    import shutil

    old = tmp_path / "old"
    shutil.copytree(
        COMPANIES_DIR / company_id,
        old,
        ignore=shutil.ignore_patterns(
            "item_processes.csv",
            "workorders.yaml",
            "shipments.yaml",
            "delivery_notes.yaml",
            "invoices.yaml",
            "files",
            # 在庫の動きの種は指示書の種を指すので、指示書を外した古い置き場では一緒に外す (受入の種も新しい種類)
            "stock_moves.yaml",
            "receipts.yaml",
            # 出荷の知らせ・受信した注文の写しと検収・入金の種は、発注と請求書の種を指すので一緒に外す
            "shipping_notices.yaml",
            "acceptances.yaml",
            "payments.yaml",
        ),
    )
    demo.load(old)
    assert more_counts()["出荷"] == 0

    summary = demo.load(COMPANIES_DIR / company_id)

    assert all(summary[kind]["入れた"] == 0 and summary[kind]["飛ばした"] > 0 for kind in demo.KINDS)
    expected = EXPECTED_MORE_COUNTS[company_id]
    assert summary["指示書"]["入れた"] == expected["指示書"]
    assert summary["出荷"]["入れた"] == expected["出荷"]
    assert summary["納品書"]["入れた"] == expected["納品書"]
    assert summary["請求書"]["入れた"] == expected["請求書"]
    assert summary["標準の工程"]["入れた"] == 30
    assert summary["図面"]["入れた"] == expected["図面"]


@pytest.mark.django_db
@pytest.mark.parametrize(("company_id", "manuals"), [("a_machining", 2), ("b_plating", 1)])
def test_examples_load_part_seeds(company_id, manuals, no_registration, capsys):
    """部品が持つ種 (業務マニュアル) も同じ load で入り、2 回目は飛ばす。"""
    from seizou.manuals.models import Manual

    summary = demo.load(COMPANIES_DIR / company_id)

    assert summary["業務マニュアル"] == {"入れた": manuals, "飛ばした": 0}
    assert Manual.objects.count() == manuals
    assert f"業務マニュアル: 入れた {manuals} 件・飛ばした 0 件" in capsys.readouterr().out

    again = demo.load(COMPANIES_DIR / company_id)

    assert again["業務マニュアル"] == {"入れた": 0, "飛ばした": manuals}
    assert Manual.objects.count() == manuals


@pytest.mark.django_db
def test_part_seed_without_match_prints_nothing(tmp_path, capsys):
    """種のフォルダの名前が部品の種に無い会社では、業務マニュアルの行を出さない (0 件の行を増やさない)。"""
    get_user_model().objects.create_superuser("admin", password="pass-for-tests-only")
    seeds = write_seeds(tmp_path / "seeds")

    summary = demo.load(seeds)

    assert summary["業務マニュアル"] == {"入れた": 0, "飛ばした": 0}
    assert "業務マニュアル" not in capsys.readouterr().out
