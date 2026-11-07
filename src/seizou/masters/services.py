"""masters の公開する関数 (01 の 10.4)。ほかの部品と画面はこの関数だけを通してマスターを書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は受けるが、None 以外が来たら NotImplementedError を投げる。
  黙って捨てると会社の項目が消えるため。保存は core の fields ができてから足す。
- masters の決まり (品番の一意・取引先の種類の 3 値・請求の仕方の 2 値・支払の月の 4 値・支払日の 1〜31・取引先 × 取引先の品番の一意) に当たったら、
  日本語の文の ValueError を投げる。中心の決まり (RULES.md の番号) は masters に無い。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 部品の図面 (drawing) に新しいファイルが来たら、保存の前に 1 回だけ読んで表示用の PNG を作る (05 の 7 節)。
  作れなかったときも部品と元のファイルは保存し、理由の 1 文を drawing_preview_error に残す (例外にしない)。
- 部品の図面を新しいファイルに差し替えたら、前の図面を版 (ItemDrawingVersion) に残す (ファイルは写さず名前の字を持つ)。
  版に戻すのは restore_drawing_version。
- 使用停止 (active が偽。N135): 消さずに候補から外す。候補の母集団は active_items・active_partners、
  新しい受注と発注を作る所 (orders・purchasing の services) の守りは refuse_inactive。今までの記録はそのまま。
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

from seizou.core import calendar
from seizou.core.services import company_or_none

from .drawings import page_name
from .models import Item, Partner, PartnerItem
from .models import Process
from .models import Holiday
from .models import PRICE_BASIS_CHOICES, PRICE_BASIS_LUMP, PRICE_BASIS_UNIT, price_basis_text  # noqa: F401  (orders・purchasing・connect が読む)

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


def map_partner_item(partner, item, partner_part_no, user, *, partner_item_name=""):
    """取引先の品番を自社の品目に対応させる。

    同じ (取引先, 取引先の品番) が既にあれば、同じ品目を指していても ValueError。
    呼ぶ側は先に resolve_partner_item で引き、無いときだけ呼ぶ。
    """
    with transaction.atomic():
        existing = (
            PartnerItem.objects.select_related("item")
            .filter(partner=partner, partner_part_no=partner_part_no)
            .first()
        )
        if existing is not None:
            raise ValueError(
                f"保存できません。取引先 {partner} の品番 {partner_part_no} は、既に {existing.item} に対応しています。"
                "今の対応を使うか、別の品番にしてください。"
            )
        return PartnerItem.objects.create(
            partner=partner,
            item=item,
            partner_part_no=partner_part_no,
            partner_item_name=partner_item_name,
        )


def resolve_partner_item(partner, partner_part_no):
    """取引先の品番から自社の品目を引く。無ければ None。"""
    mapping = (
        PartnerItem.objects.select_related("item")
        .filter(partner=partner, partner_part_no=partner_part_no)
        .first()
    )
    return mapping.item if mapping is not None else None


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

    今の並びは消してから作り直す。空の並び・工程でない物・同じ工程の 2 回は、何も変えずに日本語の ValueError。
    返す物は作った ItemProcess の並び。user は受けるだけ。
    """
    processes = list(processes)
    if not processes:
        raise ValueError("保存できません。工程がありません。工程を 1 つ以上渡してください。")
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
