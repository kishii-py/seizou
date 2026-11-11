"""一覧の「CSV 取り込み」から入った取り込みの後は、開いていた一覧へ戻る (ダッシュボードへ移らない)。

入口 (部品マスターの一覧と取引先の一覧の「CSV 取り込み」) は、開いた一覧の道を ?next= で渡す。取り込みの画面は model を持たないので、
保存の後の移り先は ?next= → / の順で決まる (土台の get_success_url_for)。直す前は入口が ?next= を渡さず、取り込んだ後に
ダッシュボードへ移っていた (済んだ知らせは「部品マスターで確かめる」と言うのに、一覧が開かない)。
ダッシュボードの「最初にすること」からの入口は ?next= を渡さず、取り込んだ後はダッシュボードへ戻る (今のまま)。
会社と品番は作り物。
"""

from urllib.parse import quote

import pytest
from django.urls import reverse

from tests.test_masters_csv_import import csv_bytes, post_csv

CASES = [
    ("masters:item_list", "masters:item_import", "自社品番,品名", "P-9001,作り物の軸"),
    ("masters:partner_list", "masters:partner_import", "取引先コード,名前,取引先の種類", "C901,作り物の得意先,得意先"),
]


@pytest.mark.django_db
@pytest.mark.parametrize(("list_name", "import_name", "_header", "_row"), CASES)
def test_list_button_passes_the_opened_list_as_next(client_logged_in, list_name, import_name, _header, _row):
    opened = f"{reverse(list_name)}?q=P-1"
    body = client_logged_in.get(opened).content.decode()
    # 開いた一覧の道 (絞りの字も) を、そのまま ?next= に渡す
    assert f'href="{reverse(import_name)}?next={quote(opened, safe="/")}"' in body


@pytest.mark.django_db
@pytest.mark.parametrize(("list_name", "import_name", "header", "row"), CASES)
def test_import_goes_back_to_the_list_it_was_opened_from(client_logged_in, list_name, import_name, header, row):
    from django.core.files.uploadedfile import SimpleUploadedFile

    opened = f"{reverse(list_name)}?q=P-1"
    upload = SimpleUploadedFile("import.csv", csv_bytes(header, row), content_type="text/csv")
    response = client_logged_in.post(
        f"{reverse(import_name)}?next={quote(opened, safe='/')}", {"encoding": "utf-8-sig", "file": upload}
    )
    assert response.status_code == 302
    assert response.url == opened


@pytest.mark.django_db
@pytest.mark.parametrize(("_list_name", "import_name", "header", "row"), CASES)
def test_import_without_next_still_goes_to_the_dashboard(client_logged_in, _list_name, import_name, header, row):
    """ダッシュボードの「最初にすること」からの入口 (?next= 無し) は、今のままダッシュボードへ戻る。"""
    response = post_csv(client_logged_in, import_name, csv_bytes(header, row))
    assert response.status_code == 302
    assert response.url == "/"
