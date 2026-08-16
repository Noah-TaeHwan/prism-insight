import logging
import unicodedata
from types import SimpleNamespace

import firecrawl
import pytest
import requests
from firecrawl.v2.utils import http_client as firecrawl_http_client

import firecrawl_client


class FakeFirecrawlApp:
    def __init__(self, *, search_error=None, scrape_error=None):
        self.search_calls = []
        self.scrape_calls = []
        self.search_error = search_error
        self.scrape_error = scrape_error
        self.search_result = SimpleNamespace(web=[])
        self.scrape_result = SimpleNamespace(markdown="# Article")

    def search(self, query, **kwargs):
        self.search_calls.append((query, kwargs))
        if self.search_error is not None:
            raise self.search_error
        return self.search_result

    def scrape(self, url, **kwargs):
        self.scrape_calls.append((url, kwargs))
        if self.scrape_error is not None:
            raise self.scrape_error
        return self.scrape_result


def _install_fake_app(monkeypatch, fake_app):
    monkeypatch.setattr(firecrawl_client, "get_firecrawl_app", lambda: fake_app)


def test_is_firecrawl_configured_returns_false_when_env_is_missing(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)

    assert firecrawl_client.is_firecrawl_configured() is False


@pytest.mark.parametrize("value", ["", "   ", "\t\n"])
def test_is_firecrawl_configured_returns_false_for_empty_or_whitespace_env(
    monkeypatch,
    value,
):
    monkeypatch.setenv("FIRECRAWL_API_KEY", value)

    assert firecrawl_client.is_firecrawl_configured() is False


@pytest.mark.parametrize(
    "value",
    ["  example key  ", "  your-api-key  ", "  YOUR_API_KEY  "],
)
def test_is_firecrawl_configured_returns_false_for_trimmed_placeholders(
    monkeypatch,
    value,
):
    monkeypatch.setenv("FIRECRAWL_API_KEY", value)

    assert firecrawl_client.is_firecrawl_configured() is False


def test_is_firecrawl_configured_returns_true_for_non_placeholder_env(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key-not-real")

    assert firecrawl_client.is_firecrawl_configured() is True


def test_get_firecrawl_app_configures_one_attempt_and_explicit_http_timeout(
    monkeypatch,
):
    constructor_calls = []
    sentinel = object()

    def fake_constructor(**kwargs):
        constructor_calls.append(kwargs)
        return sentinel

    monkeypatch.setattr(firecrawl_client, "_firecrawl_app", None)
    monkeypatch.setattr(firecrawl_client, "_get_api_key", lambda: "dummy-key")
    monkeypatch.setattr(firecrawl, "FirecrawlApp", fake_constructor)

    assert firecrawl_client.get_firecrawl_app() is sentinel
    assert constructor_calls == [
        {
            "api_key": "dummy-key",
            "timeout": 30,
            "max_retries": 1,
        }
    ]


def test_sdk_http_client_max_retries_one_makes_one_post_with_30_second_timeout(
    monkeypatch,
):
    post_calls = []

    def fake_post(url, **kwargs):
        post_calls.append((url, kwargs))
        raise requests.RequestException("injected transport failure")

    monkeypatch.setattr(firecrawl_http_client.requests, "post", fake_post)
    client = firecrawl_http_client.HttpClient(
        api_key="dummy-key",
        api_url="https://firecrawl.invalid",
        timeout=30,
        max_retries=1,
    )

    with pytest.raises(requests.RequestException, match="injected transport failure"):
        client.post("/v2/search", {"query": "offline contract test"})

    assert len(post_calls) == 1
    assert post_calls[0][0] == "https://firecrawl.invalid/v2/search"
    assert post_calls[0][1]["timeout"] == 30


def test_search_forwards_news_source_and_tbs(monkeypatch):
    fake_app = FakeFirecrawlApp()
    _install_fake_app(monkeypatch, fake_app)

    result = firecrawl_client.firecrawl_search(
        "Apple news",
        limit=5,
        sources=["news"],
        tbs="qdr:w",
    )

    assert result is fake_app.search_result
    assert fake_app.search_calls == [
        (
            "Apple news",
            {"limit": 5, "sources": ["news"], "tbs": "qdr:w"},
        )
    ]
    assert "scrape_options" not in fake_app.search_calls[0][1]


def test_success_log_omits_raw_query_and_preserves_search_result(monkeypatch, caplog):
    query = "Apple\nFORGED_QUERY SECRET_QUERY_PAYLOAD"
    fake_app = FakeFirecrawlApp()
    fake_app.search_result = SimpleNamespace(web=[object(), object()])
    _install_fake_app(monkeypatch, fake_app)

    with caplog.at_level(logging.INFO, logger=firecrawl_client.__name__):
        result = firecrawl_client.firecrawl_search(query, limit=4)

    assert result is fake_app.search_result
    assert fake_app.search_calls == [(query, {"limit": 4})]
    success_records = [
        record
        for record in caplog.records
        if record.name == firecrawl_client.__name__
        and record.getMessage().startswith("Firecrawl search")
    ]
    assert len(success_records) == 1
    message = success_records[0].getMessage()
    assert message == "Firecrawl search completed: results=2, with_content=False"
    assert query not in message
    for forbidden in ("Apple", "FORGED_QUERY", "SECRET_QUERY_PAYLOAD"):
        assert forbidden not in message
    assert len(message.splitlines()) == 1
    assert not any(
        unicodedata.category(character).startswith("C")
        or unicodedata.category(character) in {"Zl", "Zp"}
        for character in message
    )


def test_scrape_requests_markdown_main_content(monkeypatch):
    fake_app = FakeFirecrawlApp()
    _install_fake_app(monkeypatch, fake_app)

    result = firecrawl_client.firecrawl_scrape("https://example.com/article")

    assert result is fake_app.scrape_result
    assert fake_app.scrape_calls == [
        (
            "https://example.com/article",
            {
                "formats": ["markdown"],
                "only_main_content": True,
                "max_age": 7_200_000,
                "timeout": 30_000,
            },
        )
    ]


def test_search_error_returns_none(monkeypatch, caplog):
    exception_detail = "private-test-detail"
    fake_app = FakeFirecrawlApp(search_error=RuntimeError(exception_detail))
    _install_fake_app(monkeypatch, fake_app)

    with caplog.at_level(logging.ERROR, logger=firecrawl_client.__name__):
        result = firecrawl_client.firecrawl_search("Apple news")

    assert result is None
    assert "RuntimeError" in caplog.text
    assert exception_detail not in caplog.text


def test_content_search_type_error_does_not_retry_plain_search(monkeypatch, caplog):
    exception_detail = "private-test-detail"
    fake_app = FakeFirecrawlApp(search_error=TypeError(exception_detail))
    _install_fake_app(monkeypatch, fake_app)

    with caplog.at_level(logging.ERROR, logger=firecrawl_client.__name__):
        result = firecrawl_client.firecrawl_search("Apple news", with_content=True)

    assert result is None
    assert len(fake_app.search_calls) == 1
    assert "TypeError" in caplog.text
    assert exception_detail not in caplog.text


def test_scrape_error_returns_none(monkeypatch, caplog):
    exception_detail = "private-test-detail"
    fake_app = FakeFirecrawlApp(scrape_error=RuntimeError(exception_detail))
    _install_fake_app(monkeypatch, fake_app)

    with caplog.at_level(logging.ERROR, logger=firecrawl_client.__name__):
        result = firecrawl_client.firecrawl_scrape("https://example.com/article")

    assert result is None
    assert "RuntimeError" in caplog.text
    assert exception_detail not in caplog.text


def test_search_without_content_preserves_existing_call_shape(monkeypatch):
    fake_app = FakeFirecrawlApp()
    _install_fake_app(monkeypatch, fake_app)

    result = firecrawl_client.firecrawl_search("Apple news", limit=3)

    assert result is fake_app.search_result
    assert fake_app.search_calls == [("Apple news", {"limit": 3})]


def test_search_with_content_requests_markdown_main_content(monkeypatch):
    fake_app = FakeFirecrawlApp()
    _install_fake_app(monkeypatch, fake_app)

    result = firecrawl_client.firecrawl_search(
        "Apple news",
        limit=2,
        with_content=True,
    )

    assert result is fake_app.search_result
    assert len(fake_app.search_calls) == 1
    query, kwargs = fake_app.search_calls[0]
    assert query == "Apple news"
    assert set(kwargs) == {"limit", "scrape_options"}
    assert kwargs["limit"] == 2
    assert kwargs["scrape_options"].formats == ["markdown"]
    assert kwargs["scrape_options"].only_main_content is True
