"""Unit tests for ingestion-runner failure detection.

Run from the backend directory: `python -m pytest tests/test_ingestion_runner.py`
"""

import pytest

from app.services.ingestion_runner import _agent_reply_is_rate_limited


class _FakeChats:
    """Stand-in for SinasClient.chats with a synchronous get()."""

    def __init__(self, chat: dict):
        self._chat = chat

    def get(self, chat_id: str) -> dict:
        return self._chat


class _FakeClient:
    def __init__(self, chat: dict):
        self.chats = _FakeChats(chat)


# The assistant reply Sinas stores when Anthropic rejects the call with a 400
# spend/usage cap (captured from a real ingestion run). The 429 markers do not
# match this, so before the fix the unit was marked succeeded with no data.
_USAGE_CAP_REPLY = (
    "An error occurred while processing your message. Please try again. "
    "Error: Error code: 400 - {'type': 'error', 'error': "
    "{'type': 'invalid_request_error', 'message': "
    "'You have reached your specified API usage limits. ...'}}"
)


@pytest.mark.asyncio
async def test_usage_cap_error_is_detected_as_failure():
    chat = {
        "messages": [
            {"role": "user", "content": "Classify document abc."},
            {"role": "assistant", "content": _USAGE_CAP_REPLY},
        ]
    }
    assert await _agent_reply_is_rate_limited(_FakeClient(chat), "chat-1") is True


@pytest.mark.asyncio
async def test_rate_limit_error_still_detected():
    chat = {
        "messages": [
            {"role": "assistant", "content": "Error code: 429 - rate_limit_error"},
        ]
    }
    assert await _agent_reply_is_rate_limited(_FakeClient(chat), "chat-1") is True


@pytest.mark.asyncio
async def test_clean_reply_is_not_a_failure():
    chat = {
        "messages": [
            {"role": "assistant", "content": "Classification Complete. Bulletin article."},
        ]
    }
    assert await _agent_reply_is_rate_limited(_FakeClient(chat), "chat-1") is False
