"""Windowsコンソールの押下・解放イベントでキーリピートを除外する。"""
import ctypes
from ctypes import wintypes as wt
from time import perf_counter


class _Char(ctypes.Union):
    _fields_ = [("UnicodeChar", wt.WCHAR), ("AsciiChar", ctypes.c_char)]


class _Key(ctypes.Structure):
    _fields_ = [("down", wt.BOOL), ("repeat", wt.WORD), ("vk", wt.WORD),
                ("scan", wt.WORD), ("char", _Char), ("control", wt.DWORD)]


class _Payload(ctypes.Union):
    _fields_ = [("key", _Key), ("padding", ctypes.c_byte * 16)]


class _Record(ctypes.Structure):
    _fields_ = [("kind", wt.WORD), ("payload", _Payload)]


class Keyboard:
    def __init__(self):
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.GetStdHandle.argtypes = [wt.DWORD]
        self.api.GetStdHandle.restype = wt.HANDLE
        self.api.GetConsoleMode.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        self.api.SetConsoleMode.argtypes = [wt.HANDLE, wt.DWORD]
        self.api.GetNumberOfConsoleInputEvents.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        self.api.ReadConsoleInputW.argtypes = [wt.HANDLE, ctypes.POINTER(_Record), wt.DWORD,
                                             ctypes.POINTER(wt.DWORD)]
        self.handle = self.api.GetStdHandle(wt.DWORD(-10))
        mode = wt.DWORD()
        self._check(self.api.GetConsoleMode(self.handle, ctypes.byref(mode)))
        self.original_mode = mode.value
        # Quick Editによる処理停止を防ぎ、行入力とエコーを無効化。
        self._check(self.api.SetConsoleMode(self.handle, (mode.value | 0x80) & ~(0x40 | 0x02 | 0x04)))
        self.held = set()
        try:
            self.discard()
        except Exception:
            self.close()
            raise

    @staticmethod
    def _check(result):
        if not result:
            raise ctypes.WinError(ctypes.get_last_error())

    def read(self):
        count = wt.DWORD()
        self._check(self.api.GetNumberOfConsoleInputEvents(self.handle, ctypes.byref(count)))
        if not count.value:
            return []
        records = (_Record * count.value)()
        received = wt.DWORD()
        self._check(self.api.ReadConsoleInputW(self.handle, records, count, ctypes.byref(received)))
        result = []
        mapping = {13: "start", 32: "parry", 76: "life", 81: "quit"}
        for record in records[:received.value]:
            if record.kind != 1:
                continue
            key = record.payload.key
            if not key.down:
                self.held.discard(key.vk)
            elif key.vk not in self.held:
                self.held.add(key.vk)
                if key.vk in mapping:
                    result.append((mapping[key.vk], perf_counter()))
        return result

    def discard(self):
        """待ち入力を処理して捨てる。押下状態は残し長押しの再受付を防ぐ。"""
        self.read()

    def close(self):
        self._check(self.api.SetConsoleMode(self.handle, self.original_mode))
