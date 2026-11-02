"""R13 の違反の見本 (form の save の中で表に直接書く)。検査が読むだけで、動かさない。"""

from django import forms

from .models import Item


class ItemForm(forms.ModelForm):
    class Meta:
        model = Item
        fields = ("part_no", "name")

    def save(self, commit=True):
        self.instance.name = self.cleaned_data["name"].strip()
        self.instance.save()
        return self.instance
