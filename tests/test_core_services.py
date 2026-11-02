"""core の services (連番と会社の設定)。"""

import pytest
from django.db import transaction

from seizou.core import services
from seizou.core.models import Company, NumberSequence


@pytest.mark.django_db
def test_company_creates_default_row_once():
    assert Company.objects.count() == 0
    first = services.company()
    second = services.company()
    assert first.pk == second.pk
    assert Company.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_next_number_requires_atomic():
    # transaction=True で、テストを包むトランザクションを外す (外で呼んだときの振る舞いを見るため)。
    with pytest.raises(RuntimeError):
        services.next_number("order")


@pytest.mark.django_db
def test_next_number_seq_increments_per_kind(settings):
    settings.SEIZOU = {"order_number": "seq"}
    with transaction.atomic():
        assert services.next_number("order") == "SO-000001"
        assert services.next_number("order") == "SO-000002"
        assert services.next_number("shipment") == "SH-000001"
    assert NumberSequence.objects.get(kind="order", period="").last == 2


@pytest.mark.django_db
def test_next_number_ym_seq_has_period(settings):
    settings.SEIZOU = {"order_number": "ym_seq"}
    with transaction.atomic():
        number = services.next_number("order")
    prefix, period, seq = number.split("-")
    assert prefix == "SO"
    assert len(period) == 4 and period.isdigit()
    assert seq == "0001"


@pytest.mark.django_db
def test_next_number_unknown_kind():
    with transaction.atomic(), pytest.raises(KeyError):
        services.next_number("nope")
