"""R14 の違反の見本 (cursor)。検査が読むだけで、動かさない。"""

from django.db import connection


def count_items():
    with connection.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM masters_item")
        return cur.fetchone()[0]
