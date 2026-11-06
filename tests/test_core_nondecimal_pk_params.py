"""pk の引数に数字でない字 (例: 上付きの 2「²」。`str.isdigit` は真を返すが `int` は落ちる) を渡しても 500 にならない。

N225 (C66) のレーンが 11-07 に見つけた物 (出荷の新規登録の `?order_line=²` で 500)。pk の字を `isdigit` で見てから `int` に
渡していた 5 か所 (出荷の新規登録と一括出荷の初期値、指示書の発行の初期値、マニュアルのフォルダの引数と tag) を `isdecimal` にした
(本体の門 11-07 01:1x)。
"""

import pytest
from django.urls import reverse

SUPERSCRIPT_TWO = "²"


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("name", "key"),
    [("shipping:shipment_create", "order_line"), ("workorders:workorder_create", "order_line")],
)
def test_nondecimal_initial_pk_renders_the_form(logged_in, name, key):
    response = logged_in.get(f"{reverse(name)}?{key}={SUPERSCRIPT_TWO}")
    assert response.status_code == 200


@pytest.mark.django_db
def test_nondecimal_bulk_line_does_not_crash(logged_in):
    response = logged_in.get(f"{reverse('shipping:shipment_bulk_create')}?line={SUPERSCRIPT_TWO}&line=1")
    assert response.status_code < 500


@pytest.mark.django_db
def test_nondecimal_manual_folder_is_404_not_500(logged_in):
    response = logged_in.get(f"{reverse('manuals:manual_list')}?folder={SUPERSCRIPT_TWO}")
    assert response.status_code == 404
