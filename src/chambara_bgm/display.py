"""日本語の表示幅を考慮した状態行とイベントログ。"""
import os
import shutil
import sys
import unicodedata
from threading import RLock

LABELS = {"easy": "簡単", "normal": "普通", "hard": "難しい"}
CLEAR_LINE = "\r\x1b[2K"


def cell_width(text):
    return sum(0 if unicodedata.combining(char) else
               2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def fit_line(text, limit):
    if cell_width(text) <= limit:
        return text
    result = ""
    for char in text:
        if cell_width(result + char) > max(0, limit - 1):
            break
        result += char
    return result + ("…" if limit else "")


class ConsoleDisplay:
    def __init__(self, output=None, interactive=None, columns=None):
        self.output = sys.stdout if output is None else output
        self.columns = columns or (lambda: shutil.get_terminal_size(fallback=(80, 24)).columns)
        self.restore_mode = lambda: None
        self.interactive = self.output.isatty() if interactive is None else interactive
        if self.interactive and interactive is None and os.name == "nt":
            self.interactive = self._enable_windows_vt()
        self.last_key = None
        self.last_status = None
        self.visible = False
        self._lock = RLock()
        self._closed = False

    def _enable_windows_vt(self):
        """WindowsコンソールではANSI行消去を有効化し、終了時に元へ戻す。"""
        import ctypes
        from ctypes import wintypes as wt
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.GetConsoleMode.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        api.SetConsoleMode.argtypes = [wt.HANDLE, wt.DWORD]
        try:
            import msvcrt
            handle = wt.HANDLE(msvcrt.get_osfhandle(self.output.fileno()))
            mode = wt.DWORD()
            if not api.GetConsoleMode(handle, ctypes.byref(mode)):
                return False
            if not api.SetConsoleMode(handle, mode.value | 0x04):
                return False
            self.restore_mode = lambda: api.SetConsoleMode(handle, mode.value)
            return True
        except (OSError, ValueError):
            return False

    def _limit(self):
        return max(1, self.columns() - 1)

    def _write(self, text):
        self.output.write(text)
        self.output.flush()

    def _clear(self):
        if self.interactive and self.visible:
            self._write(CLEAR_LINE)
            self.visible = False

    def _render(self):
        if self.last_status is not None:
            self._write(CLEAR_LINE + fit_line(self.last_status, self._limit()))
            self.visible = True

    def status(self, stage, status, waiting=False, synchronizing=False):
        with self._lock:
            if self._closed:
                return
            if status is None:
                message = ("音声出力開始待ち" if waiting else
                           "音声同期確認中" if synchronizing else "再生時刻確認中")
                key = (stage, message)
            else:
                loop, ms, window = status
                state = "有効" if window is not None else "無効"
                message = f"{LABELS[stage]} | {ms:.1f} ms | 判定:{state} | 周回{loop}"
                key = (stage, loop, window is not None)
            self.last_status = message
            if self.interactive:
                self._render()
            elif key != self.last_key:
                self._write(message + "\n")
            self.last_key = key

    def line(self, message, restore_status=False):
        with self._lock:
            if self._closed:
                return
            self._clear()
            # ログは省略せず、表示幅に合わせて改行する。
            limit = self._limit() if self.interactive else None
            for line in message.split("\n"):
                part = ""
                for char in line:
                    if limit and part and cell_width(part + char) > limit:
                        self._write(part + "\n")
                        part = ""
                    part += char
                self._write(part + "\n")
            if self.interactive and restore_status:
                self._render()

    def event(self, event):
        with self._lock:
            if self._closed:
                return
            labels = {"stage_changed": "曲切り替え", "parry_success": "パリイ成功",
                      "parry_failure": "パリイ失敗", "life_decrement": "ライフ減少信号受付",
                      "game_finished": "ゲーム終了"}
            if event.kind in ("stage_changed", "game_finished"):
                self.last_status = None
                self.last_key = None
            position = "" if event.position_ms is None else f" {event.position_ms:.1f} ms / 周回{event.loop}"
            self.line(f"{labels[event.kind]}: {LABELS[event.stage]}{position} {event.reason}".rstrip(),
                      restore_status=True)

    def clear_status(self):
        """戦闘終了時に状態行と重複抑制用の記録を消去する。"""

        with self._lock:
            if self._closed:
                return
            self.last_status = None
            self.last_key = None
            self._clear()

    def close(self):
        with self._lock:
            if self._closed:
                return
            try:
                self.clear_status()
            finally:
                self.restore_mode()
                self._closed = True
