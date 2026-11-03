"""2 社の休日の種 (masters.demo_seed。core の demo.load の PART_SEEDS が呼ぶ)。

例の種 (examples/companies/<id>/holidays.yaml) を demo.load で入れ、2 回目は飛ばすこと、祝日 18 件と会社の休みの数、
会社の休みが全部平日 (定休日と重ねない) ことを見る。種のファイルの無いフォルダと、知らない種類の字も見る。HTTP は呼ばない。
"""

import datetime
import shutil
from pathlib import Path

import pytest

from seizou.core import demo
from seizou.core import fields as company_fields
from seizou.masters import demo_seed
from seizou.masters.models import Holiday

COMPANIES_DIR = Path(__file__).resolve().parent.parent / "examples" / "companies"
#: 会社の id → (休日の件数, 会社の休みの件数)。祝日は 2 社とも 18 件
EXPECTED = {"a_machining": (24, 6), "b_plating": (27, 9)}


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
def test_examples_load_holiday_seeds_twice(company_id, no_registration, media, no_http, capsys):
    total, company_days = EXPECTED[company_id]

    summary = demo.load(COMPANIES_DIR / company_id)

    assert summary["休日"] == {"入れた": total, "飛ばした": 0}
    assert f"休日: 入れた {total} 件・飛ばした 0 件" in capsys.readouterr().out
    assert len(summary["注意"]) == 1  # 例の種では「注意」の文を増やさない
    assert Holiday.objects.count() == total
    assert Holiday.objects.filter(kind=Holiday.KIND_PUBLIC).count() == 18
    assert Holiday.objects.filter(kind=Holiday.KIND_COMPANY).count() == company_days
    assert Holiday.objects.get(date=datetime.date(2026, 11, 23)).kind == Holiday.KIND_PUBLIC
    # 会社の休みは平日だけ (定休日と重ねない)。祝日は暦のまま (2026-05-03 の憲法記念日は日曜で、05-06 が振替休日)
    company_dates = Holiday.objects.filter(kind=Holiday.KIND_COMPANY).values_list("date", flat=True)
    assert all(day.weekday() < 5 for day in company_dates)
    weekend_public = [d for d in Holiday.objects.filter(kind=Holiday.KIND_PUBLIC).values_list("date", flat=True) if d.weekday() >= 5]
    assert weekend_public == [datetime.date(2026, 5, 3)]

    again = demo.load(COMPANIES_DIR / company_id)

    assert again["休日"] == {"入れた": 0, "飛ばした": total}
    assert f"休日: 入れた 0 件・飛ばした {total} 件" in capsys.readouterr().out
    assert Holiday.objects.count() == total


@pytest.mark.django_db
def test_holiday_seed_without_file_and_with_unknown_kind(tmp_path, no_registration, media, no_http, capsys):
    seeds = tmp_path / "seeds"
    shutil.copytree(COMPANIES_DIR / "a_machining", seeds)
    (seeds / demo_seed.HOLIDAYS_FILE).unlink()

    summary = demo.load(seeds)

    assert summary["休日"] == {"入れた": 0, "飛ばした": 0}
    assert "休日:" not in capsys.readouterr().out
    assert not Holiday.objects.exists()

    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / demo_seed.HOLIDAYS_FILE).write_text(
        "休日:\n  - 日付: 2026-11-27\n    名前: 作り物の休み\n    種類: 臨時\n", encoding="utf-8"
    )
    with pytest.raises(ValueError) as error:
        demo_seed.load(bad, None)
    assert "holidays.yaml の 1 件目" in str(error.value) and "臨時" in str(error.value)
    assert not Holiday.objects.exists()


@pytest.mark.django_db
def test_holiday_seed_reads_kind_values_and_date_strings(tmp_path):
    (tmp_path / demo_seed.HOLIDAYS_FILE).write_text(
        "休日:\n"
        "  - 日付: '2026-11-27'\n    名前: 作り物の休み\n    種類: company\n"
        "  - 日付: 2026-11-23\n    名前: 勤労感謝の日\n    種類: public\n",
        encoding="utf-8",
    )

    assert demo_seed.load(tmp_path, None) == {"入れた": 2, "飛ばした": 0}
    assert list(Holiday.objects.values_list("date", "kind")) == [
        (datetime.date(2026, 11, 23), Holiday.KIND_PUBLIC),
        (datetime.date(2026, 11, 27), Holiday.KIND_COMPANY),
    ]
