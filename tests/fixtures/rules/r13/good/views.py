"""R13 の違反しない見本 (表を書くのは services の関数だけ)。検査が読むだけで、動かさない。

辞書の update、位置の引数のある delete、読むだけの問い合わせ、commit=False の save は違反にしない。
"""

from django.core.cache import cache
from django.shortcuts import redirect, render

from . import services
from .forms import ItemForm
from .models import Item


def item_create(request):
    form = ItemForm(request.POST)
    if form.is_valid():
        draft = form.save(commit=False)
        services.create_item(part_no=draft.part_no, name=draft.name)
        cache.delete("item_count")
    return redirect("masters:item_list")


def item_list(request):
    context = {"items": Item.objects.filter(active=True).order_by("part_no")}
    context.update({"screen_name": "品目の一覧"})
    return render(request, "masters/item_list.html", context)
