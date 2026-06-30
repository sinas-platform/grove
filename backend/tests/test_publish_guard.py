"""Unit tests for the publish_result empty-result guard.

publish_result must refuse to publish a Result that has no attached documents
(422), so a merge that produced an empty parent can never be published. A
Result with at least one document publishes normally.

Run from the backend directory: `python -m pytest tests/test_publish_guard.py`
"""

import uuid

import pytest
from fastapi import HTTPException

from app.api.v1.retrieval import publish_result


class _FakeResult:
    def __init__(self, rid):
        self.id = rid
        self.status = "draft"
        self.published_at = None


class _ScalarResult:
    def __init__(self, value):
        self._value = value

    def scalar_one(self):
        return self._value


class _FakeSession:
    """Async session stand-in: get() returns a preset Result, execute()
    returns a preset document count, commit() records that it ran."""

    def __init__(self, doc_count, result):
        self._doc_count = doc_count
        self._result = result
        self.committed = False

    async def get(self, _model, _rid):
        return self._result

    async def execute(self, _stmt):
        return _ScalarResult(self._doc_count)

    async def commit(self):
        self.committed = True


@pytest.mark.asyncio
async def test_publish_empty_result_is_rejected():
    rid = uuid.uuid4()
    session = _FakeSession(doc_count=0, result=_FakeResult(rid))
    with pytest.raises(HTTPException) as exc:
        await publish_result(rid, session=session)
    assert exc.value.status_code == 422
    assert session.committed is False


@pytest.mark.asyncio
async def test_publish_result_with_documents_succeeds():
    rid = uuid.uuid4()
    result = _FakeResult(rid)
    session = _FakeSession(doc_count=3, result=result)
    out = await publish_result(rid, session=session)
    assert out["status"] == "published"
    assert out["id"] == rid
    assert result.published_at is not None
    assert session.committed is True


@pytest.mark.asyncio
async def test_publish_missing_result_is_404():
    rid = uuid.uuid4()
    session = _FakeSession(doc_count=0, result=None)
    with pytest.raises(HTTPException) as exc:
        await publish_result(rid, session=session)
    assert exc.value.status_code == 404
    assert session.committed is False
