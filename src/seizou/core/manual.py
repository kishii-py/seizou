"""使い方のページ (画面の目録と使い方の設計の決め。画面ごとの説明書。業務マニュアルの部品 seizou.manuals とは別)。会社の置き場の manual/ にある画面ごとの説明書を、目次つきで 1 ページに並べる画面と、その約束。

約束 (会社の置き場が持つ物。/seizou:manual の命令が書く):
  置き場: Path(settings.BASE_DIR) / "manual" の 1 か所。BASE_DIR が無ければ、置き場が無いのと同じに扱う。
  断片: 画面の目録 (seizou.core.screens.all_screens) の 1 画面ごとに manual/<名前>.html。
    名前は URL の名前の : を _ に替えた物 (例 orders:order_list → manual/orders_order_list.html)。
    中身は素の HTML の <section> 1 つ (class・<script>・<style> を持たない)。UTF-8 で書く。
    断片の無い画面には「まだありません」の文を出す。UTF-8 で読めない断片の画面には、作り直しを頼む 1 文を出す。
  前書き: manual/index.html があれば、本文の先頭に出す (無くてよい)。
  絵: manual/images/<名前>.png。断片の中から images/<名前>.png の相対の道で指す。
    ページの道は末尾に / を付けた /manual/ なので、相対の道は /manual/images/<名前> に解ける。
    そこは manual_image が返す (ログインが要る。拡張子は .png・.jpg・.jpeg・.webp だけ)。
    冊子 (PDF) では <base href="/manual/"> で同じ道に解け、帳票の部品 (core.reports.fetch_local の /manual/images/ の根) が manual/images/ から同じ制限で読む。
  読むだけ。置き場に書かない。断片の中身は直さず、そのまま出す (mark_safe)。

画面の並び:
  目次と本文は目録の並び (部品の並びと URL の並び) のまま。左のメニューの区分には分けない。
  使い方の画面と印刷の目次は、頭に「はじめに」(前書きがあるとき) と「業務の流れ」(流れがあるとき) の行を置き、その後が目録の並び (開発の記録 docs/nights/20261106/N265.md)。
  /manual/?print=1 は印刷用の 1 枚 (core/manual_print.html。左のメニューと上の帯なし) を返す。

冊子 (PDF):
  /manual/pdf/ (manual_pdf) は、表紙・目次・画面ごとの章・ページ番号の冊子 (core/manual_booklet.html) を帳票の部品 (core.reports) で PDF にして返す。
  PDF を作れなければ、同じ 1 枚を印刷用の HTML にし、上に知らせを出して返す。
  命令 manage.py seizou_manual_pdf は、同じ冊子を manual/使い方.pdf に書き出す (--out で書き出し先を替える)。
  口の Content-Disposition は ASCII の名前 (PDF_FILENAME) と日本語の名前 (BOOKLET_FILENAME。UTF-8 を % で書いた filename*) の両方を持つ。
  冊子に絵が入る (断片の <img src="images/…"> をそのまま出し、帳票の部品が manual/images/ から読む。無い絵は飛ばす)。
  表紙の主色は BOOKLET_PRIMARY_COLOR の固定の 1 色 (会社の行に色の列が無い)。副題は BOOKLET_SUBTITLE。

小窓 (画面と枠の「?」):
  /manual/fragment/<anchor>/ (manual_fragment) は、htmx の要求に、その画面の断片を小窓の中身 (core/_manual_fragment.html) にして返す。
  ?part=<h3 の id> を付けると、断片のその <h3 id="…"> から次の <h3 か </section> の前までだけを出す。無ければ画面の全部を出す。
  htmx でない要求 (新しいタブ・JS の無いとき) は /manual/#<anchor> へ移す。anchor が画面の目録に無ければ 404。
  絵の相対の道 (src="images/…") は、小窓を開いたページの道で解けないように /manual/images/ に直して出す (置き場のファイルは直さない)。

自動の付け足し (開発の記録 docs/nights/20261105/N168.md。置き場のファイルは直さず、出すときに断片の字の後に足す。断片の無い画面と読めない画面には足さない):
  入れる物: 画面の view が form_class を持ち、断片に <h3>入れる物</h3> が無ければ、欄の名前・必須・説明の表 (input_rows)。
  絵: 断片に <img が無く、manual/images/<名前>.png があれば、その絵 (命令 manage.py seizou_manual_shots が撮る)。
  止まるときの文: 部品の services.py の断りの文のうち REFUSAL_HEADS で始まり、画面から起きない文の表 (NOT_FROM_SCREEN) に無い物 (refusal_texts)。
    使い方の画面と印刷と小窓は、画面の view の service からたどれる文だけを「この画面で止まるときの文」として画面ごとに足す (screen_refusals。開発の記録 docs/nights/20261106/N256.md)。
    service を持たない画面 (一覧・詳細・ダッシュボードなど) と、たどれる文が無い画面には足さない。
    冊子は、その部品の最初の章の前に 1 回だけ置く (manual_sections(booklet=True) の app_refusals)。小窓で枠 (?part=) を選んだときは何も足さない。

業務の流れの章 (開発の記録 docs/nights/20261105/N169.md。断片は使わず、固定の表 FLOWS から描く):
  使い方の画面と印刷の 1 枚は画面の箱の並びの前に、冊子は目次 (と前書き) の後で最初の画面の章の前に、同じ core/_manual_flows.html を出す。
  手順の画面の名前は、同じページのその画面の章 (id = 断片の名前) へ飛ぶ。
  使い方の画面と印刷の目次には頭に 1 行入れ (開発の記録 docs/nights/20261106/N265.md)、冊子の目次と章の番号と栞には入れない。
  手順の画面が目録に無い (部品が入っていない) 手順は落とす。最初の手順が落ちた流れは出さない。流れが 1 つも無ければ章を出さない。
"""

from __future__ import annotations

import ast
import re
from datetime import date, datetime
from datetime import timezone as dt_timezone
from importlib import metadata
from pathlib import Path
from urllib.parse import quote

from django import forms as django_forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db import DatabaseError
from django.http import FileResponse, Http404
from django.http import HttpResponse
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.urls import Resolver404, resolve, reverse
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe
from django.views.decorators.cache import never_cache
from django.views.generic import TemplateView

from seizou.core import reports
from seizou.core.screens import all_screens
from seizou.core.views import ScreenMixin

#: 断片を UTF-8 で読めなかった印 (read_fragment が返す)
UNREADABLE = object()

#: 前書きの断片の名前 (manual/index.html)
INTRO_NAME = "index"

#: 絵の口が返す拡張子と型 (SVG は中に script を持てるので出さない)
IMAGE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}

#: 印刷用の 1 枚を選ぶ GET の値 (/manual/?print=1)
PRINT_PARAM = "print"

#: 冊子の 1 枚 (帳票の部品 core.reports が描く)
BOOKLET_TEMPLATE = "core/manual_booklet.html"

#: 命令の既定の書き出し先の名前 (会社の置き場の manual/ の下)
BOOKLET_FILENAME = "使い方.pdf"

#: 口の Content-Disposition の ASCII の名前 (filename=。古いブラウザ用)。日本語の名前は BOOKLET_FILENAME を UTF-8 の % の形の filename* で添える
PDF_FILENAME = "seizou-manual.pdf"

#: PDF を作れないときの知らせ (shipping・manuals の PDF_NOTICE と同じ字。ほかの app の views は import しない)
PDF_NOTICE = "PDF の準備がありません。印刷用の画面をお使いください。"

#: 枠を選ぶ GET の鍵 (/manual/fragment/<anchor>/?part=<h3 の id>)
PART_PARAM = "part"

#: 枠の id の形。外れた値は「無い」と同じ扱い (画面の全部を出す)。値を HTML に戻さない
PART_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

#: 小窓の中身 (小窓の枠は base.html の #seizou-modal の 1 か所)
FRAGMENT_TEMPLATE = "core/_manual_fragment.html"

#: 断片の無い画面の 1 文の前半 (テンプレートが /seizou:manual の案内を続ける)
MISSING_SENTENCE = "この画面の説明書はまだありません。"

#: 業務の流れの章の流れ (開発の記録 docs/nights/20261105/N169.md)。1 つの流れ = {"title", "steps"}。1 つの手順 = {"url_name", "do", "stage", "note"} と、相手の会社の手順だけ "remote": True。
#: url_name は画面の目録の URL の名前 (相手の会社の手順は、相手が使う同じ画面)。do は人がすること、note は終わると変わること (無ければ空)。
#: stage は受注の段 (orders.timeline.STAGES) か発注の段 (purchasing.timeline.STAGES) の字そのまま (段の変わらない手順は空)。字を変えるときは、その 2 つと合わせる。
FLOWS = (
    {
        "title": "注文を受けてから入金まで",
        "steps": (
            {"url_name": "orders:order_create", "do": "注文書や電話で受けた注文を、得意先と明細を入れて登録する。", "stage": "受注", "note": "生産管理ボードに出る。"},
            {"url_name": "connect:inbox_accept", "do": "seizou でつながった取引先の注文は、受信した注文から受け入れる。", "stage": "受注", "note": "受注ができ、相手に注文回答が送られる。"},
            {"url_name": "receiving:receiving_create", "do": "得意先からワークや支給材が届いたら「入荷」で記録する (表面処理のように現物が無いと始められない会社向け)。", "stage": "", "note": ""},
            {"url_name": "workorders:workorder_create", "do": "生産管理ボードで明細を選び、指示書を発行する。", "stage": "指示", "note": ""},
            {"url_name": "progress:progress_create", "do": "最初の工程の実績を入れる。", "stage": "着手", "note": "指示書の状態が「着手済」になる。"},
            {"url_name": "progress:progress_create", "do": "工程が終わるたびに、実績の数と日時を入れる。", "stage": "完了", "note": "最後の工程の実績に「この工程を完了にする」を付けると「完了」になる。"},
            {"url_name": "shipping:shipment_create", "do": "できた物を送るときに、明細ごとに出荷を記録する。", "stage": "出荷", "note": "受注の状態が「一部出荷」か「出荷済」になる。"},
            {"url_name": "shipping:deliverynote_create", "do": "同じ得意先の出荷をまとめて、納品書を作る。", "stage": "納品", "note": ""},
            {"url_name": "billing:invoice_create", "do": "締日の後に、出荷をまとめて請求書を作る。", "stage": "請求", "note": ""},
            {"url_name": "billing:payment_create", "do": "入金があったら、請求書に入金の日付と金額を記録する。", "stage": "入金", "note": "請求書の残が減る。"},
        ),
    },
    {
        "title": "発注から受入まで",
        "steps": (
            {"url_name": "purchasing:purchaseorder_create", "do": "協力企業と明細を入れて、発注を保存する。", "stage": "発注", "note": "seizou でつながった協力企業には注文が送られ、段が「送付」に進む。"},
            {"url_name": "purchasing:purchaseorder_detail", "do": "協力企業の注文回答と出荷の知らせを、発注の詳細で確かめる。", "stage": "回答", "note": "seizou でつながった協力企業だけ。出荷の知らせが届くと段が「出荷の知らせ」に進む。"},
            {"url_name": "purchasing:receipt_create", "do": "物が届いたら、発注の明細ごとに受入日と数を記録する。", "stage": "受入", "note": "seizou でつながった協力企業には検収が送られ、段が「検収」に進む。"},
        ),
    },
    {
        "title": "取引先とつなぐ",
        "steps": (
            {"url_name": "connect:connection_create", "do": "取引先ごとに、向き「送る」と「受ける」の 2 件を、同じ鍵の置き場の名前で登録する。", "stage": "", "note": "「送る」には相手の受け口の URL を入れる。"},
            {"url_name": "connect:connection_detail", "do": "会社の置き場で manage.py seizou_secret を打って鍵の値を作り、.env に置いて相手に人の手で渡す。", "stage": "", "note": "「鍵の設定」が「済み」になる。"},
            {"url_name": "connect:connection_create", "remote": True, "do": "相手の会社も、こちらとの接続を 2 件登録し、受け取った鍵の値を .env に置く。", "stage": "", "note": ""},
            {"url_name": "purchasing:purchaseorder_create", "do": "接続した協力企業に発注を保存する。", "stage": "送付", "note": "注文が相手の「受信した注文」に入る。"},
            {"url_name": "connect:inbox_accept", "remote": True, "do": "相手が受信した注文を受け入れる。", "stage": "回答", "note": "注文回答が返り、発注の状態が「相手が受け入れた」になる。"},
            {"url_name": "shipping:shipment_create", "remote": True, "do": "相手が出荷を記録する。", "stage": "出荷の知らせ", "note": "出荷の知らせが届く。"},
            {"url_name": "purchasing:receipt_create", "do": "届いた物を受入に記録する。", "stage": "検収", "note": "検収が相手に送られ、相手の受注の明細に入る。"},
        ),
    },
)

#: 冊子の主色 (表紙の帯と題・章の頭の帯と番号)。会社の行に色の列が無いので固定の濃い青
BOOKLET_PRIMARY_COLOR = "#1f3a5f"

#: 冊子の表紙の副題
BOOKLET_SUBTITLE = "画面ごとの操作の手引きと、お金と数の決まり"

#: 止まるときの文の 1 文目の語 (開発の記録 docs/nights/20261105/N156.md の型。tests/test_wording.py の REFUSAL_HEADS と同じ字の組)
REFUSAL_HEADS = (
    "保存できません。",
    "取り消せません。",
    "削除できません。",
    "送れません。",
    "受け入れられません。",
    "断れません。",
    "発行できません。",
    "着手できません。",
    "記録できません。",
    "出荷できません。",
    "読み取れません。",
    "保留できません。",
)

#: 止まるときの文のうち、画面からは起きない物 (開発の記録 docs/nights/20261106/N256.md = 作者の決め)。1 行 = (部品, refusal_texts の字, 画面から起きない訳)。
#: 画面の form が作らない値 (英字の鍵や選択肢の外の値) への断りで、画面を使う人には起きないので、冊子・使い方の画面・印刷・小窓の全部から除く。
#: 字は _literal_text の字 (f-string の {…} は「…」)。英字を含むかで一律に除かない (.env・URL・brand_color・industry の文は画面から起きる)。
#: services.py の文を直して字が合わなくなると tests/test_core_manual_auto.py が落ちる (表が何も言わずに効かなくなるのを防ぐ)。
NOT_FROM_SCREEN = (
    (
        "workorders",
        "発行できません。知らない項目 (…) があります。指示書の工程には process と planned_qty だけを渡してください。",
        "指示書の発行の form は、工程を必ず process と planned_qty の 2 つの鍵で作る (workorders/forms.py の service_kwargs)。",
    ),
    (
        "workorders",
        "発行できません。工程の項目 (…) がありません。指示書の工程には process と planned_qty を渡してください。",
        "同じ (発行の form が 2 つの鍵を必ず作る)。",
    ),
    (
        "orders",
        "保存できません。入口 … は使えません。手入力 (manual)・読み取り (intake)・受信した注文 (inbox) のどれかにしてください。",
        "受注の form は source を渡さない (orders/forms.py に source の欄が無い)。",
    ),
    (
        "orders",
        "保存できません。明細の id (…) と order_line (…) が違う明細を指しています。どちらか 1 つにするか、同じ明細を指してください。",
        "明細の form の line() は order_line の鍵だけを渡し、id を渡さない (orders/forms.py の LINE_KEYS に id が無い)。",
    ),
    (
        "connect",
        "保存できません。向き … は使えません。送る (send) か受ける (receive) にしてください。",
        "つながりの form は model の選ぶ欄の向きをそのまま使い、選択肢の外の値は form が止める。つながりの CSV の取り込みは無い。",
    ),
    (
        "connect",
        "受け入れられません。承認した日時は timezone 付きの日時にしてください。",
        "受け入れの form は item_mapping だけを渡す。accepted_at は作り物の種のための引数。",
    ),
    (
        "connect",
        "送れません。言葉が 2000 字を超えています。分けて送ってください。",
        "会話の書く欄は form の max_length で 2000 字まで。超えた字は form が止める (connect/forms.py の ChatMessageForm)。",
    ),
    (
        "connect",
        "保存できません。自社の連絡先のメールアドレスが、メールアドレスの形ではありません。例 info@example.jp の形で入れてください。",
        "つながりの form の欄は model のメールアドレスの欄 (254 字まで) で、形の違う字と長い字は form が止める (connect/forms.py の ConnectionForm)。",
    ),
    (
        "core",
        "保存できません。… は画面からは変えられません。答えの YAML の settings に書いてください。",
        "設定の変更の画面は、画面から変えない鍵を 404 にする (core/settings_screen.py の SettingUpdateView.dispatch)。",
    ),
    (
        "core",
        "保存できません。… の値は真偽 (true / false) にしてください (入れた値: …)。",
        "真偽の鍵は丸の選択で、form が真偽に直して渡す (core/forms.py の SettingForm)。",
    ),
    (
        "core",
        "保存できません。access_control は department・write・money の 3 つの鍵を持つ辞書で、値はそれぞれ off / log / on にしてください (入れた値: …)。",
        "権限の判定は 3 つの選ぶ欄で、form が 3 つの鍵の辞書を作る (core/forms.py の SettingForm)。",
    ),
    (
        "core",
        "保存できません。hidden_fields は項目の名前の並びにしてください (入れた値: …)。",
        "使わない項目は複数の check で、form が字の並びを作る (core/forms.py の SettingForm)。",
    ),
    (
        "core",
        "保存できません。メールの送り方は、host・port・security・user・from・from_name・site_url の鍵を持つ辞書にしてください。",
        "メールの送り方の変更の画面は 7 つの欄で、form が .env で決めない 4 つの鍵の辞書を作る (core/forms.py の SettingForm)。",
    ),
    (
        "core",
        "保存できません。メールの送り方の「サーバー」は、空白の無い 253 字までの名前にしてください (例 smtp.example.jp)。",
        "サーバーは置き場の .env の EMAIL_HOST で決め、画面の欄は読むだけ (core/forms.py の SettingForm。core/conf.py の MAIL_ENV)。",
    ),
    (
        "core",
        "保存できません。メールの送り方の「ポート」は 1〜65535 の整数にしてください。",
        "ポートは置き場の .env の EMAIL_PORT で決め、画面の欄は読むだけ (core/forms.py の SettingForm。core/conf.py の MAIL_ENV)。",
    ),
    (
        "core",
        "保存できません。メールの送り方の「暗号化の方式」は starttls・ssl・none のどれかにしてください。",
        "暗号化の方式は置き場の .env の EMAIL_SECURITY で決め、画面の欄は読むだけ (core/forms.py の SettingForm。core/conf.py の MAIL_ENV)。",
    ),
    (
        "core",
        "保存できません。メールの送り方の「アカウント」は 254 字までにしてください。",
        "アカウントの欄は 254 字までで、長い字は form が止める (core/forms.py の SettingForm)。",
    ),
    (
        "core",
        "保存できません。メールの送り方の「差出人」がメールアドレスの形ではありません。例 notice@example.jp の形で入れてください。",
        "差出人の欄はメールアドレスの欄で、形の違う字と長い字は form が止める (core/forms.py の SettingForm)。",
    ),
    (
        "core",
        "保存できません。メールの送り方の「差出人の名前」は、改行の無い 100 字までにしてください。",
        "差出人の名前の欄は 100 字までの 1 行の欄で、長い字は form が止め、改行は打てない (core/forms.py の SettingForm)。",
    ),
    (
        "masters",
        "保存できません。取引先の種類 … は使えません。得意先 (customer)・協力企業 (subcontractor)・両方 (both) のどれかにしてください。",
        "取引先の form は model の選ぶ欄をそのまま使う。CSV の取り込みは、選ぶ欄の字を core/seeds.py の _choice_value が services より前に断る。",
    ),
    (
        "masters",
        "保存できません。支払の月 … は使えません。当日 (same_day)・当月 (current)・翌月 (next)・翌々月 (after_next) のどれかか、空にしてください。",
        "form は model の選ぶ欄。CSV は、支払の月の列があれば _choice_value が断り、無ければ read_payment_day (masters/forms.py) が選択肢の字からだけ月を作る。",
    ),
    (
        "masters",
        "保存できません。工程でない物 (…) が入っています。工程 (Process) だけを渡してください。",
        "標準の工程の 3 つの画面の form と services は、工程の表の行 (Process) だけを渡す (masters/forms.py の _ProcessChoices と、今の並びの行の process)。",
    ),
    (
        "masters",
        "保存できません。工程 … が 2 回あります。同じ工程は 1 回だけにしてください。",
        "標準の工程の追加の候補は並びに無い工程だけで、add_item_processes は今ある工程を飛ばす。順を直すと外すは今の並びの行から並びを作る。",
    ),
)

#: 断片が自分で持つ「入れる物」の見出し (あれば表を足さない)
INPUT_HEADING = re.compile(r"<h3\b[^>]*>入れる物</h3>")

#: 道の引数 (<int:pk> など)。画面の view を引くときは 0 に替える
PATH_ARG = re.compile(r"<(?:\w+:)?\w+>")


def manual_dir() -> Path | None:
    """説明書の置き場 (会社の置き場の manual/)。settings に BASE_DIR が無ければ None。"""
    base = getattr(settings, "BASE_DIR", None)
    if not base:
        return None
    return Path(base) / "manual"


def fragment_name(url_name: str) -> str:
    """断片の名前。URL の名前の : を _ に替えた物 (例 orders:order_list → orders_order_list)。"""
    return url_name.replace(":", "_")


def manual_flows() -> list[dict]:
    """業務の流れの章の材料 (FLOWS の並びのまま)。手順の画面が目録に無い (部品が入っていない) 手順は落とす。

    最初の手順が落ちた流れは入れない (流れの入口の部品が無い)。読むだけ。
    1 つの流れ = {"title", "steps"}。1 つの手順 = {"anchor" (飛ぶ先の断片の名前), "screen" (画面の名前), "remote", "do", "stage", "note"}。
    """
    names = {screen.url_name: screen.name for screen in all_screens()}
    flows = []
    for flow in FLOWS:
        if flow["steps"][0]["url_name"] not in names:
            continue
        steps = [
            {
                "anchor": fragment_name(step["url_name"]),
                "screen": names[step["url_name"]],
                "remote": step.get("remote", False),
                "do": step["do"],
                "stage": step["stage"],
                "note": step["note"],
            }
            for step in flow["steps"]
            if step["url_name"] in names
        ]
        flows.append({"title": flow["title"], "steps": steps})
    return flows


def read_fragment(name: str):
    """manual/<name>.html を UTF-8 で読んだ字。ファイルが無ければ None、UTF-8 で読めなければ UNREADABLE。"""
    folder = manual_dir()
    if folder is None:
        return None
    path = folder / f"{name}.html"
    if not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return UNREADABLE


def view_class_of(screen):
    """目録の画面 (Screen) の view の class。道の引数を 0 に替えて URL の木で引く。引けなければ None。"""
    try:
        return resolve(PATH_ARG.sub("0", screen.path)).func.view_class
    except (Resolver404, AttributeError):
        return None


def _literal_text(node) -> str | None:
    """断りの文の式の字。f-string の {…} は「…」に、足し算の右の名前や呼び出しも「…」にする。字で始まらない式は None。"""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(part.value if isinstance(part, ast.Constant) else "…" for part in node.values)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = _literal_text(node.left)
        if left is None:
            return None
        right = _literal_text(node.right)
        return left + ("…" if right is None else right)
    return None


def _services_tree(app: str):
    """部品 (seizou の下の app) の services.py を pathlib で読み、ast にした物 (import しない・inspect を使わない)。無ければ None。"""
    path = Path(__file__).resolve().parent.parent / app / "services.py"
    if not path.is_file():
        return None
    return ast.parse(path.read_text(encoding="utf-8"))


def _raised_texts(tree) -> list[str]:
    """ast の木の中の raise ValueError と raise RuleViolation の文のうち、REFUSAL_HEADS で始まる物。同じ文は 1 つにし、ast.walk の並びで返す。"""
    texts: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call)):
            continue
        func = node.exc.func
        name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", "")
        if name not in ("ValueError", "RuleViolation"):
            continue
        for arg in node.exc.args:
            text = _literal_text(arg)
            if text and text.startswith(REFUSAL_HEADS) and text not in texts:
                texts.append(text)
    return texts


def _not_from_screen(app: str) -> set[str]:
    """画面から起きない文の表 (NOT_FROM_SCREEN) のうち、部品 app の字の集合。"""
    return {text for part, text, _reason in NOT_FROM_SCREEN if part == app}


def all_refusal_texts(app: str) -> list[str]:
    """部品の services.py の断りの文のうち REFUSAL_HEADS で始まる物の全部 (画面から起きない文の表 NOT_FROM_SCREEN を当てる前)。

    services.py の無い部品と、会社が足した app は空の並び。相手の会社への返事の文は頭の語で始まらないので入らない。
    """
    tree = _services_tree(app)
    return [] if tree is None else _raised_texts(tree)


def refusal_texts(app: str) -> list[str]:
    """部品 (seizou の下の app) の services.py の raise ValueError と raise RuleViolation の文のうち、REFUSAL_HEADS で始まる物。

    ファイルを pathlib で読み、ast で見る (import しない・inspect を使わない)。同じ文は 1 つにし、ファイルの並びのまま返す。
    services.py の無い部品と、会社が足した app は空の並び。相手の会社への返事の文は頭の語で始まらないので入らない。
    画面から起きない文の表 (NOT_FROM_SCREEN) の文は除く (冊子・使い方の画面・印刷・小窓の全部に効く。開発の記録 docs/nights/20261106/N256.md)。
    """
    excluded = _not_from_screen(app)
    return [text for text in all_refusal_texts(app) if text not in excluded]


def _refusal_index(app: str) -> dict:
    """部品 1 つの、画面ごとの止まるときの文をたどる材料。services.py を 1 回だけ読む。

    texts = refusal_texts(app) と同じ並び。owners = {文: その文を raise する services.py の頭の関数の名前の集合}。
    refs = {頭の関数の名前: その関数の中で名前 (ast.Name) で引いている、同じ services.py の頭の関数の名前の集合}。
    名前を全部見るので、呼び出しのほかに、関数を引数で渡す形 (例 masters の import_partners が _import_rows に create_partner を渡す) も入る。
    """
    tree = _services_tree(app)
    if tree is None:
        return {"texts": [], "owners": {}, "refs": {}}
    excluded = _not_from_screen(app)
    top = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
    owners: dict[str, set[str]] = {}
    refs: dict[str, set[str]] = {}
    for name, node in top.items():
        for text in _raised_texts(node):
            owners.setdefault(text, set()).add(name)
        refs[name] = {sub.id for sub in ast.walk(node) if isinstance(sub, ast.Name) and sub.id in top and sub.id != name}
    texts = [text for text in _raised_texts(tree) if text not in excluded]
    return {"texts": texts, "owners": owners, "refs": refs}


def screen_refusals(screen, indexes: dict[str, dict] | None = None) -> list[str]:
    """画面 (目録の Screen) 1 つの止まるときの文。その画面の view の service からたどれる文だけを、refusal_texts の並びで返す。

    service は view の class の属性 (土台の BaseFormView の service。前例 core/access.py)。無ければ空の並び (節を出さない)。
    読む services.py は service.__module__ の部品の名前で決める (前例 core/views/ の get_action)。seizou の部品の services.py の外の関数は空。
    service の関数から、同じ services.py の頭の関数を名前でたどり、新しい関数が出なくなるまで繰り返す。別の部品の services.py へはたどらない。
    同じ字の文が 2 つ以上の関数にあるときは、どれか 1 つの関数がたどれれば残す。
    indexes は部品ごとの材料の入れ物 (1 回の描画で services.py を部品ごとに 1 回だけ読むため)。
    """
    service = getattr(view_class_of(screen), "service", None)
    if service is None:
        return []
    module = getattr(service, "__module__", "").split(".")
    start = getattr(service, "__name__", "")
    if len(module) != 3 or module[0] != "seizou" or module[2] != "services":
        return []
    app = module[1]
    if indexes is None:
        indexes = {}
    if app not in indexes:
        indexes[app] = _refusal_index(app)
    index = indexes[app]
    if start not in index["refs"]:
        return []
    reached = {start}
    todo = [start]
    while todo:
        for name in index["refs"][todo.pop()]:
            if name not in reached:
                reached.add(name)
                todo.append(name)
    return [text for text in index["texts"] if index["owners"].get(text, set()) & reached]


def input_rows(form_class) -> list[tuple[str, str, str]]:
    """入力の画面の「入れる物」の表の行 (欄の名前, 必須 か 空, 説明)。form は作らない (base_fields を読む)。

    説明は help_text。無ければ選ぶ欄の選択肢の字を「・」で並べる (表から引く選ぶ欄は DB を読むので並べない)。隠れた欄は入れない。
    """
    rows = []
    for name, field in form_class.base_fields.items():
        if field.widget.is_hidden:
            continue
        note = str(field.help_text or "")
        if not note and isinstance(field, django_forms.ChoiceField) and not isinstance(field, django_forms.ModelChoiceField):
            note = "・".join(str(label) for value, label in field.choices if value not in ("", None))
        rows.append((str(field.label or name), "必須" if field.required else "", note))
    return rows


def decorate_fragment(screen, html: str, refusals: list[str] | None = None) -> str:
    """断片の字の後に、入れる物の表・絵・止まるときの文を足した字 (置き場のファイルは直さない)。足す物が無ければそのまま返す。

    入れる物の表は、phone で画面全体が横に送れないよう、表だけを横に送れる箱 (overflow-x-auto) に包む。
    """
    extra = []
    form_class = getattr(view_class_of(screen), "form_class", None)
    if form_class is not None and not INPUT_HEADING.search(html):
        rows = input_rows(form_class)
        if rows:
            extra.append(
                format_html(
                    '<h3>入れる物</h3><div class="overflow-x-auto"><table><thead><tr><th>欄の名前</th><th>必須</th><th>説明</th></tr></thead><tbody>{}</tbody></table></div>',
                    format_html_join("", "<tr><td>{}</td><td>{}</td><td>{}</td></tr>", rows),
                )
            )
    name = fragment_name(screen.url_name)
    folder = manual_dir()
    if "<img" not in html and folder is not None and (folder / "images" / f"{name}.png").is_file():
        extra.append(format_html('<figure class="seizou-manual-shot"><img src="images/{}.png" alt="{} の画面"></figure>', name, screen.name))
    if refusals:
        extra.append(format_html("<h3>この画面で止まるときの文</h3><ul>{}</ul>", format_html_join("", "<li>{}</li>", ((text,) for text in refusals))))
    return html + "".join(extra)


def manual_sections(booklet: bool = False) -> list[dict]:
    """目録の 1 画面ごとの本文の材料。目録の並びのまま返す。

    1 画面 = {"name", "help", "url_name", "anchor", "html", "unreadable", "app_refusals"}。
    anchor は断片の名前 (目次の link の飛び先)。html は断片の字に自動の付け足しをした字 (mark_safe) か None。
    booklet が偽 (使い方の画面と印刷) は、その画面の service からたどれる止まるときの文 (screen_refusals) を画面ごとに足す。
    真 (冊子) は足さず、部品の全部の文 (refusal_texts) を部品の最初の画面の app_refusals に入れる。services.py は部品ごとに 1 回だけ読む。
    """
    sections = []
    refusals: dict[str, list[str]] = {}
    indexes: dict[str, dict] = {}
    for screen in all_screens():
        anchor = fragment_name(screen.url_name)
        fragment = read_fragment(anchor)
        unreadable = fragment is UNREADABLE
        first_of_app = screen.app not in refusals
        if first_of_app:
            refusals[screen.app] = refusal_texts(screen.app) if booklet else []
        if isinstance(fragment, str):
            fragment = decorate_fragment(screen, fragment, None if booklet else screen_refusals(screen, indexes))
        sections.append(
            {
                "name": screen.name,
                "help": screen.help,
                "url_name": screen.url_name,
                "anchor": anchor,
                "html": None if fragment is None or unreadable else mark_safe(fragment),
                "unreadable": unreadable,
                "app_refusals": refusals[screen.app] if booklet and first_of_app else [],
            }
        )
    return sections


def package_version() -> str:
    """seizou の package の版 (冊子の表紙に出す)。package が入っていなければ空の字。"""
    try:
        return metadata.version("seizou")
    except metadata.PackageNotFoundError:
        return ""


def manual_date() -> date | None:
    """説明書を作った日。manual/ の下の断片 (*.html。前書きを含む) の最終更新の一番新しい物を、TIME_ZONE の日付にした物。

    置き場が無い・断片が 1 つも無いなら None (表紙に出さない)。
    """
    folder = manual_dir()
    if folder is None or not folder.is_dir():
        return None
    times = [path.stat().st_mtime for path in folder.glob("*.html") if path.is_file()]
    if not times:
        return None
    return timezone.localtime(datetime.fromtimestamp(max(times), tz=dt_timezone.utc)).date()


def booklet_context() -> dict:
    """冊子の材料 (口と命令が同じ物を描く)。帳票の部品は context processor を通らないので、会社名と版もここで渡す。

    会社名は会社の設定の 1 行の名前。DB を読めなければ "seizou" (context processor と同じ落とし方)。
    置き場と表に書かない (会社の行が無いときだけ、services.company が既定の 1 行を作る。context processor と同じ)。
    """
    from seizou.core import services

    try:
        company_name = services.company().name
    except DatabaseError:
        company_name = "seizou"
    folder = manual_dir()
    intro = read_fragment(INTRO_NAME)
    return {
        "company_name": company_name,
        "system_name": "seizou",
        "version": package_version(),
        "manual_date": manual_date(),
        "sections": manual_sections(booklet=True),
        "intro": None if intro is None or intro is UNREADABLE else mark_safe(intro),
        "flows": manual_flows(),
        "intro_unreadable": intro is UNREADABLE,
        "has_manual": folder is not None and folder.is_dir(),
        "primary_color": BOOKLET_PRIMARY_COLOR,
        "subtitle": BOOKLET_SUBTITLE,
    }


def render_booklet_pdf() -> bytes:
    """冊子の PDF の bytes。WeasyPrint が無ければ reports.PdfUnavailable がそのまま上がる (口と命令がそれぞれ受ける)。"""
    return reports.render_pdf(BOOKLET_TEMPLATE, booklet_context())


class ManualView(ScreenMixin, TemplateView):
    """使い方の画面 (画面ごとの説明書)。左に目次、右に全部の画面の断片。GET の print=1 で印刷用の 1 枚。読むだけ。

    使う人: 全員 / いつ: 画面の意味が分からないとき (特に初めての人) / 何のために: その画面の使い方を読む / 次にすること: 読んだ画面へ戻る
    """

    screen_name = "使い方"
    screen_help = "画面ごとの使い方。左の目次から読みたい画面を選びます。印刷用の 1 枚も開けます。"
    screen_icon = "book"
    template_name = "core/manual.html"
    print_template_name = "core/manual_print.html"

    def get_template_names(self):
        if self.request.GET.get(PRINT_PARAM) == "1":
            return [self.print_template_name]
        return super().get_template_names()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        folder = manual_dir()
        intro = read_fragment(INTRO_NAME)
        context["sections"] = manual_sections()
        context["intro"] = None if intro is None or intro is UNREADABLE else mark_safe(intro)
        context["flows"] = manual_flows()
        context["intro_unreadable"] = intro is UNREADABLE
        context["has_manual"] = folder is not None and folder.is_dir()
        context["print_url"] = f"{reverse('core:manual')}?{PRINT_PARAM}=1"
        context["pdf_url"] = reverse("core:manual_pdf")
        return context


@login_required
def manual_image(request, name: str):
    """manual/images/<name> の絵を返す。名前が . で始まる・.. を含む・拡張子が外れ・ファイルが無い・
    manual/images/ の外を指す、のどれかなら 404。読むだけ。"""
    folder = manual_dir()
    suffix = Path(name).suffix.lower()
    if folder is None or name.startswith(".") or ".." in name or suffix not in IMAGE_TYPES:
        raise Http404("絵がありません。")
    images = (folder / "images").resolve()
    path = (images / name).resolve()
    if path.parent != images or not path.is_file():
        raise Http404("絵がありません。")
    return FileResponse(path.open("rb"), content_type=IMAGE_TYPES[suffix])


@login_required
@never_cache
def manual_pdf(request):
    """使い方の冊子の PDF (表紙・目次・画面ごとの章・ページ番号)。作れなければ、印刷用の HTML の上に知らせを出して返す。読むだけ。"""
    context = booklet_context()
    try:
        data = reports.render_pdf(BOOKLET_TEMPLATE, context)
    except reports.PdfUnavailable:
        return HttpResponse(reports.render_html(BOOKLET_TEMPLATE, {**context, "pdf_notice": PDF_NOTICE}))
    response = HttpResponse(data, content_type="application/pdf")
    response["Content-Disposition"] = f"inline; filename=\"{PDF_FILENAME}\"; filename*=UTF-8''{quote(BOOKLET_FILENAME, safe='')}"
    return response


def screen_for_anchor(anchor: str):
    """断片の名前 (anchor) の画面 (目録の Screen)。目録に無い名前 (.. や : 入り・前書きの index も) なら None。

    目録は 1 回だけ歩く (URL の木を読むので、1 要求に 1 回)。
    """
    for screen in all_screens():
        if fragment_name(screen.url_name) == anchor:
            return screen
    return None


def absolute_images(html: str) -> str:
    """断片の中の絵の相対の道 (src="images/…") を、使い方のページの道 (/manual/images/…) に直した字。

    小窓は /manual/ でないページに差し込まれるので、相対の道のままでは絵が出ない。置き場のファイルは直さない。
    """
    return html.replace('src="images/', f'src="{reverse("core:manual")}images/')


def fragment_body(html: str) -> str:
    """画面の全部を小窓に出すときの中身。最初の <h2>…</h2> を 1 つだけ外す (小窓の題が画面の名前を出すので、2 度出さない)。"""
    return re.sub(r"<h2\b[^>]*>.*?</h2>", "", html, count=1, flags=re.S)


def fragment_part(html: str, part: str | None) -> str | None:
    """断片のうち、枠の見出し <h3 id="<part>"> から、次の <h3 か </section> の手前まで (無ければ末尾まで)。

    part の形が PART_ID に合わない・その見出しが断片に無いなら None (口が画面の全部に落とす)。
    """
    if not part or not PART_ID.fullmatch(part):
        return None
    start = re.search(rf'<h3\b[^>]*\sid="{re.escape(part)}"', html)
    if start is None:
        return None
    end = re.compile(r"<h3\b|</section>").search(html, start.end())
    return html[start.start() : end.start() if end else len(html)]


def fragment_for_modal(fragment: str, part: str | None, screen=None) -> str:
    """小窓に出す字。枠 (part) が断片にあればその枠だけ、無ければ画面の全部 (最初の <h2> を外す)。絵の道は直す。

    screen (目録の Screen) を渡すと、画面の全部を出すときだけ自動の付け足し (その画面の service からたどれる止まるときの文を含む) をする。
    """
    piece = fragment_part(fragment, part)
    if piece is None:
        piece = fragment_body(fragment if screen is None else decorate_fragment(screen, fragment, screen_refusals(screen)))
    return absolute_images(piece)


@login_required
@never_cache
def manual_fragment(request, anchor: str):
    """画面と枠の「?」が開く小窓の中身 (その画面の断片。?part=<h3 の id> で枠だけ)。読むだけ。

    htmx でない要求は使い方のページのその画面の所 (/manual/#<anchor>) へ移す。anchor が目録に無ければ 404。
    part の値は HTML に戻さない。
    """
    screen = screen_for_anchor(anchor)
    if screen is None:
        raise Http404("この画面の説明書はありません。")
    manual_url = f"{reverse('core:manual')}#{anchor}"
    if not request.headers.get("HX-Request"):
        return HttpResponseRedirect(manual_url)
    fragment = read_fragment(anchor)
    unreadable = fragment is UNREADABLE
    html = None if fragment is None or unreadable else mark_safe(fragment_for_modal(fragment, request.GET.get(PART_PARAM), screen))
    return render(
        request,
        FRAGMENT_TEMPLATE,
        {"screen": screen, "html": html, "unreadable": unreadable, "manual_url": manual_url},
    )
