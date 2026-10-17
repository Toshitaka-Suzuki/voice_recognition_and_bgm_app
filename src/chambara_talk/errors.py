"""アプリ内で想定しているエラーを種類別に定義するモジュール。"""


class ChambaraTalkError(Exception):
    """設定・録音・認識など、アプリ内で想定しているエラーの基底クラス。"""


class ConfigurationError(ChambaraTalkError):
    """設定ファイルまたは会話例ファイルが不正なときのエラー。"""

    pass


class AudioError(ChambaraTalkError):
    """マイクの確認または録音に失敗したときのエラー。"""

    pass


class SpeechRecognitionError(ChambaraTalkError):
    """faster-whisperの読み込みまたは音声認識に失敗したときのエラー。"""

    pass


class LLMError(ChambaraTalkError):
    """Ollamaへの接続またはコメント生成に失敗したときのエラー。"""

    pass


class TTSError(ChambaraTalkError):
    """pyttsx3の初期化または音声読み上げに失敗したときのエラー。"""

    pass


class ConversationCancelled(ChambaraTalkError):
    """外部信号によって進行中の会話処理が中断されたときの通知。"""

    pass
