"""詳細の土台 (BaseDetailView)・関係する物のタブ (related_tab)・小窓の土台 (BaseModalView) (core.views の package)。"""

from __future__ import annotations


from django.db import models
from django.http import HttpResponseRedirect
from django.template.loader import select_template
from django.utils.cache import patch_vary_headers
from django.views.generic import DetailView

from seizou.core import access
from seizou.core import fields as company_fields
from seizou.core import history

from .base import ScreenMixin, display_value, file_info, file_url_for, hidden_detail_fields, is_master, model_url, screen_name_of


class BaseDetailView(ScreenMixin, DetailView):
    """詳細の土台。1 件と、関係する物の一覧 (tabs = ((見出し, related_name), ...)。3 つ目の FK の名前の並びは任意。related_tab)。

    履歴のタブと「最後に直した」の 1 行は core.history (作者の決め)。model に印 (keeps_history か history_owner) が
    あるときだけ context の history に core.history.timeline を置く (tabs には入れない。開くまで問い合わせない)。
    model に印 keeps_report_copy があるときだけ、context の report_copies に帳票の写し (core.services.report_copies の並び) を置く
    (「発行の時の写し」の枠。core/_report_copies.html)。
    """

    tabs: tuple = ()
    opens_by_department = False
    template_name = "core/detail.html"

    def field_items(self, obj) -> list[dict]:
        """項目の一覧。1 項目 = {"label", "value", "master", "file", "money", "boolean"} (file はファイルの項目だけ、file_info の形。
        money は金額の項目なら真で、テンプレートが yen_if で ¥ を付ける。テンプレートの filter の引数に使うので、全部の行が鍵を持つ。
        boolean は値が真偽なら真で、テンプレートが bool_mark で印にする。作者の決め)。
        url は、マスターでない相手に詳細の画面があり、部署でその部品を使える人のときだけ、その詳細の URL (無ければ None。全部の行が鍵を持つ)。
        明細は親の伝票 (model の detail_link_target)。テンプレートが link にする (record_url。作者の決め)。"""
        items = []
        hidden = hidden_detail_fields(obj)
        for f in obj._meta.concrete_fields:
            if f.primary_key or f.name in ("password",) or f.name in hidden:
                continue
            if access.is_money_name(f.name) and not access.can_see_money(self.request.user):
                continue  # 金額の閲覧が無い人には、金額の項目を項目ごと出さない (「—」も出さない。開発の記録 docs/nights/20261105/N155.md)
            value = display_value(obj, f.name)
            is_file = isinstance(f, models.FileField)
            items.append(
                {
                    "label": str(f.verbose_name),
                    "value": "" if is_file else value,
                    "boolean": isinstance(value, bool),
                    "money": access.is_money_name(f.name),
                    "master": value if is_master(value) else None,
                    "file": file_info(value, file_url_for(obj, f.name)) if is_file else None,
                    "url": None if is_file else self.record_url(value),
                }
            )
            # 受注の明細のような相手の表が部品を持つなら、部品の行を足して小窓で開けるようにする (作者の決め)
            linked = getattr(value, "item", None) if value is not None and not is_master(value) else None
            if is_master(linked) and not any(c.name == "item" for c in obj._meta.concrete_fields):
                items.append(
                    {"label": "部品", "value": linked, "boolean": False, "master": linked, "file": None, "money": False, "url": None}
                )
        for col in company_fields.columns_for(obj.__class__):
            value = company_fields.ext_value(obj, col.name)
            items.append(
                {"label": col.label, "value": value, "boolean": isinstance(value, bool), "master": None, "file": None, "money": False, "url": None}
            )
        return items

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = context["object"]
        fields = self.field_items(obj)
        context["fields"] = fields
        context["files"] = [item for item in fields if item["file"]]
        context["tabs"] = [
            self.tab_for(obj, label, related, names) for label, related, names in _tab_specs(self.tabs) if hasattr(obj, related)
        ]
        # 右の列のある詳細は、件数のある最初のタブで開く (無ければ 1 つ目。選ばないと何も出ないため = 開発の記録 docs/nights/20261105/N150.md。作者の決め)
        chosen = next((tab for tab in context["tabs"] if tab["count"] >= 1), context["tabs"][0] if context["tabs"] else None)
        for tab in context["tabs"]:
            tab["selected"] = tab is chosen
        context["edit_url"] = model_url(obj.__class__, "update", obj.pk)
        if context["edit_url"] and not access.can_write_model(self.request.user, obj.__class__, "update"):
            context["edit_url"] = None
        list_url = model_url(obj.__class__, "list")
        context["list_name"] = screen_name_of(list_url)
        # 部署で一覧の部品を開けない人には、パンくずの一覧の名前を link にしない (字だけ。押すと 403 になる入口を出さない = 作者の決め。
        # 判定は左のメニューと同じ access.can_open。記録は書かない)。詳細は部署を見ないので、部署の外の人も開ける。
        context["list_url"] = list_url if access.can_open(self.request.user, obj._meta.app_label, record=False) else None
        context["status"] = self.status_for(obj)
        context["history"] = history.timeline(obj, self.request.user) if history.keeps_history(obj.__class__) else None
        # 帳票の写し (R34) は、写しを持つ model (class 属性 keeps_report_copy) の詳細でだけ読む (ほかの詳細の問い合わせを増やさない)。
        # 枠 (core/_report_copies.html) を出すかは、テンプレートが行の有無と金額の閲覧 (seizou.hide_money) で決める。小窓には置かない。
        if getattr(obj, "keeps_report_copy", False):
            from seizou.core.services import report_copies

            context["report_copies"] = list(report_copies(obj))
        return context

    def tab_for(self, obj, label: str, related: str, select_related=()) -> dict:
        """関係する物の 1 つのタブ (related_tab)。自分の詳細の画面が無い行 (明細) は、親の記録の詳細へ移る link にする。

        行き先の決め方は、基本の表の値と同じ record_url (model の detail_link_target = 親の受注・発注。部署でその部品を開けない人には
        link を出さない)。自分の詳細の画面がある行の link は、今までどおり related_tab が付けた物のまま変えない。
        親を読む問い合わせは足さない前提 (タブの行の字が親を読むので、行は親と一緒に読んである。例: 指示書の「協力企業」のタブ)。
        """
        tab = related_tab(obj, label, related, select_related)
        for row in tab["rows"]:
            if row["url"] is None:
                row["url"] = self.record_url(row["obj"])
        return tab

    def status_for(self, obj) -> str | None:
        """見出しの横の状態の印。choices を持つ status の項目があればその表示名、無ければ None。"""
        field = next((f for f in obj._meta.concrete_fields if f.name == "status" and f.choices), None)
        return display_value(obj, "status") if field is not None else None

    def record_url(self, value) -> str | None:
        """基本の表の値を押した時に移る詳細の URL (field_items の url)。移れなければ None (テンプレートは今の字のまま出す)。

        マスターでない相手 (_meta を持つ物) で、その詳細の画面が引けるときだけ URL。明細は model の detail_link_target
        (親の受注・発注。明細に詳細の画面が無いため) を読む。core は受注と発注の model を知らない (detail_hidden_fields と同じ約束)。
        マスターは小窓 (master_link)、人と ContentType は詳細の URL が引けないので None。
        URL が引けたときだけ、部署でその部品を開けるかを access.can_open の record=False で見る (関わる記録の link の
        orders/timeline.py の _linked_url と同じ判定。log の時に、開いていない画面を開いたと記録に書かない)。
        URL の無い値では self.request を読まない (request の無い BaseDetailView() でも field_items が通る)。
        作者の決め。
        """
        if value is None or getattr(value, "_meta", None) is None or is_master(value):
            return None
        target = getattr(value, "detail_link_target", value)
        url = model_url(target.__class__, "detail", target.pk)
        if url is None or not access.can_open(self.request.user, target._meta.app_label, record=False):
            return None
        return url


def _tab_specs(tabs) -> list[tuple]:
    """tabs の要素を (見出し, related_name, select_related の名前の並び) にそろえる (詳細と小窓が読む)。

    要素は 2 つ組 (見出し, related_name) か、3 つ組 (見出し, related_name, ("FK の名前", ...))。2 つ組の名前の並びは空。
    """
    return [(tab[0], tab[1], tuple(tab[2]) if len(tab) > 2 else ()) for tab in tabs]


def related_tab(obj, label: str, related: str, select_related=()) -> dict:
    """関係する物の 1 つのタブ (詳細と小窓)。{"label", "related", "count", "rows"}。行は最大 100 件、1 行 = {"obj", "url"}。

    select_related は、行の __str__ が読む FK の名前 (tabs の 3 つ目)。名前があれば行と相手を 1 回の SELECT で引く
    (タブの行の数だけ SELECT が出ないため。開発の記録 docs/nights/20261106/N174.md)。親の get_queryset が prefetch した related には書かない (取り直しになる)。
    """
    manager = getattr(obj, related)
    queryset = manager.all()
    if select_related:
        queryset = queryset.select_related(*select_related)
    rows = [{"obj": row, "url": model_url(row.__class__, "detail", row.pk)} for row in queryset[:100]]
    return {"label": label, "related": related, "count": manager.count(), "rows": rows}


class BaseModalView(ScreenMixin, DetailView):
    """小窓の土台。見るだけと、詳細への移動のボタンと、編集の権限のある人には編集の link。template は <app>/_modal.html か core/_modal.html。

    項目 (先頭の modal_fields 個) の後に、会社ごとの項目 (一覧に出す物。一覧の列と同じ取り方) を見出しと値で並べる。
    1 項目 = {"label", "value", "money", "boolean"} (boolean は値が真偽なら真で、テンプレートが bool_mark で印にする。作者の決め)。
    tabs = ((見出し, related_name), ...) があれば、関係する物の一覧も出す (詳細のタブと同じ形。3 つ目の FK の名前の並びも同じ)。
    htmx でない要求 (link を新しいタブで開いた時・画面の目録や履歴や AI に質問の窓から小窓の道を直に開いた時) には、枠の無い中身でなく詳細の画面へ移す。
    詳細の画面が無い表 (計画の詳細の指示書の工程) は、土台の画面の中に小窓と同じ中身を置いた画面の全体 (core/modal_page.html) を返す
    (決まりの番号の説明 rule_help と同じ形。作者の決め)。同じ URL で中身と全体を返すので Vary: HX-Request を付ける。
    """

    modal_fields: int = 8
    tabs: tuple = ()
    opens_by_department = False
    #: 画面の全体の形 (htmx でない要求で、詳細の画面が無いとき)。小窓のテンプレートを whole_page で include する
    whole_page_template = "core/modal_page.html"

    def get(self, request, *args, **kwargs):
        self.whole_page = not request.headers.get("HX-Request")
        self.object = self.get_object()
        url = model_url(self.object.__class__, "detail", self.object.pk) if self.whole_page else None
        if url:
            response = HttpResponseRedirect(url)
        else:
            response = self.render_to_response(self.get_context_data(object=self.object))
        patch_vary_headers(response, ("HX-Request",))
        return response

    def modal_template_names(self) -> list[str]:
        """小窓の中身のテンプレートの候補 (template_name か、<app>/_modal.html → core/_modal.html の順)。"""
        if self.template_name:
            return [self.template_name]
        return [f"{self.model._meta.app_label}/_modal.html", "core/_modal.html"]

    def get_template_names(self):
        if getattr(self, "whole_page", False):
            return [self.whole_page_template]
        return self.modal_template_names()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        obj = context["object"]
        if getattr(self, "whole_page", False):
            # 画面の全体は小窓と同じテンプレートを include する (字を 2 か所に書かない)。候補の並びから、実在する 1 つの名前に決めて渡す
            context["whole_page"] = True
            context["modal_template"] = select_template(self.modal_template_names()).template.name
        items = []
        hidden = hidden_detail_fields(obj)
        for f in obj._meta.concrete_fields:
            if f.primary_key or f.name in hidden:
                continue
            if access.is_money_name(f.name) and not access.can_see_money(self.request.user):
                continue  # 金額の閲覧が無い人には、金額の項目を項目ごと出さない (「—」も出さない。開発の記録 docs/nights/20261105/N155.md)
            value = display_value(obj, f.name)
            if isinstance(f, models.FileField):
                info = file_info(value)
                value = info["name"] if info else ""
            items.append(
                {
                    "label": str(f.verbose_name),
                    "value": value,
                    "money": access.is_money_name(f.name),
                    "boolean": isinstance(value, bool),
                }
            )
        ext_items = []
        for col in company_fields.list_columns_for(obj.__class__):
            value = company_fields.ext_value(obj, col.name)
            ext_items.append({"label": col.label, "value": value, "money": False, "boolean": isinstance(value, bool)})
        context["fields"] = items[: self.modal_fields] + ext_items
        context["tabs"] = [
            related_tab(obj, label, related, names) for label, related, names in _tab_specs(self.tabs) if hasattr(obj, related)
        ]
        context["detail_url"] = model_url(obj.__class__, "detail", obj.pk)
        context["edit_url"] = model_url(obj.__class__, "update", obj.pk)
        if context["edit_url"] and not access.can_write_model(self.request.user, obj.__class__, "update"):
            context["edit_url"] = None
        return context
