"""見張りの道の読み取りの一致率 (N107。精査 A7 の 4 節の 6): 届いた注文で承認して受注にした見張りの読み取りも、一致率に数える。

読み取りの行は intake の extract_to_inbox で作る (本物の API は呼ばない。_ask_claude を作り物の返事に差し替える)。
返事は明細 2 行 = X-100 ×10 単価 120 / X-200 ×5 単価 null (承認すると品目の標準単価)。同じ中身の注文書は 1 行にまとまるので、
2 枚目からはファイルの末尾の字を変える。HTTP は requests.post を「呼んだら落とす」に差し替える。名前は作り物だけ。
"""

from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from seizou.connect import services as connect_services
from seizou.core import dashboard
from seizou.intake import services
from seizou.intake.models import Extraction
from seizou.orders import services as orders_services
from tests import test_connect_inbox_intake, test_intake_watch
from tests.test_intake_extract import PDF_BYTES
from tests.test_intake_watch import UNKNOWN_SENDER, answer_with, upload

# 見張りの試験の fixture を借りる (pytest は module の名前で fixture を見つける)。
# no_real_api・no_http・media は autouse (本物の API と HTTP を呼んだら落とす・上げたファイルは試験の一時のフォルダ)。
# customer = 作り物の得意先 (差出人の名前「作り物の得意先」で当たる)。
no_real_api = test_intake_watch.no_real_api
no_http = test_intake_watch.no_http
media = test_intake_watch.media
with_key = test_intake_watch.with_key
user = test_intake_watch.user
customer = test_intake_watch.customer
# 品番 X-100 と X-200 の作り物の品目 (標準単価つき。単価の無い明細の承認に要る)。
items = test_connect_inbox_intake.items


def test_watch_path_order_is_counted_and_fix_lowers_same(with_key, monkeypatch, client, customer, items, user):
    # 1 枚目 = 差出人の名前が当たった見張りの読み取り。届いた注文で承認して受注にする
    answer_with(monkeypatch, "作り物の得意先")
    matched, _created = services.extract_to_inbox(upload(), user)
    order = connect_services.accept(matched, items, user)
    extraction = Extraction.objects.get(pk=matched.extraction_id)
    assert extraction.status == Extraction.STATUS_INBOX and extraction.order_id is None

    full = {"extractions": 1, "lines": 2, "same": 2, "fixed": 0}
    assert dashboard.reading_match(extraction=extraction) == full
    assert dashboard.reading_match(customer=customer) == full

    # 2 枚目 = 差出人の確認待ち (読み取りの行の得意先は空のまま)。差出人を選んで承認すると、選んだ得意先で数える
    answer_with(monkeypatch, UNKNOWN_SENDER)
    waiting, _created = services.extract_to_inbox(upload(PDF_BYTES + b"% 2\n"), user)
    connect_services.choose_sender(waiting, customer, user)
    connect_services.accept(waiting, items, user)
    assert Extraction.objects.get(pk=waiting.extraction_id).customer_id is None

    # 3 枚目 = 画面の道 (「受注にした」) は今までどおり
    answer_with(monkeypatch, "作り物の得意先")
    screen = services.extract_order(upload(PDF_BYTES + b"% 3\n"), customer, user)
    services.accept_extracted(screen, items, user)

    with CaptureQueriesContext(connection) as captured:
        match = dashboard.reading_match(customer=customer)
    assert len(captured.captured_queries) <= 2
    assert match == {"extractions": 3, "lines": 6, "same": 6, "fixed": 0}

    # 1 枚目の受注の 1 行目の数量を直すと (精査の再現の手順の形)、「そのまま」が 1 行減る
    first, second = order.lines.get(line_no=1), order.lines.get(line_no=2)
    orders_services.update_order(order, {}, [{"id": first.pk, "qty": Decimal("13")}, {"id": second.pk}], user)
    assert dashboard.reading_match(extraction=extraction) == {"extractions": 1, "lines": 2, "same": 1, "fixed": 1}

    client.force_login(user)
    body = client.get(reverse("masters:partner_detail", args=[customer.pk])).content.decode()
    assert "読み取りから受注にした明細: 明細 6 行中 5 行そのまま (人が直したのは 1 行)" in body


def test_inbox_reading_without_order_is_not_counted(with_key, monkeypatch, client, customer, items, user):
    answer_with(monkeypatch, "作り物の得意先")
    received, _created = services.extract_to_inbox(upload(), user)
    rejected, _created = services.extract_to_inbox(upload(PDF_BYTES + b"% 2\n"), user)
    connect_services.reject(rejected, "作り物の断り", user)
    answer_with(monkeypatch, UNKNOWN_SENDER)
    waiting, _created = services.extract_to_inbox(upload(PDF_BYTES + b"% 3\n"), user)
    connect_services.choose_sender(waiting, customer, user)

    # 届いた注文のまま (届いた・断った・差出人を選んだだけ) の読み取りは数えない
    assert Extraction.objects.filter(status=Extraction.STATUS_INBOX).count() == 3
    for extraction in Extraction.objects.all():
        assert dashboard.reading_match(extraction=extraction) is None
    assert dashboard.reading_match(customer=customer) is None
    client.force_login(user)
    body = client.get(reverse("masters:partner_detail", args=[customer.pk])).content.decode()
    assert "読み取りから受注にした明細" not in body

    # 1 枚を承認すると、その 1 枚だけを数える
    connect_services.accept(received, items, user)
    assert dashboard.reading_match(customer=customer) == {"extractions": 1, "lines": 2, "same": 2, "fixed": 0}
    assert dashboard.reading_match(extraction=Extraction.objects.get(pk=waiting.extraction_id)) is None
