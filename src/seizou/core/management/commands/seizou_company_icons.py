"""会社の印 (favicon) を、今のロゴから作り直す (決め 11-06 00:0x。開発の記録 docs/nights/20261106/N173.md)。

開発の記録 docs/nights/20261105/N166.md より前にロゴを上げた置き場には、印 (company/favicon.png と company/apple-touch-icon.png) が無い。
ロゴを上げ直さずに、ロゴのファイルから印のファイルだけを作る (core.services._write_company_icons。設定の画面と同じ作り方)。
表は書かない (会社の行を読むだけ。update_company は呼ばない。会社の行が無くても作らない)。
ロゴが無いときと、ロゴを絵として開けないときは、印を作らずに終わりの番号 2。
"""

from django.core.management.base import BaseCommand, CommandError

from seizou.core import services
from seizou.core.models import Company

#: ロゴが無いときの 1 行
NO_LOGO = "ロゴがありません。設定の「会社の情報」からロゴを上げてください。"


class Command(BaseCommand):
    help = "会社の印 (favicon) を今のロゴから作り直す (表は書かない)。ロゴが無ければ終わりの番号 2。"

    def handle(self, *args, **options):
        row = Company.objects.order_by("pk").first()
        if row is None or not row.logo:
            raise CommandError(NO_LOGO, returncode=2)
        if not services._write_company_icons(row):
            raise CommandError(
                f"印を作れません。ロゴ ({row.logo.name}) を絵として開けません。"
                "設定の「会社の情報」から PNG か JPEG のロゴを上げ直してください。",
                returncode=2,
            )
        self.stdout.write("印を作りました: " + "・".join(services.COMPANY_ICONS))
