"""R13 の違反の見本 (テンプレートタグの中で表に直接書く)。検査が読むだけで、動かさない。"""

from django import template

from ..models import Item

register = template.Library()


@register.simple_tag
def item_or_new(part_no):
    item, _ = Item.objects.get_or_create(part_no=part_no, defaults={"name": "作り物の品目"})
    return item
