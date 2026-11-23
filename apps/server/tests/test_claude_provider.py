"""Request shape of the Claude provider (no network: a fake client)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
from commentator.services.llm.providers import ClaudeCommentaryProvider
from commentator.services.llm.settings import CommentarySettings


class FakeClient:
    def __init__(self, *, rate_limit_fast: bool = False) -> None:
        self.requests: list[dict[str, Any]] = []
        self.rate_limit_fast = rate_limit_fast
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def with_options(self, **_: Any) -> FakeClient:
        return self

    def _create(self, **request: Any) -> Any:
        self.requests.append(dict(request))
        if self.rate_limit_fast and request.get("speed") == "fast":
            response = httpx2.Response(
                429, request=httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
            )
            raise anthropic.RateLimitError("fast mode limit", response=response, body=None)
        speed = request.get("speed", "standard")
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text="よし！")],
            model=request["model"],
            stop_reason="end_turn",
            usage=SimpleNamespace(speed=speed),
        )


def prompt(**changes: Any) -> Any:
    settings = CommentarySettings(provider="claude", **changes)
    return SimpleNamespace(settings=settings, system="system", user="user")


def test_opus_uses_fast_mode_by_default() -> None:
    client = FakeClient()
    output = ClaudeCommentaryProvider(client=client).generate(prompt(), timeout=10)
    assert client.requests[0]["speed"] == "fast"
    assert output.model == "claude-opus-5-5 (fast)"


def test_fast_mode_can_be_turned_off() -> None:
    client = FakeClient()
    output = ClaudeCommentaryProvider(client=client).generate(
        prompt(fast_mode=False), timeout=10
    )
    assert "speed" not in client.requests[0]
    assert output.model == "claude-opus-5-5"


def test_models_without_fast_mode_never_send_it() -> None:
    client = FakeClient()
    ClaudeCommentaryProvider(client=client).generate(
        prompt(claude_model="claude-sonnet-5-5"), timeout=10
    )
    assert "speed" not in client.requests[0]


def test_fast_mode_rate_limit_falls_back_to_standard_speed() -> None:
    client = FakeClient(rate_limit_fast=True)
    output = ClaudeCommentaryProvider(client=client).generate(prompt(), timeout=10)
    assert [request.get("speed") for request in client.requests] == ["fast", None]
    assert output.text == "よし！"
    assert output.model == "claude-opus-5-5"
