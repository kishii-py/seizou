"""手元の Claude Code から会社のシステムを読み書きする MCP サーバーを立てる管理コマンド。

会社の置き場で manage.py seizou_mcp を打つと、標準入力と標準出力で JSON-RPC を話す stdio のサーバーが立ち、
seizou.core.mcp_tools の道具を登録する (読む道具の全部と、--read-only でなければ書く道具)。

- 標準出力は MCP の通信だけが使う。この命令は標準出力に 1 字も書かず、知らせは標準エラーに 1 行だけ書く。
  Django の起動の検査も外す (検査の文が標準出力に出る道を断つ。設定の誤りは道具を呼んだときに文で出る)。
- 書く人 (利用者) は --user があればそれ、無ければ環境変数 SEIZOU_MCP_USER
  (会社の置き場の .env に書けば、会社の settings.py が起動のときに環境変数に入れる)。
  名前があるのに利用者がいないか、有効でなければ止まる。どちらも無ければ書く道具は登録するが、呼ぶと断りの文を返す。
- 利用者と会社の名前は、サーバーを回す前 (この thread) に引く。サーバーの中は event loop の thread で回る。
"""

from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from seizou.core import conf, mcp_tools
from seizou.core.models import Company

#: 利用者の名前を入れる環境変数 (値は会社の置き場の .env に置く)
USER_ENV = "SEIZOU_MCP_USER"

#: MCP の package が無いときの文
MISSING_PACKAGE_MESSAGE = "MCP の部品 (mcp) が入っていません。seizou を入れた場所で uv sync を打ってください。"


def resolve_user(name: str):
    """名前の利用者。名前が空なら None。いない利用者と有効でない利用者は CommandError。"""
    if not name:
        return None
    User = get_user_model()
    user = User.objects.filter(**{User.USERNAME_FIELD: name}).first()
    if user is None:
        raise CommandError(
            f"利用者 {name} がいません。管理の画面の利用者の一覧で名前を確かめ、"
            f"--user か .env の {USER_ENV} を直してください。"
        )
    if not user.is_active:
        raise CommandError(
            f"利用者 {name} は有効でないので、書く人にできません。管理の画面で利用者の状態を確かめてください。"
        )
    return user


def company_name() -> str:
    """会社の名前 (会社の表の 1 行目)。無ければ会社の id、それも無ければ「この会社」。"""
    company = Company.objects.order_by("pk").first()
    if company is not None and company.name:
        return company.name
    return conf.company_id() or "この会社"


def instructions(user, *, read_only: bool = False) -> str:
    """サーバーの使い方の 1 段落 (Claude Code が最初に読む)。"""
    if read_only:
        tools = (
            "読む道具 (会社・画面の目録・部品・受注と注文残・指示書・出荷・使い方・業務マニュアル・受入と検収・入金・フィードバック) があります。"
            "今は読むだけで起こしたので、書く道具はありません。"
        )
    else:
        tools = (
            "読む道具 (会社・画面の目録・部品・受注と注文残・指示書・出荷・使い方・業務マニュアル・受入と検収・入金・フィードバック) と、"
            "書く道具 (受注を作る・出荷する・工程の予定を置く) があります。"
            "書く道具は画面と同じ決まりとポカよけを通ります。"
        )
        if user is None:
            tools += "今は利用者を決めずに起こしたので、書く道具は使えません (呼ぶと断りの文を返します)。"
        else:
            tools += f"今の利用者は {user.get_username()} です。"
    return (
        f"{company_name()} の業務システム (seizou) の道具です。{tools}"
        "ok が偽なら、error か warnings を人に見せてください。"
        "warnings は人が確かめてから、同じ引数に confirm=true で呼び直すと保存します。"
        "数と金額は字で返します。"
        "受入と入金の金額は、金額の閲覧の判定に従い、見せない相手には「—」で返します。"
    )


def build_server(user, *, read_only: bool = False):
    """道具を登録した MCP サーバー (まだ回さない)。MCP の package が無ければ CommandError。"""
    try:
        from mcp.server.mcpserver import MCPServer
    except ImportError as exc:
        raise CommandError(MISSING_PACKAGE_MESSAGE) from exc
    server = MCPServer(name="seizou", instructions=instructions(user, read_only=read_only))
    bound = {tool.__name__: tool for tool in mcp_tools.viewer_tools(user)}
    for tool in mcp_tools.READ_TOOLS:
        server.add_tool(bound.get(tool.__name__, tool))
    if not read_only:
        for tool in mcp_tools.write_tools(user):
            server.add_tool(tool)
    return server


def started_line(user, *, read_only: bool = False) -> str:
    """標準エラーに書く 1 行。"""
    if user is None:
        who = "なし (読むだけ)"
    elif read_only:
        who = f"{user.get_username()} (読むだけ)"
    else:
        who = user.get_username()
    return f"MCP サーバーを立てました: {company_name()} / 利用者 {who}"


class Command(BaseCommand):
    help = "手元の Claude Code が会社のシステムを読み書きする MCP サーバー (stdio) を立てる。"

    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument(
            "--user",
            default="",
            help=f"書く人の利用者名。無ければ環境変数 {USER_ENV}。どちらも無ければ書く道具は断りの文を返す。",
        )
        parser.add_argument(
            "--read-only",
            action="store_true",
            dest="read_only",
            help="書く道具を登録しない (読む道具だけ)。",
        )

    def handle(self, *args, **options):
        name = (options["user"] or os.environ.get(USER_ENV, "")).strip()
        user = resolve_user(name)
        read_only = options["read_only"]
        server = build_server(user, read_only=read_only)
        self.stderr.write(started_line(user, read_only=read_only))
        server.run(transport="stdio")
