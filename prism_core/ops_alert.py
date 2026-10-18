"""Best-effort Telegram alerts to the private maintenance chat.

Never falls back to a public channel, never raises: an alert must not change
the outcome of the work that triggered it.
"""
import asyncio
import logging
import os
from prism_core.log_redaction import install as _install_log_redaction  # noqa: E402
_install_log_redaction()

logger = logging.getLogger(__name__)

_MAX_CHARS = 3500


def _target():
    chat_id = os.getenv('OPS_ALERT_CHAT_ID') or os.getenv('OAUTH_ALERT_CHAT_ID')
    token = os.getenv('OPS_ALERT_BOT_TOKEN') or os.getenv('OAUTH_ALERT_BOT_TOKEN')
    return chat_id, token


async def send_ops_alert(text: str) -> bool:
    chat_id, token = _target()
    if not chat_id or not token:
        logger.warning('Ops alert not sent (maintenance chat not configured): %s', text[:300])
        return False
    try:
        import httpx

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': chat_id, 'text': text[:_MAX_CHARS], 'disable_web_page_preview': True})
        if response.status_code != 200:
            logger.warning('Ops alert rejected: HTTP %s', response.status_code)
        return response.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.warning('Ops alert failed: %s', type(e).__name__)
        return False


def send_ops_alert_sync(text: str) -> bool:
    """send_ops_alert for synchronous cron runners (no running event loop); never raises."""
    try:
        return asyncio.run(send_ops_alert(text))
    except Exception as e:  # noqa: BLE001
        logger.warning('Ops alert failed: %s', type(e).__name__)
        return False


# Skips we chose (one share costs more than the budget, budget blocks): not broker refusals.
_INTENTIONAL_SKIPS = ('below one share', 'quantity is 0', 'budget unavailable', 'blocked_budget')


def buy_rejection(result):
    """Why a real buy did not go through, or None when it did or was an intentional skip."""
    if not isinstance(result, dict) or result.get('success'):
        return None
    reason = ' '.join(str(result.get(k) or '') for k in ('status', 'reason_code', 'message')).strip()
    if any(skip in reason.lower() for skip in _INTENTIONAL_SKIPS):
        return None
    return reason or 'unknown'


def _order_text(kind, market, failures, log_hint):
    from prism_core.log_redaction import redact
    lines = [f'[주문 오류] {market} {kind} {len(failures)}건 - 주문이 나가지 않았을 수 있습니다']
    lines += [f'- {ticker}: {redact(str(reason))[:200]}' for ticker, reason in failures]
    lines.append(f'로그: {log_hint}')
    return '\n'.join(lines)


def alert_order_errors(kind: str, market: str, failures, log_hint: str) -> bool:
    """One maintenance-chat line per real-money order that failed, so it is seen the same day."""
    failures = list(failures)
    return bool(failures) and send_ops_alert_sync(_order_text(kind, market, failures, log_hint))


async def alert_buy_rejected(market: str, ticker: str, result, log_hint: str) -> bool:
    """Same alert from the async entry pipelines (new entries and re-entries share it)."""
    reason = buy_rejection(result)
    return bool(reason) and await send_ops_alert(_order_text('매수(신규·재진입)', market, [(ticker, reason)], log_hint))
