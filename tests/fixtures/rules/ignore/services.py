"""黙らせる印の見本。番号つきの印は合い、番号の無い印は合わない。検査が読むだけで、動かさない。"""

from django.db import connection


def count_items():
    with connection.cursor() as cur:  # seizou: ignore[R14] 作り物の見本。番号つきの印
        cur.execute("SELECT COUNT(*) FROM masters_item")
        return cur.fetchone()[0]


def count_partners():
    with connection.cursor() as cur:  # seizou: ignore 番号の無い印は合わない
        cur.execute("SELECT COUNT(*) FROM masters_partner")
        return cur.fetchone()[0]
