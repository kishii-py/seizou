from django.apps import AppConfig


class AssistantConfig(AppConfig):
    """AI チャット。全部の画面の右下の窓で、今の画面とマニュアルと読むだけの道具を使って質問に答える。

    表は提案 (AssistantProposal) の 1 つだけを持つ (二度の承認を止めるため。開発の記録 docs/nights/20261108/N351.md)。
    """

    name = "seizou.assistant"
    label = "assistant"
    verbose_name = "AI チャット"
