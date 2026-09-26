from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .config import LLMSettings
from .errors import LLMError
from .models import ConversationExample, ConversationPhase


class ChatBackend:
    def __init__(self, settings: LLMSettings, domain_terms: Sequence[str]) -> None:
        self.settings = settings
        self.domain_terms = tuple(domain_terms)
        self._client: Any = None

    def _get_client(self):  # noqa: ANN202
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
        try:
            response = self._get_client().list()
            raw_models = getattr(response, "models", None)
            if raw_models is None and isinstance(response, dict):
                raw_models = response.get("models", [])
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
        if isinstance(model, dict):
            return str(model.get("model") or model.get("name") or "")
        return str(getattr(model, "model", None) or getattr(model, "name", ""))

    def build_messages(
        self,
        phase: ConversationPhase,
        transcript: str,
        examples: Sequence[ConversationExample],
    ) -> list[dict[str, str]]:
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
        messages = self.build_messages(phase, transcript, examples)
        response = self._chat(messages)
        if len(response) > self.settings.max_response_chars:
            response = self._request_shortening(response)
        return self._clip_to_limit(response)

    def _chat(self, messages: list[dict[str, str]]) -> str:
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
        try:
            return self._chat(messages)
        except LLMError:
            return response

    def _clip_to_limit(self, text: str) -> str:
        limit = self.settings.max_response_chars
        text = text.strip()
        if len(text) <= limit:
            return text
        candidate = text[:limit]
        last_stop = max(candidate.rfind("。"), candidate.rfind("！"), candidate.rfind("？"))
        if last_stop >= max(1, limit // 2):
            return candidate[: last_stop + 1]
        return candidate[: max(1, limit - 1)].rstrip() + "…"

