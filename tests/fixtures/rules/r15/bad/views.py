"""R15 の違反の見本 (Django の generic の画面を直に継承する)。検査が読むだけで、動かさない。"""

from django.views import generic
from django.views.generic import ListView

from .models import Order


class OrderListView(ListView):
    model = Order


class OrderDetailView(generic.DetailView):
    model = Order
