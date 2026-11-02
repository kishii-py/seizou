from django.apps import AppConfig


class AssistantConfig(AppConfig):
    """AI チャット。全部の画面の右下の窓で、今の画面とマニュアルと読むだけの道具を使って質問に答える。表を持たない。"""

    name = "seizou.assistant"
    label = "assistant"
    verbose_name = "AI チャット"
