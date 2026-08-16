"""
US Stock Analysis Module

Generate comprehensive stock analysis reports for US stocks.
Uses yfinance MCP server for market data and US-specific agents.
"""
import os
import asyncio
import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

from mcp_agent.app import MCPApp

# Set up import paths
import sys
import importlib.util
_prism_us_dir = Path(__file__).parent.parent
_project_root = Path(__file__).parent.parent.parent

# Import from main project's cores using direct file import to avoid namespace collision
# This is necessary because prism-us/cores/ shadows the main project's cores/
def _import_from_project_root(module_name: str, file_path: Path):
    """Import a module directly from a specific file path."""
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

# Import report_generation from main project's cores
_report_gen_module = _import_from_project_root(
    "main_report_generation",
    _project_root / "cores" / "report_generation.py"
)
generate_report = _report_gen_module.generate_report
generate_summary = _report_gen_module.generate_summary
generate_investment_strategy = _report_gen_module.generate_investment_strategy
get_disclaimer = _report_gen_module.get_disclaimer
generate_market_report = _report_gen_module.generate_market_report

# Import utils from main project's cores
_utils_module = _import_from_project_root(
    "main_utils",
    _project_root / "cores" / "utils.py"
)
clean_markdown = _utils_module.clean_markdown

# Add prism-us directory for local imports
sys.path.insert(0, str(_prism_us_dir))

# Task 4 defers loading the root Firecrawl transport until fetch execution.
# Load this sibling by file path because the repository also has a root cores/
# package that may already be cached before demo.py loads this standalone module.
_news_prefetch_path = _prism_us_dir / "cores" / "us_news_prefetch.py"
_news_prefetch_spec = importlib.util.spec_from_file_location(
    "_prism_us_news_prefetch",
    _news_prefetch_path,
)
if _news_prefetch_spec is None or _news_prefetch_spec.loader is None:
    raise ImportError(f"Could not load local news prefetch from {_news_prefetch_path}")
_news_prefetch_module = importlib.util.module_from_spec(_news_prefetch_spec)
sys.modules[_news_prefetch_spec.name] = _news_prefetch_module
_news_prefetch_spec.loader.exec_module(_news_prefetch_module)
fetch_recent_news = _news_prefetch_module.fetch_recent_news

# Import from prism-us local cores.agents using relative import path
# We need to import the local agents module directly
_agents_path = _prism_us_dir / "cores" / "agents" / "__init__.py"
_spec = importlib.util.spec_from_file_location("us_agents", _agents_path)
_us_agents_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_us_agents_module)
get_us_agent_directory = _us_agents_module.get_us_agent_directory

# Market analysis cache storage (global variable)
_us_market_analysis_cache = {}

# Import chart functions from us_stock_chart module
_chart_module = _import_from_project_root(
    "us_stock_chart",
    _prism_us_dir / "cores" / "us_stock_chart.py"
)
get_us_price_chart_html = _chart_module.get_us_price_chart_html
get_us_institutional_chart_html = _chart_module.get_us_institutional_chart_html
get_us_technical_chart_html = _chart_module.get_us_technical_chart_html

_social_client_module = _import_from_project_root(
    "us_social_sentiment_client",
    _prism_us_dir / "cores" / "us_social_sentiment_client.py"
)
USSocialSentimentClient = _social_client_module.USSocialSentimentClient


MAX_SOCIAL_SENTIMENT_CHARS = 8_000
MAX_TASK4_CONTEXT_CHARS = 50_000
MAX_NEWS_ANALYSIS_CONTEXT_CHARS = 70_000
MAX_TARGET_COMPANY_NAME_CHARS = 200
MAX_TARGET_TICKER_CHARS = 32
MAX_TARGET_REFERENCE_DATE_CHARS = 16
INVALID_LOG_TICKER = "INVALID_TICKER"
INVALID_LOG_COMPANY = "INVALID_COMPANY"
INVALID_LOG_REFERENCE_DATE = "INVALID_REFERENCE_DATE"
_SAFE_EXCEPTION_TYPE_NAMES = (
    (RuntimeError, "RuntimeError"),
    (ValueError, "ValueError"),
    (TypeError, "TypeError"),
    (AttributeError, "AttributeError"),
    (TimeoutError, "TimeoutError"),
    (ConnectionError, "ConnectionError"),
    (OSError, "OSError"),
    (Exception, "Exception"),
)
_TASK4_CONTEXT_KEYS = {"schema_version", "trust", "articles"}
_TASK4_ARTICLE_FIELDS = {
    "title",
    "outlet",
    "published_date",
    "url",
    "snippet",
    "body",
}
_TASK4_CANDIDATE_FIELDS = (
    "title",
    "outlet",
    "published_date",
    "url",
    "snippet",
)
_NEWS_ANALYSIS_CONTEXT_KEYS = {
    "schema_version",
    "trust",
    "target",
    "firecrawl_news",
    "social_sentiment",
}
_NEWS_TARGET_KEYS = {"company_name", "ticker", "reference_date"}
_NEWS_UNAVAILABLE_PLACEHOLDERS = {
    "ko": "_최근 뉴스 출처를 확보하고 검증할 수 없어 뉴스 분석을 생략했습니다. 다른 보고서 섹션은 독립적으로 처리됩니다._",
    "en": "_Recent news sources could not be secured and validated, so news analysis was skipped. Other report sections are processed independently._",
    "ja": "_最近のニュース情報源を確保・検証できなかったため、ニュース分析を省略しました。その他のレポートセクションは独立して処理されます。_",
    "zh": "_由于无法获取并验证近期新闻来源，本次跳过了新闻分析。其他报告部分将独立处理。_",
    "es": "_No fue posible obtener y validar fuentes de noticias recientes, por lo que se omitió el análisis de noticias. Las demás secciones del informe se procesan de forma independiente._",
    "fr": "_Les sources d’actualités récentes n’ont pas pu être obtenues et validées ; l’analyse des actualités a donc été omise. Les autres sections du rapport sont traitées indépendamment._",
    "de": "_Aktuelle Nachrichtenquellen konnten nicht beschafft und validiert werden; daher wurde die Nachrichtenanalyse übersprungen. Die übrigen Berichtsabschnitte werden unabhängig verarbeitet._",
}
_NEWS_DISABLED_PLACEHOLDERS = {
    "ko": "_뉴스 분석이 비활성화되었습니다. 다른 보고서 섹션은 독립적으로 처리됩니다._",
    "en": "_News analysis is disabled. Other report sections are processed independently._",
    "ja": "_ニュース分析は無効になっています。その他のレポートセクションは独立して処理されます。_",
    "zh": "_新闻分析已禁用。其他报告部分将独立处理。_",
    "es": "_El análisis de noticias está desactivado. Las demás secciones del informe se procesan de forma independiente._",
    "fr": "_L’analyse des actualités est désactivée. Les autres sections du rapport sont traitées indépendamment._",
    "de": "_Die Nachrichtenanalyse ist deaktiviert. Die übrigen Berichtsabschnitte werden unabhängig verarbeitet._",
}


def _safe_log_ticker(value) -> str:
    """Return a bounded ASCII ticker for log records, or a fixed token."""
    if (
        type(value) is str
        and re.fullmatch(r"[A-Za-z0-9.\-^_]{1,32}", value, flags=re.ASCII)
    ):
        return value
    return INVALID_LOG_TICKER


def _safe_log_company(value) -> str:
    """Return a bounded company label without controls or line separators."""
    if (
        type(value) is str
        and 1 <= len(value) <= MAX_TARGET_COMPANY_NAME_CHARS
        and not any(
            unicodedata.category(character).startswith("C")
            or unicodedata.category(character) in {"Zl", "Zp"}
            for character in value
        )
    ):
        return value
    return INVALID_LOG_COMPANY


def _safe_log_reference_date(value) -> str:
    """Return a strict YYYYMMDD reference date for log records."""
    if type(value) is str and re.fullmatch(r"[0-9]{8}", value, flags=re.ASCII):
        return value
    return INVALID_LOG_REFERENCE_DATE


def _safe_exception_name(exc) -> str:
    """Return only fixed names for exact ordinary exception classes."""
    exc_type = type(exc)
    for allowed_type, fixed_name in _SAFE_EXCEPTION_TYPE_NAMES:
        if exc_type is allowed_type:
            return fixed_name
    return "Exception"


def _fixed_news_placeholder(placeholders: dict[str, str], language) -> str:
    safe_language = language if isinstance(language, str) and language in placeholders else "en"
    return placeholders[safe_language]


def _get_news_unavailable_placeholder(language) -> str:
    """Return fixed app-authored copy for an unavailable grounded-news source."""
    return _fixed_news_placeholder(_NEWS_UNAVAILABLE_PLACEHOLDERS, language)


def _get_news_disabled_placeholder(language) -> str:
    """Return fixed app-authored copy for explicitly disabled news analysis."""
    return _fixed_news_placeholder(_NEWS_DISABLED_PLACEHOLDERS, language)


def _contains_unsafe_unicode(value: str) -> bool:
    return any(
        character not in {"\n", "\t"}
        and unicodedata.category(character).startswith("C")
        for character in value
    )


def _sanitize_target_string(value, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    bounded = value[: limit * 2]
    sanitized = "".join(
        character
        for character in bounded
        if not unicodedata.category(character).startswith("C")
    )
    return sanitized.strip()[:limit]


def _sanitize_social_sentiment(value) -> str | None:
    if not isinstance(value, str):
        return None
    bounded = value[: MAX_SOCIAL_SENTIMENT_CHARS * 2]
    sanitized = "".join(
        character
        for character in bounded
        if character in {"\n", "\t"}
        or not unicodedata.category(character).startswith("C")
    )
    sanitized = sanitized.strip()[:MAX_SOCIAL_SENTIMENT_CHARS]
    return sanitized or None


def _record_matches_retained_article(record: dict, article) -> bool:
    try:
        return all(
            record[field] == getattr(article, field)
            for field in _TASK4_CANDIDATE_FIELDS
        )
    except Exception:
        return False


def _build_news_analysis_context(
    news_result,
    social_sentiment=None,
    *,
    company_name,
    ticker,
    reference_date,
) -> str | None:
    """Build bounded user-level JSON from aligned Task 4 Firecrawl records."""
    if news_result is None:
        return None

    try:
        retained_articles = news_result.articles
        raw_context = news_result.context
    except Exception:
        return None
    if (
        not isinstance(retained_articles, list)
        or not retained_articles
        or len(retained_articles) > 3
        or not isinstance(raw_context, str)
        or len(raw_context) > MAX_TASK4_CONTEXT_CHARS
    ):
        return None

    try:
        parsed = json.loads(raw_context)
    except (TypeError, ValueError, RecursionError):
        return None
    if (
        not isinstance(parsed, dict)
        or set(parsed) != _TASK4_CONTEXT_KEYS
        or type(parsed.get("schema_version")) is not int
        or parsed.get("schema_version") != 1
        or parsed.get("trust") != "untrusted_external_news_data"
        or not isinstance(parsed.get("articles"), list)
    ):
        return None

    context_records = parsed["articles"]
    for record in context_records:
        if (
            not isinstance(record, dict)
            or set(record) != _TASK4_ARTICLE_FIELDS
            or any(not isinstance(record[field], str) for field in _TASK4_ARTICLE_FIELDS)
            or any(_contains_unsafe_unicode(record[field]) for field in _TASK4_ARTICLE_FIELDS)
        ):
            return None

    aligned_records = []
    used_indices = set()
    for retained_article in retained_articles:
        matching_index = next(
            (
                index
                for index, record in enumerate(context_records)
                if index not in used_indices
                and _record_matches_retained_article(record, retained_article)
            ),
            None,
        )
        if matching_index is None:
            return None
        used_indices.add(matching_index)
        aligned_records.append(context_records[matching_index])

    wrapper = {
        "schema_version": 1,
        "trust": "untrusted_external_analysis_data",
        "target": {
            "company_name": _sanitize_target_string(
                company_name,
                MAX_TARGET_COMPANY_NAME_CHARS,
            ),
            "ticker": _sanitize_target_string(
                ticker,
                MAX_TARGET_TICKER_CHARS,
            ),
            "reference_date": _sanitize_target_string(
                reference_date,
                MAX_TARGET_REFERENCE_DATE_CHARS,
            ),
        },
        "firecrawl_news": {
            "schema_version": 1,
            "trust": "untrusted_external_news_data",
            "articles": aligned_records,
        },
        "social_sentiment": _sanitize_social_sentiment(social_sentiment),
    }
    try:
        serialized = json.dumps(
            wrapper,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, RecursionError):
        return None
    if len(serialized) > MAX_NEWS_ANALYSIS_CONTEXT_CHARS:
        return None
    return serialized


def _is_valid_news_analysis_context(value) -> bool:
    """Validate a bounded grounded-news wrapper before scheduling paid work."""
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_NEWS_ANALYSIS_CONTEXT_CHARS
    ):
        return False
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, RecursionError):
        return False
    if (
        not isinstance(parsed, dict)
        or set(parsed) != _NEWS_ANALYSIS_CONTEXT_KEYS
        or type(parsed.get("schema_version")) is not int
        or parsed.get("schema_version") != 1
        or parsed.get("trust") != "untrusted_external_analysis_data"
    ):
        return False

    target = parsed.get("target")
    if (
        not isinstance(target, dict)
        or set(target) != _NEWS_TARGET_KEYS
        or any(not isinstance(target[field], str) for field in _NEWS_TARGET_KEYS)
        or any(
            unicodedata.category(character).startswith("C")
            for field in _NEWS_TARGET_KEYS
            for character in target[field]
        )
        or len(target["company_name"]) > MAX_TARGET_COMPANY_NAME_CHARS
        or len(target["ticker"]) > MAX_TARGET_TICKER_CHARS
        or len(target["reference_date"]) > MAX_TARGET_REFERENCE_DATE_CHARS
    ):
        return False

    firecrawl_news = parsed.get("firecrawl_news")
    if (
        not isinstance(firecrawl_news, dict)
        or set(firecrawl_news) != _TASK4_CONTEXT_KEYS
        or type(firecrawl_news.get("schema_version")) is not int
        or firecrawl_news.get("schema_version") != 1
        or firecrawl_news.get("trust") != "untrusted_external_news_data"
        or not isinstance(firecrawl_news.get("articles"), list)
        or not firecrawl_news["articles"]
        or len(firecrawl_news["articles"]) > 3
    ):
        return False
    for record in firecrawl_news["articles"]:
        if (
            not isinstance(record, dict)
            or set(record) != _TASK4_ARTICLE_FIELDS
            or any(not isinstance(record[field], str) for field in _TASK4_ARTICLE_FIELDS)
            or any(_contains_unsafe_unicode(record[field]) for field in _TASK4_ARTICLE_FIELDS)
        ):
            return False

    social_sentiment = parsed.get("social_sentiment")
    if social_sentiment is not None and (
        not isinstance(social_sentiment, str)
        or len(social_sentiment) > MAX_SOCIAL_SENTIMENT_CHARS
        or _contains_unsafe_unicode(social_sentiment)
    ):
        return False
    return True


async def analyze_us_stock(
    ticker: str = "AAPL",
    company_name: str = "Apple Inc.",
    reference_date: str = None,
    language: str = "ko",
    include_news: bool = True,
    macro_context: dict = None
) -> str:
    """
    Generate comprehensive stock analysis report for US stock.

    Args:
        ticker: Stock ticker symbol (e.g., "AAPL", "MSFT")
        company_name: Company name (e.g., "Apple Inc.")
        reference_date: Analysis reference date (YYYYMMDD format)
        language: Language code (default: "ko")
        include_news: Whether to include grounded Firecrawl news analysis

    Returns:
        str: Generated final report markdown text
    """
    # 1. Initial setup and preprocessing
    app = MCPApp(name="us_stock_analysis")

    # Use today's date if reference_date is not provided
    if reference_date is None:
        reference_date = datetime.now().strftime("%Y%m%d")
    safe_log_ticker = _safe_log_ticker(ticker)
    safe_log_company = _safe_log_company(company_name)
    safe_log_reference_date = _safe_log_reference_date(reference_date)

    async with app.run() as parallel_app:
        logger = parallel_app.logger
        logger.info(
            f"Starting: {safe_log_company}({safe_log_ticker}) US analysis - "
            f"reference date: {safe_log_reference_date}"
        )

        # 2. Create dictionary to store data as shared resource
        section_reports = {}

        # 3. Define sections to analyze (US-specific)
        # yfinance sections: run sequentially to avoid rate limits
        yfinance_sections = [
            "price_volume_analysis",           # Technical analysis (yfinance OHLCV)
            "institutional_holdings_analysis",  # yfinance holders
            "company_status",                  # yfinance financials
            "company_overview",                # yfinance info
            "market_index_analysis"            # yfinance indices
        ]
        # Non-yfinance sections: schedule news only after grounded context validation
        parallel_sections = []
        if not include_news:
            section_reports["news_analysis"] = _get_news_disabled_placeholder(language)
            logger.info("Skipping news_analysis (disabled)")
        # Always include news_analysis in base_sections for report structure
        base_sections = yfinance_sections + ["news_analysis"]

        # 4. Prefetch data to reduce MCP tool call overhead
        try:
            _prefetch_path = Path(__file__).parent / "data_prefetch.py"
            _prefetch_spec = importlib.util.spec_from_file_location("us_data_prefetch", _prefetch_path)
            _prefetch_module = importlib.util.module_from_spec(_prefetch_spec)
            _prefetch_spec.loader.exec_module(_prefetch_module)
            prefetched = _prefetch_module.prefetch_us_analysis_data(ticker) or {}
            logger.info(
                f"Prefetched US data for {safe_log_ticker}: "
                f"{list(prefetched.keys()) if prefetched else 'none'}"
            )
        except Exception as e:
            logger.warning(
                "US data prefetch failed, falling back to MCP: "
                f"{_safe_exception_name(e)}"
            )
            prefetched = {}

        news_result = None
        news_analysis_context = None
        news_failure_status = None
        retained_article_count = 0
        if include_news:
            try:
                news_result = await asyncio.to_thread(
                    fetch_recent_news,
                    company_name,
                    ticker,
                )
            except Exception as exc:
                news_failure_status = f"fetch_error:{_safe_exception_name(exc)}"
            else:
                if news_result is None:
                    news_failure_status = "invalid_result"
                else:
                    try:
                        retained_articles = news_result.articles
                        raw_news_context = news_result.context
                    except Exception:
                        news_failure_status = "invalid_result"
                    else:
                        if not isinstance(retained_articles, list):
                            news_failure_status = "invalid_result"
                        elif not retained_articles:
                            news_failure_status = "no_usable_articles"
                        elif len(retained_articles) > 3 or not isinstance(raw_news_context, str):
                            news_failure_status = "invalid_result"
                        else:
                            retained_article_count = len(retained_articles)
                            try:
                                candidate_context = _build_news_analysis_context(
                                    news_result,
                                    company_name=company_name,
                                    ticker=ticker,
                                    reference_date=reference_date,
                                )
                            except Exception as exc:
                                news_failure_status = (
                                    f"invalid_context:{_safe_exception_name(exc)}"
                                )
                            else:
                                if _is_valid_news_analysis_context(candidate_context):
                                    news_analysis_context = candidate_context
                                else:
                                    news_failure_status = "invalid_context"

        if news_analysis_context is not None and os.getenv("ADANOS_API_KEY"):
            try:
                social_client = USSocialSentimentClient()
                social_snapshot = await asyncio.to_thread(
                    social_client.get_social_sentiment_markdown,
                    ticker,
                    7,
                )
                if social_snapshot:
                    try:
                        enriched_context = _build_news_analysis_context(
                            news_result,
                            social_sentiment=social_snapshot,
                            company_name=company_name,
                            ticker=ticker,
                            reference_date=reference_date,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Grounded social enrichment unavailable for "
                            f"{safe_log_ticker}: {_safe_exception_name(exc)}"
                        )
                    else:
                        if _is_valid_news_analysis_context(enriched_context):
                            news_analysis_context = enriched_context
                            logger.info(
                                f"Prefetched social sentiment for {safe_log_ticker}"
                            )
                        else:
                            logger.warning(
                                "Grounded social enrichment unavailable for "
                                f"{safe_log_ticker}: "
                                "invalid_context"
                            )
            except Exception as exc:
                logger.warning(
                    "US social sentiment prefetch unavailable for "
                    f"{safe_log_ticker}: {_safe_exception_name(exc)}"
                )

        if news_analysis_context is not None:
            prefetched["news_analysis_context"] = news_analysis_context
            parallel_sections.append("news_analysis")
            logger.info(
                f"Grounded news ready for {safe_log_ticker}: "
                f"{retained_article_count} retained article(s)"
            )
        elif include_news:
            section_reports["news_analysis"] = _get_news_unavailable_placeholder(
                language
            )
            logger.warning(
                f"Grounded news unavailable for {safe_log_ticker}: "
                f"{news_failure_status or 'invalid_context'}"
            )

        # 5. Get US-specific agents (with prefetched data)
        agents = get_us_agent_directory(company_name, ticker, reference_date, base_sections, language, prefetched_data=prefetched)

        # 6. Execute base analysis using HYBRID mode
        # - yfinance sections: sequential with 2 sec delay (rate limit friendly)
        # - news_analysis: parallel with yfinance sections (prefetched data only)
        logger.info(f"Running US analysis in HYBRID mode for {safe_log_company}...")
        logger.info(f"  - yfinance sections (sequential): {yfinance_sections}")
        logger.info(f"  - parallel sections: {parallel_sections}")

        async def process_yfinance_sections():
            """Process yfinance-dependent sections sequentially"""
            results = {}
            for section in yfinance_sections:
                if section in agents:
                    logger.info(f"Processing {section} for {safe_log_company}...")
                    try:
                        agent = agents[section]
                        if section == "market_index_analysis":
                            if "report" in _us_market_analysis_cache:
                                logger.info("Using cached US market analysis")
                                report = _us_market_analysis_cache["report"]
                            else:
                                logger.info("Generating new US market analysis")
                                report = await generate_market_report(
                                    agent, section, reference_date, logger, language
                                )
                                _us_market_analysis_cache["report"] = report
                        else:
                            report = await generate_report(
                                agent, section, company_name, ticker, reference_date, logger, language
                            )
                        results[section] = report
                        # Add delay between yfinance calls to avoid rate limits
                        # 3 seconds gives yfinance time to reset rate limits
                        await asyncio.sleep(3)
                    except Exception as e:
                        logger.error(
                            f"Error processing {section}: {_safe_exception_name(e)}"
                        )
                        results[section] = f"Analysis failed: {section}"
            return results

        async def process_parallel_section(section):
            """Process a non-yfinance section with its own MCPApp context"""
            if section == "news_analysis" and news_analysis_context is None:
                fallback = (
                    _get_news_unavailable_placeholder(language)
                    if include_news
                    else _get_news_disabled_placeholder(language)
                )
                return section, fallback
            if section not in agents:
                return section, None

            section_app = MCPApp(name=f"us_stock_analysis_{section}")
            async with section_app.run() as section_context:
                section_logger = section_context.logger
                section_logger.info(
                    f"Processing {section} for {safe_log_company}..."
                )
                try:
                    agent = agents[section]
                    if section == "news_analysis":
                        report = await generate_report(
                            agent,
                            section,
                            company_name,
                            ticker,
                            reference_date,
                            section_logger,
                            language,
                            grounded_context=news_analysis_context,
                        )
                    else:
                        report = await generate_report(
                            agent,
                            section,
                            company_name,
                            ticker,
                            reference_date,
                            section_logger,
                            language,
                        )
                    return section, report
                except Exception as e:
                    section_logger.error(
                        f"Error processing {section}: {_safe_exception_name(e)}"
                    )
                    return section, f"Analysis failed: {section}"

        # Execute hybrid: yfinance sequential + parallel sections concurrently
        parallel_tasks = [process_parallel_section(s) for s in parallel_sections]
        yfinance_task = process_yfinance_sections()

        # Run both concurrently
        all_results = await asyncio.gather(yfinance_task, *parallel_tasks)

        # Collect results
        # First result is yfinance sections dict
        yfinance_results = all_results[0]
        section_reports.update(yfinance_results)

        # Remaining results are (section, report) tuples from parallel sections
        for result in all_results[1:]:
            if result and result[1] is not None:
                section_reports[result[0]] = result[1]

        # 6. Integrate content from other reports
        combined_reports = ""
        for section in base_sections:
            if section in section_reports:
                combined_reports += f"\n\n--- {section.upper()} ---\n\n"
                combined_reports += section_reports[section]

        # 7. Generate investment strategy
        try:
            logger.info(
                f"Processing investment_strategy for {safe_log_company}..."
            )

            investment_strategy = await generate_investment_strategy(
                section_reports, combined_reports, company_name, ticker, reference_date, logger, language
            )
            section_reports["investment_strategy"] = investment_strategy.lstrip('\n')
            logger.info(f"Completed investment_strategy - {len(investment_strategy)} characters")
        except Exception as e:
            logger.error(
                "Error processing investment_strategy: "
                f"{_safe_exception_name(e)}"
            )
            section_reports["investment_strategy"] = "Investment strategy analysis failed"

        # 8. Generate executive summary
        try:
            logger.info(f"Processing summary for {safe_log_company}...")
            summary = await generate_summary(
                section_reports, company_name, ticker, reference_date, logger, language
            )
            # Remove duplicate title/date if the agent added them
            # Pattern: "# Company Name (TICKER) Analysis Report\n**Publication Date:** ..."
            import re
            summary = summary.lstrip('\n')
            # Remove any leading H1 title that matches the report title pattern
            summary = re.sub(
                r'^#\s*' + re.escape(company_name) + r'\s*\(' + re.escape(ticker) + r'\)[^\n]*\n+',
                '',
                summary,
                flags=re.IGNORECASE
            )
            # Remove any publication date line right after title removal
            summary = re.sub(
                r'^\*{0,2}(Publication Date|발행일)\*{0,2}\s*:\s*[^\n]+\n+',
                '',
                summary,
                flags=re.IGNORECASE
            )
            # Remove leading separators (---)
            summary = re.sub(r'^-{3,}\s*\n+', '', summary)
            section_reports["summary"] = summary.lstrip('\n')
            logger.info(f"Completed summary - {len(summary)} characters")
        except Exception as e:
            logger.error(f"Error processing summary: {_safe_exception_name(e)}")
            section_reports["summary"] = "Summary generation failed"

        # 9. Generate charts (optional - may fail if yfinance data unavailable)
        # Charts are inserted into specific sections:
        # - Price chart → 1-1. Price and Volume Analysis
        # - Institutional chart → 1-2. Institutional Ownership Analysis
        # - Technical indicators chart → 4. Market Analysis
        price_chart_html = ""
        institutional_chart_html = ""
        technical_chart_html = ""

        try:
            import yfinance as yf

            # Get stock data for charts
            stock = yf.Ticker(ticker)
            hist = stock.history(period="1y")

            if not hist.empty:
                # 1. Price Chart (Candlestick with MA and Volume)
                price_chart_html = get_us_price_chart_html(
                    ticker, company_name, hist, width=900, dpi=80
                )
                if price_chart_html:
                    logger.info(f"Generated price chart for {safe_log_ticker}")
                else:
                    logger.warning(
                        f"Failed to generate price chart for {safe_log_ticker}"
                    )

                # 2. Institutional Holdings Chart
                major_holders = stock.major_holders
                institutional_holders = stock.institutional_holders
                institutional_chart_html = get_us_institutional_chart_html(
                    ticker, company_name, major_holders, institutional_holders, width=900, dpi=80
                )
                if institutional_chart_html:
                    logger.info(
                        f"Generated institutional chart for {safe_log_ticker}"
                    )
                else:
                    logger.warning(
                        f"Failed to generate institutional chart for {safe_log_ticker}"
                    )

                # 3. Technical Indicators Chart (RSI + MACD)
                technical_chart_html = get_us_technical_chart_html(
                    ticker, company_name, hist, width=900, dpi=80
                )
                if technical_chart_html:
                    logger.info(
                        "Generated technical indicators chart for "
                        f"{safe_log_ticker}"
                    )
                else:
                    logger.warning(
                        "Failed to generate technical indicators chart for "
                        f"{safe_log_ticker}"
                    )

        except Exception as e:
            logger.warning(f"Chart generation skipped: {_safe_exception_name(e)}")

        # 10. Compile final report
        # Header
        formatted_date = f"{reference_date[:4]}.{reference_date[4:6]}.{reference_date[6:]}"

        # Build chart sections with fallback messages
        price_chart_section = ""
        if price_chart_html:
            price_chart_section = f"\n\n#### Price Chart\n\n{price_chart_html}\n"

        institutional_chart_section = ""
        if institutional_chart_html:
            institutional_chart_section = f"\n\n#### Institutional Holdings Chart\n\n{institutional_chart_html}\n"

        technical_chart_section = ""
        if technical_chart_html:
            technical_chart_section = f"\n\n#### Technical Indicators (RSI & MACD)\n\n{technical_chart_html}\n"

        # Build macro section before final report composition
        macro_section = ""
        if macro_context:
            report_prose = macro_context.get("report_prose", "")
            if report_prose:
                macro_header = "### 거시경제 환경\n\n" if language == "ko" else "### Macroeconomic Environment\n\n"
                macro_section = macro_header + report_prose + "\n\n"
            else:
                # Fallback: build from structured fields if report_prose is empty
                regime = macro_context.get("market_regime", "sideways")
                regime_rationale = macro_context.get("regime_rationale", "")
                leading = macro_context.get("leading_sectors", [])
                lagging = macro_context.get("lagging_sectors", [])
                risks = macro_context.get("risk_events", [])

                if language == "ko":
                    regime_labels = {
                        "parabolic": "폭주 강세장",
                        "strong_bull": "강한 강세장", "moderate_bull": "보통 강세장",
                        "sideways": "횡보장", "moderate_bear": "보통 약세장", "strong_bear": "강한 약세장"
                    }
                    macro_section += "### 거시경제 환경\n\n"
                    macro_section += f"**시장 체제**: {regime_labels.get(regime, regime)}\n\n"
                    if regime_rationale:
                        macro_section += f"**판단 근거**: {regime_rationale}\n\n"
                    if leading:
                        sectors_str = ", ".join([s.get("sector", "") for s in leading[:3]])
                        macro_section += f"**주도 섹터**: {sectors_str}\n\n"
                    if lagging:
                        sectors_str = ", ".join([s.get("sector", "") for s in lagging[:3]])
                        macro_section += f"**소외 섹터**: {sectors_str}\n\n"
                    if risks:
                        for r in risks[:3]:
                            macro_section += f"- ⚠️ {r.get('event', '')} (영향: {r.get('severity', 'medium')})\n"
                        macro_section += "\n"
                else:
                    macro_section += "### Macroeconomic Environment\n\n"
                    macro_section += f"**Market Regime**: {regime.replace('_', ' ').title()}\n\n"
                    if regime_rationale:
                        macro_section += f"**Rationale**: {regime_rationale}\n\n"
                    if leading:
                        sectors_str = ", ".join([s.get("sector", "") for s in leading[:3]])
                        macro_section += f"**Leading Sectors**: {sectors_str}\n\n"
                    if lagging:
                        sectors_str = ", ".join([s.get("sector", "") for s in lagging[:3]])
                        macro_section += f"**Lagging Sectors**: {sectors_str}\n\n"
                    if risks:
                        for r in risks[:3]:
                            macro_section += f"- ⚠️ {r.get('event', '')} (Severity: {r.get('severity', 'medium')})\n"
                        macro_section += "\n"

        # Language-specific headers
        if language == "ko":
            headers = {
                "title": f"# {company_name} ({ticker}) 분석 보고서",
                "pub_date": "발행일",
                "exec_summary": "## 핵심 요약",
                "tech_analysis": "## 1. 기술적 분석",
                "fundamental": "## 2. 펀더멘털 분석",
                "news": "## 3. 최근 주요 뉴스 요약",
                "market": "## 4. 시장 분석",
                "strategy": "## 5. 투자 전략 및 의견",
            }
        else:
            headers = {
                "title": f"# {company_name} ({ticker}) Analysis Report",
                "pub_date": "Publication Date",
                "exec_summary": "## Executive Summary",
                "tech_analysis": "## 1. Technical Analysis",
                "fundamental": "## 2. Fundamental Analysis",
                "news": "## 3. Recent Major News Summary",
                "market": "## 4. Market Analysis",
                "strategy": "## 5. Investment Strategy and Opinion",
            }

        final_report = f"""{headers["title"]}

**{headers["pub_date"]}:** {formatted_date}

---

{section_reports.get("summary", headers["exec_summary"] + " - Summary not available")}

---

{headers["tech_analysis"]}

{section_reports.get("price_volume_analysis", "Analysis not available")}
{price_chart_section}
{section_reports.get("institutional_holdings_analysis", "Analysis not available")}
{institutional_chart_section}
---

{headers["fundamental"]}

{section_reports.get("company_status", "Analysis not available")}

{section_reports.get("company_overview", "Analysis not available")}

---

{headers["news"]}

{section_reports.get("news_analysis", "Analysis not available")}

---

{headers["market"]}

{section_reports.get("market_index_analysis", "Analysis not available")}
{technical_chart_section}
{macro_section}
---

{headers["strategy"]}

{section_reports.get("investment_strategy", "Strategy not available")}

---

{get_disclaimer(language)}
"""

        # 11. Clean up markdown formatting
        final_report = clean_markdown(final_report)

        logger.info(
            f"Final report generated: {safe_log_company}({safe_log_ticker}) - "
            f"{len(final_report)} characters"
        )

        return final_report


def clear_us_market_cache():
    """Clear the US market analysis cache"""
    global _us_market_analysis_cache
    _us_market_analysis_cache = {}


if __name__ == "__main__":
    import time
    import threading
    import signal

    # Timeout after 60 minutes
    def exit_after_timeout():
        time.sleep(3600)
        print("60-minute timeout reached: forcefully terminating process")
        os.kill(os.getpid(), signal.SIGTERM)

    # Start timer as background thread
    timer_thread = threading.Thread(target=exit_after_timeout, daemon=True)
    timer_thread.start()

    start = time.time()

    # Run analysis for Apple Inc.
    result = asyncio.run(analyze_us_stock(
        ticker="AAPL",
        company_name="Apple Inc.",
        reference_date=datetime.now().strftime("%Y%m%d"),
        language="en"
    ))

    # Save result
    output_path = f"AAPL_Apple Inc_{datetime.now().strftime('%Y%m%d')}_gpt5.4-mini.md"
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(result)

    end = time.time()
    print(f"Total execution time: {end - start:.2f} seconds")
    print(f"Final report length: {len(result):,} characters")
    print(f"Report saved to: {output_path}")
