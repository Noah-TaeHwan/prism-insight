#!/usr/bin/env python3
"""
Firecrawl Client Module

Singleton FirecrawlApp instance with helper functions for search and agent calls.
API key is loaded from FIRECRAWL_API_KEY env var or mcp_agent.config.yaml fallback.
"""
import logging
import os
from typing import Literal, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

FIRECRAWL_HTTP_TIMEOUT_SECONDS = 30
FIRECRAWL_MAX_ATTEMPTS = 1

# Singleton instance
_firecrawl_app = None


def is_firecrawl_configured() -> bool:
    """Return whether a non-placeholder API key is set in the environment."""
    key = os.getenv("FIRECRAWL_API_KEY", "").strip()
    placeholders = {"example key", "your-api-key", "your_api_key"}
    return bool(key) and key.casefold() not in placeholders


def _get_api_key() -> str:
    """Resolve Firecrawl API key from environment or mcp_agent.config.yaml."""
    key = os.getenv("FIRECRAWL_API_KEY")
    if key:
        return key

    # Fallback: read from mcp_agent.config.yaml
    try:
        import yaml
        config_path = os.path.join(os.path.dirname(__file__), "mcp_agent.config.yaml")
        with open(config_path, 'r') as f:
            config = yaml.safe_load(f)
        key = config.get("mcp", {}).get("servers", {}).get("firecrawl", {}).get("env", {}).get("FIRECRAWL_API_KEY")
        if key:
            logger.info("FIRECRAWL_API_KEY loaded from mcp_agent.config.yaml")
            return key
    except Exception as e:
        logger.warning(f"Failed to read mcp_agent.config.yaml: {e}")

    raise ValueError("FIRECRAWL_API_KEY not found in environment or mcp_agent.config.yaml")


def get_firecrawl_app():
    """Return singleton FirecrawlApp instance."""
    global _firecrawl_app
    if _firecrawl_app is None:
        from firecrawl import FirecrawlApp
        _firecrawl_app = FirecrawlApp(
            api_key=_get_api_key(),
            timeout=FIRECRAWL_HTTP_TIMEOUT_SECONDS,
            max_retries=FIRECRAWL_MAX_ATTEMPTS,
        )
        logger.info("FirecrawlApp singleton initialized")
    return _firecrawl_app


def firecrawl_search(
    query: str,
    limit: int = 10,
    with_content: bool = False,
    sources: list[str] | None = None,
    tbs: str | None = None,
):
    """
    Search the web via Firecrawl.

    Args:
        query: Search query string
        limit: Maximum number of results (default 10, costs 2 credits per 10)
        with_content: When True, scrape full markdown content for each result.
                      Each result item will have a .markdown attribute with the
                      article body (more credits used, but much richer data).
        sources: Optional Firecrawl search source filters.
        tbs: Optional Google time-based search filter.

    Returns:
        SearchData object with .web list of results, or None on error
    """
    try:
        app = get_firecrawl_app()
        search_kwargs = {"limit": limit}
        if sources is not None:
            search_kwargs["sources"] = sources
        if tbs is not None:
            search_kwargs["tbs"] = tbs

        if with_content:
            try:
                from firecrawl.v2.types import ScrapeOptions
                scrape_opts = ScrapeOptions(
                    formats=["markdown"],
                    only_main_content=True,
                )
            except ImportError as exc:
                # Fallback: SDK version doesn't support ScrapeOptions — use plain search
                logger.warning(
                    "ScrapeOptions unavailable: error_type=%s; falling back to plain search",
                    type(exc).__name__,
                )
                result = app.search(query, **search_kwargs)
            else:
                result = app.search(
                    query,
                    **search_kwargs,
                    scrape_options=scrape_opts,
                )
        else:
            result = app.search(query, **search_kwargs)
        logger.info(
            "Firecrawl search completed: results=%s, with_content=%s",
            len(result.web) if result and result.web else 0,
            with_content,
        )
        return result
    except Exception as exc:
        logger.error(
            "Firecrawl search failed: error_type=%s, limit=%s, with_content=%s",
            type(exc).__name__,
            limit,
            with_content,
        )
        return None


def firecrawl_scrape(
    url: str,
    *,
    max_age: int = 7_200_000,
    timeout: int = 30_000,
):
    """Scrape a URL's main markdown content, returning None on error."""
    try:
        app = get_firecrawl_app()
        return app.scrape(
            url,
            formats=["markdown"],
            only_main_content=True,
            max_age=max_age,
            timeout=timeout,
        )
    except Exception as exc:
        logger.error(
            "Firecrawl scrape failed: error_type=%s, max_age=%s, timeout=%s",
            type(exc).__name__,
            max_age,
            timeout,
        )
        return None


def _extract_agent_text(result) -> Optional[str]:
    """Extract readable text from Firecrawl agent result, trying multiple formats."""
    if not result:
        return None

    # Try result.data (common in SDK v4)
    if hasattr(result, 'data') and result.data:
        data = result.data
        if isinstance(data, dict):
            # Try known keys in priority order
            for key in ['telegram_message', 'result', 'text', 'answer', 'report', 'report_content', 'content']:
                val = data.get(key)
                if val and isinstance(val, str) and len(val) > 50:
                    return val
            # Try nested dict — search all string values recursively
            for key, val in data.items():
                if isinstance(val, str) and len(val) > 100:
                    return val
                if isinstance(val, dict):
                    for k2, v2 in val.items():
                        if isinstance(v2, str) and len(v2) > 100:
                            return v2
            # Last resort: stringify the whole dict
            text = str(data)
            if len(text) > 50:
                return text
        elif isinstance(data, str) and len(data) > 50:
            return data
        else:
            return str(data)

    # Try result as dict directly
    if isinstance(result, dict):
        for key in ['data', 'result', 'telegram_message', 'text']:
            val = result.get(key)
            if val and isinstance(val, str) and len(val) > 50:
                return val
            if isinstance(val, dict):
                return _extract_agent_text(type('Obj', (), {'data': val})())

    # Try result as string
    if isinstance(result, str) and len(result) > 50:
        return result

    return None


def firecrawl_agent(prompt: str, max_credits: int = 200, model: Literal["spark-1-mini", "spark-1-pro"] = "spark-1-mini") -> Optional[str]:
    """
    Run Firecrawl agent (Spark) with a prompt.

    Args:
        prompt: Natural language prompt for the agent
        max_credits: Maximum credits to spend (default 200)
        model: Agent model to use (default "spark-1-mini")

    Returns:
        Agent response text, or None on error
    """
    try:
        app = get_firecrawl_app()
        result = app.agent(
            prompt=prompt,
            model=model,
            max_credits=max_credits,
        )
        # Debug: log raw result structure
        logger.info(f"Firecrawl agent raw result type: {type(result)}")
        if result:
            logger.info(f"Firecrawl agent result attrs: {[a for a in dir(result) if not a.startswith('_')]}")

        # Extract text from result — try multiple response formats
        text = _extract_agent_text(result)
        if text:
            logger.info(f"Firecrawl agent response: {len(text)} chars")
            return text

        logger.warning(f"Firecrawl agent returned empty result. Raw: {str(result)[:500]}")
        return None
    except Exception as e:
        logger.error(f"Firecrawl agent failed: {e}")
        return None
