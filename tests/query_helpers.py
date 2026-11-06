"""受注の一覧の試験 (tests/test_orders_board*) が共通に使う道具。

受注の一覧を 1 回開いた間の、問い合わせの数を数える。
"""

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse


def count_queries(client):
    with CaptureQueriesContext(connection) as queries:
        response = client.get(reverse("orders:orderline_list"))
    assert response.status_code == 200
    return len(queries), response
