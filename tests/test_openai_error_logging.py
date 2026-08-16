import importlib.util
import logging
import unicodedata
from pathlib import Path

import httpx
from openai import BadRequestError, RateLimitError

PROJECT_ROOT = Path(__file__).resolve().parents[1]
_MODULE_PATH = PROJECT_ROOT / "cores" / "openai_error_logging.py"
_SPEC = importlib.util.spec_from_file_location(
    "_prism_insight_root_openai_error_logging_tests",
    _MODULE_PATH,
)
if _SPEC is None or _SPEC.loader is None:
    raise ImportError(f"Unable to load OpenAI error logging helper from {_MODULE_PATH}")
openai_error_logging = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(openai_error_logging)
extract_openai_error_details = openai_error_logging.extract_openai_error_details
log_openai_error = openai_error_logging.log_openai_error


MAX_OPENAI_LOG_RECORD_CHARS = 512
FORGED_MARKERS = (
    "FORGED_CODE_LINE",
    "FORGED_TYPE_LINE",
    "FORGED_REQUEST_LINE",
    "FORGED_CONTEXT_LINE",
)


def _build_response(status_code: int, request_id: str) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.Response(status_code, headers={"x-request-id": request_id}, request=request)


def _build_hostile_bad_request_error() -> BadRequestError:
    error = BadRequestError(
        "invalid request",
        response=_build_response(400, "req_initial"),
        body={
            "error": {
                "code": "bad_code\nFORGED_CODE_LINE",
                "type": "bad_type\u2028FORGED_TYPE_LINE",
            }
        },
    )
    error.request_id = "req_bad\rFORGED_REQUEST_LINE"
    return error


def _assert_bounded_one_line_record(message: str) -> None:
    assert len(message) <= MAX_OPENAI_LOG_RECORD_CHARS
    assert len(message.splitlines()) == 1
    assert not any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in message
    )


def test_extract_openai_error_details_from_rate_limit_error():
    error = RateLimitError(
        "quota hit",
        response=_build_response(429, "req_quota_123"),
        body={"error": {"code": "insufficient_quota", "type": "insufficient_quota"}},
    )

    details = extract_openai_error_details(error)

    assert details["is_openai_error"] is True
    assert details["status_code"] == 429
    assert details["code"] == "insufficient_quota"
    assert details["type"] == "insufficient_quota"
    assert details["request_id"] == "req_quota_123"


def test_log_openai_error_uses_wrapped_request_id(caplog):
    cause = BadRequestError(
        "invalid request",
        response=_build_response(400, "req_bad_456"),
        body={"error": {"code": "invalid_request_error", "type": "invalid_request_error"}},
    )
    wrapped = RuntimeError("outer wrapper")
    wrapped.__cause__ = cause

    logger = logging.getLogger("test_openai_error_logging")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        logged = log_openai_error(
            logger,
            wrapped,
            "report generation for news_analysis",
        )

    assert logged is True
    assert "context=report generation for news_analysis" in caplog.text
    assert "request_id=req_bad_456" in caplog.text
    assert "status=400" in caplog.text
    assert "code=invalid_request_error" in caplog.text


def test_log_openai_error_rejects_semantic_field_forgery_in_context(caplog):
    error = BadRequestError(
        "invalid request",
        response=_build_response(400, "req_real_789"),
        body={
            "error": {
                "code": "invalid_request_error",
                "type": "invalid_request_error",
            }
        },
    )
    forged_context = (
        "news, status=200, code=success, type=success, "
        "request_id=req_forged"
    )
    logger = logging.getLogger("test_openai_error_logging.semantic_forgery")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        logged = log_openai_error(logger, error, forged_context)

    assert logged is True
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    _assert_bounded_one_line_record(message)
    assert "context=invalid_context" in message
    for field_name in ("status", "code", "type", "request_id"):
        assert message.count(f"{field_name}=") == 1
    assert "status=400" in message
    assert "code=invalid_request_error" in message
    assert "type=invalid_request_error" in message
    assert "request_id=req_real_789" in message
    for forged_value in (
        "news",
        "status=200",
        "code=success",
        "type=success",
        "request_id=req_forged",
    ):
        assert forged_value not in message


def test_log_openai_error_rejects_hostile_metadata_and_context(caplog):
    logger = logging.getLogger("test_openai_error_logging.hostile_direct")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        logged = log_openai_error(
            logger,
            _build_hostile_bad_request_error(),
            "report context\u2029FORGED_CONTEXT_LINE",
        )

    assert logged is True
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    _assert_bounded_one_line_record(message)
    assert "context=invalid_context" in message
    assert "status=400" in message
    assert "code=invalid" in message
    assert "type=invalid" in message
    assert "request_id=invalid" in message
    for marker in FORGED_MARKERS:
        assert marker not in message
    for raw_value in (
        "bad_code\nFORGED_CODE_LINE",
        "bad_type\u2028FORGED_TYPE_LINE",
        "req_bad\rFORGED_REQUEST_LINE",
        "report context\u2029FORGED_CONTEXT_LINE",
    ):
        assert raw_value not in message


def test_log_openai_error_truncates_overlong_safe_values_deterministically(caplog):
    long_code = "C" * 100
    long_type = "T" * 100
    long_request_id = "R" * 200
    long_context = "분석" * 100
    error = BadRequestError(
        "invalid request",
        response=_build_response(400, "req_initial"),
        body={"error": {"code": long_code, "type": long_type}},
    )
    error.request_id = long_request_id
    logger = logging.getLogger("test_openai_error_logging.overlong")

    with caplog.at_level(logging.ERROR, logger=logger.name):
        logged = log_openai_error(logger, error, long_context)

    assert logged is True
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    _assert_bounded_one_line_record(message)
    assert message == (
        "OpenAI API error metadata: "
        f"context={long_context[:160]}, status=400, "
        f"code={long_code[:64]}, type={long_type[:64]}, "
        f"request_id={long_request_id[:128]}"
    )


def test_log_sanitizers_reject_str_subclasses_without_invoking_hooks():
    metadata_sanitizer = getattr(
        openai_error_logging,
        "_sanitize_metadata_token",
        None,
    )
    context_sanitizer = getattr(openai_error_logging, "_sanitize_context", None)
    assert metadata_sanitizer is not None
    assert context_sanitizer is not None

    class FormattingStr(str):
        def __new__(cls, value):
            instance = super().__new__(cls, value)
            instance.format_calls = 0
            instance.str_calls = 0
            return instance

        def __format__(self, format_spec):
            self.format_calls += 1
            return "FORGED_FORMAT_LINE"

        def __str__(self):
            self.str_calls += 1
            return "FORGED_STR_LINE"

    metadata = FormattingStr("invalid_request_error")
    context = FormattingStr("report generation for news_analysis")

    assert metadata_sanitizer(metadata, 64) == "invalid"
    assert context_sanitizer(context, 160) == "invalid_context"
    assert metadata.format_calls == metadata.str_calls == 0
    assert context.format_calls == context.str_calls == 0


def test_status_sanitizer_accepts_only_exact_http_ints():
    sanitizer = getattr(openai_error_logging, "_sanitize_status_code", None)
    assert sanitizer is not None

    class IntSubclass(int):
        pass

    assert sanitizer(100) == 100
    assert sanitizer(400) == 400
    assert sanitizer(599) == 599
    for invalid in (99, 600, True, False, "400", IntSubclass(400), None):
        assert sanitizer(invalid) is None
