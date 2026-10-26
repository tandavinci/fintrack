"""Advisor chat service (Step 5 / G5 — "advisor chat reads stored recs + holdings").

The advisor answers a user's free-text question about THEIR portfolio, grounded
in REAL stored data. Two STEERING rules shape every line here:

  * Rule 1 — every model call goes through the gateway. We call
    ``gateway.client.chat_completion`` (the in-process OpenAI-format pass-through
    to Ollama). We NEVER touch Ollama directly and NEVER a paid API. The chat
    path is the gateway's interactive /quick lane, so running it inside a web
    request is explicitly allowed (only the DEEP lane is forbidden in a request).
  * Rule 3 — "models explain, code computes". The ONLY portfolio facts the model
    sees are the ones this module assembles from the DB (holdings + the latest
    stored quant/deep recommendations) into a compact CONTEXT block. The system
    prompt forbids inventing any number not in that block and tells the model to
    answer "I don't have that information in your stored data" when asked outside
    it. We never let the model be the source of a price or figure.

The context assembly is factored into a PURE helper ``_format_context`` (plain
data in, string out — no DB, no model) so it is unit-testable offline. ``answer``
offloads the blocking ``chat_completion`` to a worker thread via
``anyio.to_thread.run_sync`` so the async route stays responsive, and maps a
gateway failure to :class:`AdvisorUnavailable` which the router turns into 503.
"""

from __future__ import annotations

from typing import Any

import anyio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.models.portfolio import Holding, Portfolio, WatchlistItem
from backend.app.models.stock import Stock
from backend.app.repositories.recommendation_repository import (
    RecommendationRepository,
)
from gateway.client import (
    GatewayHTTPError,
    GatewayUnavailable,
    chat_completion,
)
from gateway.settings import settings as gateway_settings

# Bounds so the assembled context fits the gateway's hard 8k num_ctx cap. A 3B
# model on 4 GB VRAM has little headroom — keep the grounding compact.
MAX_TICKERS = 25
MAX_RATIONALE_CHARS = 180
MAX_CONTEXT_CHARS = 6000
MAX_QUESTION_CHARS = 2000

# One prompt version string so the advisor's model calls are traceable in the
# gateway log alongside the deep lane's DEEP_PROMPT_VERSION.
ADVISOR_PROMPT_VERSION = "advisor-v1"

# The system prompt — the single place rule 3 is enforced in the model's own
# instructions. It must: define the advisor as a portfolio assistant for a
# PERSONAL research tool; forbid inventing prices/numbers not in the context;
# tell the model to say it lacks the data rather than guess; and remind the user
# this is decision support, not financial advice.
SYSTEM_PROMPT = (
    "You are FinTrack's portfolio advisor, a careful assistant inside a "
    "personal research tool. You answer the user's question about THEIR "
    "portfolio using ONLY the data in the CONTEXT block the user provides "
    "(their holdings and the latest stored quant/deep recommendations). "
    "Do NOT invent, estimate, or guess any price, quantity, return, "
    "confidence, or other number that is not present in that CONTEXT. If the "
    "question asks about something not in the CONTEXT, reply exactly: "
    '"I don\'t have that information in your stored data." '
    "Keep answers concise and reference the tickers and stored actions you "
    "were given. This is decision support only, not financial advice; never "
    "tell the user to place a trade."
)


class AdvisorUnavailable(Exception):
    """Raised when the gateway/Ollama cannot be reached for the chat call.

    The router maps this to HTTP 503 ("advisor_model_unavailable"). It wraps the
    gateway's own :class:`GatewayUnavailable` / :class:`GatewayHTTPError` so the
    service layer exposes one failure type.
    """


def _format_context(
    holdings_rows: list[dict[str, Any]],
    recs_by_ticker: dict[str, dict[str, dict[str, Any]]],
) -> str:
    """Pure helper: assemble the factual CONTEXT block the model is grounded on.

    ``holdings_rows`` is a list of plain dicts, each with at least ``ticker``,
    ``quantity`` and ``avg_price`` (watchlist-only tickers may carry
    ``quantity``/``avg_price`` = None). ``recs_by_ticker`` maps a ticker to a
    ``{source: {action, confidence, as_of, rationale}}`` dict (only the sources
    that have a stored row appear). Both are PLAIN data so this is unit-testable
    with no DB and no model.

    The output is a compact, bounded, human-readable block: a HOLDINGS section
    then a RECOMMENDATIONS section. It is capped at :data:`MAX_CONTEXT_CHARS` so
    it fits the 8k num_ctx budget; the cap trims from the end (recommendations
    are less load-bearing than the holdings the question is usually about).
    """
    lines: list[str] = []

    lines.append("CONTEXT (the only portfolio facts you may use):")
    lines.append("")
    lines.append("HOLDINGS:")
    held = [h for h in holdings_rows if h.get("quantity") is not None]
    if held:
        for h in held[:MAX_TICKERS]:
            qty = h.get("quantity")
            avg = h.get("avg_price")
            avg_str = "n/a" if avg is None else f"{avg}"
            lines.append(
                f"- {h['ticker']}: quantity {qty}, average buy price {avg_str}"
            )
    else:
        lines.append("- (no holdings on record)")

    # Watchlist-only tickers (no quantity) listed separately so the model knows
    # they are watched, not owned.
    watched = [
        h["ticker"] for h in holdings_rows if h.get("quantity") is None
    ]
    if watched:
        lines.append("")
        lines.append("WATCHLIST (watched, not owned): " + ", ".join(watched[:MAX_TICKERS]))

    lines.append("")
    lines.append("LATEST STORED RECOMMENDATIONS:")
    if recs_by_ticker:
        emitted = 0
        for ticker in sorted(recs_by_ticker):
            if emitted >= MAX_TICKERS:
                break
            by_source = recs_by_ticker[ticker]
            for source in ("quant", "deep"):
                rec = by_source.get(source)
                if rec is None:
                    continue
                rationale = (rec.get("rationale") or "").strip()
                if len(rationale) > MAX_RATIONALE_CHARS:
                    rationale = rationale[: MAX_RATIONALE_CHARS - 1].rstrip() + "…"
                conf = rec.get("confidence")
                conf_str = "n/a" if conf is None else f"{float(conf):.2f}"
                lines.append(
                    f"- {ticker} [{source}]: {rec.get('action')} "
                    f"(confidence {conf_str}, as of {rec.get('as_of')})"
                    + (f" — {rationale}" if rationale else "")
                )
            emitted += 1
    else:
        lines.append("- (no stored recommendations yet)")

    block = "\n".join(lines)
    if len(block) > MAX_CONTEXT_CHARS:
        block = block[:MAX_CONTEXT_CHARS].rstrip() + "\n… (context truncated)"
    return block


async def _load_holdings_rows(
    session: AsyncSession, user_id: int
) -> list[dict[str, Any]]:
    """Read the user's holdings (+ watchlist) as plain dicts keyed by ticker.

    Holdings join ``Holding.stock_id -> Stock.ticker_symbol``. Watchlist tickers
    the user does not hold are appended with ``quantity``/``avg_price`` = None so
    the model knows they are watched, not owned. Returns plain dicts (no ORM) so
    the downstream formatting stays DB-free and testable.
    """
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Holdings for the user's portfolio(s).
    holding_stmt = (
        select(Holding, Stock.ticker_symbol)
        .join(Portfolio, Holding.portfolio_id == Portfolio.id)
        .join(Stock, Holding.stock_id == Stock.id)
        .where(Portfolio.user_id == user_id)
    )
    result = await session.execute(holding_stmt)
    for holding, ticker in result.all():
        if ticker in seen:
            continue
        seen.add(ticker)
        rows.append(
            {
                "ticker": ticker,
                "quantity": holding.total_quantity,
                # Decimal -> str keeps the exact stored figure (no float drift).
                "avg_price": str(holding.average_price),
            }
        )

    # Watchlist tickers the user does not already hold.
    watch_stmt = (
        select(Stock.ticker_symbol)
        .join(WatchlistItem, WatchlistItem.stock_id == Stock.id)
        .where(WatchlistItem.user_id == user_id)
    )
    watch_result = await session.execute(watch_stmt)
    for (ticker,) in watch_result.all():
        if ticker in seen:
            continue
        seen.add(ticker)
        rows.append({"ticker": ticker, "quantity": None, "avg_price": None})

    return rows


async def _load_recs_by_ticker(
    session: AsyncSession, tickers: list[str]
) -> dict[str, dict[str, dict[str, Any]]]:
    """For each ticker, the latest stored quant + deep recommendation as dicts.

    Uses the recommendation repo's ``latest_by_source`` (the shared read path),
    projecting each ORM row to the compact ``{action, confidence, as_of,
    rationale}`` dict the pure formatter consumes. Only sources with a stored row
    appear; a ticker with nothing stored is omitted entirely.
    """
    repo = RecommendationRepository(session)
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for ticker in tickers[:MAX_TICKERS]:
        by_source = await repo.latest_by_source(ticker)
        if not by_source:
            continue
        projected: dict[str, dict[str, Any]] = {}
        for source, row in by_source.items():
            projected[source] = {
                "action": row.action,
                "confidence": row.confidence,
                "as_of": row.as_of.isoformat(),
                "rationale": row.rationale,
            }
        out[ticker] = projected
    return out


async def build_context(session: AsyncSession, user_id: int) -> str:
    """Assemble the compact CONTEXT block from the user's REAL stored data.

    This is the ONLY source of portfolio facts the model sees. It reads the
    user's holdings/watchlist and the latest stored quant/deep recommendation for
    each, then hands the plain data to the pure :func:`_format_context`.
    """
    holdings_rows = await _load_holdings_rows(session, user_id)
    tickers = [h["ticker"] for h in holdings_rows]
    recs_by_ticker = await _load_recs_by_ticker(session, tickers)
    return _format_context(holdings_rows, recs_by_ticker)


def _count_recs(recs_by_ticker: dict[str, dict[str, dict[str, Any]]]) -> int:
    """Total stored recommendations across all tickers/sources (for provenance)."""
    return sum(len(sources) for sources in recs_by_ticker.values())


def _build_messages(
    context_block: str, question: str, history: list[dict[str, str]] | None
) -> list[dict[str, str]]:
    """Build the OpenAI ``messages`` list: system + optional prior turns + user.

    The user turn combines the CONTEXT block and the question so the model's only
    factual grounding is the block we assembled. Prior turns (if any) are passed
    through verbatim for conversational continuity.
    """
    messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        for turn in history:
            role = turn.get("role")
            content = turn.get("content")
            if role in ("user", "assistant") and content:
                messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": f"{context_block}\n\nQUESTION: {question}",
        }
    )
    return messages


def _extract_content(response: dict[str, Any]) -> str:
    """Pull the assistant text out of an OpenAI chat-completions response."""
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    return (message.get("content") or "").strip()


async def answer(
    session: AsyncSession,
    user_id: int,
    question: str,
    history: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Answer ``question`` for ``user_id``, grounded in their stored data.

    Builds the context, constructs the messages list, calls the gateway's
    ``chat_completion`` (offloaded to a worker thread since it is blocking), and
    returns ``{answer, grounded_on: {holdings, recommendations}}``. Raises
    :class:`AdvisorUnavailable` if the gateway/Ollama is unreachable.
    """
    question = (question or "").strip()[:MAX_QUESTION_CHARS]

    holdings_rows = await _load_holdings_rows(session, user_id)
    tickers = [h["ticker"] for h in holdings_rows]
    recs_by_ticker = await _load_recs_by_ticker(session, tickers)
    context_block = _format_context(holdings_rows, recs_by_ticker)

    messages = _build_messages(context_block, question, history)
    payload = {
        "model": gateway_settings.GATEWAY_MODEL,
        "messages": messages,
        "temperature": 0.2,
    }

    try:
        response = await anyio.to_thread.run_sync(chat_completion, payload)
    except (GatewayUnavailable, GatewayHTTPError) as exc:
        raise AdvisorUnavailable(str(exc)) from exc

    return {
        "answer": _extract_content(response),
        "grounded_on": {
            "holdings": sum(1 for h in holdings_rows if h.get("quantity") is not None),
            "recommendations": _count_recs(recs_by_ticker),
        },
    }
