"""2 社の入金の種 (billing.demo_seed。core の demo.load の PART_SEEDS が呼ぶ)。

例の種 (examples/companies/<id>/) を demo.load で入れ、2 回目は飛ばすこと、2 社を合わせて 全額・一部・未入金 が
1 つずつ映ること、種の指す請求書が請求書の種にあることを見る。HTTP は呼ばない (呼んだら落とす)。
"""

import datetime
import shutil
from decimal import Decimal
from pathlib import Path

import pytest
import yaml

from seizou.billing import demo_seed
from seizou.billing.models import Invoice, Payment
from seizou.core import demo
from seizou.core import fields as company_fields

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
#: 種の入金の件数 (A 社 = 一部入金 1・全額 1、B 社 = 全額 1)
EXPECTED = {"a_machining": 2, "b_plating": 1}
#: 会社の id: [(得意先の取引先コード, 発行日, 入金の状態の字)]
EXPECTED_STATUS = {
    "a_machining": [
        ("C002", datetime.date(2026, 10, 29), "一部入金"),
        ("C003", datetime.date(2026, 11, 2), "未入金"),
        ("C004", datetime.date(2026, 10, 31), "入金済み"),
    ],
    "b_plating": [("C001", datetime.date(2026, 10, 29), "入金済み"), ("C004", datetime.date(2026, 11, 2), "未入金")],
}
METHOD_WORDS = ("振込", "現金", "手形", "その他", "", None)


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


def _invoice(code, issued_on):
    return Invoice.objects.get(customer__code=code, issued_on=issued_on)


@pytest.mark.django_db
@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_examples_load_payment_seeds_twice(company_id, no_registration, media, no_http, capsys):
    made = EXPECTED[company_id]

    summary = demo.load(COMPANIES_DIR / company_id)

    assert summary["入金"] == {"入れた": made, "飛ばした": 0}
    assert f"入金: 入れた {made} 件・飛ばした 0 件" in capsys.readouterr().out
    assert len(summary["注意"]) == 1  # 例の種では「注意」の文を増やさない
    got = [
        (code, issued_on, _invoice(code, issued_on).payment_status_label())
        for code, issued_on, _label in EXPECTED_STATUS[company_id]
    ]
    assert got == EXPECTED_STATUS[company_id]
    first = _invoice(*EXPECTED_STATUS[company_id][0][:2])
    if company_id == "a_machining":
        assert first.paid_total() == Decimal("100000")
        assert first.balance() > 0
        assert _invoice("C004", datetime.date(2026, 10, 31)).balance() == 0  # 3 枚目は全額
    else:
        assert first.balance() == 0  # 全額 (金額は請求書の合計)
    payment = Payment.objects.get(invoice=first)
    assert payment.invoice == first
    assert payment.method == Payment.METHOD_TRANSFER
    assert payment.status == Payment.STATUS_RECORDED
    assert payment.recorded_by is None  # 試験に管理者がいない

    again = demo.load(COMPANIES_DIR / company_id)

    assert again["入金"] == {"入れた": 0, "飛ばした": made}
    assert Payment.objects.count() == made


@pytest.mark.django_db
def test_seed_without_files_is_silent(tmp_path):
    assert demo_seed.load(tmp_path, None) == {"入れた": 0, "飛ばした": 0}

    # 入金の種は取引先と請求書の種を指すので、それらの無い置き場では ValueError で止まる (黙って飛ばさない)
    shutil.copy(COMPANIES_DIR / "a_machining" / "payments.yaml", tmp_path / "payments.yaml")
    with pytest.raises(ValueError, match="payments.yaml の 1 件目"):
        demo_seed.load(tmp_path, None)
    assert Payment.objects.count() == 0


def _yaml(path, root):
    return yaml.safe_load(path.read_text(encoding="utf-8"))[root]


@pytest.mark.parametrize("company_id", ["a_machining", "b_plating"])
def test_seed_references_exist(company_id):
    folder = COMPANIES_DIR / company_id
    invoices = [(entry["得意先の取引先コード"], entry["発行日"]) for entry in _yaml(folder / "invoices.yaml", "請求書")]
    paid = set()
    for entry in _yaml(folder / "payments.yaml", "請求書"):
        key = (entry["得意先の取引先コード"], entry["発行日"])
        assert key in invoices, key
        paid.add(key)
        for payment in entry["入金"]:
            assert Decimal(str(payment["金額"])) > 0
            assert payment.get("入金の方法") in METHOD_WORDS
    assert [key for key in invoices if key not in paid]  # 入金の無い請求書 (未入金の見本) が 1 つ以上ある


def test_method_words():
    assert demo_seed._method("現金") == "cash"
    assert demo_seed._method("") == "transfer"
    assert demo_seed._method("cash") == "cash"
    with pytest.raises(ValueError, match="小切手"):
        demo_seed._method("小切手")
