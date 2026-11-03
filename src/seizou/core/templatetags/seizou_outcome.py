"""使う人の結果の数字の tag (読むだけ。N83)。部品の画面の class は context を足せない (R15) ので、テンプレートが tag で読む。部品の無い会社でも load できるよう core に置く。

{% reading_match customer=object as match %}      読み取りの一致率 (core.dashboard.reading_match。無ければ None)
{% reading_match extraction=object as match %}    その 1 枚の読み取りの一致率 (受注にした後だけ。core/_reading_match.html で出す)
"""

from django import template

from seizou.core import dashboard

register = template.Library()


@register.simple_tag
def reading_match(customer=None, extraction=None):
    return dashboard.reading_match(customer=customer, extraction=extraction)
