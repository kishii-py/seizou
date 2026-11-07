"""消費税の扱いをトータルだけにし、消費税の端数の向きで丸める (決め LAW-2・C95-1b・C95-1c)。

選べる値・向きの列・値の移し (core と masters の migration)・移した記録・画面の入口を見る。
ほかの版が直さないファイルに置き、LAW-2 と C95-1b と C95-1c の形が後の直しで崩れたら気づけるようにする。
"""

import importlib
import logging

import pytest
from django.apps import apps

from seizou.core.models import Company
from seizou.masters.forms import PartnerForm
from seizou.masters.models import Partner

CORE_MOVE = "seizou.core.migrations.0005_company_tax_mode_total"
MASTERS_MOVE = "seizou.masters.migrations.0013_partner_tax_mode_total"


def _settings_lines(caplog):
    return [record.getMessage() for record in caplog.records if record.name == "seizou.settings"]


# ---------------------------------------------------------------- (a) 選べる値と向きの列


def test_choices_are_total_only_and_rounding_column():
    assert Company.TAX_CHOICES == [("total", "トータル")]
    assert list(Partner._meta.get_field("tax_mode").choices) == [("total", "トータル")]
    assert Company._meta.get_field("tax_mode").default == "total"
    company_rounding = Company._meta.get_field("tax_rounding")
    assert company_rounding.default == "floor"
    assert list(company_rounding.choices) == Company.ROUNDING_CHOICES
    partner_rounding = Partner._meta.get_field("tax_rounding")
    assert partner_rounding.blank
    assert list(partner_rounding.choices) == Company.ROUNDING_CHOICES
    assert Company().get_tax_rounding_display() == "切捨"


# ---------------------------------------------------------------- (b) core の移し


@pytest.mark.django_db
def test_core_move_to_total_keeps_former_direction(caplog, capsys):
    caplog.set_level(logging.INFO, logger="seizou.settings")
    move_to_total = importlib.import_module(CORE_MOVE).move_to_total
    company = Company.objects.create(name="作り物の会社", tax_mode="line_floor", tax_rounding="ceil")

    move_to_total(apps, None)

    company.refresh_from_db()
    assert (company.tax_mode, company.tax_rounding) == ("total", "floor")
    lines = _settings_lines(caplog)
    assert len(lines) == 1
    for word in ("各行切捨", "トータル", "切捨", "作り物の会社"):
        assert word in lines[0], word
    out = capsys.readouterr().out
    assert lines[0] in out

    move_to_total(apps, None)
    assert len(_settings_lines(caplog)) == 1
    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------- (c) masters の移し


@pytest.mark.django_db
def test_masters_move_to_total_keeps_or_copies_direction(caplog, capsys):
    caplog.set_level(logging.INFO, logger="seizou.settings")
    move_to_total = importlib.import_module(MASTERS_MOVE).move_to_total
    Company.objects.create(name="作り物の会社", rounding="ceil")
    made = {
        "P1": ("line_round", "floor"),
        "P2": ("line_floor", "ceil"),
        "P3": ("", "round"),
        "P4": ("total", ""),
    }
    for code, (tax_mode, rounding) in made.items():
        Partner.objects.create(
            code=code, name=f"作り物の取引先 {code}", role=Partner.ROLE_CUSTOMER, tax_mode=tax_mode, rounding=rounding
        )

    move_to_total(apps, None)

    rows = {p.code: (p.tax_mode, p.tax_rounding) for p in Partner.objects.all()}
    assert rows == {
        "P1": ("total", "round"),
        "P2": ("total", "floor"),
        # トータルか空の行は、今の金額の端数の向き (取引先の値。空なら会社の値) を写す (決め C95-1c)
        "P3": ("", "round"),
        "P4": ("total", "ceil"),
    }
    # 金額の端数の扱いは触らない
    assert {p.code: p.rounding for p in Partner.objects.all()} == {code: r for code, (_m, r) in made.items()}
    lines = _settings_lines(caplog)
    assert len(lines) == 4
    for code, line in zip(("P1", "P2", "P3", "P4"), lines):
        assert code in line, line
    assert "各行四捨五入" in lines[0] and "各行切捨" in lines[1]
    assert "各行" not in lines[2] and "各行" not in lines[3]
    for line, label in zip(lines, ("四捨五入", "切捨", "四捨五入", "切上")):
        assert f"消費税の端数の向きを {label} にした" in line, line
    out = capsys.readouterr().out
    assert all(line in out for line in lines)

    move_to_total(apps, None)
    assert len(_settings_lines(caplog)) == 4
    assert {p.code: (p.tax_mode, p.tax_rounding) for p in Partner.objects.all()} == rows


@pytest.mark.django_db
def test_masters_move_without_company_row_uses_round(capsys):
    move_to_total = importlib.import_module(MASTERS_MOVE).move_to_total
    partner = Partner.objects.create(code="C001", name="作り物の得意先", role=Partner.ROLE_CUSTOMER, tax_mode="total")

    move_to_total(apps, None)

    partner.refresh_from_db()
    # 会社の行が無いときは masters.services.company_rounding と同じ四捨五入
    assert (partner.tax_mode, partner.tax_rounding) == ("total", "round")
    assert Company.objects.count() == 0


# ---------------------------------------------------------------- (d) 取引先の form


@pytest.mark.django_db
def test_partner_form_choices_and_former_value_error():
    fields = PartnerForm().fields
    assert [value for value, _label in fields["tax_mode"].choices] == ["", "total"]
    assert [value for value, _label in fields["tax_rounding"].choices] == ["", "round", "ceil", "floor"]

    form = PartnerForm(data={"code": "C001", "name": "作り物の得意先", "role": "customer", "tax_mode": "line_round"})
    assert not form.is_valid()
    assert "tax_mode" in form.errors
    assert "tax_rounding" not in form.errors
