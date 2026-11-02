"""R13 の違反の見本 (画面の中で表に直接書く)。検査が読むだけで、動かさない。

R15 に当たらないように、関数の画面で書く。
"""

from django.shortcuts import get_object_or_404, redirect

from .forms import ItemForm
from .models import Item, Order


def item_create(request):
    Item.objects.create(part_no=request.POST["part_no"], name=request.POST["name"])
    return redirect("masters:item_list")


def item_update(request, pk):
    form = ItemForm(request.POST, instance=get_object_or_404(Item, pk=pk))
    if form.is_valid():
        form.save()
    return redirect("masters:item_list")


def item_remove(request, pk):
    obj = get_object_or_404(Item, pk=pk)
    obj.delete()
    return redirect("masters:item_list")


def order_close(request, pk):
    Order.objects.filter(pk=pk).update(status="closed")
    return redirect("orders:order_list")
