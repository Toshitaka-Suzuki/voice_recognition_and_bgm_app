"""WebSocket信号から会話・BGM・パリイ判定を進める状態機械。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from enum import Enum
from functools import partial
from threading import Event
from time import perf_counter
from typing import Any

from chambara_bgm.engine import Event as BgmEvent
from chambara_talk.models import (
    ConversationPhase,
    FeedbackOutcome,
    PhaseRunResult,
)

from .protocol import Signal, error_message, received

SendFunction = Callable[[dict[str, object]], Awaitable[None]]


class AppState(str, Enum):
    """統合アプリが受理できる次の信号を決める内部状態。"""

    STANDBY = "standby"
    PRE_INTERVIEW = "pre_interview"
    WAITING_BATTLE_START = "waiting_battle_start"
    BATTLE = "battle"
    POST_INTERVIEW = "post_interview"


class IntegratedController:
    """すべての入力信号を直列化し、ゲーム全体の状態を管理する。"""

    def __init__(
        self,
        conversation: Any,
        game: Any,
        output=print,  # noqa: ANN001
        *,
        prepare: Callable[[], None] | None = None,
        cleanup: Callable[[], None] | None = None,
        bgm_display: Any | None = None,
    ) -> None:
        """会話・BGM部品と、任意のBGMデバッグ表示を受け取る。"""

        self.conversation = conversation
        self.game = game
        self.output = output
        self._prepare = prepare
        self._cleanup = cleanup
        self.bgm_display = bgm_display
        # SAPI5とCopilotのイベントループは、作成から終了まで同じ1スレッドで扱います。
        self._conversation_executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="chambara-conversation"
        )
        self._closed = False
        self.state = AppState.STANDBY
        self.weak_point_count: int | None = None
        self.outcome: FeedbackOutcome | None = None
        self.shutdown_event = asyncio.Event()
        self._send: SendFunction | None = None
        self._state_lock = asyncio.Lock()
        self._conversation_lock = asyncio.Lock()
        self._phase_generation = 0
        self._cancel_event: Event | None = None
        self._phase_tasks: set[asyncio.Task] = set()
        self._health_task: asyncio.Task | None = None

    async def prepare(self) -> None:
        """マイク・TTS・LLM・認識モデルを会話専用スレッドで確認する。"""

        if self._prepare is not None:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(self._conversation_executor, self._prepare)

    async def connect(self, send: SendFunction) -> None:
        """制御接続を登録し、接続通知と現在のstandbyを順に送る。"""

        async with self._state_lock:
            await self._reset_locked(send_status=False)
            self._send = send
            await self._send_message({"type": "connected", "text": "接続しました"})
            await self._send_status("standby")
            if self._health_task is None or self._health_task.done():
                self._health_task = asyncio.create_task(self._health_loop())

    async def disconnect(self, send: SendFunction) -> None:
        """現在の制御接続が切れた場合だけゲームを安全停止する。"""

        async with self._state_lock:
            if self._send is not send:
                return
            self._send = None
            await self._reset_locked(send_status=False)
            self.output("通信切断: 進行中のゲームを破棄してstandbyへ戻しました。")

    async def close(self) -> None:
        """アプリ終了時に会話とBGMを停止し、監視タスクを片付ける。"""

        if self._closed:
            return
        self._closed = True
        async with self._state_lock:
            self._send = None
            await self._reset_locked(send_status=False)
        if self._health_task is not None:
            self._health_task.cancel()
            try:
                await self._health_task
            except asyncio.CancelledError:
                pass
        # 実行中の非キャンセル処理があれば完了を待ち、同じ専用スレッドで後処理します。
        loop = asyncio.get_running_loop()
        if self._cleanup is not None:
            await loop.run_in_executor(self._conversation_executor, self._cleanup)
        self._conversation_executor.shutdown(wait=True, cancel_futures=True)

    async def handle(self, signal: Signal, received_at: float | None = None) -> None:
        """検証済み信号を現在状態に応じて1件処理する。"""

        async with self._state_lock:
            timestamp = perf_counter() if received_at is None else received_at
            if signal.name == "game_start":
                await self._handle_game_start(signal)
            elif signal.name == "battle_start":
                await self._handle_battle_start(signal)
            elif signal.name == "parry":
                await self._handle_parry(signal, timestamp)
            elif signal.name == "weak_point_count":
                await self._handle_weak_point(signal)
            elif signal.name == "battle_end":
                await self._handle_battle_end(signal)

    async def _handle_game_start(self, signal: Signal) -> None:
        if self.state != AppState.STANDBY:
            await self._state_error("ゲームスタートはstandby中だけ受け付けます。")
            return
        await self._send_message(received(signal))
        self.state = AppState.PRE_INTERVIEW
        await self._send_status("pre_interview")
        self._begin_interview(ConversationPhase.STRATEGY)

    async def _handle_battle_start(self, signal: Signal) -> None:
        if self.state not in (AppState.PRE_INTERVIEW, AppState.WAITING_BATTLE_START):
            await self._state_error("戦闘開始は開始前インタビュー中または終了後だけ受け付けます。")
            return
        await self._send_message(received(signal))
        if self.state == AppState.PRE_INTERVIEW:
            self._cancel_interview_locked()
        try:
            self.game.start()
        except Exception as exc:
            await self._runtime_failure_locked(f"BGMを開始できませんでした: {exc}")
            return
        self.weak_point_count = 3
        self.outcome = None
        self.state = AppState.BATTLE
        self.output("戦闘開始: easy BGMを再生します。")

    async def _handle_parry(self, signal: Signal, timestamp: float) -> None:
        if self.state != AppState.BATTLE:
            await self._state_error("パリイは戦闘中だけ受け付けます。")
            return
        await self._send_message(received(signal))
        try:
            event = self.game.submit_parry(timestamp=timestamp)
        except Exception as exc:
            await self._runtime_failure_locked(f"パリイ判定を継続できません: {exc}")
            return
        if event is None:
            result = {"type": "parry_result", "value": "failure", "reason": "not_ready"}
            if self.bgm_display is not None:
                self.bgm_display.event(
                    BgmEvent(
                        "parry_failure",
                        self.game.track.id,
                        reason="判定準備中（再生位置を取得できません）",
                    )
                )
        elif event.kind == "parry_success":
            result = {"type": "parry_result", "value": "success"}
        else:
            result = {
                "type": "parry_result",
                "value": "failure",
                "reason": "outside_window_or_already_successful",
            }
        await self._send_message(result)

    async def _handle_weak_point(self, signal: Signal) -> None:
        if self.state != AppState.BATTLE:
            await self._state_error("弱点残り回数は戦闘中だけ受け付けます。")
            return
        count = signal.count
        if count is None or count > 3:
            await self._state_error("弱点残り回数は戦闘中の現在値として0〜3で指定してください。")
            return
        assert self.weak_point_count is not None
        if count > self.weak_point_count:
            await self._state_error("弱点残り回数を増やすことはできません。")
            return
        await self._send_message(received(signal))
        if count == self.weak_point_count:
            return
        self.weak_point_count = count
        if count == 0:
            await self._finish_battle_locked(FeedbackOutcome.HUMAN_WIN)
            return
        stage = "normal" if count == 2 else "hard"
        try:
            self.game.switch_stage(stage)
        except Exception as exc:
            await self._runtime_failure_locked(f"BGMを切り替えできませんでした: {exc}")

    async def _handle_battle_end(self, signal: Signal) -> None:
        if self.state != AppState.BATTLE:
            await self._state_error("戦闘終了は戦闘中だけ受け付けます。")
            return
        await self._send_message(received(signal))
        await self._finish_battle_locked(FeedbackOutcome.ROBOT_WIN)

    async def _finish_battle_locked(self, outcome: FeedbackOutcome) -> None:
        """BGMを止め、勝敗を保持して終了インタビューを開始する。"""

        try:
            self.game.stop(outcome.value)
        except Exception as exc:
            self.output(f"BGM停止エラー: {exc}")
        finally:
            self._clear_bgm_status()
        self.outcome = outcome
        self.state = AppState.POST_INTERVIEW
        await self._send_status("post_interview")
        self._begin_interview(ConversationPhase.FEEDBACK, outcome)

    def _begin_interview(
        self,
        phase: ConversationPhase,
        outcome: FeedbackOutcome | None = None,
    ) -> None:
        """会話をバックグラウンドで開始し、通信受信を止めない。"""

        self._phase_generation += 1
        generation = self._phase_generation
        cancel_event = Event()
        self._cancel_event = cancel_event
        task = asyncio.create_task(self._run_interview(phase, outcome, generation, cancel_event))
        self._phase_tasks.add(task)
        task.add_done_callback(self._phase_tasks.discard)

    async def _run_interview(
        self,
        phase: ConversationPhase,
        outcome: FeedbackOutcome | None,
        generation: int,
        cancel_event: Event,
    ) -> None:
        """会話部品を直列実行し、現世代の結果だけを状態へ反映する。"""

        try:
            async with self._conversation_lock:
                if generation != self._phase_generation or cancel_event.is_set():
                    return
                loop = asyncio.get_running_loop()
                result = await loop.run_in_executor(
                    self._conversation_executor,
                    partial(
                        self.conversation.run_phase,
                        phase,
                        outcome=outcome,
                        cancel_event=cancel_event,
                    ),
                )
            await self._interview_finished(phase, generation, result)
        except Exception as exc:
            async with self._state_lock:
                if generation == self._phase_generation:
                    await self._runtime_failure_locked(f"インタビューを継続できません: {exc}")

    async def _interview_finished(
        self,
        phase: ConversationPhase,
        generation: int,
        result: PhaseRunResult,
    ) -> None:
        async with self._state_lock:
            if generation != self._phase_generation:
                return
            if result == PhaseRunResult.QUIT:
                self.shutdown_event.set()
                return
            if result != PhaseRunResult.COMPLETED:
                return
            self._cancel_event = None
            if phase == ConversationPhase.STRATEGY and self.state == AppState.PRE_INTERVIEW:
                self.state = AppState.WAITING_BATTLE_START
                await self._send_status("pre_interview_finished")
            elif phase == ConversationPhase.FEEDBACK and self.state == AppState.POST_INTERVIEW:
                await self._send_status("post_interview_finished")
                self.state = AppState.STANDBY
                self.weak_point_count = None
                self.outcome = None
                await self._send_status("standby")

    def _cancel_interview_locked(self) -> None:
        """現在の会話世代を無効化し、停止可能な処理へキャンセルを通知する。"""

        if self._cancel_event is not None:
            self._cancel_event.set()
        self._cancel_event = None
        self._phase_generation += 1

    async def _reset_locked(self, *, send_status: bool) -> None:
        """BGM・会話・ゲーム内データを破棄してstandbyへ戻す。"""

        self._cancel_interview_locked()
        try:
            self.game.stop("reset")
        except Exception as exc:
            self.output(f"BGM停止エラー: {exc}")
        finally:
            self._clear_bgm_status()
        self.state = AppState.STANDBY
        self.weak_point_count = None
        self.outcome = None
        if send_status and self._send is not None:
            await self._send_status("standby")

    async def _runtime_failure_locked(self, text: str) -> None:
        """実行時異常を通知して安全なstandbyへ戻す。"""

        failed_state = self.state.value
        if self._send is not None:
            await self._send_message(error_message(text, failed_state))
        self.output(text)
        await self._reset_locked(send_status=self._send is not None)

    async def _state_error(self, text: str) -> None:
        await self._send_message(error_message(text, self.state.value))

    async def _send_status(self, value: str) -> None:
        await self._send_message({"type": "status", "value": value})

    async def _send_message(self, message: dict[str, object]) -> None:
        if self._send is not None:
            await self._send(message)

    async def _health_loop(self) -> None:
        """BGMのコールバック停止などを100msごとに検出する。"""

        while True:
            await asyncio.sleep(0.1)
            async with self._state_lock:
                if self.state != AppState.BATTLE:
                    continue
                try:
                    now = perf_counter()
                    self._display_bgm_status(now)
                    warning = self.game.player.take_warning(now)
                    if warning:
                        self.output(warning)
                except Exception as exc:
                    await self._runtime_failure_locked(f"BGM出力異常: {exc}")

    def _display_bgm_status(self, now: float) -> None:
        """現在の曲、再生位置、判定状態、周回を表示部品へ渡す。"""

        status = self.game.status(now)
        if self.bgm_display is None:
            return
        self.bgm_display.status(
            self.game.track.id,
            status,
            waiting=self.game.player.waiting_for_first_callback,
            synchronizing=self.game.player.synchronizing,
        )

    def _clear_bgm_status(self) -> None:
        """表示部品がある場合だけ、戦闘中の状態行を消去する。"""

        if self.bgm_display is not None:
            self.bgm_display.clear_status()
