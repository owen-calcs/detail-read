from detail_read.app import _api_error
from detail_read.mcp_client import MissingKeyError


class _Resp:
    status_code = 401


class _HTTPStatusError(Exception):
    response = _Resp()


def test_rejected_key_inside_nested_exception_groups_is_a_401():
    wrapped = ExceptionGroup("tg", [ExceptionGroup("inner", [_HTTPStatusError("Client error")])])
    err = _api_error(wrapped)
    assert err.status_code == 401 and "rejected" in err.detail


def test_missing_key_is_a_401():
    assert _api_error(MissingKeyError("No connectionshub API key")).status_code == 401


def test_other_failures_are_502():
    assert _api_error(ExceptionGroup("tg", [RuntimeError("boom")])).status_code == 502
