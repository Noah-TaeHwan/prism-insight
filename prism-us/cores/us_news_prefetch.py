from dataclasses import dataclass
import importlib.util
import ipaddress
import json
from pathlib import Path
import re
import sys
import threading
import unicodedata
from collections.abc import Mapping
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


SEARCH_RESULT_LIMIT = 8
MAX_ARTICLES = 3
MAX_BODY_CHARS = 4_000
MAX_TITLE_CHARS = 300
MAX_SNIPPET_CHARS = 1_000
MAX_DATE_CHARS = 64
MAX_URL_CHARS = 2_048
MAX_CONTEXT_CHARS = 50_000

_CONTEXT_SCHEMA_VERSION = 1
_CONTEXT_TRUST_LABEL = "untrusted_external_news_data"

_LEADING_COMPANY_ARTICLES = {"the", "a", "an"}
_TRAILING_COMPANY_SUFFIXES = {
    "inc",
    "incorporated",
    "corp",
    "corporation",
    "company",
    "co",
    "ltd",
    "limited",
    "llc",
    "plc",
    "holdings",
    "holding",
    "group",
}
_TRAILING_COMPANY_SUFFIX_SEQUENCES = {
    ("s", "a"),
    ("n", "v"),
    ("l", "p"),
}


@dataclass(frozen=True)
class NewsCandidate:
    title: str
    url: str
    snippet: str
    published_date: str
    outlet: str


@dataclass(frozen=True)
class NewsPrefetchResult:
    context: str
    articles: list[NewsCandidate]
    search_calls: int
    scrape_calls: int
    fallback_used: bool


_FIRECRAWL_CLIENT_CANONICAL_NAME = "firecrawl_client"
_FIRECRAWL_CLIENT_ALIAS = "_prism_insight_root_firecrawl_client"
_ROOT_FIRECRAWL_CLIENT_PATH = (
    Path(__file__).resolve().parents[2] / "firecrawl_client.py"
).resolve()
_FIRECRAWL_CLIENT_MODULE = None
_FIRECRAWL_CLIENT_LOAD_LOCK = threading.Lock()


def _module_is_expected_root_client(module) -> bool:
    try:
        module_file = getattr(module, "__file__")
        return Path(module_file).resolve() == _ROOT_FIRECRAWL_CLIENT_PATH
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def _transport_import_error() -> ImportError:
    return ImportError("unable to resolve Firecrawl transport")


def _load_root_firecrawl_client():
    global _FIRECRAWL_CLIENT_MODULE

    with _FIRECRAWL_CLIENT_LOAD_LOCK:
        canonical = sys.modules.get(_FIRECRAWL_CLIENT_CANONICAL_NAME)
        alias = sys.modules.get(_FIRECRAWL_CLIENT_ALIAS)
        existing_modules = [
            module for module in (canonical, alias) if module is not None
        ]

        if any(
            not _module_is_expected_root_client(module)
            for module in existing_modules
        ):
            raise _transport_import_error()
        if (
            canonical is not None
            and alias is not None
            and canonical is not alias
        ):
            raise _transport_import_error()

        if _FIRECRAWL_CLIENT_MODULE is not None:
            if not _module_is_expected_root_client(_FIRECRAWL_CLIENT_MODULE):
                raise _transport_import_error()
            if existing_modules and existing_modules[0] is not _FIRECRAWL_CLIENT_MODULE:
                raise _transport_import_error()
            return _FIRECRAWL_CLIENT_MODULE

        if existing_modules:
            _FIRECRAWL_CLIENT_MODULE = existing_modules[0]
            return _FIRECRAWL_CLIENT_MODULE

        spec = importlib.util.spec_from_file_location(
            _FIRECRAWL_CLIENT_ALIAS,
            _ROOT_FIRECRAWL_CLIENT_PATH,
        )
        if spec is None or spec.loader is None:
            raise _transport_import_error()

        module = importlib.util.module_from_spec(spec)
        prior_alias = sys.modules.get(_FIRECRAWL_CLIENT_ALIAS)
        sys.modules[_FIRECRAWL_CLIENT_ALIAS] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            if prior_alias is None:
                if sys.modules.get(_FIRECRAWL_CLIENT_ALIAS) is module:
                    sys.modules.pop(_FIRECRAWL_CLIENT_ALIAS, None)
            else:
                sys.modules[_FIRECRAWL_CLIENT_ALIAS] = prior_alias
            raise
        if (
            sys.modules.get(_FIRECRAWL_CLIENT_ALIAS) is not module
            or not _module_is_expected_root_client(module)
        ):
            if prior_alias is None:
                if sys.modules.get(_FIRECRAWL_CLIENT_ALIAS) is module:
                    sys.modules.pop(_FIRECRAWL_CLIENT_ALIAS, None)
            else:
                sys.modules[_FIRECRAWL_CLIENT_ALIAS] = prior_alias
            raise _transport_import_error()
        _FIRECRAWL_CLIENT_MODULE = module

        return _FIRECRAWL_CLIENT_MODULE


def firecrawl_search(
    query: str,
    limit: int = 10,
    sources: list[str] | None = None,
    tbs: str | None = None,
):
    try:
        client = _load_root_firecrawl_client()
        return client.firecrawl_search(
            query,
            limit=limit,
            sources=sources,
            tbs=tbs,
        )
    except Exception:
        return None


def firecrawl_scrape(url: str):
    try:
        client = _load_root_firecrawl_client()
        return client.firecrawl_scrape(url)
    except Exception:
        return None


def _read_field(item, field: str):
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _normalize_hostname(hostname: str) -> tuple[str, bool]:
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        labels = hostname.split(".")
        if not labels:
            raise ValueError

        ascii_labels = []
        for label in labels:
            if not label:
                raise ValueError
            ascii_label = label.encode("idna").decode("ascii").lower()
            if (
                len(ascii_label.encode("ascii")) > 63
                or ascii_label.startswith("-")
                or ascii_label.endswith("-")
                or re.fullmatch(r"[a-z0-9-]+", ascii_label) is None
            ):
                raise ValueError
            ascii_labels.append(ascii_label)

        ascii_hostname = ".".join(ascii_labels)
        if len(ascii_hostname.encode("ascii")) > 253:
            raise ValueError
        return ascii_hostname, False

    return str(address).lower(), address.version == 6


def normalize_url(url: str) -> str:
    raw_url = str(url)
    if any(
        character == "\\"
        or character.isspace()
        or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in raw_url
    ) or re.search(r"%(?![0-9a-fA-F]{2})", raw_url):
        raise ValueError("invalid URL")

    decoded_url = unquote(raw_url)
    if any(
        unicodedata.category(character) in {"Cc", "Cf", "Cs"}
        for character in decoded_url
    ):
        raise ValueError("invalid URL")

    try:
        raw_url = raw_url.strip()
        parts = urlsplit(raw_url)
        scheme = parts.scheme.lower()
        hostname = parts.hostname
        username = parts.username
        password = parts.password
        port = parts.port

        if scheme not in {"http", "https"} or not hostname:
            raise ValueError
        if username is not None or password is not None or "@" in parts.netloc:
            raise ValueError
        hostname, is_ipv6 = _normalize_hostname(hostname)
    except (UnicodeError, ValueError):
        raise ValueError("invalid URL") from None

    if is_ipv6:
        hostname = f"[{hostname}]"
    netloc = hostname if port is None else f"{hostname}:{port}"

    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not (
            key.casefold().startswith("utm_")
            or key.casefold() in {"gclid", "fbclid"}
        )
    ]

    return urlunsplit(
        (
            scheme,
            netloc,
            parts.path.rstrip("/"),
            urlencode(query_items),
            "",
        )
    )


def _normalize_title(title: str) -> str:
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return " ".join(
        "".join(
            character
            if character.isalnum()
            or unicodedata.category(character).startswith("M")
            else " "
            for character in normalized
        )
        .split()
    )


def _has_bounded_match(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text) is not None


def _has_ticker_match(text: str, ticker: str) -> bool:
    if not ticker:
        return False

    escaped_ticker = re.escape(ticker)
    if len(ticker) == 1 and ticker.isalnum():
        decorated_patterns = (
            rf"(?<![a-z0-9])\${escaped_ticker}(?![a-z0-9])",
            rf"(?<![a-z0-9])\({escaped_ticker}\)(?![a-z0-9])",
            rf"(?<![a-z0-9])(?:nyse|nasdaq|amex):{escaped_ticker}(?![a-z0-9])",
        )
        return any(re.search(pattern, text) is not None for pattern in decorated_patterns)

    return (
        re.search(rf"(?<![a-z0-9]){escaped_ticker}(?![a-z0-9])", text)
        is not None
    )


def _company_phrase(company_name: str) -> str:
    normalized_name = unicodedata.normalize(
        "NFKC",
        _stripped_string(company_name),
    ).casefold()
    came_from_dot_com = re.search(r"\.com\b", normalized_name) is not None
    words = _normalize_title(normalized_name).split()

    while words and words[0] in _LEADING_COMPANY_ARTICLES:
        words.pop(0)
    while words:
        if (
            len(words) >= 2
            and tuple(words[-2:]) in _TRAILING_COMPANY_SUFFIX_SEQUENCES
        ):
            del words[-2:]
        elif words[-1] in _TRAILING_COMPANY_SUFFIXES:
            words.pop()
        else:
            break
    if came_from_dot_com and words and words[-1] == "com":
        words.pop()

    return " ".join(words)


def _outlet_from_url(url: str) -> str:
    hostname = urlsplit(normalize_url(url)).hostname or ""
    if hostname.startswith("www."):
        return hostname[4:]
    return hostname


def _stripped_string(value) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _sanitize_untrusted_text(value: str, limit: int) -> str:
    bounded_source = value[: limit * 2]
    sanitized = "".join(
        character
        for character in bounded_source
        if character in {"\n", "\t"}
        or not unicodedata.category(character).startswith("C")
    )
    return sanitized.strip()[:limit]


def select_news_candidates(
    items,
    company_name: str,
    ticker: str,
    limit: int = MAX_ARTICLES,
) -> list[NewsCandidate]:
    if items is None or limit <= 0:
        return []

    company_phrase = _company_phrase(company_name)
    ticker_token = unicodedata.normalize(
        "NFKC",
        _stripped_string(ticker),
    ).casefold()
    candidates = []
    seen_urls = set()
    seen_titles = set()

    for item in items:
        if len(candidates) >= limit:
            break

        try:
            title_value = _read_field(item, "title")
            raw_url_value = _read_field(item, "url")
            published_date_value = _read_field(item, "date")
            snippet_value = _read_field(item, "snippet")
            snippet = (
                _sanitize_untrusted_text(snippet_value, MAX_SNIPPET_CHARS)
                if isinstance(snippet_value, str)
                else ""
            )
            if not snippet:
                description_value = _read_field(item, "description")
                snippet = (
                    _sanitize_untrusted_text(
                        description_value,
                        MAX_SNIPPET_CHARS,
                    )
                    if isinstance(description_value, str)
                    else ""
                )

            if not (
                isinstance(title_value, str)
                and isinstance(raw_url_value, str)
                and isinstance(published_date_value, str)
            ):
                continue
            if len(raw_url_value) > MAX_URL_CHARS:
                continue

            title = _sanitize_untrusted_text(title_value, MAX_TITLE_CHARS)
            raw_url = raw_url_value.strip()
            published_date = _sanitize_untrusted_text(
                published_date_value,
                MAX_DATE_CHARS,
            )
            if not title or not raw_url or not published_date:
                continue

            searchable_text = unicodedata.normalize(
                "NFKC",
                f"{title} {snippet}",
            ).casefold()
            searchable_words = _normalize_title(searchable_text)
            if not (
                _has_ticker_match(searchable_text, ticker_token)
                or _has_bounded_match(searchable_words, company_phrase)
            ):
                continue

            normalized_url = normalize_url(raw_url)
            if len(normalized_url) > MAX_URL_CHARS:
                continue
            normalized_title = _normalize_title(title)
            if normalized_url in seen_urls or (
                normalized_title and normalized_title in seen_titles
            ):
                continue

            seen_urls.add(normalized_url)
            if normalized_title:
                seen_titles.add(normalized_title)
            candidates.append(
                NewsCandidate(
                    title=title,
                    url=normalized_url,
                    snippet=snippet,
                    published_date=published_date,
                    outlet=_outlet_from_url(normalized_url),
                )
            )
        except Exception:
            continue

    return candidates


def _extract_news_items(search_result) -> list:
    try:
        news_items = _read_field(search_result, "news")
        if news_items is None or isinstance(
            news_items,
            (str, bytes, bytearray, Mapping),
        ):
            return []
        iterator = iter(news_items)
    except Exception:
        return []

    extracted = []
    while len(extracted) < SEARCH_RESULT_LIMIT:
        try:
            extracted.append(next(iterator))
        except StopIteration:
            break
        except Exception:
            break
    return extracted


def _dump_context_records(records: list[dict[str, str]]) -> str:
    return json.dumps(
        {
            "schema_version": _CONTEXT_SCHEMA_VERSION,
            "trust": _CONTEXT_TRUST_LABEL,
            "articles": records,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _serialize_grounded_context(
    included_articles: list[tuple[NewsCandidate, str]],
) -> tuple[str, list[tuple[NewsCandidate, str]]]:
    if not included_articles:
        return _dump_context_records([]), []

    retained_articles = list(included_articles)
    records = [
        {
            "title": _sanitize_untrusted_text(article.title, MAX_TITLE_CHARS),
            "outlet": _sanitize_untrusted_text(article.outlet, 253),
            "published_date": _sanitize_untrusted_text(
                article.published_date,
                MAX_DATE_CHARS,
            ),
            "url": article.url[:MAX_URL_CHARS],
            "snippet": _sanitize_untrusted_text(
                article.snippet,
                MAX_SNIPPET_CHARS,
            ),
            "body": _sanitize_untrusted_text(body, MAX_BODY_CHARS),
        }
        for article, body in retained_articles
    ]
    context = _dump_context_records(records)

    while len(context) > MAX_CONTEXT_CHARS and records:
        changed = False
        excess = len(context) - MAX_CONTEXT_CHARS
        for record in reversed(records):
            if record["body"]:
                trim_count = min(len(record["body"]), excess)
                record["body"] = record["body"][:-trim_count]
                changed = True
                break
        if not changed:
            records.pop()
            retained_articles.pop()
        context = _dump_context_records(records)

    return context, retained_articles


def _build_grounded_context(
    included_articles: list[tuple[NewsCandidate, str]],
) -> str:
    context, _retained_articles = _serialize_grounded_context(included_articles)
    return context


def fetch_recent_news(company_name: str, ticker: str) -> NewsPrefetchResult:
    query = f'"{company_name}" OR {ticker} stock news'
    search_calls = 1
    fallback_used = False

    try:
        week_result = firecrawl_search(
            query,
            limit=SEARCH_RESULT_LIMIT,
            sources=["news"],
            tbs="qdr:w",
        )
    except Exception:
        week_result = None
    week_items = _extract_news_items(week_result)
    selected_candidates = select_news_candidates(
        week_items,
        company_name,
        ticker,
        limit=MAX_ARTICLES,
    )

    if len(selected_candidates) < MAX_ARTICLES:
        fallback_used = True
        search_calls += 1
        try:
            month_result = firecrawl_search(
                query,
                limit=SEARCH_RESULT_LIMIT,
                sources=["news"],
                tbs="qdr:m",
            )
        except Exception:
            month_result = None
        month_items = _extract_news_items(month_result)
        selected_candidates = select_news_candidates(
            week_items + month_items,
            company_name,
            ticker,
            limit=MAX_ARTICLES,
        )

    scrape_calls = 0
    included_articles = []
    for candidate in selected_candidates:
        scrape_calls += 1
        try:
            scrape_result = firecrawl_scrape(candidate.url)
            markdown = _read_field(scrape_result, "markdown")
        except Exception:
            continue
        if not isinstance(markdown, str):
            continue
        body = _sanitize_untrusted_text(markdown, MAX_BODY_CHARS)
        if not body:
            continue

        included_articles.append((candidate, body))

    context, retained_articles = _serialize_grounded_context(included_articles)
    articles = [article for article, _body in retained_articles]
    return NewsPrefetchResult(
        context=context,
        articles=articles,
        search_calls=search_calls,
        scrape_calls=scrape_calls,
        fallback_used=fallback_used,
    )
