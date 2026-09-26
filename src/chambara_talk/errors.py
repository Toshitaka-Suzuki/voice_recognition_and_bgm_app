class ChambaraTalkError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(ChambaraTalkError):
    pass


class AudioError(ChambaraTalkError):
    pass


class SpeechRecognitionError(ChambaraTalkError):
    pass


class LLMError(ChambaraTalkError):
    pass


class TTSError(ChambaraTalkError):
    pass
