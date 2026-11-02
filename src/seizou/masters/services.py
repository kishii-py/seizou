"""masters の公開する関数 (01 の 10.4)。ほかの部品と画面はこの関数だけを通してマスターを書く。

- 引数の順は 対象 → 値 → user → * の後のキーワード (01 の 10.1)。
- user は記録のために受ける。記録の列がまだ無いので、今は受けるだけで書かない。
- ext (会社ごとの項目) は受けるが、None 以外が来たら NotImplementedError を投げる。
  黙って捨てると会社の項目が消えるため。保存は core の fields ができてから足す。
- masters の決まり (品番の一意・取引先の種類の 3 値・支払の月の 3 値・支払日の 1〜31・取引先 × 取引先の品番の一意) に当たったら、
  日本語の文の ValueError を投げる。中心の決まり (RULES.md の番号) は masters に無い。
- 入力の欄の誤り (型・必須) は form の仕事。ここでは full_clean() を呼ばない。
- 部品の図面 (drawing) に新しいファイルが来たら、保存の前に 1 回だけ読んで表示用の PNG を作る (05 の 7 節)。
  作れなかったときも部品と元のファイルは保存し、理由の 1 文を drawing_preview_error に残す (例外にしない)。
- 部品の図面を新しいファイルに差し替えたら、前の図面を版 (ItemDrawingVersion) に残す (ファイルは写さず名前の字を持つ)。
  版に戻すのは restore_drawing_version。
"""

from __future__ import annotations

import io
import os
import threading
import unicodedata
from decimal import ROUND_CEILING, ROUND_FLOOR, ROUND_HALF_UP, Decimal, InvalidOperation

from django.core.files.base import ContentFile, File
from django.db import transaction
from django.db.models import Max
from django.db.models.fields.files import FieldFile
from PIL import Image, ImageFile

from seizou.core.models import Company

from .models import Item, Partner, PartnerItem
from .models import Process

_ITEM_KEYS = frozenset(
    {"part_no", "name", "drawing_no", "drawing", "unit", "standard_price", "note"}
)
_PARTNER_KEYS = frozenset(
    {
        "code",
        "name",
        "short_name",
        "role",
        "closing_day",
        "payment_month",
        "payment_day",
        "rounding",
        "tax_mode",
        "note",
    }
)
_PROCESS_KEYS = frozenset({"code", "name", "is_outsourced", "sort_order", "use_in_plan"})
_ROLES = frozenset(value for value, _label in Partner.ROLE_CHOICES)
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
            f"知らない項目 ({'・'.join(unknown)}) があります。"
            f"{table_name}の列の名前だけを渡してください。"
        )


def _refuse_duplicate_part_no(part_no, *, exclude_pk=None):
    others = Item.objects.filter(part_no=part_no)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        raise ValueError(f"品番 {part_no} は既にあります。別の品番にしてください。")


def _refuse_duplicate_partner_code(code, *, exclude_pk=None):
    others = Partner.objects.filter(code=code)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        raise ValueError(f"取引先コード {code} は既にあります。別のコードにしてください。")


def _refuse_unknown_role(role):
    if role not in _ROLES:
        raise ValueError(
            f"取引先の種類 {role!r} は使えません。"
            "得意先 (customer)・外注先 (subcontractor)・両方 (both) のどれかにしてください。"
        )


def _payment_terms(data):
    """支払の月と支払日を見て、空を表の空の値 (支払の月は ""、支払日は None) にそろえた data を返す。

    支払の月は 当月 (current)・翌月 (next)・翌々月 (after_next) か空。支払日は 1〜31 の整数か空。外れは ValueError。
    """
    data = dict(data)
    if "payment_month" in data:
        month = data["payment_month"] or ""
        if month and month not in _PAYMENT_MONTHS:
            raise ValueError(
                f"支払の月 {month!r} は使えません。"
                "当月 (current)・翌月 (next)・翌々月 (after_next) のどれかか、空にしてください。"
            )
        data["payment_month"] = month
    if "payment_day" in data:
        day = data["payment_day"]
        if day == "":
            day = None
        if day is not None and (isinstance(day, bool) or not isinstance(day, int) or day not in _PAYMENT_DAY_RANGE):
            raise ValueError(f"支払日 {day!r} は使えません。1〜31 の整数か空にしてください (月末は 31)。")
        data["payment_day"] = day
    return data


def _refuse_duplicate_process_code(code, *, exclude_pk=None):
    others = Process.objects.filter(code=code)
    if exclude_pk is not None:
        others = others.exclude(pk=exclude_pk)
    if others.exists():
        raise ValueError(f"工程コード {code} は既にあります。別のコードにしてください。")


#: 図面の画素の上限。A0 を 400dpi で読んだ絵 (約 2.5 億画素) が入る大きさ (00 の案)。
DRAWING_MAX_PIXELS = 300_000_000
#: 表示用の PNG の長い辺の上限。大きい絵は縮める (原寸は元のファイルの link)。
PREVIEW_MAX_SIDE = 2000
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
    """新しく上げた図面を読み、(表示用の PNG の bytes か None, 誤りの文か "") を返す。例外を外に出さない。

    拡張子が DRAWING_IMAGE_SUFFIXES でなければ読まない。読んだ中身が PNG・JPEG ならそのまま絵で出すので None。
    ほか (TIFF など) は 1 ページ目を PNG にし、長い辺を PREVIEW_MAX_SIDE まで縮める。
    壊れている・大きすぎるときは None と誤りの文。読んだ後は (誤りのときも) 読む位置を先頭に戻す
    (戻さないと元のファイルの保存が空になる)。
    """
    if not (getattr(file, "name", "") or "").lower().endswith(DRAWING_IMAGE_SUFFIXES):
        return None, ""
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
        return None, DRAWING_BROKEN_MESSAGE
    finally:
        file.seek(0)


def _read_drawing(file):
    """_drawing_preview の中身。Pillow の上限を外した中で呼ぶ。"""
    with Image.open(file) as image:
        width, height = image.size
        if width * height > DRAWING_MAX_PIXELS:
            return None, DRAWING_TOO_LARGE_MESSAGE.format(width=width, height=height)
        image.load()  # Image.open は頭だけ読む。壊れた中身はここで例外になる。複数ページは開いたときの 1 枚目
        if image.format in ("PNG", "JPEG"):
            return None, ""
        preview = image
        if preview.mode == "1":
            preview = preview.convert("L")
        elif preview.mode not in _PREVIEW_MODES:
            has_alpha = "A" in preview.getbands() or "transparency" in preview.info
            preview = preview.convert("RGBA" if has_alpha else "RGB")
        if max(preview.size) > PREVIEW_MAX_SIDE:
            preview.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE), Image.Resampling.LANCZOS)
        buffer = io.BytesIO()
        preview.save(buffer, format="PNG")
        return buffer.getvalue(), ""


def _with_drawing_preview(data):
    """data の図面 (drawing) を見て、表示用 (drawing_preview) と誤り (drawing_preview_error) を足した data を返す。

    読むのは表と置き場に書く前。足した 2 つは部品の行と元のファイルと一緒に 1 回で書く。
    - 新しく上げたファイル: 読んで、表示用の PNG (作れたとき) と誤りの文 (作れなかったとき) を足す。
    - 今の FieldFile (選び直していない) と、鍵が無いとき: data のまま (3 つとも変えない)。
    - False・None・空 (入力の画面の「消す」): 図面と 2 つを空の字にする。
    """
    if "drawing" not in data:
        return data
    drawing = data["drawing"]
    if _is_new_upload(drawing):
        png, error = _drawing_preview(drawing)
        preview = ""
        if png is not None:
            stem = os.path.splitext(os.path.basename(drawing.name))[0]
            preview = ContentFile(png, name=f"{stem}.png")
        return {**data, "drawing_preview": preview, "drawing_preview_error": error}
    if isinstance(drawing, FieldFile):
        return data
    if not drawing:
        return {**data, "drawing": "", "drawing_preview": "", "drawing_preview_error": ""}
    return data


def create_item(data, user, *, ext=None):
    """品目を作る。data は Item の列の名前を鍵にした辞書 (part_no と name は必須)。

    図面に新しいファイルが来たら、表示用の PNG を作る (作れなければ理由を drawing_preview_error に残し、部品は保存する)。
    """
    _refuse_unknown_keys(data, _ITEM_KEYS, "部品")
    data = _with_drawing_preview(data)
    with transaction.atomic():
        _refuse_duplicate_part_no(data["part_no"])
        item = Item.objects.create(**data)
        _save_ext(item, ext)
        return item


def update_item(item, data, user, *, ext=None):
    """data にある鍵だけを書き換える。品番を変えるときも一意を見る。

    図面は、新しいファイルなら元・表示用・誤りの 3 つを書き換え、「消す」(False・None・空) なら 3 つとも空にし、
    今のファイル (FieldFile) か鍵が無ければ 3 つとも変えない。差し替え・外した古いファイルは置き場に残す。
    """
    _refuse_unknown_keys(data, _ITEM_KEYS, "部品")
    data = _with_drawing_preview(data)
    with transaction.atomic():
        if "part_no" in data:
            _refuse_duplicate_part_no(data["part_no"], exclude_pk=item.pk)
        if _is_new_upload(data.get("drawing")):
            _keep_drawing_as_version(item)
        for key, value in data.items():
            setattr(item, key, value)
        item.save()
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
        raise ValueError(f"版 {version.version_no} はこの部品の版ではありません。この部品の版を選んでください。")
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

    支払の月は current・next・after_next か空、支払日は 1〜31 の整数か空 (31 は月末)。
    """
    _refuse_unknown_keys(data, _PARTNER_KEYS, "取引先")
    _refuse_unknown_role(data.get("role"))
    data = _payment_terms(data)
    with transaction.atomic():
        _refuse_duplicate_partner_code(data["code"])
        partner = Partner.objects.create(**data)
        _save_ext(partner, ext)
        return partner


def update_partner(partner, data, user, *, ext=None):
    """data にある鍵だけを書き換える。取引先の種類・取引先コード・支払の条件は create_partner と同じ決まりで見る。"""
    _refuse_unknown_keys(data, _PARTNER_KEYS, "取引先")
    if "role" in data:
        _refuse_unknown_role(data["role"])
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
    """工程を作る。工程コードは一意。data の鍵は code・name・is_outsourced・sort_order・use_in_plan。"""
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
                f"取引先 {partner} の品番 {partner_part_no} は、既に {existing.item} に対応しています。"
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


def round_amount(partner, value):
    """金額 (数量 × 単価) の端数を 1 円の単位に丸める 1 か所の関数。

    方法は取引先の小計の計算方法。空なら会社の既定 (Company の 1 行の rounding。行が無ければ round)。
    round = 四捨五入、ceil = 切上 (ROUND_CEILING)、floor = 切捨 (ROUND_FLOOR)。
    """
    mode = partner.rounding or _company_rounding()
    return value.quantize(Decimal("1"), rounding=_ROUNDING_MODES[mode])


def _company_rounding():
    # core の services.company() ができたら、それに替える。
    company = Company.objects.first()
    return company.rounding if company is not None else "round"


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


def candidates(kind, query, page, *, per_page=30):
    """選ぶ欄の候補を 1 ページ返す (05 の 2 節)。

    kind は "item" (表示の字は「品番 品名」) か "partner" (「コード 名前」)。
    表示の字と query を畳み、query を空白で切った語の全部を含む行を出す。空の query は全件。
    page が整数に読めなければ 1。母集団は数千件の前提で、全件を読んでから Python で絞る。
    """
    if kind == "item":
        rows = [
            (pk, f"{part_no} {name}")
            for pk, part_no, name in Item.objects.values_list("pk", "part_no", "name")
        ]
    elif kind == "partner":
        rows = [
            (pk, f"{code} {name}")
            for pk, code, name in Partner.objects.values_list("pk", "code", "name")
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


def _import_rows(rows, user, model, key, label, create, update):
    """CSV の行を 1 つのトランザクションで入れる。鍵の列の値が既にあれば update、無ければ create。

    どれかの行で services が断ったら、行の番号を付けて ValueError を上げ直す (全部が巻き戻り、1 件も入らない)。
    """
    made = updated = 0
    with transaction.atomic():
        for row in rows:
            line, data, ext = row["line"], row["data"], row.get("ext")
            try:
                if _is_blank(data.get(key)):
                    raise ValueError(f"{label}が空です。値を入れてください。")
                current = model.objects.filter(**{key: data[key]}).first()
                if current is None:
                    create(data, user, ext=ext)
                    made += 1
                else:
                    update(current, data, user, ext=ext)
                    updated += 1
            except ValueError as exc:
                raise ValueError(f"{line} 行目: {exc}") from exc
    return {"入れた": made, "更新した": updated}


def import_items(rows, user):
    """部品の CSV の行を入れる (05 の 8 節)。自社品番が既にあれば update_item、無ければ create_item。

    rows の 1 つは {"line": 行の番号, "data": {列の名前: 値}, "ext": {会社ごとの項目の列の名前: 値} か None}。
    data は CSV にある列だけ (無い列は更新しても今の値のまま)。返す物は {"入れた": n, "更新した": m}。
    """
    return _import_rows(rows, user, Item, "part_no", "自社品番", create_item, update_item)


def import_partners(rows, user):
    """取引先の CSV の行を入れる。取引先コードが既にあれば update_partner、無ければ create_partner。形は import_items と同じ。"""
    return _import_rows(rows, user, Partner, "code", "取引先コード", create_partner, update_partner)


def set_item_processes(item, processes, user):
    """部品の標準の工程の並びを置き換える。processes は Process の並びで、並びの順に順を 1 から付ける。

    今の並びは消してから作り直す。空の並び・工程でない物・同じ工程の 2 回は、何も変えずに日本語の ValueError。
    返す物は作った ItemProcess の並び。user は受けるだけ。
    """
    processes = list(processes)
    if not processes:
        raise ValueError("工程がありません。工程を 1 つ以上渡してください。")
    for process in processes:
        if not isinstance(process, Process):
            raise ValueError(f"工程でない物 ({process!r}) が入っています。工程 (Process) だけを渡してください。")
    seen = set()
    for process in processes:
        if process.pk in seen:
            raise ValueError(f"工程 {process.code} が 2 回あります。同じ工程は 1 回だけにしてください。")
        seen.add(process.pk)
    with transaction.atomic():
        item.item_processes.all().delete()
        return [item.item_processes.create(process=process, seq=seq) for seq, process in enumerate(processes, start=1)]
