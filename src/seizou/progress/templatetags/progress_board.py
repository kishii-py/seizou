"""進捗の盤の template tag (読むだけ。表に書かない)。

{% progress_board as board %}: 盤の中身を boards.build_board で作る。
工程は request.GET の process。自動の更新の印 after は、土台が htmx の部分を描くとき (context の partial が真) だけ読む。
ブラウザで ?after= を付けて開いても盤の全部を出す。htmx の部分で after が無いか読めないときは、
一番新しい実績の pk を印にする (行を返さず、取りに行く要素だけが今の印で入れ直される)。
"""

from django import template

from seizou.progress import boards

register = template.Library()


@register.simple_tag(takes_context=True)
def progress_board(context):
    params = context["request"].GET
    process = boards.parse_process(params.get("process"))
    after = None
    if context.get("partial"):
        after = boards.parse_after(params.get("after"))
        if after is None:
            after = boards.newest_pk()
    return boards.build_board(process, after)
