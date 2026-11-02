"""core の利用者と会社の表、選べる設定の既定。"""

import pytest
from django.contrib.auth import get_user_model

from seizou.core import conf
from seizou.core.models import Company


@pytest.mark.django_db
def test_user_model_is_core_user():
    User = get_user_model()
    assert User._meta.label == "core.User"
    user = User.objects.create_user(username="tester", password="tests-only")
    assert user.pk


@pytest.mark.django_db
def test_company_defaults():
    company = Company.objects.create(name="作り物の会社")
    assert company.rounding == "round"
    assert company.tax_mode == "total"
    assert str(company) == "作り物の会社"


def test_setting_defaults_and_unknown_key(settings):
    settings.SEIZOU = {}
    assert conf.setting("over_shipment") == "warn"
    assert conf.setting("partial_shipment") is True
    with pytest.raises(KeyError):
        conf.setting("no_such_key")


def test_setting_reads_company_value(settings):
    settings.SEIZOU = {"order_number": "ym_seq"}
    assert conf.setting("order_number") == "ym_seq"
