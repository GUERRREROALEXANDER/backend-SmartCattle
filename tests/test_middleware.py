"""Unit checks for the request id sanitiser.

Tested directly because the interesting cases -- a header that could forge a
log line -- are the ones a client would not send by accident, so they are
awkward to cover through the endpoint alone.
"""

import pytest

from app.middleware import MAX_REQUEST_ID_LENGTH, _safe_request_id


def test_a_missing_header_gets_a_fresh_id():
    generated = _safe_request_id(None)
    assert len(generated) == 32
    assert generated != _safe_request_id(None)


@pytest.mark.parametrize("supplied", [
    "frontend-retry-7",
    "abc123",
    "a_b-c",
    "x" * MAX_REQUEST_ID_LENGTH,
])
def test_a_usable_id_is_kept(supplied):
    # So a retrying frontend or a proxy keeps one id across the hop.
    assert _safe_request_id(supplied) == supplied


def test_surrounding_whitespace_is_trimmed():
    assert _safe_request_id("  abc123  ") == "abc123"


@pytest.mark.parametrize("supplied", [
    "",
    "   ",
    "with space",
    "line\nbreak",
    "carriage\rreturn",
    'quote"here',
    "brace{here}",
    "semi;colon",
    "x" * (MAX_REQUEST_ID_LENGTH + 1),
])
def test_an_unusable_id_is_replaced(supplied):
    # Replaced rather than escaped. This value lands in every log line for the
    # request: a newline forges a log entry, and an oversized one bloats every
    # line it touches.
    generated = _safe_request_id(supplied)
    assert generated != supplied
    assert len(generated) == 32
    assert generated.isalnum()
