"""R13 の違反しない見本 (services の中で表を書く。services.py は R13 の対象の外)。検査が読むだけで、動かさない。"""

from .models import Item


def create_item(part_no, name):
    return Item.objects.create(part_no=part_no, name=name)


def rename_item(item, name):
    item.name = name
    item.save()
    return item


def remove_item(item):
    item.delete()
