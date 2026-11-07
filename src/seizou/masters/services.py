"""masters の公開する関数 (01 の 10.4)。ほかの部品と画面はこの関数だけを通してマスターを書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は受けるが、None 以外が来たら NotImplementedError を投げる。
  黙って捨てると会社の項目が消えるため。保存は core の fields ができてから足す。
- masters の決まり (品番の一意・取引先の種類の 3 値・請求の仕方の 2 値・支払の月の 4 値・支払日の 1〜31・取引先 × 取引先の品番の一意 (品番のある行だけ。品番の空の行は取引先と部品ごとに 1 つ)) に当たったら、
  日本語の文の ValueError を投げる。中心の決まり (RULES.md の番号) は masters に無い。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 部品の図面 (drawing) に新しいファイルが来たら、保存の前に 1 回だけ読んで表示用の PNG を作る (05 の 7 節)。
  作れなかったときも部品と元のファイルは保存し、理由の 1 文を drawing_preview_error に残す (例外にしない)。
- 部品の図面を新しいファイルに差し替えたら、前の図面を版 (ItemDrawingVersion) に残す (ファイルは写さず名前の字を持つ)。
  版に戻すのは restore_drawing_version。
- 使用停止 (active が偽。N135): 消さずに候補から外す。候補の母集団は active_items・active_partners、
  新しい受注と発注を作る所 (orders・purchasing の services) の守りは refuse_inactive。今までの記録はそのまま。
- 部品の標準の工程の並び (ItemProcess) を書くのは set_item_processes だけ。画面の足す・順を直す・外す (add_item_processes・move_item_process・remove_item_process) も、直した並びを作ってこれを呼ぶ (変更の履歴を 1 か所で残すため。決め C19-3・HIST-1)。
- 選ぶ小窓の行 (effective_prices・price_choices) と発注の工程の最初の値 (default_process・with_default_process) は読むだけ (C100)。
"""

from __future__ import annotations

import io
import os
import threading
import unicodedata
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.files.base import ContentFile, File
from django.db import transaction
from django.db.models import Max, Q
from django.db.models.fields.files import FieldFile
from PIL import Image, ImageFile, ImageSequence

from seizou.core import access, calendar
from seizou.core.services import company_or_none
from seizou.core.templatetags.seizou_tags import comma

from .drawings import page_name
from .models import Item, ItemPrice, Partner, PartnerItem
from .models import Process
from .models import ItemProcess
from .models import Holiday
from .models import PRICE_BASIS_CHOICES, PRICE_BASIS_LUMP, PRICE_BASIS_UNIT, price_basis_text  # noqa: F401  (orders・purchasing・connect が読む)
from dataclasses import dataclass
from django.db.models import Case, Count, IntegerField, OuterRef, Subquery, When
from django.utils import timezone

_ITEM_KEYS = frozenset(
    {"part_no", "name", "drawing_no", "drawing", "unit", "standard_price", "note", "active"}
)
_PARTNER_KEYS = frozenset(
    {
        "code",
        "name",
        "short_name",
        "role",
        "billing_method",
        "closing_day",
        "payment_month",
        "payment_day",
        "rounding",
        "tax_mode",
        "tax_rounding",
        "note",
        "active",
    }
)
_PROCESS_KEYS = frozenset({"code", "name", "is_outsourced", "sort_order", "use_in_plan", "active"})
_PARTNER_ITEM_KEYS = frozenset({"partner", "item", "partner_part_no", "partner_item_name"})
_ROLES = frozenset(value for value, _label in Partner.ROLE_CHOICES)
_BILLING_METHODS = frozenset(value for value, _label in Partner.BILLING_METHOD_CHOICES)
_PAYMENT_MONTHS = frozenset(value for value, _label in Partner.PAYMENT_MONTH_CHOICES)
_PAYMENT_DAY_RANGE = range(1, 32)  # 1〜31 (31 は月末)
_ROUNDING_MODES = {
    "round": ROUND_HALF_UP,
    "ceil": ROUND_CEILING,
    "floor": ROUND_FLOOR,
}


def _save_ext(obj, ext):
    """会社ごとの項目 (ext) を拡張の表に書く。登録が無ければ core の fields が ValueError を投げる (黙って捨てない)。"""
    if ext is not None:
        from seizou.core import fields

        fields.save_ext(obj, ext)


def _refuse_unknown_keys(data, allowed, table_name):
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(
            f"保存できません。知らない項目 ({'・'.join(unknown)}) があります。"
            f"{table_name}の列の名前だけを渡してください。"
        )


def _refuse_duplicate_part_no(part_no, *, exclude_pk=None):
    others = Item.objects.filter(part_no=part_no)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        raise ValueError(f"保存できません。品番 {part_no} は既にあります。別の品番にしてください。")


def _refuse_duplicate_partner_code(code, *, exclude_pk=None):
    others = Partner.objects.filter(code=code)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        raise ValueError(f"保存できません。取引先コード {code} は既にあります。別のコードにしてください。")


def _refuse_unknown_role(role):
    if role not in _ROLES:
        raise ValueError(
            f"保存できません。取引先の種類 {role!r} は使えません。"
            "得意先 (customer)・協力企業 (subcontractor)・両方 (both) のどれかにしてください。"
        )


def _refuse_unknown_billing_method(method):
    if method not in _BILLING_METHODS:
        raise ValueError(
            f"保存できません。請求の仕方 {method!r} は使えません。"
            "締日でまとめる (closing)・都度 (per_delivery) のどちらかにしてください。"
        )


def _payment_terms(data):
    """支払の月と支払日を見て、空を表の空の値 (支払の月は ""、支払日は None) にそろえた data を返す。

    支払の月は 当日 (same_day)・当月 (current)・翌月 (next)・翌々月 (after_next) か空。支払日は 1〜31 の整数か空。外れは ValueError。
    当日のとき支払日は使わないが、入っていても断らない (表にはそのまま残す)。
    """
    data = dict(data)
    if "payment_month" in data:
        month = data["payment_month"] or ""
        if month and month not in _PAYMENT_MONTHS:
            raise ValueError(
                f"保存できません。支払の月 {month!r} は使えません。"
                "当日 (same_day)・当月 (current)・翌月 (next)・翌々月 (after_next) のどれかか、空にしてください。"
            )
        data["payment_month"] = month
    if "payment_day" in data:
        day = data["payment_day"]
        if day == "":
            day = None
        if day is not None and (isinstance(day, bool) or not isinstance(day, int) or day not in _PAYMENT_DAY_RANGE):
            raise ValueError(f"保存できません。支払日 {day!r} は使えません。1〜31 の整数か空にしてください (月末は 31)。")
        data["payment_day"] = day
    return data


def _refuse_duplicate_process_code(code, *, exclude_pk=None):
    others = Process.objects.filter(code=code)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        raise ValueError(f"保存できません。工程コード {code} は既にあります。別のコードにしてください。")


#: 図面の画素の上限。A0 を 400dpi で読んだ絵 (約 2.5 億画素) が入る大きさ (00 の案)。
DRAWING_MAX_PIXELS = 300_000_000
#: 表示用の PNG の長い辺の上限。大きい絵は縮める (原寸は元のファイルの link)。
PREVIEW_MAX_SIDE = 2000
#: 表示用の PNG を作るページの上限 (1 ページ目を含む)。複数ページの TIFF の超えた分は作らない。
DRAWING_MAX_PAGES = 20
#: Pillow で読む図面の拡張子 (大文字も)。ほかの形 (PDF など) は読まずに link だけ。
DRAWING_IMAGE_SUFFIXES = (".tif", ".tiff", ".png", ".jpg", ".jpeg")
#: 表示用の絵を作れなかったときの文 (本体の決めの字)。Pillow の例外の文は足さない。
DRAWING_BROKEN_MESSAGE = "図面の表示用の絵は作れませんでした (ファイルが壊れています)。元のファイルは保存してあります。"
DRAWING_TOO_LARGE_MESSAGE = (
    "図面の表示用の絵は作れませんでした (絵が大きすぎます。{width}×{height} 画素)。"
    "元のファイルは保存してあります。解像度を下げて付け直すと絵を作れます。"
)
# PNG に保存できる形。白黒 (1) は L にしてから縮める (そのまま縮めると細い線が消える)。
_PREVIEW_MODES = ("L", "RGB", "RGBA")
# Pillow の上限 (Image.MAX_IMAGE_PIXELS) と、途中で切れた絵を読むか (ImageFile.LOAD_TRUNCATED_IMAGES) は
# module 全体の値なので、変えて戻す間を 1 つずつにする。
_PIXEL_LIMIT_LOCK = threading.Lock()


def _is_new_upload(value):
    """新しく上げたファイルか。Django の File で、表に既にある FieldFile でない物 (画面の入力では UploadedFile)。"""
    return isinstance(value, File) and not isinstance(value, FieldFile)


def _drawing_preview(file):
    """新しく上げた図面を読み、(表示用の PNG の bytes か None, 誤りの文か "", 2 ページ目から後の PNG の bytes の並び) を返す。
    例外を外に出さない。

    拡張子が DRAWING_IMAGE_SUFFIXES でなければ読まない。読んだ中身が PNG・JPEG ならそのまま絵で出すので None。
    ほか (TIFF など) は 1 ページ目を PNG にし、長い辺を PREVIEW_MAX_SIDE まで縮める。複数ページなら 2 ページ目から後
    (DRAWING_MAX_PAGES まで) も同じ形の PNG にする。
    壊れている・大きすぎるときは None と誤りの文。読んだ後は (誤りのときも) 読む位置を先頭に戻す
    (戻さないと元のファイルの保存が空になる)。
    """
    if not (getattr(file, "name", "") or "").lower().endswith(DRAWING_IMAGE_SUFFIXES):
        return None, "", []
    try:
        file.seek(0)
        with _PIXEL_LIMIT_LOCK:
            # Pillow の既定の上限では A0 の図面が読めないので、変換の間だけ外し、上限は DRAWING_MAX_PIXELS で見る。
            # 帳票の PDF の道具 (WeasyPrint) は import のときに途中で切れた絵も読む設定にするので、
            # 変換の間だけ切れた絵を誤りにする (そのままだと壊れた図面に知らせが出ない)。
            saved_limit = Image.MAX_IMAGE_PIXELS
            saved_truncated = ImageFile.LOAD_TRUNCATED_IMAGES
            Image.MAX_IMAGE_PIXELS = None
            ImageFile.LOAD_TRUNCATED_IMAGES = False
            try:
                return _read_drawing(file)
            finally:
                Image.MAX_IMAGE_PIXELS = saved_limit
                ImageFile.LOAD_TRUNCATED_IMAGES = saved_truncated
    # 壊れた中身で Pillow が出す例外 (UnidentifiedImageError・OSError・SyntaxError・ValueError・EOFError・
    # DecompressionBombError など) は全部、壊れているの文にする。部品の保存を止めず、例外の文も出さない。
    except Exception:  # noqa: BLE001
        return None, DRAWING_BROKEN_MESSAGE, []
    finally:
        file.seek(0)


def _read_drawing(file):
    """_drawing_preview の中身。Pillow の上限を外した中で呼ぶ。"""
    with Image.open(file) as image:
        width, height = image.size
        if width * height > DRAWING_MAX_PIXELS:
            return None, DRAWING_TOO_LARGE_MESSAGE.format(width=width, height=height), []
        image.load()  # Image.open は頭だけ読む。壊れた中身はここで例外になる。複数ページは開いたときの 1 枚目
        if image.format in ("PNG", "JPEG"):
            return None, "", []
        first = _page_png(image)
        return first, "", _later_pages(image)


def _page_png(page):
    """読んだ 1 ページを表示用の PNG の bytes にする (白黒は L に、長い辺を PREVIEW_MAX_SIDE まで縮める)。"""
    preview = page
    if preview.mode == "1":
        preview = preview.convert("L")
    elif preview.mode not in _PREVIEW_MODES:
        has_alpha = "A" in preview.getbands() or "transparency" in preview.info
        preview = preview.convert("RGBA" if has_alpha else "RGB")
    if max(preview.size) > PREVIEW_MAX_SIDE:
        preview.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE), Image.Resampling.LANCZOS)
    buffer = io.BytesIO()
    preview.save(buffer, format="PNG")
    return buffer.getvalue()


def _later_pages(image):
    """複数ページの絵の 2 ページ目から後 (DRAWING_MAX_PAGES まで) の PNG の bytes の並び。

    読めない・大きすぎるページがあれば、そのページから後は作らない (誤りの文にしない。1 ページ目は見られる)。
    """
    pages = []
    for index, page in enumerate(ImageSequence.Iterator(image)):
        if index == 0:
            continue
        if index >= DRAWING_MAX_PAGES:
            break
        try:
            width, height = page.size
            if width * height > DRAWING_MAX_PIXELS:
                break
            page.load()
            pages.append(_page_png(page))
        except Exception:  # noqa: BLE001
            break
    return pages


def _with_drawing_preview(data):
    """data の図面 (drawing) を見て、表示用 (drawing_preview)・誤り (drawing_preview_error)・ページ数
    (drawing_page_count) を足した data と、2 ページ目から後の PNG の bytes の並びを返す。

    読むのは表と置き場に書く前。足した 3 つは部品の行と元のファイルと一緒に 1 回で書く。
    2 ページ目から後の PNG は、名前が表示用の保存の後に決まるので、呼ぶ側が保存の後に _save_preview_pages で書く。
    - 新しく上げたファイル: 読んで、表示用の PNG (作れたとき) と誤りの文 (作れなかったとき) を足す。ページ数は 1 にし直す。
    - 今の FieldFile (選び直していない) と、鍵が無いとき: data のまま (4 つとも変えない)。
    - False・None・空 (入力の画面の「消す」): 図面と 2 つを空の字にし、ページ数を 1 にする。
    """
    if "drawing" not in data:
        return data, []
    drawing = data["drawing"]
    if _is_new_upload(drawing):
        png, error, pages = _drawing_preview(drawing)
        preview = ""
        if png is not None:
            stem = os.path.splitext(os.path.basename(drawing.name))[0]
            preview = ContentFile(png, name=f"{stem}.png")
        else:
            pages = []
        extra = {"drawing_preview": preview, "drawing_preview_error": error, "drawing_page_count": 1}
        return {**data, **extra}, pages
    if isinstance(drawing, FieldFile):
        return data, []
    if not drawing:
        return {**data, "drawing": "", "drawing_preview": "", "drawing_preview_error": "", "drawing_page_count": 1}, []
    return data, []


def _save_preview_pages(item, pages):
    """表示用の 2 ページ目から後の PNG を置き場に書き、部品のページ数 (作れたページの数) を書く。

    名前は保存した表示用の名前から決める (<同じフォルダ>/<stem>-p<n>.png)。その名前が置き場に既にある
    (ほかの図面の物) ときは、そのページから後を作らない (ほかの図面のファイルを指さないため)。
    """
    if not pages or not item.drawing_preview:
        return
    storage = item.drawing_preview.storage
    count = 1
    for number, png in enumerate(pages, start=2):
        name = page_name(item.drawing_preview.name, number)
        if storage.exists(name) or storage.save(name, ContentFile(png)) != name:
            break
        count = number
    if count != item.drawing_page_count:
        item.drawing_page_count = count
        item.save(update_fields=["drawing_page_count"])


def create_item(data, user, *, ext=None):
    """品目を作る。data は Item の列の名前を鍵にした辞書 (part_no と name は必須)。

    図面に新しいファイルが来たら、表示用の PNG を作る (作れなければ理由を drawing_preview_error に残し、部品は保存する)。
    """
    _refuse_unknown_keys(data, _ITEM_KEYS, "部品")
    data, pages = _with_drawing_preview(data)
    with transaction.atomic():
        _refuse_duplicate_part_no(data["part_no"])
        item = Item.objects.create(**data)
        _save_preview_pages(item, pages)
        _save_ext(item, ext)
        return item


def update_item(item, data, user, *, ext=None):
    """data にある鍵だけを書き換える。品番を変えるときも一意を見る。

    図面は、新しいファイルなら元・表示用・誤りの 3 つを書き換え、「消す」(False・None・空) なら 3 つとも空にし、
    今のファイル (FieldFile) か鍵が無ければ 3 つとも変えない。差し替え・外した古いファイルは置き場に残す。
    """
    _refuse_unknown_keys(data, _ITEM_KEYS, "部品")
    data, pages = _with_drawing_preview(data)
    with transaction.atomic():
        if "part_no" in data:
            _refuse_duplicate_part_no(data["part_no"], exclude_pk=item.pk)
        if _is_new_upload(data.get("drawing")):
            _keep_drawing_as_version(item)
        for key, value in data.items():
            setattr(item, key, value)
        item.save()
        _save_preview_pages(item, pages)
        _save_ext(item, ext)
        return item


def _keep_drawing_as_version(item, *, note=""):
    """部品の今の図面を版の行に残す (差し替えと版に戻すの前)。今の図面が無ければ何もせず None。

    呼ぶ側の transaction.atomic の中で呼ぶ。部品の行を select_for_update で取り直し、表にある 4 つ
    (図面の名前の字・表示用の名前の字・誤りの文・ページ数) を写す。ファイルは写さない (名前の字を入れる)。
    版の番号は、この部品の版の番号の最大 + 1。
    """
    current = Item.objects.select_for_update().get(pk=item.pk)
    if not current.drawing:
        return None
    last = current.drawing_versions.aggregate(last=Max("version_no"))["last"] or 0
    return current.drawing_versions.create(
        version_no=last + 1,
        drawing=current.drawing.name,
        drawing_preview=current.drawing_preview.name or "",
        drawing_preview_error=current.drawing_preview_error,
        page_count=current.drawing_page_count,
        note=note,
    )


def restore_drawing_version(item, version, user, *, note=""):
    """選んだ版の図面を部品の今の図面に戻す。今の図面は新しい版として残す (note はその版の備考)。

    写すのは版の 4 つ (図面と表示用の名前の字・誤りの文・ページ数)。表示用の PNG は作り直さず、ファイルも写さない。
    選んだ版の行は消さない。今の図面が無い部品は版を作らずに戻す。別の部品の版は、何も変えずに ValueError。
    返す物は部品。user は受けるだけ。
    """
    if version.item_id != item.pk:
        raise ValueError(f"保存できません。版 {version.version_no} はこの部品の版ではありません。この部品の版を選んでください。")
    with transaction.atomic():
        _keep_drawing_as_version(item, note=note)
        item.drawing = version.drawing.name
        item.drawing_preview = version.drawing_preview.name or ""
        item.drawing_preview_error = version.drawing_preview_error
        item.drawing_page_count = version.page_count
        item.save(update_fields=["drawing", "drawing_preview", "drawing_preview_error", "drawing_page_count"])
        return item


def create_partner(data, user, *, ext=None):
    """取引先を作る。取引先の種類は customer・subcontractor・both のどれか。取引先コードは一意。

    請求の仕方は closing (締日でまとめる)・per_delivery (都度) のどちらかで、渡さなければ closing。
    支払の月は same_day・current・next・after_next か空、支払日は 1〜31 の整数か空 (31 は月末)。
    """
    _refuse_unknown_keys(data, _PARTNER_KEYS, "取引先")
    _refuse_unknown_role(data.get("role"))
    if "billing_method" in data:
        _refuse_unknown_billing_method(data["billing_method"])
    data = _payment_terms(data)
    with transaction.atomic():
        _refuse_duplicate_partner_code(data["code"])
        partner = Partner.objects.create(**data)
        _save_ext(partner, ext)
        return partner


def update_partner(partner, data, user, *, ext=None):
    """data にある鍵だけを書き換える。取引先の種類・請求の仕方・取引先コード・支払の条件は create_partner と同じ決まりで見る。"""
    _refuse_unknown_keys(data, _PARTNER_KEYS, "取引先")
    if "role" in data:
        _refuse_unknown_role(data["role"])
    if "billing_method" in data:
        _refuse_unknown_billing_method(data["billing_method"])
    data = _payment_terms(data)
    with transaction.atomic():
        if "code" in data:
            _refuse_duplicate_partner_code(data["code"], exclude_pk=partner.pk)
        for key, value in data.items():
            setattr(partner, key, value)
        partner.save()
        _save_ext(partner, ext)
        return partner


def create_process(data, user):
    """工程を作る。工程コードは一意。data の鍵は code・name・is_outsourced・sort_order・use_in_plan・active。"""
    _refuse_unknown_keys(data, _PROCESS_KEYS, "工程")
    with transaction.atomic():
        _refuse_duplicate_process_code(data["code"])
        return Process.objects.create(**data)


def update_process(process, data, user):
    """data にある鍵だけを書き換える。工程コードを変えるときも一意を見る。"""
    _refuse_unknown_keys(data, _PROCESS_KEYS, "工程")
    with transaction.atomic():
        if "code" in data:
            _refuse_duplicate_process_code(data["code"], exclude_pk=process.pk)
        for key, value in data.items():
            setattr(process, key, value)
        process.save()
        return process


# ---------------------------------------------------------------- 部品の取引先 (決め C19-1・C98-1・C98-4)

#: 部品の取引先の追加で、同じ取引先と部品の行が既にあったときの緑の知らせの字 (入力の土台の with_saved_note が読む。断りではない)
PARTNER_ITEM_EXISTS_MESSAGE = "この取引先は、もうこの部品に登録してあります。"
#: 品番が空の行が同じ取引先と部品にもうあるときの断りの字 (決め C98-4)
_BLANK_PARTNER_ITEM_MESSAGE = "保存できません。この取引先には、品番が空の行がもうあります。単価はその行に足してください。"


def _refuse_taken_partner_part_no(partner, partner_part_no, *, exclude_pk=None):
    """同じ取引先に同じ品番の行があれば ValueError。品番は空でない字で呼ぶ。

    訳: 取引先の品番から自社の部品を 1 つに引けるようにするため (受け入れと読み取りの自動のつなぎ)。出所 = 未確認。
    変えてよい条件 = 1 つの取引先の品番を 2 つの部品に結ぶ形を本人が決めたとき。
    """
    taken = PartnerItem.objects.select_related("item").filter(partner=partner, partner_part_no=partner_part_no)
    if exclude_pk is not None:
        taken = taken.exclude(pk=exclude_pk)
    existing = taken.first()
    if existing is not None:
        raise ValueError(
            f"保存できません。取引先 {partner} の品番 {partner_part_no} は、既に {existing.item} に対応しています。"
            "今の対応を使うか、別の品番にしてください。"
        )


def add_partner_item(data, user):
    """部品の取引先の行を足す。data の鍵は partner・item・partner_part_no・partner_item_name。返す物は部品の取引先の行。

    取引先の品番は前後の空白を外す。取引先の品名は空でよい。
    品番がある: 同じ取引先に同じ品番の行があれば ValueError (今の決まり)。無ければ、同じ取引先と部品に品番の空の行があれば
    その行に品番を入れて返す (品名は、その行の品名が空で、渡された品名があるときだけ入れる)。品番の空の行が無ければ新しい行を作る。
    品番が空: 同じ取引先と部品の行が既にあれば (品番の空の行があればその行、無ければ pk の小さい 1 行)、行を作らずにその行を返し、
    返す物の saved_message を PARTNER_ITEM_EXISTS_MESSAGE にする (入力の土台が緑の知らせの字にする。断りではない)。無ければ品番の空の行を作る。
    user は受けるだけ。

    訳: 部品と取引先の組を 1 か所で足し、品番の空の行を増やさず、品番が分かったら空の行に入れて、付いた単価の行と見積書をそのまま使うため。
    同じ取引先の同じ品番を断るのは今の決まり (出所 = 未確認)、品番の空の行を埋めるのと増やさないのは決め C19-1 と C98-4 (出所 = 本人の決め)。
    変えてよい条件 = 1 つの取引先が 1 つの部品を何行持てるかを本人が決め直したとき。
    """
    _refuse_unknown_keys(data, _PARTNER_ITEM_KEYS, "部品の取引先")
    partner = data["partner"]
    item = data["item"]
    part_no = str(data.get("partner_part_no") or "").strip()
    name = data.get("partner_item_name") or ""
    with transaction.atomic():
        rows = PartnerItem.objects.filter(partner=partner, item=item).order_by("pk")
        blank = rows.filter(partner_part_no="").first()
        if part_no:
            _refuse_taken_partner_part_no(partner, part_no)
            if blank is None:
                return PartnerItem.objects.create(
                    partner=partner, item=item, partner_part_no=part_no, partner_item_name=name
                )
            blank.partner_part_no = part_no
            fields = ["partner_part_no"]
            if not blank.partner_item_name and name:
                blank.partner_item_name = name
                fields.append("partner_item_name")
            blank.save(update_fields=fields)
            return blank
        existing = blank or rows.first()
        if existing is not None:
            existing.saved_message = PARTNER_ITEM_EXISTS_MESSAGE
            return existing
        return PartnerItem.objects.create(partner=partner, item=item, partner_part_no="", partner_item_name=name)


def update_partner_item(partner_item, data, user):
    """部品の取引先の行の、取引先の品番と品名を直す (鍵は partner_part_no と partner_item_name だけ。部品と取引先は変えない)。

    品番は前後の空白を外す。渡した鍵だけを直す。返す物は部品の取引先の行。user は受けるだけ。
    品番があって、同じ取引先のほかの行が同じ品番なら ValueError (今の決まり。add_partner_item と同じ字)。
    品番を空にして、同じ取引先と部品にほかの品番の空の行があれば ValueError (決め C98-4)。どちらも行は変えない。

    訳: 同じ取引先の同じ品番を断るのは、取引先の品番から自社の部品を 1 つに引くため (今の決まり。出所 = 未確認)。
    品番が空の 2 つ目の断りは、品番が空の 2 行は画面で見分けがつかず、それぞれに同じ条件の単価の行を足せて、
    同じ条件の単価の行を 1 つにする決め (C98-2) が崩れるため (出所 = 本人の決め C98-4)。
    変えてよい条件 = 1 つの取引先が 1 つの部品を何行持てるかを本人が決め直したとき。
    """
    _refuse_unknown_keys(data, frozenset({"partner_part_no", "partner_item_name"}), "部品の取引先")
    data = dict(data)
    if "partner_part_no" in data:
        data["partner_part_no"] = str(data["partner_part_no"] or "").strip()
    if "partner_item_name" in data:
        data["partner_item_name"] = data["partner_item_name"] or ""
    with transaction.atomic():
        if "partner_part_no" in data:
            part_no = data["partner_part_no"]
            if part_no:
                _refuse_taken_partner_part_no(partner_item.partner, part_no, exclude_pk=partner_item.pk)
            else:
                others = PartnerItem.objects.filter(
                    partner_id=partner_item.partner_id, item_id=partner_item.item_id, partner_part_no=""
                ).exclude(pk=partner_item.pk)
                if others.exists():
                    raise ValueError(_BLANK_PARTNER_ITEM_MESSAGE)
        for key, value in data.items():
            setattr(partner_item, key, value)
        partner_item.save()
        return partner_item


def delete_partner_item(partner_item, user):
    """部品の取引先の行を消す。無ければ日本語の ValueError。None を返す (土台は開いた詳細 ?next= へ戻す)。

    行は atomic の中で取り直して消す。その行の単価の行も一緒に消す (決め C98-1)。金額の閲覧の権限は見ない (決め C98-6 = a。
    部品の取引先の行は取引先と品番の登録が主で、金額を見ない人も選び間違えた行を消せるようにするため。消える単価の行の数は確かめの小窓に出す)。
    付けた見積書の添付ファイルは消さない (付けた先の無い添付ファイルは、一覧の「付けた先」が「(削除された記録)」)。user は受けるだけ。

    訳: 取引先を選び間違えた行は、直す画面で部品と取引先を直せないので、消して足し直すため。
    間違えた行が残ると、単価表から選ぶときと単価のずれを比べるときに誤った単価を使うおそれがある。出所 = 本人の決め C98-1。
    変えてよい条件 = 消し方 (消すか使用停止か) を本人が決め直したとき。
    """
    with transaction.atomic():
        row = PartnerItem.objects.select_for_update().filter(pk=partner_item.pk).first()
        if row is None:
            raise ValueError("削除できません。この部品の取引先の行は既に消してあります。")
        # 単価の行は部品の取引先の行を PROTECT で指すので、先に消す (消す道を services の 1 つにする)
        ItemPrice.objects.filter(partner_item=row).delete()
        row.delete()
    return None


def map_partner_item(partner, item, partner_part_no, user, *, partner_item_name=""):
    """取引先の品番を自社の品目に対応させる (受け入れと読み取りと種の読み込みが、品番のあるときだけ呼ぶ)。

    同じ (取引先, 取引先の品番) が既にあれば、同じ品目を指していても ValueError。
    呼ぶ側は先に resolve_partner_item で引き、無いときだけ呼ぶ。
    同じ取引先と部品に品番の空の行があれば、その行に品番を入れる (行を増やさない。その行の単価の行と見積書を、
    そのまま受け入れの後も使うため)。中身は add_partner_item。
    """
    return add_partner_item(
        {"partner": partner, "item": item, "partner_part_no": partner_part_no, "partner_item_name": partner_item_name},
        user,
    )


def resolve_partner_item(partner, partner_part_no):
    """取引先の品番から自社の品目を引く。無ければ None。

    品番が空か空白だけなら、表を引かずに None (品番の空の行は自動のつなぎに使わない。決め C19-1)。
    """
    if not str(partner_part_no or "").strip():
        return None
    mapping = (
        PartnerItem.objects.select_related("item")
        .filter(partner=partner, partner_part_no=partner_part_no)
        .first()
    )
    return mapping.item if mapping is not None else None


# ---------------------------------------------------------------- 単価の行 (単価表。決め PRICE-0〜3・C98-1〜C98-3・C98-5・C98-6)

_ITEM_PRICE_KEYS = frozenset(
    {
        "item",
        "partner_item",
        "kind",
        "process",
        "min_qty",
        "unit_price",
        "price_basis",
        "start_date",
        "quote_no",
        "quote_date",
        "quote_expires",
        "note",
    }
)
_PRICE_PARTNER_MESSAGE = (
    "保存できません。選んだ取引先は、この部品の取引先のタブにありません。"
    "部品の詳細の取引先のタブの「単価を足す」から開き直してください。"
)
_PRICE_POSITIVE_MESSAGE = "保存できません。単価は 0 より大きい数にしてください。"
_MIN_QTY_POSITIVE_MESSAGE = "保存できません。数量の下限は 0 より大きい数にしてください。数量によらないときは空にします。"


def _same_band_message(row, user):
    """同じ条件の単価の行がもうあるときの断りの字 (決め C98-2) の、頭の「保存できません。」の後ろ。

    金額を見られる人には今の行の単価を括弧で添え、金額の閲覧が無い人 (access.hides_money が真) には括弧の無い字。
    字は本人に見せた字のまま。頭の「保存できません。」は raise の側に書く (断りの字の頭を試験が辿れるように)。
    """
    price = "" if access.hides_money(user) else f" (¥{comma(row.unit_price)} / {row.unit_text})"
    return (
        f"同じ条件の単価の行がもうあります{price}。"
        "改定なら適用開始日を入れてください。今の単価を直すなら、今の行を編集してください。"
    )


def _check_item_price(item, values, user, *, exclude_pk=None):
    """単価の行の値を、保存の前に 3 つの順に見る。当たれば日本語の ValueError (行は変えない)。values は保存する値の辞書。

    1. 取引先の行があって、その行の部品が item と違う → 断る。
       訳: 単価は登録した取引先にしか付けないため (取引先の行は同じ部品の物だけ。記録どうしの矛盾を止める)。出所 = 本人の決め PRICE-2。
       変えてよい条件 = 単価の持ち方を本人が決め直したとき。
    2. 単価か数量の下限が 0 以下 → 断る (数量の下限の空は通す)。
       訳: 単価表は受注と発注の明細の元で、明細は単価 0 以下を止める (受注の R02・発注の表の制約) ので、元の段階で止めて、
       気づいたその場で直せるようにするため。数量の下限の 0 は空 (数量によらない) と区別できないため。出所 = 本人の決め C98-3。
       変えてよい条件 = 明細の単価の決まりが変わったとき。
    3. 同じ条件 (部品・取引先の行か共通・売りか発注か・工程・数量の下限・適用開始日) の行が exclude_pk のほかにある → 断る。
       訳: 条件がまったく同じで単価だけが違う 2 行があると、単価表から選ぶときと単価のずれを比べるときに、どちらが今の単価か
       決まらないため。改定は適用開始日を入れた新しい行、同じ日の直しは今の行の編集で足りる。出所 = 本人の決め C98-2。
       変えてよい条件 = 単価の行を区別する欄を本人が決め直したとき。
    """
    partner_item = values.get("partner_item")
    if partner_item is not None and partner_item.item_id != item.pk:
        raise ValueError(_PRICE_PARTNER_MESSAGE)
    unit_price = values.get("unit_price")
    if unit_price is not None and unit_price <= 0:
        raise ValueError(_PRICE_POSITIVE_MESSAGE)
    min_qty = values.get("min_qty")
    if min_qty is not None and min_qty <= 0:
        raise ValueError(_MIN_QTY_POSITIVE_MESSAGE)
    same = ItemPrice.objects.select_related("item").filter(item=item, kind=values.get("kind"))
    for name in ("partner_item", "process", "min_qty", "start_date"):
        value = values.get(name)
        same = same.filter(**{f"{name}__isnull": True}) if value is None else same.filter(**{name: value})
    if exclude_pk is not None:
        same = same.exclude(pk=exclude_pk)
    row = same.order_by("pk").first()
    if row is not None:
        raise ValueError(f"保存できません。{_same_band_message(row, user)}")


def _price_values(values):
    """保存する値をそろえる: 売りの行は工程を空にする (売りの単価は工程を持たない。断らない)・見積の番号の前後の空白を外す・
    字の欄の空 (None) を空の字にする。"""
    values = dict(values)
    if values.get("kind") == ItemPrice.KIND_SALE:
        values["process"] = None
    for name in ("quote_no", "note"):
        if name in values:
            values[name] = str(values[name] or "").strip() if name == "quote_no" else (values[name] or "")
    if "price_basis" in values and not values["price_basis"]:
        values["price_basis"] = PRICE_BASIS_UNIT
    return values


def add_item_price(data, user):
    """単価の行を 1 つ足す。data の鍵は _ITEM_PRICE_KEYS (item と kind と unit_price は要る)。作った行を返す。

    売りの行は工程を空にして保存する。部品の行を select_for_update で取ってから _check_item_price を呼ぶ
    (同じ部品の単価の行の書き込みを順に並べ、同じ条件の行の 2 つの要求が同時に通らないようにする。SQLite では何もしないが、
    書き込みが 1 つずつなので同じ)。user は受けるだけ (断りの字に金額を出すかの判定には使う)。
    金額の閲覧の権限が無い人の追加は、入力の土台の金額の門 (core.access の MONEY_ACTIONS) が断る (決め C98-5。この関数は権限を見ない)。
    """
    _refuse_unknown_keys(data, _ITEM_PRICE_KEYS, "単価の行")
    values = _price_values(data)
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=values["item"].pk)
        values["item"] = item
        _check_item_price(item, values, user)
        return ItemPrice.objects.create(**values)


def update_item_price(item_price, data, user):
    """単価の行の値を直す。鍵は _ITEM_PRICE_KEYS から item を除いた物 (部品は変えない)。渡した鍵だけを直し、直した行を返す。

    今の行の値に渡した鍵の値を重ねた値で、売りなら工程を空にしてから、自分を除いて _check_item_price を呼ぶ。
    user は受けるだけ。金額の閲覧の権限が無い人の編集は、入力の土台の金額の門が断る (決め C98-5)。
    """
    _refuse_unknown_keys(data, _ITEM_PRICE_KEYS - {"item"}, "単価の行")
    current = {name: getattr(item_price, name) for name in _ITEM_PRICE_KEYS - {"item"}}
    values = _price_values({**current, **data})
    with transaction.atomic():
        item = Item.objects.select_for_update().get(pk=item_price.item_id)
        _check_item_price(item, values, user, exclude_pk=item_price.pk)
        changed = set(data) | ({"process"} if values.get("process") != current.get("process") else set())
        for name in changed:
            setattr(item_price, name, values[name])
        item_price.save()
        return item_price


def delete_item_price(item_price, user):
    """単価の行を消す。無ければ日本語の ValueError。None を返す (土台は開いた部品の詳細 ?next= へ戻す)。

    行は atomic の中で取り直して消す。user は受けるだけ。
    金額の閲覧の権限が無い人の削除は、入力の土台の金額の門 (core.access の MONEY_ACTIONS) が断る (決め C98-6。この関数は権限を見ない)。

    訳: 打ち間違えた単価の行が残ると、単価表から選ぶときと単価のずれを比べるときに誤った単価を使うおそれがあるため。
    受注と発注の明細は単価を写して持つので、消しても今までの明細の金額は変わらない。改定の前の単価は消さずに適用開始日で残す (決め PRICE-3)。
    出所 = 本人の決め C98-1。変えてよい条件 = 消し方を本人が決め直したとき。
    """
    with transaction.atomic():
        row = ItemPrice.objects.select_for_update().filter(pk=item_price.pk).first()
        if row is None:
            raise ValueError("削除できません。この単価の行は既に消してあります。")
        row.delete()
    return None


def _price_sort_key(row):
    """item_prices の並びの鍵。取引先の行 (取引先コード → 取引先の品番 → 行の pk) が先・共通が後、
    売り → 協力企業への発注、工程 (空が先 → 並び順 → 工程コード)、適用開始日の新しい順 (空は後)、数量の下限の小さい順 (空が先)、pk。"""
    partner_item = row.partner_item
    whose = (1, "", "", 0) if partner_item is None else (0, partner_item.partner.code, partner_item.partner_part_no, partner_item.pk)
    kind = 0 if row.kind == ItemPrice.KIND_SALE else 1
    process = (0, 0, "") if row.process is None else (1, row.process.sort_order, row.process.code)
    start = (1, 0) if row.start_date is None else (0, -row.start_date.toordinal())
    min_qty = (0, 0) if row.min_qty is None else (1, row.min_qty)
    return (whose, kind, process, start, min_qty, row.pk)


def item_prices(item):
    """部品の単価の行を全部、1 回の問い合わせで引き、並べた list で返す (読むだけ)。

    並びは取引先の行が先・共通が後 (決め PRICE-2)、その中で 売り → 協力企業への発注、工程、適用開始日の新しい順 (空は後)、
    数量の下限の小さい順 (空が先)、pk。並べ替えは Python で行う (空の扱いを SQLite と PostgreSQL で同じにするため)。
    部品の詳細の取引先のタブと、単価表から選ぶ小窓・単価のずれの知らせ (C100・C101) が読む。
    """
    rows = ItemPrice.objects.filter(item=item).select_related("item", "partner_item__partner", "process")
    return sorted(rows, key=_price_sort_key)


# ---------------------------------------------------------------- 単価表から選ぶ小窓の行と、発注の明細の工程の最初の値 (C100。読むだけ)


def _price_band(price):
    """適用開始日のほかが同じ条件 (取引先の行か共通・売りか発注か・工程・数量の下限) の行の組の鍵。"""
    return (price.partner_item_id, price.kind, price.process_id, price.min_qty)


def _start_rank(price):
    """適用開始日の新しさ。空は一番古い扱い (ほかに効く行が無ければ効く)。"""
    return 0 if price.start_date is None else price.start_date.toordinal()


def effective_prices(item, *, kind, partner=None, on_date):
    """on_date に効いている単価の行の list (読むだけ)。item_prices の 1 回の問い合わせから選び、並びは item_prices のまま。

    kind の行のうち、取引先の行が partner の物 (部品の取引先の行の取引先が partner) と共通の行を取る。partner が None なら共通の行だけ。
    適用開始日が on_date より後の行は出さない。同じ条件 (取引先の行か共通・売りか発注か・工程・数量の下限) の行は、
    適用開始日が on_date 以前で一番新しい 1 行だけを残す (適用開始日が空の行は一番古い扱い)。

    訳: その日に効いている単価だけを選ぶ小窓とずれの知らせに使い、改定の前の単価と、まだ効かない単価を出さないため。出所 = 本人の決め PRICE-2・PRICE-3。
    変えてよい条件 = 単価の行の効く日の決め方を本人が決め直したとき。
    """
    partner_pk = getattr(partner, "pk", partner)
    rows = [
        price
        for price in item_prices(item)
        if price.kind == kind
        and (price.partner_item_id is None or (partner_pk is not None and price.partner_item.partner_id == partner_pk))
        and (price.start_date is None or price.start_date <= on_date)
    ]
    newest = {}
    for price in rows:
        band = _price_band(price)
        if band not in newest or _start_rank(price) > _start_rank(newest[band]):
            newest[band] = price
    kept = {price.pk for price in newest.values()}
    return [price for price in rows if price.pk in kept]


@dataclass(frozen=True)
class PriceChoice:
    """選ぶ小窓の 1 行。price = 単価の行・fits = 今の数量に合う・expired = 見積の有効期限を過ぎた (price.quote_expired)。"""

    price: ItemPrice
    fits: bool
    expired: bool


def _fitting_pks(prices, qty):
    """今の数量に合う行の pk の集まり。取引先の行か共通 × 工程の組ごとに、数量の下限 (空は 0) が qty 以下で一番大きい 1 行。
    期限切れの行も数える。qty が None なら空。"""
    if qty is None:
        return set()
    best = {}
    for price in prices:
        floor = price.min_qty or 0
        if floor > qty:
            continue
        group = (price.partner_item_id, price.process_id)
        if group not in best or floor > (best[group].min_qty or 0):
            best[group] = price
    return {price.pk for price in best.values()}


def _choice_sort_key(choice):
    """選ぶ小窓の並びの鍵。段 (取引先の行で期限の内 → 共通で期限の内 → 期限切れ (取引先 → 共通))、
    期限の内の段では今の数量に合う行が先、次に工程 (空 → 並び順 → 工程コード)・数量の下限 (空 → 小さい順)・pk。"""
    price = choice.price
    common = price.partner_item_id is None
    tier = 2 if choice.expired else (1 if common else 0)
    fits_first = 0 if (choice.fits and not choice.expired) else 1
    process = (0, 0, "") if price.process is None else (1, price.process.sort_order, price.process.code)
    min_qty = (0, 0) if price.min_qty is None else (1, price.min_qty)
    return (tier, 1 if common else 0, fits_first, process, min_qty, price.pk)


def price_choices(item, *, kind, partner=None, on_date=None, qty=None):
    """選ぶ小窓に並べる行 (PriceChoice の list。読むだけ)。on_date が None なら今日。行は effective_prices の行。

    fits は取引先の行か共通 × 工程の組ごとに、数量の下限 (空は 0) が qty 以下で一番大きい行 (期限切れの行も数える。qty が None ならどれも偽)。
    並びは 3 段 (明細の取引先の行で期限の内 → 共通の行で期限の内 → 見積の有効期限を過ぎた行 (取引先 → 共通))。
    並べ替えは Python で行う (空の扱いを SQLite と PostgreSQL で同じにするため)。
    単価のずれの知らせが比べる相手を決めるときも、この関数の fits と expired を使える。

    訳: 明細の取引先の単価を先に、共通を後に、今の数量に合う帯を上に、見積の有効期限を過ぎた単価を下に出し、選び間違いを減らすため。
    出所 = 本人の決め PRICE-0・PRICE-2・PRICE-3b。変えてよい条件 = 並びの決めを本人が決め直したとき。
    """
    if on_date is None:
        on_date = timezone.localdate()
    prices = effective_prices(item, kind=kind, partner=partner, on_date=on_date)
    fitting = _fitting_pks(prices, qty)
    choices = [PriceChoice(price=price, fits=price.pk in fitting, expired=price.quote_expired) for price in prices]
    return sorted(choices, key=_choice_sort_key)


#: 発注の明細の工程の最初の値に数える、部品の標準の工程の並びの行の絞り (default_process と with_default_process が同じ物を使う)
_DEFAULT_PROCESS_FILTER = {"process__active": True, "process__is_outsourced": True}


def default_process(item):
    """部品の標準の工程の並びのうち、使用中で協力企業に出す工程がちょうど 1 つならその Process、無いか 2 つ以上なら None (読むだけ)。

    訳: 協力企業に出す工程が 1 つだけの部品は、発注の明細の工程を最初から入れて手間を減らすため。2 つ以上ならどれを出すか分からないので入れない。
    出所 = 本人の決め PRICE-5。変えてよい条件 = 最初の値の決め方を本人が決め直したとき。
    """
    rows = list(item.item_processes.filter(**_DEFAULT_PROCESS_FILTER).select_related("process")[:2])
    return rows[0].process if len(rows) == 1 else None


def with_default_process(items):
    """部品の queryset に default_process (工程の pk か None) を足して返す (読むだけ)。default_process と同じ決まり。
    候補の口が 1 ページ分を 1 回で引くため (数と最初の 1 つを Subquery で引く。問い合わせは増えない)。"""
    outsourced = ItemProcess.objects.filter(item=OuterRef("pk"), **_DEFAULT_PROCESS_FILTER)
    count = Subquery(
        outsourced.order_by().values("item").annotate(rows=Count("pk")).values("rows")[:1], output_field=IntegerField()
    )
    first = Subquery(outsourced.order_by("seq", "pk").values("process")[:1], output_field=IntegerField())
    return items.annotate(default_process_count=count).annotate(
        default_process=Case(When(default_process_count=1, then=first), default=None, output_field=IntegerField())
    )


def round_amount(partner, value, *, rounding=None):
    """金額 (数量 × 単価) の端数を 1 円の単位に丸める 1 か所の関数。

    方法は取引先の小計の計算方法。空なら会社の既定 (Company の 1 行の rounding。行が無ければ round)。
    round = 四捨五入、ceil = 切上 (ROUND_CEILING)、floor = 切捨 (ROUND_FLOOR)。
    rounding に会社の既定 (company_rounding() の値) を渡すと、会社の行を読まずにそれを使う
    (多くの行を丸める呼び手が、輪の前に 1 回だけ読んで渡すため)。取引先の方法があれば、そちらが先。
    訳: 金額 (数量 × 単価) の端数を、取引先 (空なら会社) の小計の計算方法どおりに 1 円に丸める所を 1 か所にするため。出所 = RULES.md R03。
    変えてよい条件 = 丸め方は取引先と会社の設定で選ぶ。設定どおりに丸めること (R03) は変えない (R は廃止で残す)。
    """
    mode = partner.rounding or rounding or company_rounding()
    return value.quantize(Decimal("1"), rounding=_ROUNDING_MODES[mode])


#: 単価の単位の値の組 (決め PRICE-1b)。受注と発注の明細と会社どうしの注文が受ける値。
PRICE_BASIS_VALUES = frozenset(value for value, _label in PRICE_BASIS_CHOICES)


def refuse_unknown_price_basis(value):
    """単価の単位の値を確かめて返す。部品の単位 (unit) と式 (lump) のほかは日本語の ValueError。
    あり得ない入力を表に書く前に断る (画面の欄は選ぶ欄なので、ここに来るのは画面の外から呼ぶ時だけ)。

    訳: 金額の式 (line_amount) が知らない値を部品の単位として数え、式のつもりの明細の金額が数量倍になるのを止めるため。出所 = 作者の決め PRICE-1b と AI の案。
    変えてよい条件 = 単価の単位の種類を足すとき (masters.models の PRICE_BASIS_CHOICES と一緒に直す)。
    """
    if value not in PRICE_BASIS_VALUES:
        raise ValueError(
            f"保存できません。単価の単位 {value!r} は使えません。部品の単位 (unit) か式 (lump) にしてください。"
        )
    return value


def line_amount(partner, qty, unit_price, price_basis=PRICE_BASIS_UNIT, *, rounding=None):
    """受注と発注の明細の金額 (読むだけ)。単価の単位が式 (lump) なら単価のまま、ほかは数量 × 単価。
    端数は round_amount で取引先 (空なら会社) の小計の計算方法どおりに 1 円に丸める (rounding は round_amount と同じ)。
    受注 (orders.services) と発注 (purchasing.services) の金額をこの 1 つの式にそろえる。

    訳: 一式の明細は数量を掛けずに単価を金額にし、式の行も端数をほかの金額と同じ 1 か所で丸めるため。出所 = 作者の決め PRICE-1b と RULES.md R03。
    変えてよい条件 = 単価の単位の種類を足すとき (例 中小企業共通 EDI の単価基準数量で数える不定貫の品物) と、作者が一式の金額の決め方を決め直したとき。
    """
    if price_basis == PRICE_BASIS_LUMP:
        return round_amount(partner, unit_price, rounding=rounding)
    return round_amount(partner, qty * unit_price, rounding=rounding)


def reaches_line_qty(line_qty, done_qty, qty):
    """この 1 回 (出荷か受入) で明細の数量に初めて届くか (読むだけ)。前の回までの合計 done_qty が数量に届いておらず、qty を足すと数量以上になれば真。

    訳: 一式の明細の全額を、全部を出し終えた 1 回にだけ付けるため (前の回は 0 円)。出所 = 作者の決め PRICE-1c。
    変えてよい条件 = 作者が一式の金額を請求に載せる時を決め直したとき。
    """
    return done_qty < line_qty <= done_qty + qty


def delivery_amount(partner, qty, unit_price, price_basis, *, completes, rounding=None):
    """出荷か受入の 1 回分の金額 (読むだけ)。単価の単位が式 (lump) なら、明細を出し終えた回 (completes が真) は一式の全額 (line_amount)、ほかの回は 0。
    部品の単位 (unit) は今と同じく line_amount (数量 × 単価を取引先 (空なら会社) の小計の計算方法で丸める)。completes は式のときだけ効く。
    出荷 (shipping.services.amount_of_shipment) と受入 (purchasing.services.receive) がこの 1 つの式を通す。
    price_basis と completes に既定の値を置かない (渡し忘れると一式の全額が黙って 0 になるか数量倍になるので、TypeError で止める)。

    訳: 一式の明細を分けて出荷・受入したとき、全部を出し終えた回で一式の全額を載せ、それまでの回は 0 円にするため。出所 = 作者の決め PRICE-1c (発注の受入も同じ) と PRICE-1b。
    変えてよい条件 = 作者が一式の金額を請求に載せる時を決め直したとき。
    """
    if price_basis == PRICE_BASIS_LUMP and not completes:
        return Decimal("0")
    return line_amount(partner, qty, unit_price, price_basis, rounding=rounding)


def company_rounding():
    """会社の既定の端数の方法 (Company の 1 行の rounding)。行が無ければ "round"。会社の行は作らない。"""
    company = company_or_none()
    return company.rounding if company is not None else "round"


def round_tax(partner, value):
    """消費税 (請求書の金額の合計 × 率) の端数を 1 円の単位に丸める 1 か所の関数。

    向きは取引先の消費税の端数の向き (tax_rounding)。空なら会社の既定 (company_tax_rounding()。行が無ければ切捨)。
    round = 四捨五入、ceil = 切上、floor = 切捨 (金額の round_amount と同じ表)。金額の端数の扱い (rounding) は読まない。
    インボイス制度は、消費税の端数の処理を請求書の税率ごとの合計に 1 回と決めている (確かめた資料 = 中小企業共通 EDI ガイドブック 1.1)。
    向き (四捨五入・切上・切捨) の決まりは確かめた資料に無い (国税庁の資料は未読)。
    訳: 請求書の消費税の端数を、取引先 (空なら会社) の消費税の端数の向きどおりに 1 円に丸める所を 1 か所にするため。出所 = 本人の決め (C95-1b。会社の既定は切捨)。
    変えてよい条件 = 向きは取引先と会社の設定で選ぶ。合計に 1 回だけ丸めること (決め LAW-2) は変えない。
    """
    mode = partner.tax_rounding or company_tax_rounding()
    return value.quantize(Decimal("1"), rounding=_ROUNDING_MODES[mode])


def company_tax_rounding():
    """会社の既定の消費税の端数の向き (Company の 1 行の tax_rounding)。行が無ければ "floor" (切捨)。会社の行は作らない。"""
    company = company_or_none()
    return company.tax_rounding if company is not None else "floor"


# カタカナ (ァ〜ヶ) をひらがなに写す表。
_KATAKANA_TO_HIRAGANA = {code: code - 0x60 for code in range(0x30A1, 0x30F7)}


def _fold(text):
    """照合のために字を畳む。全角と半角、カタカナとひらがな、大文字と小文字を同じにする。"""
    text = unicodedata.normalize("NFKC", text)
    return text.translate(_KATAKANA_TO_HIRAGANA).casefold()


def _page_number(page):
    try:
        number = int(page)
    except (TypeError, ValueError):
        return 1
    return max(number, 1)


def _active(model, keep):
    keep = getattr(keep, "pk", keep)
    condition = Q(active=True)
    if keep not in (None, ""):
        condition |= Q(pk=keep)
    return model.objects.filter(condition)


def active_items(*, keep=None):
    """使用中の部品 (候補の母集団。読むだけ。N135)。

    keep (部品か pk) は使用停止でも残す。今ある受注を直す画面が、今の明細の部品を候補に残すため (直しを止めない)。
    """
    return _active(Item, keep)


def active_partners(*, keep=None):
    """使用中の取引先 (候補の母集団。読むだけ。N135)。keep は active_items と同じ (直す受注の今の得意先を残す)。

    取引先の種類の絞り (得意先だけ・協力企業だけ) は呼ぶ側が filter で足す。
    """
    return _active(Partner, keep)


def active_processes(*, keep=None):
    """使用中の工程 (指示書の発行で選ぶ工程の候補の母集団。読むだけ。N140)。並びは工程の表の既定 (並び順 → 工程コード)。

    keep (工程か pk) は使用停止でも残す (active_items と同じ口)。今ある指示書の工程と部品の標準の工程の並びの行は消さない。
    """
    return _active(Process, keep)


def refuse_inactive(partner, items=()):
    """新しい受注と発注を作る所 (orders・purchasing の services) が呼ぶ守り (N135)。表に書かない。

    取引先か部品のどれかが使用停止なら、日本語の ValueError (入力の土台が赤の知らせにする)。
    今ある受注と発注の直しと、今ある受注から先の記録 (指示書・出荷・請求書) では呼ばない。
    """
    if partner is not None and not partner.active:
        raise ValueError(f"保存できません。使用停止の取引先です ({partner})。使用中の取引先を選ぶか、取引先の編集で「使用中」に戻してください。")
    for item in items:
        if item is not None and not item.active:
            raise ValueError(f"保存できません。使用停止の部品です ({item})。使用中の部品を選ぶか、部品の編集で「使用中」に戻してください。")


def candidates(kind, query, page, *, per_page=30):
    """選ぶ欄の候補を 1 ページ返す (05 の 2 節)。

    kind は "item" (表示の字は「品番 品名」) か "partner" (「コード 名前」)。
    表示の字と query を畳み、query を空白で切った語の全部を含む行を出す。空の query は使用中の全件 (使用停止は出さない。N135)。
    page が整数に読めなければ 1。母集団は数千件の前提で、全件を読んでから Python で絞る。
    """
    if kind == "item":
        rows = [
            (pk, f"{part_no} {name}")
            for pk, part_no, name in active_items().values_list("pk", "part_no", "name")
        ]
    elif kind == "partner":
        rows = [
            (pk, f"{code} {name}")
            for pk, code, name in active_partners().values_list("pk", "code", "name")
        ]
    else:
        raise ValueError(f"候補の種類 {kind!r} は使えません。item か partner にしてください。")

    terms = _fold(query or "").split()
    hits = [(pk, text) for pk, text in rows if all(term in _fold(text) for term in terms)]
    start = (_page_number(page) - 1) * per_page
    return {
        "results": [{"id": pk, "text": text} for pk, text in hits[start : start + per_page]],
        "pagination": {"more": start + per_page < len(hits)},
    }


_COPY_LABELS = {"unit": "単位", "unit_price": "単価"}


def _is_blank(value):
    return value is None or (isinstance(value, str) and value.strip() == "")


def _same_value(now, master_value):
    """今の欄の値とマスタの値を、マスタと同じ型にそろえて比べる。"""
    if _is_blank(now):
        return False
    if isinstance(master_value, Decimal):
        try:
            return Decimal(str(now).strip()) == master_value
        except InvalidOperation:
            return False
    return str(now).strip() == master_value


def _values_to_copy(source):
    if isinstance(source, Item):
        return {"unit": source.unit, "unit_price": source.standard_price}
    # 取引先から受注の欄に写す物は無い。
    return {}


def copy_values(source, current, copied_marks):
    """マスタの値を欄へ写す 4 つの規則 (05 の 2 節) を 1 か所で当てる。

    source は Item (単位と標準単価を、欄の unit と unit_price に写す) か Partner (写す物は無い)。
    current は今の欄の値の辞書、copied_marks は前に写した欄の名前の集合。
    規則: マスタに値が無ければ何もしない / 今の値と同じなら知らせない /
    欄が空か前に写した値なら写して「写した」/ 人が入れた値なら残して「残した」。
    返す物は (写す値の辞書, 知らせの文の一覧)。
    """
    values = {}
    copied = []
    kept = []
    for field, master_value in _values_to_copy(source).items():
        if _is_blank(master_value):
            continue
        now = current.get(field)
        if _same_value(now, master_value):
            continue
        if _is_blank(now) or field in copied_marks:
            values[field] = master_value
            copied.append(_COPY_LABELS[field])
        else:
            kept.append(_COPY_LABELS[field])

    messages = []
    if copied:
        messages.append(f"{'と'.join(copied)}を部品から写しました。")
    if kept:
        messages.append(f"{'と'.join(kept)}は入力のまま残しました。")
    return values, messages


class ImportResult(dict):
    """CSV の取り込みの結果 ({"入れた": n, "更新した": m} の辞書のまま)。saved_note は、入力の土台 (core.views.with_saved_note) が緑の知らせの後ろに付ける字。"""

    saved_note = ""


def _import_rows(rows, user, model, key, label, create, update, *, note=""):
    """CSV の行を 1 つのトランザクションで入れる。鍵の列の値が既にあれば update、無ければ create。

    どれかの行で services が断ったら、行の番号を付けて ValueError を上げ直す (全部が巻き戻り、1 件も入らない)。
    note は知らせの後ろに付ける 1 文。
    """
    made = updated = 0
    with transaction.atomic():
        for row in rows:
            line, data, ext = row["line"], row["data"], row.get("ext")
            try:
                if _is_blank(data.get(key)):
                    raise ValueError(f"保存できません。{label}が空です。値を入れてください。")
                current = model.objects.filter(**{key: data[key]}).first()
                if current is None:
                    create(data, user, ext=ext)
                    made += 1
                else:
                    update(current, data, user, ext=ext)
                    updated += 1
            except ValueError as exc:
                raise ValueError(f"保存できません。{line} 行目: {str(exc).removeprefix('保存できません。')}") from exc
    result = ImportResult({"入れた": made, "更新した": updated})
    result.saved_note = f"{made} 件入れ、{updated} 件直しました。{note}"
    return result


def import_items(rows, user, *, price_skipped=False):
    """部品の CSV の行を入れる (05 の 8 節)。自社品番が既にあれば update_item、無ければ create_item。

    rows の 1 つは {"line": 行の番号, "data": {列の名前: 値}, "ext": {会社ごとの項目の列の名前: 値} か None}。
    data は CSV にある列だけ (無い列は更新しても今の値のまま)。返す物は ImportResult ({"入れた": n, "更新した": m} の辞書)。
    price_skipped が真なら (金額の閲覧が無い人の取り込みで標準単価の列を読まなかった)、知らせに 1 文を足す。
    """
    note = "標準単価の列は金額の閲覧の権限が無いので読みませんでした。" if price_skipped else ""
    return _import_rows(rows, user, Item, "part_no", "自社品番", create_item, update_item, note=note)


def import_partners(rows, user, *, tax_reread=0):
    """取引先の CSV の行を入れる。取引先コードが既にあれば update_partner、無ければ create_partner。形は import_items と同じ。

    tax_reread が 1 以上なら (消費税の端数の扱いの前の字「各行四捨五入」「各行切捨」を、form がトータルと
    消費税の端数の向きに読み替えた行の数。決め C95-3)、知らせに読み替えた行の数の 1 文を足す。
    """
    note = (
        f"消費税の端数の扱いの前の字 (各行四捨五入・各行切捨) の {tax_reread} 行を、トータルと消費税の端数の向きに読み替えました。"
        if tax_reread
        else ""
    )
    return _import_rows(rows, user, Partner, "code", "取引先コード", create_partner, update_partner, note=note)


def set_item_processes(item, processes, user):
    """部品の標準の工程の並びを置き換える。processes は Process の並びで、並びの順に順を 1 から付ける。

    今の並びは消してから作り直す。空の並びなら全部の行を消して [] を返す (部品を作った直後と同じ形)。工程でない物・同じ工程の 2 回は、何も変えずに日本語の ValueError。
    返す物は作った ItemProcess の並び。user は受けるだけ。

    訳: 空の並びを受けるのは、部品の詳細の「外す」で最後の 1 つも外せるようにするため (選び間違えて足した 1 つ目を、別の工程を足してから外す 2 手なしで外せる)。空の並びは部品を作った直後にもある形で、新しい記録の状態は増えない。出所 = 本人の決め C112-1。前の空の断りは作り物のデータの読み込みのために作った守りで、画面の決めの記録は無かった (出所 = 未確認)。
    変えてよい条件 = 並びを空にできるかを本人が決め直したとき。
    """
    processes = list(processes)
    for process in processes:
        if not isinstance(process, Process):
            raise ValueError(f"保存できません。工程でない物 ({process!r}) が入っています。工程 (Process) だけを渡してください。")
    seen = set()
    for process in processes:
        if process.pk in seen:
            raise ValueError(f"保存できません。工程 {process.code} が 2 回あります。同じ工程は 1 回だけにしてください。")
        seen.add(process.pk)
    with transaction.atomic():
        item.item_processes.all().delete()
        return [item.item_processes.create(process=process, seq=seq) for seq, process in enumerate(processes, start=1)]


def _current_processes(item):
    """部品の今の標準の工程の並び (順の並びの Process)。atomic の中で、部品の行を取り直した後に読む。"""
    return [row.process for row in item.item_processes.select_related("process").order_by("seq")]


def _locked_item_process(item_process):
    """並びの行を atomic の中で取り直す (部品と工程も一緒に)。無ければ None (ほかの人が並びを直して、行が作り直された)。"""
    return ItemProcess.objects.select_for_update().select_related("item", "process").filter(pk=item_process.pk).first()


def add_item_processes(data, user):
    """部品の標準の工程の並びの最後に工程を足す (決め C19-3。部品の詳細の「工程を足す」)。data の鍵は item・processes。

    processes は Process の並びで、渡した順のまま足す (画面は工程の表の並び順で渡す)。今の並びにある工程は足さずに飛ばし、
    足す物が無ければ何も書かない。書くのは set_item_processes。部品を返す。

    訳: 標準の工程の並びを種の読み込みでなく画面から入れられるようにし、作り物のデータを使わない会社でも、指示書の発行が写す並びと、発注の明細の工程の最初の値 (決め PRICE-5) が効くようにするため。今ある工程を断らずに飛ばすのは、2 人が同時に同じ工程を足しても止めずに同じ並びになるため。出所 = 本人の決め C19-3 (飛ばす形は作者の決め)。
    変えてよい条件 = 標準の工程の入れ方を本人が決め直したとき。
    """
    _refuse_unknown_keys(data, frozenset({"item", "processes"}), "標準の工程")
    with transaction.atomic():
        # 2 人が同時に足しても、後の人が先の人の足した並びを読むため (SQLite では select_for_update は効かないが害は無い)
        item = Item.objects.select_for_update().get(pk=data["item"].pk)
        current = _current_processes(item)
        have = {process.pk for process in current}
        adding = []
        for process in data.get("processes") or ():
            if getattr(process, "pk", None) in have:
                continue
            have.add(getattr(process, "pk", None))
            adding.append(process)
        if adding:
            set_item_processes(item, current + adding, user)
    return item


def move_item_process(item_process, data, user):
    """標準の工程の並びの 1 行を、data の seq (1 から) の位置へ動かす (決め C19-3。行の「順を直す」)。ほかの行は 1 つずつずれる。

    seq が 1 より小さければ先頭、行の数より大きければ最後に置く。位置が今と同じなら何も書かない。
    行は atomic の中で取り直し、無ければ日本語の ValueError。書くのは set_item_processes。部品を返す。

    訳: 工程の順が変わったときに、1 つの工程の位置だけを直すため。行の数の外の順を断らずに端へ置くのは、画面の欄が 1〜行の数に限る (form) ので、ほかの道から来ても並びが崩れないため。出所 = 本人の決め C19-3 (位置の直し方と端へ置く形は作者の決め)。
    変えてよい条件 = 並べ替えの形を本人が決め直したとき。
    """
    _refuse_unknown_keys(data, frozenset({"seq"}), "標準の工程")
    with transaction.atomic():
        row = _locked_item_process(item_process)
        if row is None:
            raise ValueError("保存できません。この工程は、もう標準の工程の並びにありません。部品の詳細を開き直してください。")
        item = Item.objects.select_for_update().get(pk=row.item_id)
        current = _current_processes(item)
        rest = [process for process in current if process.pk != row.process_id]
        position = min(max(int(data["seq"]), 1), len(current))
        moved = rest[: position - 1] + [row.process] + rest[position - 1 :]
        if [process.pk for process in moved] != [process.pk for process in current]:
            set_item_processes(item, moved, user)
    return item


def remove_item_process(item_process, user):
    """標準の工程の並びから 1 行を外す (決め C19-3。行の「⋮」の「外す」)。後ろの行の順は 1 つずつ前へ詰める。

    最後の 1 行も外せ、並びは空になる (決め C112-1)。工程の表の行と、発行済みの指示書の工程は消さない。
    行は atomic の中で取り直し、無ければ日本語の ValueError。書くのは set_item_processes。部品を返し、済んだ知らせの字を saved_message に置く。

    訳: 間違えて足した工程や、もう使わない工程を並びから外し、指示書の発行で写さないようにするため。工程の表と発行済みの指示書を残すのは、並びが部品ごとの写す元で、工程そのものではないため。出所 = 本人の決め C19-3。最後の 1 行も外せて並びが空になるのは、選び間違えて足した 1 つ目を、別の工程を足してから外す 2 手なしで外せるようにするため (出所 = 本人の決め C112-1)。
    変えてよい条件 = 標準の工程の消し方か、並びを空にできるかを本人が決め直したとき。
    """
    with transaction.atomic():
        row = _locked_item_process(item_process)
        if row is None:
            raise ValueError("削除できません。この工程は、もう標準の工程の並びにありません。部品の詳細を開き直してください。")
        item = Item.objects.select_for_update().get(pk=row.item_id)
        rest = [process for process in _current_processes(item) if process.pk != row.process_id]
        set_item_processes(item, rest, user)
    # 入力の土台の済んだ知らせは関数の名前で「削除しました。」になるので置き換える (工程の表の行は消さないので「削除」と言わない)
    item.saved_message = f"{row.process} を標準の工程から外しました。"
    return item


# ---------------------------------------------------------------- 会社の休日 (稼働日の数え方は core.calendar)

_HOLIDAY_KEYS = frozenset({"date", "name", "kind"})
_HOLIDAY_KINDS = frozenset(value for value, _label in Holiday.KIND_CHOICES)


def create_holiday(data, user):
    """休日を 1 日足す。日付は一意。data の鍵は date・name・kind。

    種類が祝日 (public) か会社の休み (company) でない・名前が空・同じ日付の休日が既にある、のどれかなら日本語の ValueError。
    書いた後に稼働日の控え (core.calendar) を捨てる。作った行を返す (土台は休日の詳細へ移す)。
    """
    _refuse_unknown_keys(data, _HOLIDAY_KEYS, "休日")
    if data.get("kind") not in _HOLIDAY_KINDS:
        raise ValueError("保存できません。休日の種類は 祝日 か 会社の休み です。")
    if not str(data.get("name") or "").strip():
        raise ValueError("保存できません。休日の名前を入れてください。")
    with transaction.atomic():
        day = data.get("date")
        existing = Holiday.objects.filter(date=day).first()
        if existing is not None:
            raise ValueError(f"保存できません。{day:%Y-%m-%d} の休日は既にあります ({existing.name})。")
        row = Holiday.objects.create(**data, created_by=user if getattr(user, "pk", None) is not None else None)
    # 同じ要求のこの後の読みが新しい休日を数えるよう、控えを捨てる (別の process は CACHE_SECONDS 秒で読み直す)
    calendar.reset_cache()
    return row


def delete_holiday(holiday, user):
    """休日を消す。無ければ日本語の ValueError。None を返す (土台が一覧へ移す)。

    行は atomic の中で取り直して消す。書いた後に稼働日の控え (core.calendar) を捨てる。user は受けるだけ (記録の列が無い)。
    """
    with transaction.atomic():
        row = Holiday.objects.select_for_update().filter(pk=holiday.pk).first()
        if row is None:
            raise ValueError("削除できません。この休日は既に消してあります。")
        row.delete()
    calendar.reset_cache()
    return None
