"""端末出力を捕捉し、日本語幅・イベント分離・状態抑制を検証する。"""
import io
import unittest

from chambara_bgm.display import CLEAR_LINE, ConsoleDisplay, cell_width, fit_line
from chambara_bgm.engine import Event


class DisplayTests(unittest.TestCase):
    def make_display(self, width=80, interactive=True):
        output = io.StringIO()
        return ConsoleDisplay(output, interactive, lambda: width), output

    def test_japanese_width_and_terminal_limits(self):
        self.assertEqual(cell_width("普通 abc"), 8)
        for width in (20, 40, 80, 100):
            with self.subTest(width=width):
                display, output = self.make_display(width)
                display.status("normal", (9999, 48901.4, None))
                text = output.getvalue().removeprefix(CLEAR_LINE)
                self.assertLessEqual(cell_width(text), width - 1)
                self.assertNotIn("\n", text)
        self.assertEqual(fit_line("日本語", 3), "日…")

    def test_shorter_update_and_resize_clear_old_line(self):
        width = [100]
        output = io.StringIO()
        display = ConsoleDisplay(output, True, lambda: width[0])
        display.status("normal", (1000, 1234567, 1))
        width[0] = 40
        display.status("normal", None, waiting=True)
        parts = output.getvalue().split(CLEAR_LINE)
        self.assertEqual(len(parts), 3)
        self.assertEqual(parts[-1], "音声出力開始待ち")
        self.assertLessEqual(cell_width(parts[-1]), 39)

    def test_event_separate_line_and_redraw(self):
        display, output = self.make_display()
        display.status("easy", (1, 1000, 0))
        output.seek(0)
        output.truncate()
        display.event(Event("parry_success", "easy", 1, 1000, 0))
        text = output.getvalue()
        self.assertTrue(text.startswith(CLEAR_LINE + "パリイ成功:"))
        self.assertIn("\n" + CLEAR_LINE + "簡単", text)
        display.event(Event("stage_changed", "normal"))
        self.assertIsNone(display.last_status)
        self.assertFalse(display.visible)

    def test_narrow_event_preserves_full_text(self):
        display, output = self.make_display(40)
        message = "アプリを停止しました: " + "長い診断情報" * 20
        display.line(message)
        lines = output.getvalue().splitlines()
        self.assertEqual("".join(lines), message)
        self.assertTrue(all(cell_width(line) <= 39 for line in lines))

    def test_synchronizing_status_and_warning_keep_separate_lines(self):
        display, output = self.make_display()
        display.status("easy", None, synchronizing=True)
        display.line("音声警告: output underflow", restore_status=True)
        self.assertIn("\n" + CLEAR_LINE + "音声同期確認中", output.getvalue())

    def test_noninteractive_only_state_changes_and_all_events(self):
        display, output = self.make_display(interactive=False)
        for ms in range(100):
            display.status("easy", (1, ms, None))
        self.assertEqual(len(output.getvalue().splitlines()), 1)
        display.status("easy", (1, 1000, 0))
        display.status("easy", (1, 1001, 1))
        display.status("easy", (2, 0, None))
        display.status("normal", None, waiting=True)
        display.status("normal", None, waiting=False)
        display.event(Event("parry_failure", "normal", 1, 2000, reason="範囲外"))
        display.close()
        text = output.getvalue()
        self.assertEqual(len(text.splitlines()), 6)
        self.assertNotIn("\r", text)
        self.assertNotIn("\x1b", text)
        self.assertEqual(text.count("パリイ失敗"), 1)


if __name__ == "__main__":
    unittest.main()
