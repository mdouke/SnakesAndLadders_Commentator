"""Commentary providers: a deterministic template, Claude and OpenAI.

Every provider takes the same ``CommentaryPrompt`` and returns ``ProviderOutput``.
Failures are raised as ``ProviderError`` with ``retryable`` set, so the service
decides about retries in one place. SDK retries are switched off for that reason.

SDKs are imported lazily: the server and the tests run without them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from threading import Lock
from time import sleep
from typing import Any, Protocol

from .context import CommentaryContext, CommentaryPrompt


@dataclass(frozen=True, slots=True)
class ProviderOutput:
    text: str
    model: str
    finish_reason: str | None = None
    truncated: bool = False
    request_id: str | None = None


class ProviderError(Exception):
    def __init__(self, message: str, *, code: str, retryable: bool) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


class CommentaryProvider(Protocol):
    name: str

    def generate(self, prompt: CommentaryPrompt, *, timeout: float) -> ProviderOutput: ...


def template_commentary(context: CommentaryContext, *, language: str, honorific: bool) -> str:
    """Safe commentary made only from facts; also the fallback for LLM failures."""
    if language == "en":
        return _template_en(context)
    name = f"{context.player_name}さん" if honorific else context.player_name
    parts = [f"{name}、出目は{context.roll}！"]
    if context.overshoot:
        parts.append(f"ゴールを超えてしまい、{context.normal_position}マス目へ戻ります。")
    else:
        parts.append(f"{context.before}マス目から{context.normal_position}マス目へ進みます。")
    if context.jump_type == "ladder":
        parts.append(f"ハシゴを登って一気に{context.final_position}マス目！")
    elif context.jump_type == "snake":
        parts.append(f"ヘビに飲まれて{context.final_position}マス目まで落ちてしまいました。")
    if "goal" in context.events:
        parts.append("見事ゴール！おめでとうございます！")
    elif "near_goal" in context.events:
        parts.append(f"ゴールまであと{context.goal - context.final_position}マス！")
    elif context.leader_name == context.player_name:
        parts.append("現在トップを走っています。")
    return "".join(parts)


def _template_en(context: CommentaryContext) -> str:
    parts = [f"{context.player_name} rolls a {context.roll}!"]
    if context.overshoot:
        parts.append(f"Past the goal, so back to square {context.normal_position}.")
    else:
        parts.append(f"From square {context.before} to {context.normal_position}.")
    if context.jump_type == "ladder":
        parts.append(f"Up the ladder to square {context.final_position}!")
    elif context.jump_type == "snake":
        parts.append(f"A snake slides them down to square {context.final_position}.")
    if "goal" in context.events:
        parts.append("Goal! Congratulations!")
    elif "near_goal" in context.events:
        parts.append(f"Only {context.goal - context.final_position} squares to go!")
    elif context.leader_name == context.player_name:
        parts.append("Leading the race!")
    return " ".join(parts)


class FakeCommentaryProvider:
    """Deterministic template commentary with an optional artificial delay."""

    name = "fake"

    def __init__(self, *, delay_seconds: float = 0.0) -> None:
        if delay_seconds < 0:
            raise ValueError("生成の待ち時間は0以上にしてください")
        self.delay_seconds = delay_seconds

    def generate(self, prompt: CommentaryPrompt, *, timeout: float) -> ProviderOutput:
        if self.delay_seconds:
            sleep(self.delay_seconds)
        text = template_commentary(
            prompt.context,
            language=prompt.settings.language,
            honorific=prompt.settings.use_names,
        )
        return ProviderOutput(text=text, model="template", finish_reason="stop")


# Claude models that accept server-side refusal fallbacks and the effort setting.
_CLAUDE_FALLBACK_MODELS = {
    "claude-fable-5-2", "claude-fable-5-1", "claude-opus-5-5", "claude-opus-5",
    "claude-sonnet-5-5",
}
_CLAUDE_EFFORT_PREFIXES = (
    "claude-fable-5", "claude-mythos-5", "claude-opus-5", "claude-sonnet-5",
    "claude-haiku-5", "claude-opus-4-6", "claude-opus-4-7", "claude-opus-4-8",
    "claude-sonnet-4-6",
)


class ClaudeCommentaryProvider:
    """Claude through the official Anthropic SDK.

    Credentials come from the SDK's normal resolution order (``ANTHROPIC_API_KEY``,
    ``ANTHROPIC_AUTH_TOKEN`` or an ``ant auth login`` profile), never from settings.
    """

    name = "claude"

    def __init__(self, *, client: Any = None) -> None:
        self._client = client
        self._lock = Lock()

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                try:
                    import anthropic
                except ImportError as error:
                    raise ProviderError(
                        "anthropic パッケージがインストールされていません",
                        code="provider_unavailable", retryable=False,
                    ) from error
                self._client = anthropic.Anthropic(max_retries=0)
            return self._client

    def generate(self, prompt: CommentaryPrompt, *, timeout: float) -> ProviderOutput:
        import anthropic

        model = prompt.settings.claude_model
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": 4000,
            "system": prompt.system,
            "messages": [{"role": "user", "content": prompt.user}],
        }
        if model.startswith(_CLAUDE_EFFORT_PREFIXES):
            # Short commentary needs little reasoning; low effort keeps latency down.
            request["output_config"] = {"effort": "low"}
        if model in _CLAUDE_FALLBACK_MODELS:
            request["betas"] = ["server-side-fallback-2026-07-01"]
            request["fallbacks"] = "default"
        try:
            client = self._get_client().with_options(timeout=timeout)
            if "betas" in request:
                response = client.beta.messages.create(**request)
            else:
                response = client.messages.create(**request)
        except anthropic.APITimeoutError as error:
            raise ProviderError("Claude の応答がタイムアウトしました",
                                code="provider_timeout", retryable=True) from error
        except anthropic.APIConnectionError as error:
            raise ProviderError("Claude に接続できませんでした",
                                code="provider_connection", retryable=True) from error
        except anthropic.RateLimitError as error:
            raise ProviderError("Claude の利用上限に達しました",
                                code="provider_rate_limited", retryable=True) from error
        except anthropic.AuthenticationError as error:
            raise ProviderError("Claude の認証に失敗しました（APIキーを確認してください）",
                                code="provider_auth", retryable=False) from error
        except anthropic.APIStatusError as error:
            raise ProviderError(f"Claude がエラーを返しました（HTTP {error.status_code}）",
                                code="provider_status", retryable=error.status_code >= 500,
                                ) from error
        except anthropic.AnthropicError as error:
            raise ProviderError(f"Claude を呼び出せませんでした: {type(error).__name__}",
                                code="provider_error", retryable=False) from error
        if response.stop_reason == "refusal":
            raise ProviderError("Claude が応答を断りました", code="provider_refused",
                                retryable=False)
        text = "".join(block.text for block in response.content if block.type == "text")
        return ProviderOutput(
            text=text,
            model=str(response.model),
            finish_reason=response.stop_reason,
            truncated=response.stop_reason == "max_tokens",
            request_id=getattr(response, "_request_id", None),
        )


class OpenAICommentaryProvider:
    """OpenAI through the official OpenAI SDK (Responses API).

    The key comes from ``OPENAI_API_KEY``, never from settings.
    """

    name = "openai"

    def __init__(self, *, client: Any = None) -> None:
        self._client = client
        self._lock = Lock()

    def _get_client(self) -> Any:
        with self._lock:
            if self._client is None:
                try:
                    import openai
                except ImportError as error:
                    raise ProviderError(
                        "openai パッケージがインストールされていません",
                        code="provider_unavailable", retryable=False,
                    ) from error
                try:
                    self._client = openai.OpenAI(max_retries=0)
                except openai.OpenAIError as error:
                    raise ProviderError(
                        "OpenAI のAPIキーが設定されていません（OPENAI_API_KEY）",
                        code="provider_auth", retryable=False,
                    ) from error
            return self._client

    def generate(self, prompt: CommentaryPrompt, *, timeout: float) -> ProviderOutput:
        import openai

        client = self._get_client()
        try:
            response = client.with_options(timeout=timeout).responses.create(
                model=prompt.settings.openai_model,
                instructions=prompt.system,
                input=prompt.user,
                max_output_tokens=4000,
            )
        except openai.APITimeoutError as error:
            raise ProviderError("OpenAI の応答がタイムアウトしました",
                                code="provider_timeout", retryable=True) from error
        except openai.APIConnectionError as error:
            raise ProviderError("OpenAI に接続できませんでした",
                                code="provider_connection", retryable=True) from error
        except openai.RateLimitError as error:
            raise ProviderError("OpenAI の利用上限に達しました",
                                code="provider_rate_limited", retryable=True) from error
        except openai.AuthenticationError as error:
            raise ProviderError("OpenAI の認証に失敗しました（APIキーを確認してください）",
                                code="provider_auth", retryable=False) from error
        except openai.APIStatusError as error:
            raise ProviderError(f"OpenAI がエラーを返しました（HTTP {error.status_code}）",
                                code="provider_status", retryable=error.status_code >= 500,
                                ) from error
        except openai.OpenAIError as error:
            raise ProviderError(f"OpenAI を呼び出せませんでした: {type(error).__name__}",
                                code="provider_error", retryable=False) from error
        for item in getattr(response, "output", None) or []:
            for part in getattr(item, "content", None) or []:
                if getattr(part, "type", None) == "refusal":
                    raise ProviderError("OpenAI が応答を断りました",
                                        code="provider_refused", retryable=False)
        status = getattr(response, "status", None)
        return ProviderOutput(
            text=str(getattr(response, "output_text", "") or ""),
            model=str(getattr(response, "model", prompt.settings.openai_model)),
            finish_reason=status,
            truncated=status == "incomplete",
            request_id=getattr(response, "_request_id", None),
        )


def provider_availability() -> dict[str, dict[str, Any]]:
    """What the admin screen shows; never reveals key values."""

    def installed(module: str) -> bool:
        try:
            __import__(module)
            return True
        except ImportError:
            return False

    return {
        "fake": {"installed": True, "credentialsConfigured": True},
        "claude": {
            "installed": installed("anthropic"),
            # The SDK can also use an `ant auth login` profile, so this is a hint only.
            "credentialsConfigured": bool(
                os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")
            ),
        },
        "openai": {
            "installed": installed("openai"),
            "credentialsConfigured": bool(os.environ.get("OPENAI_API_KEY")),
        },
    }
