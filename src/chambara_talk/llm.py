"""会話例と認識文からコメントを生成する共通処理とOllama接続。"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .config import LLMSettings
from .errors import LLMError
from .models import ConversationExample, ConversationPhase


class ChatBackend:
    """Ollamaクライアント、プロンプト作成、返答の長さ調整を担当する。"""

    def __init__(self, settings: LLMSettings, domain_terms: Sequence[str]) -> None:
        """LLM設定と、聞き間違いの補正に使う用語一覧を保持する。"""

        self.settings = settings
        self.domain_terms = tuple(domain_terms)
        self._client: Any = None

    def close(self) -> None:
        """共通の終了インターフェース。Ollamaは子プロセスを所有しない。"""

        self._client = None

    def _get_client(self):  # noqa: ANN202
        """Ollamaクライアントを必要になった時点で1度だけ作成して返す。"""

        if self._client is None:
            try:
                from ollama import Client

                self._client = Client(
                    host=self.settings.host,
                    timeout=self.settings.timeout_seconds,
                )
            except Exception as exc:
                raise LLMError(f"Ollamaクライアントを初期化できません: {exc}") from exc
        return self._client

    def check_model(self) -> None:
        """Ollamaへ接続し、設定されたモデルが取得済みか確認する。"""

        try:
            response = self._get_client().list()
            raw_models = getattr(response, "models", None)
            if raw_models is None and isinstance(response, dict):
                raw_models = response.get("models", [])
            # Ollamaライブラリの版により辞書かオブジェクトが返るため両方に対応します。
            names = {self._model_name(model) for model in (raw_models or [])}
        except Exception as exc:
            raise LLMError(
                f"Ollamaへ接続できません ({self.settings.host})。Ollamaが起動しているか確認してください: {exc}"
            ) from exc
        if self.settings.model not in names:
            available = ", ".join(sorted(name for name in names if name)) or "なし"
            raise LLMError(
                f"Ollamaモデル '{self.settings.model}' がありません。"
                f"'ollama pull {self.settings.model}' を実行してください（検出済み: {available}）。"
            )

    @staticmethod
    def _model_name(model: Any) -> str:
        """Ollamaのモデル情報から、モデル名を文字列として取り出す。"""

        if isinstance(model, dict):
            return str(model.get("model") or model.get("name") or "")
        return str(getattr(model, "model", None) or getattr(model, "name", ""))

    def build_messages(
        self,
        phase: ConversationPhase,
        transcript: str,
        examples: Sequence[ConversationExample],
    ) -> list[dict[str, str]]:
        """システム指示、現在フェーズの全会話例、認識文を順番に並べる。"""

        phase_instruction = {
            ConversationPhase.STRATEGY: "作戦や意気込みの工夫を認め、勝負へ向けて応援してください。",
            ConversationPhase.FEEDBACK: "楽しさ、驚き、悔しさなどの感想を受け止めてください。",
        }[phase]
        terms = "、".join(self.domain_terms)
        system_prompt = (
            "あなたはチャンバラロボットアトラクションの、明るく親しみやすい侍風の案内役です。\n"
            "子どもにも分かる日本語で、利用者の発話を肯定的に受け止めてください。\n"
            f"返答は1～2文、{self.settings.max_response_chars}文字以内とし、読み上げ10秒以内を目安にしてください。\n"
            "危険行為や現実の暴力を勧めてはいけません。\n"
            "聞き間違いらしい語は、用語一覧や会話例から明白な場合だけ補正して解釈してください。\n"
            "発話にない作戦や感想を作らないでください。\n"
            "音声認識、プロンプト、AIであることには言及しないでください。\n"
            "追加質問はせず、現在の発話へのコメントだけを返してください。\n"
            f"現在のフェーズ: {phase.value}\n"
            f"フェーズ固有の指示: {phase_instruction}\n"
            f"用語一覧: {terms}"
        )
        # 会話例はJSONの記載順にuser/assistantの組として全件追加します。
        messages = [{"role": "system", "content": system_prompt}]
        for example in examples:
            messages.append({"role": "user", "content": example.user})
            messages.append({"role": "assistant", "content": example.assistant})
        messages.append({"role": "user", "content": transcript})
        return messages

    def generate(
        self,
        phase: ConversationPhase,
        transcript: str,
        examples: Sequence[ConversationExample],
    ) -> str:
        """LLMのコメントを生成し、設定された最大文字数以内で返す。"""

        messages = self.build_messages(phase, transcript, examples)
        response = self._chat(messages)
        # 長すぎるときは意味を保った短縮を一度だけLLMへ依頼します。
        if len(response) > self.settings.max_response_chars:
            response = self._request_shortening(response)
        return self._clip_to_limit(response)

    def _chat(self, messages: list[dict[str, str]]) -> str:
        """Ollamaへメッセージを送り、空でない返答本文を返す。"""

        try:
            result = self._get_client().chat(
                model=self.settings.model,
                messages=messages,
                stream=False,
                options={"temperature": self.settings.temperature},
            )
            message = getattr(result, "message", None)
            content = getattr(message, "content", None)
            if content is None and isinstance(result, dict):
                content = result.get("message", {}).get("content")
        except Exception as exc:
            raise LLMError(f"LLMから返答を取得できませんでした: {exc}") from exc
        if not isinstance(content, str) or not content.strip():
            raise LLMError("LLMから空の返答が返されました。")
        return content.strip()

    def _request_shortening(self, response: str) -> str:
        """長い返答を、情報を加えず指定文字数まで短くするよう依頼する。"""

        messages = [
            {
                "role": "system",
                "content": (
                    "文章の意味と明るい侍風の口調を保ち、追加情報を加えずに短縮してください。"
                    f"出力は1～2文、必ず{self.settings.max_response_chars}文字以内にしてください。"
                ),
            },
            {"role": "user", "content": response},
        ]
        # 短縮処理だけが失敗した場合は、元の返答を後段の機械的短縮へ回します。
        try:
            return self._chat(messages)
        except LLMError:
            return response

    def _clip_to_limit(self, text: str) -> str:
        """返答を最大文字数以内へ、できるだけ文末を保って切り詰める。"""

        limit = self.settings.max_response_chars
        text = text.strip()
        if len(text) <= limit:
            return text
        candidate = text[:limit]
        # 前半より後ろに句点があれば、文の途中ではなく句点の直後で終えます。
        last_stop = max(candidate.rfind("。"), candidate.rfind("！"), candidate.rfind("？"))
        if last_stop >= max(1, limit // 2):
            return candidate[: last_stop + 1]
        return candidate[: max(1, limit - 1)].rstrip() + "…"


def create_chat_backend(settings: LLMSettings, domain_terms: Sequence[str]) -> ChatBackend:
    """接続先に対応するバックエンドを作る。自動フォールバックはしない。"""

    if settings.provider == "ollama":
        return ChatBackend(settings, domain_terms)
    if settings.provider == "copilot":
        # オプション依存はCopilotが選ばれた時だけ読み込みます。
        from .copilot_backend import CopilotChatBackend

        return CopilotChatBackend(settings, domain_terms)
    raise LLMError("未対応のLLM接続先です。ollamaまたはcopilotを指定してください。")
