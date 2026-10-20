"""LLM・マイク・音声合成を起動せず、会話の開始と完了を確認する。"""

from threading import Event

from chambara_talk.models import ConversationPhase, PhaseRunResult


class MockConversation:
    """通常と同じ状態遷移を通り、キャンセル可能な短い会話待ちを作る。"""

    def __init__(self, output=print, seconds: float = 3.0) -> None:
        self.output = output
        self.seconds = seconds

    def run_phase(self, phase, *, outcome=None, cancel_event=None):
        event = cancel_event if cancel_event is not None else Event()
        label = "作戦・意気込み" if phase == ConversationPhase.STRATEGY else "感想"
        self.output(f"模擬会話: {label}（{self.seconds:g}秒後に完了・録音と読み上げなし）")
        if event.wait(self.seconds):
            return PhaseRunResult.CANCELLED
        return PhaseRunResult.COMPLETED
