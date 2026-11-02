"""会社ごとの項目 (01 の 4 節の拡張の表と 10.3)。

会社のプロジェクトの fields/apps.py の ready() が
    register("masters.Item", "fields.ItemExt", [{"name": "ext01", "label": "材質", "type": "text", ...}, ...])
と呼ぶ。列の名前は答えた順の連番 ext01・ext02…。拡張の表は部品の表に 1 対 1 (OneToOneField。related_name は "ext")。
一覧の土台は in_list の列を自動で足し、入力の土台は欄を自動で足して ext= に分けて services に渡し、検索は searchable の列も見る。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django import forms

TYPES = ("text", "number", "date", "choice", "bool")
EXT_PREFIX = "ext__"
EXT_ACCESSOR = "ext"


@dataclass(frozen=True)
class Column:
    name: str
    label: str
    type: str = "text"
    required: bool = False
    in_list: bool = True
    searchable: bool = False
    choices: tuple = field(default_factory=tuple)

    def form_field(self) -> forms.Field:
        kwargs = {"label": self.label, "required": self.required}
        if self.type == "number":
            return forms.DecimalField(max_digits=15, decimal_places=2, **kwargs)
        if self.type == "date":
            return forms.DateField(**kwargs)
        if self.type == "choice":
            return forms.ChoiceField(choices=[(c, c) for c in self.choices], **kwargs)
        if self.type == "bool":
            return forms.BooleanField(**{**kwargs, "required": False})
        return forms.CharField(max_length=100, **kwargs)


@dataclass(frozen=True)
class Registration:
    base: str
    ext: str
    columns: tuple[Column, ...]


_REGISTRY: dict[str, Registration] = {}


def _label_of(model_or_label) -> str:
    if isinstance(model_or_label, str):
        return model_or_label
    return model_or_label._meta.label


def register(base: str, ext: str, columns) -> Registration:
    cols = []
    for i, item in enumerate(columns, start=1):
        if isinstance(item, Column):
            cols.append(item)
            continue
        spec = dict(item)
        spec.setdefault("name", f"ext{i:02d}")
        spec["type"] = spec.get("type", "text")
        if spec["type"] not in TYPES:
            raise ValueError(f"知らない型: {spec['type']} ({base} の {spec['name']})")
        spec["choices"] = tuple(spec.get("choices") or ())
        cols.append(Column(**spec))
    reg = Registration(base=base, ext=ext, columns=tuple(cols))
    _REGISTRY[base] = reg
    return reg


def clear() -> None:
    _REGISTRY.clear()


def registration_for(model) -> Registration | None:
    return _REGISTRY.get(_label_of(model))


def columns_for(model) -> list[Column]:
    """一覧に出す会社ごとの項目。"""
    reg = registration_for(model)
    return [c for c in reg.columns] if reg else []


def list_columns_for(model) -> list[Column]:
    return [c for c in columns_for(model) if c.in_list]


def form_fields_for(model) -> dict[str, forms.Field]:
    """入力の土台が足す欄。鍵は ext__<列の名前>。"""
    return {EXT_PREFIX + c.name: c.form_field() for c in columns_for(model)}


def search_fields_for(model) -> list[str]:
    """検索に使う会社ごとの項目 (ORM の lookup の形)。"""
    return [f"{EXT_ACCESSOR}__{c.name}" for c in columns_for(model) if c.searchable]


def split_ext(cleaned: dict) -> tuple[dict, dict | None]:
    """form の値を、部品の表の分と拡張の表の分 (ext=) に分ける。"""
    base = {k: v for k, v in cleaned.items() if not k.startswith(EXT_PREFIX)}
    ext = {k[len(EXT_PREFIX):]: v for k, v in cleaned.items() if k.startswith(EXT_PREFIX)}
    return base, (ext or None)


def ext_value(obj, column_name: str):
    """拡張の表の値を読む (無ければ None)。"""
    ext = getattr(obj, EXT_ACCESSOR, None)
    if ext is None:
        return None
    return getattr(ext, column_name, None)


def save_ext(obj, ext: dict | None):
    """拡張の表の 1 行を書く (無ければ作る)。services が部品の表と同じトランザクションで呼ぶ。

    登録 (register) が無いのに ext が来たら ValueError (黙って捨てると会社の項目が消えるため)。
    """
    if not ext:
        return None
    reg = registration_for(obj.__class__)
    if reg is None:
        raise ValueError(
            f"{obj._meta.verbose_name}の会社ごとの項目が登録されていません。"
            "会社のプロジェクトの fields を確かめてください。"
        )
    from django.apps import apps

    ext_model = apps.get_model(reg.ext)
    link_name = next(
        f.name
        for f in ext_model._meta.get_fields()
        if getattr(f, "one_to_one", False) and f.related_model is obj.__class__
    )
    known = {c.name for c in reg.columns}
    unknown = sorted(set(ext) - known)
    if unknown:
        raise ValueError(f"知らない会社ごとの項目 ({'・'.join(unknown)}) があります。")
    row, _created = ext_model.objects.update_or_create(**{link_name: obj}, defaults=ext)
    return row
