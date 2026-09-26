from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .errors import AudioError, LLMError, SpeechRecognitionError, TTSError
from .models import (
    ConfirmationResult,
    ConversationExamples,
    ConversationPhase,
    ConversationState,
)


class RecognitionConfirmation:
    def __init__(
        self,
        input_function: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
    ) -> None:
        self.input = input_function
        self.output = output

    def confirm(self, text: str) -> ConfirmationResult:
        self.output("\n認識結果:")
        self.output(text)
        self.output("")
        while True:
            choice = self.input("Enter: この内容で確定 / q: アプリを終了 > ").strip().casefold()
            if choice == "":
                return ConfirmationResult.CONFIRMED
            if choice == "q":
                return ConfirmationResult.QUIT
            self.output("Enterまたはqを入力してください。")


class ConversationController:
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
        input_function: Callable[[str], str] = input,
        output: Callable[[str], None] = print,
    ) -> None:
        self.recorder = recorder
        self.recognizer = recognizer
        self.chat_backend = chat_backend
        self.synthesizer = synthesizer
        self.examples = examples
        self.questions = {
            ConversationPhase.STRATEGY: strategy_question,
            ConversationPhase.FEEDBACK: feedback_question,
        }
        self.input = input_function
        self.output = output
        self.confirmation = RecognitionConfirmation(input_function, output)
        self.state = ConversationState.STRATEGY_RECORDING

    def run(self) -> bool:
        try:
            if not self._run_phase(ConversationPhase.STRATEGY):
                return False
            self.state = ConversationState.ATTRACTION_WAIT
            if self._wait_for_enter_or_quit(
                "\nアトラクション終了後、Enterで感想フェーズへ進む / qで終了 > "
            ) == ConfirmationResult.QUIT:
                return False
            if not self._run_phase(ConversationPhase.FEEDBACK):
                return False
            self.state = ConversationState.COMPLETE
            self.output("\n会話が完了しました。お疲れさまでした。")
            return True
        except (EOFError, KeyboardInterrupt):
            self.output("\n終了します。")
            return False

    def _run_phase(self, phase: ConversationPhase) -> bool:
        self._set_recording_state(phase)
        question = self.questions[phase]
        self.output(f"\n{question}")
        self._speak_or_report(question)

        while True:
            if self._wait_for_enter_or_quit(
                "Enterで録音開始 / qで終了 > "
            ) == ConfirmationResult.QUIT:
                return False

            try:
                audio = self.recorder.record_until_silence()
            except AudioError as exc:
                self.output(f"録音エラー: {exc}")
                continue
            if not audio.speech_detected:
                self.output("発話を検出できませんでした。もう一度録音してください。")
                continue

            self.output("音声を認識しています...")
            try:
                result = self.recognizer.transcribe(audio)
            except SpeechRecognitionError as exc:
                self.output(f"音声認識エラー: {exc}")
                continue
            self.output(
                f"音声認識: {result.processing_seconds:.2f}秒 "
                f"(モデル: {result.model_name})"
            )
            if result.is_empty:
                self.output("認識結果が空でした。もう一度録音してください。")
                continue

            self._set_confirmation_state(phase)
            if self.confirmation.confirm(result.text) == ConfirmationResult.QUIT:
                return False

            phase_examples = self.examples.phases[phase]
            self.output(f"LLMへ渡す会話例: {len(phase_examples)}件（{phase.value}）")
            self.output("コメントを生成しています...")
            try:
                comment = self.chat_backend.generate(phase, result.text, phase_examples)
            except LLMError as exc:
                self.output(f"LLMエラー: {exc}")
                self.output("同じフェーズの録音待機へ戻ります。")
                self._set_recording_state(phase)
                continue

            self.output("\nロボットのコメント:")
            self.output(comment)
            self._speak_or_report(comment)
            return True

    def _speak_or_report(self, text: str) -> None:
        try:
            self.synthesizer.speak(text)
        except TTSError as exc:
            self.output(f"音声合成エラー: {exc}")

    def _wait_for_enter_or_quit(self, prompt: str) -> ConfirmationResult:
        while True:
            choice = self.input(prompt).strip().casefold()
            if choice == "":
                return ConfirmationResult.CONFIRMED
            if choice == "q":
                return ConfirmationResult.QUIT
            self.output("Enterまたはqを入力してください。")

    def _set_recording_state(self, phase: ConversationPhase) -> None:
        self.state = (
            ConversationState.STRATEGY_RECORDING
            if phase == ConversationPhase.STRATEGY
            else ConversationState.FEEDBACK_RECORDING
        )

    def _set_confirmation_state(self, phase: ConversationPhase) -> None:
        self.state = (
            ConversationState.STRATEGY_CONFIRMATION
            if phase == ConversationPhase.STRATEGY
            else ConversationState.FEEDBACK_CONFIRMATION
        )

