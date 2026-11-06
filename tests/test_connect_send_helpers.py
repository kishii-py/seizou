"""相手へ送る 4 本が共通に使う小さな関数 (connect.services の _encode・_party) のテスト。

送る 4 本 (send_order・send_reply・send_acceptance・send_shipping_notice) は、本文の bytes を _encode で作り、
送り手の辞書を _party で作る。4 本の中に json.dumps を書き直していないことも見る。
会社の名前と id は作り物。
"""

import inspect

import pytest

from seizou.connect import services
from seizou.core.conf import company_id
from seizou.core.models import Company
from seizou.core.services import company

SENDERS = (
    services.send_order,
    services.send_reply,
    services.send_acceptance,
    services.send_shipping_notice,
)


def test_encode_sorts_keys_without_spaces_and_keeps_japanese():
    raw = services._encode({"b": 1, "a": "あ"})

    assert raw == '{"a":"あ","b":1}'.encode("utf-8")


@pytest.mark.django_db
def test_party_is_id_and_name_of_own_company(settings):
    settings.SEIZOU = {"id": "demo-company-a"}
    Company.objects.create(name="作り物の会社 A")

    party = services._party()

    assert set(party) == {"id", "name"}
    assert party["id"] == company_id() == "demo-company-a"
    assert party["name"] == company().name == "作り物の会社 A"


@pytest.mark.parametrize("sender", SENDERS, ids=lambda sender: sender.__name__)
def test_senders_do_not_call_json_dumps(sender):
    assert "json.dumps" not in inspect.getsource(sender)
