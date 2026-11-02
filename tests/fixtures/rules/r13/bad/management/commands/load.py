"""R13 の違反の見本 (管理コマンドの中で表に直接書く)。検査が読むだけで、動かさない。"""

from django.core.management.base import BaseCommand

from ...models import Item


class Command(BaseCommand):
    help = "作り物の品目を入れる (見本)"

    def handle(self, *args, **options):
        Item.objects.bulk_create([Item(part_no="X-001", name="作り物の品目")])
