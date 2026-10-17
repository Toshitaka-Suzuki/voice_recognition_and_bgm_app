"""質問・録音・認識確認・LLM・読み上げの順序を制御するモジュール。"""

from __future__ import annotations

import time
from collections.abc import Callable
from threading import Event
from typing import Any

from .errors import AudioError, ConversationCancelled, LLMError, SpeechRecognitionError, TTSError
from .models import (
    ConfirmationResult,
    ConversationExamples,
    ConversationPhase,
    ConversationState,
    FeedbackOutcome,
    PhaseRunResult,
)


AUTO_RETRY_DELAY_SECONDS = 1.0


class RecognitionConfirmation:
    """認識結果を表示し、利用者がEnterまたはqを入力するまで待つ。"""

    def __init__(
        self,
        input_function: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
    ) -> None:
        """入力関数と出力関数を受け取る。"""

        self.input = input_function
        self.output = output

    def _read(self, prompt: str, cancel_event: Event | None) -> str | None:
        """通常入力または統合アプリ用のキャンセル可能入力を1回読む。"""

        if cancel_event is not None and cancel_event.is_set():
            return None
        reader = getattr(self.input, "read_cancelable", None)
        if cancel_event is not None and callable(reader):
            return reader(prompt, cancel_event)
        value = self.input(prompt)
        return None if cancel_event is not None and cancel_event.is_set() else value

    def confirm(
        self,
        text: str,
        cancel_event: Event | None = None,
    ) -> ConfirmationResult:
        """認識文を表示し、確定・終了・外部キャンセルの結果を返す。"""

        self.display(text)
        while True:
            raw = self._read("Enter: この内容で確定 / q: アプリを終了 > ", cancel_event)
            if raw is None:
                return ConfirmationResult.CANCELLED
            choice = raw.strip().casefold()
            if choice == "":
                return ConfirmationResult.CONFIRMED
            if choice == "q":
                return ConfirmationResult.QUIT
            self.output("Enterまたはqを入力してください。")

    def display(self, text: str) -> None:
        """認識文を、手動確認と自動送信で共通の形式で表示する。"""

        self.output("\n認識結果:")
        self.output(text)
        self.output("")


class ConversationController:
    """会話フェーズを実行し、専門部品を決められた順序で呼び出す。"""

    def __init__(
        self,
        *,
        recorder: Any,
        recognizer: Any,
        chat_backend: Any,
        synthesizer: Any,
        examples: ConversationExamples,
        strategy_question: str,
        feedback_question: str,
        feedback_human_win_question: str | None = None,
        feedback_robot_win_question: str | None = None,
        input_function: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
        manual_input_enabled: bool = True,
    ) -> None:
        """会話部品と、Enter・qによる手動操作を使うかを受け取る。"""

        self.recorder = recorder
        self.recognizer = recognizer
        self.chat_backend = chat_backend
        self.synthesizer = synthesizer
        self.examples = examples
        self.questions = {
            ConversationPhase.STRATEGY: strategy_question,
            ConversationPhase.FEEDBACK: feedback_question,
        }
        self.feedback_questions = {
            FeedbackOutcome.HUMAN_WIN: feedback_human_win_question or feedback_question,
            FeedbackOutcome.ROBOT_WIN: feedback_robot_win_question or feedback_question,
        }
        self.input = input_function
        self.output = output
        self.manual_input_enabled = manual_input_enabled
        self.confirmation = RecognitionConfirmation(input_function, output)
        self.state = ConversationState.STRATEGY_RECORDING

    def run(self) -> bool:
        """従来どおり作戦、手動待機、感想の2フェーズを順番に実行する。"""

        if self.run_phase(ConversationPhase.STRATEGY) != PhaseRunResult.COMPLETED:
            return False
        self.state = ConversationState.ATTRACTION_WAIT
        try:
            result = self._wait_for_enter_or_quit(
                "\nアトラクション終了後、Enterで感想フェーズへ進む / qで終了 > ", None
            )
        except (EOFError, KeyboardInterrupt):
            self.output("\n終了します。")
            return False
        if result != ConfirmationResult.CONFIRMED:
            return False
        if self.run_phase(ConversationPhase.FEEDBACK) != PhaseRunResult.COMPLETED:
            return False
        self.state = ConversationState.COMPLETE
        self.output("\n会話が完了しました。お疲れさまでした。")
        return True

    def run_phase(
        self,
        phase: ConversationPhase,
        *,
        outcome: FeedbackOutcome | None = None,
        cancel_event: Event | None = None,
    ) -> PhaseRunResult:
        """指定した1フェーズだけを実行し、完了・中断・終了を返す。"""

        try:
            return self._run_phase(phase, outcome=outcome, cancel_event=cancel_event)
        except ConversationCancelled:
            return PhaseRunResult.CANCELLED
        except (EOFError, KeyboardInterrupt):
            self.output("\n終了します。")
            return PhaseRunResult.QUIT

    def _run_phase(
        self,
        phase: ConversationPhase,
        *,
        outcome: FeedbackOutcome | None,
        cancel_event: Event | None,
    ) -> PhaseRunResult:
        """1フェーズ分の質問からコメント読み上げまでを行う。"""

        if self._cancelled(cancel_event):
            return PhaseRunResult.CANCELLED
        self._set_recording_state(phase)
        question = (
            self.feedback_questions[outcome]
            if phase == ConversationPhase.FEEDBACK and outcome is not None
            else self.questions[phase]
        )
        self.output(f"\n{question}")
        if not self._speak_or_report(question, cancel_event):
            return PhaseRunResult.CANCELLED

        while True:
            if self._cancelled(cancel_event):
                return PhaseRunResult.CANCELLED
            if self.manual_input_enabled:
                confirmation = self._wait_for_enter_or_quit(
                    "Enterで録音開始 / qで終了 > ", cancel_event
                )
                if confirmation == ConfirmationResult.QUIT:
                    return PhaseRunResult.QUIT
                if confirmation == ConfirmationResult.CANCELLED:
                    return PhaseRunResult.CANCELLED
            else:
                self.output("録音を自動で開始します。")

            if self._cancelled(cancel_event):
                return PhaseRunResult.CANCELLED

            try:
                audio = (
                    self.recorder.record_until_silence()
                    if cancel_event is None
                    else self.recorder.record_until_silence(cancel_event=cancel_event)
                )
            except ConversationCancelled:
                return PhaseRunResult.CANCELLED
            except AudioError as exc:
                self.output(f"録音エラー: {exc}")
                if not self._wait_before_retry(cancel_event):
                    return PhaseRunResult.CANCELLED
                continue
            if self._cancelled(cancel_event):
                return PhaseRunResult.CANCELLED
            if not audio.speech_detected:
                self.output("発話を検出できませんでした。もう一度録音してください。")
                if not self._wait_before_retry(cancel_event):
                    return PhaseRunResult.CANCELLED
                continue

            self.output("音声を認識しています...")
            try:
                result = self.recognizer.transcribe(audio)
            except SpeechRecognitionError as exc:
                self.output(f"音声認識エラー: {exc}")
                if not self._wait_before_retry(cancel_event):
                    return PhaseRunResult.CANCELLED
                continue
            if self._cancelled(cancel_event):
                return PhaseRunResult.CANCELLED
            self.output(
                f"音声認識: {result.processing_seconds:.2f}秒 (モデル: {result.model_name})"
            )
            if result.is_empty:
                self.output("認識結果が空でした。もう一度録音してください。")
                if not self._wait_before_retry(cancel_event):
                    return PhaseRunResult.CANCELLED
                continue

            if self.manual_input_enabled:
                self._set_confirmation_state(phase)
                confirmation = self.confirmation.confirm(result.text, cancel_event)
                if confirmation == ConfirmationResult.QUIT:
                    return PhaseRunResult.QUIT
                if confirmation == ConfirmationResult.CANCELLED:
                    return PhaseRunResult.CANCELLED
            else:
                self.confirmation.display(result.text)
            if self._cancelled(cancel_event):
                return PhaseRunResult.CANCELLED

            phase_examples = self.examples.phases[phase]
            if self._cancelled(cancel_event):
                return PhaseRunResult.CANCELLED
            self.output(f"LLMへ渡す会話例: {len(phase_examples)}件（{phase.value}）")
            self.output("コメントを生成しています...")
            try:
                comment = (
                    self.chat_backend.generate(phase, result.text, phase_examples)
                    if outcome is None
                    else self.chat_backend.generate(
                        phase, result.text, phase_examples, outcome=outcome
                    )
                )
            except LLMError as exc:
                self.output(f"LLMエラー: {exc}")
                self.output("同じフェーズの録音待機へ戻ります。")
                self._set_recording_state(phase)
                if not self._wait_before_retry(cancel_event):
                    return PhaseRunResult.CANCELLED
                continue
            if self._cancelled(cancel_event):
                return PhaseRunResult.CANCELLED

            self.output("\nロボットのコメント:")
            self.output(comment)
            if not self._speak_or_report(comment, cancel_event):
                return PhaseRunResult.CANCELLED
            return PhaseRunResult.COMPLETED

    def _wait_before_retry(self, cancel_event: Event | None) -> bool:
        """自動モードだけ1秒待ち、キャンセルされていなければ再試行する。"""

        if self.manual_input_enabled:
            return not self._cancelled(cancel_event)
        self.output("1秒後に録音を再開します。")
        if cancel_event is not None:
            return not cancel_event.wait(AUTO_RETRY_DELAY_SECONDS)
        time.sleep(AUTO_RETRY_DELAY_SECONDS)
        return True

    def _speak_or_report(self, text: str, cancel_event: Event | None) -> bool:
        """文章を読み上げ、キャンセルなら``False``を返す。"""

        if self._cancelled(cancel_event):
            return False
        try:
            if cancel_event is None:
                self.synthesizer.speak(text)
            else:
                completed = self.synthesizer.speak(text, cancel_event=cancel_event)
                if completed is False:
                    return False
        except TTSError as exc:
            self.output(f"音声合成エラー: {exc}")
        return not self._cancelled(cancel_event)

    def _wait_for_enter_or_quit(
        self, prompt: str, cancel_event: Event | None
    ) -> ConfirmationResult:
        """Enter、q、外部キャンセルだけを受け付ける。"""

        while True:
            raw = self.confirmation._read(prompt, cancel_event)
            if raw is None:
                return ConfirmationResult.CANCELLED
            choice = raw.strip().casefold()
            if choice == "":
                return ConfirmationResult.CONFIRMED
            if choice == "q":
                return ConfirmationResult.QUIT
            self.output("Enterまたはqを入力してください。")

    @staticmethod
    def _cancelled(cancel_event: Event | None) -> bool:
        """キャンセルイベントが設定済みかを返す。"""

        return cancel_event is not None and cancel_event.is_set()

    def _set_recording_state(self, phase: ConversationPhase) -> None:
        """フェーズに対応する録音待機状態へ変更する。"""

        self.state = (
            ConversationState.STRATEGY_RECORDING
            if phase == ConversationPhase.STRATEGY
            else ConversationState.FEEDBACK_RECORDING
        )

    def _set_confirmation_state(self, phase: ConversationPhase) -> None:
        """フェーズに対応する認識確認状態へ変更する。"""

        self.state = (
            ConversationState.STRATEGY_CONFIRMATION
            if phase == ConversationPhase.STRATEGY
            else ConversationState.FEEDBACK_CONFIRMATION
        )
