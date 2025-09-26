#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Stock Analysis Server & CLI (with Rich TTY output routed to STDERR)

This module implements a stock-analysis workflow using:
- yfinance for price/history/PE
- Google News RSS + transformers sentiment for headline sentiment
- a simple LangGraph pipeline to draft→critique→refine analysis
- FastMCP to expose the workflow as an MCP tool (`analyze_stock`)
- Rich for readable CLI output, **sent to STDERR** to keep MCP stdio JSON clean

Transports:
- `stdio` (default): suitable for local MCP clients (e.g., Claude Desktop)
- `http` / `sse`: optional network transports (health route provided if Starlette is available)

Example usage:
    python server.py agentic    # run pretty CLI for a small portfolio and show recommendations
    python server.py stdio      # run MCP server over stdio (no stdout logs!)
    python server.py http       # run MCP server over HTTP (host/port via env)
    python server.py sse        # run MCP server over SSE (host/port via env)

Environment:
    GEN_MODEL          (default: google/flan-t5-base; uses text2text-generation)
    SENTIMENT_MODEL    (optional; let transformers choose a default if unset)
    DEVICE             ("cpu" | "cuda" | "mps"; default "cpu")
    HOST, PORT         (for http/sse transports; default 0.0.0.0:8000)
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import feedparser
import yfinance as yf
from transformers import pipeline

from langgraph.graph import StateGraph, END
from fastmcp import FastMCP

# ---- Pretty CLI (Rich) ----
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.theme import Theme
from rich import box
from rich.traceback import install as rich_traceback

# For HTTP/SSE health route (only needed for http/sse transports)
try:
    from starlette.responses import PlainTextResponse
except Exception:  # pragma: no cover - optional dependency
    PlainTextResponse = None  # type: ignore


# ===========================
# Rich setup (ALL logs -> STDERR)
# ===========================
rich_traceback(show_locals=False)

theme = Theme(
    {
        "accent": "bold cyan",
        "ok": "bold green",
        "warn": "yellow",
        "err": "bold red",
        "muted": "grey66",
    }
)

# IMPORTANT: send human-readable logs to STDERR so MCP JSON on STDOUT stays clean.
console = Console(theme=theme, stderr=True)

# Route bare prints to STDERR as well (safety net)
import functools as _functools, builtins as _builtins  # noqa: E402

print = _functools.partial(_builtins.print, file=sys.stderr)  # type: ignore


# ===========================
# Models (Hugging Face)
# ===========================
GEN_MODEL = os.getenv("GEN_MODEL", "google/flan-t5-base")  # T5 uses text2text-generation
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", None)
DEVICE = os.getenv("DEVICE", "cpu").lower()

task = "text2text-generation"

if DEVICE in ("cuda", "mps"):
    generator_pipeline = pipeline(task, model=GEN_MODEL, device_map="auto")
else:
    generator_pipeline = pipeline(task, model=GEN_MODEL)

sentiment_pipeline = (
    pipeline("sentiment-analysis", model=SENTIMENT_MODEL)
    if SENTIMENT_MODEL
    else pipeline("sentiment-analysis")
)


# ===========================
# MCP Server
# ===========================
mcp = FastMCP("investment-analysis-langgraph")


# ===========================
# Data & generation helpers
# ===========================
def hf_generate(prompt: str, max_new_tokens: int = 256) -> str:
    """
    Generate text from a prompt using the configured transformers pipeline.

    Args:
        prompt: Input prompt text.
        max_new_tokens: Upper bound on generated tokens.

    Returns:
        The generated text (best effort). On failure, a diagnostic string.
    """
    try:
        outputs = generator_pipeline(
            prompt, max_new_tokens=max_new_tokens, do_sample=False
        )
        if isinstance(outputs, list) and outputs:
            text = outputs[0].get("generated_text") or outputs[0].get("summary_text")
            return (text or str(outputs[0])).strip()
        return str(outputs)
    except Exception as e:  # pragma: no cover
        console.print(f"[warn] Generation failed: {e}")
        return f"[GENERATION FAILED] {prompt[:200]}"


def fetch_price_and_history(ticker: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Fetch latest close, daily change %, and a 1y history snapshot from yfinance.

    Args:
        ticker: Stock ticker symbol, e.g. "AAPL".

    Returns:
        (price_info, history_dict)
            price_info: dict with keys {ticker, last_close, daily_change_pct} or None
            history_dict: dict of period→{start,end} for "1 Day" | "1 Week" | "1 Month" | "1 Year" or None
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty:
            return None, None

        last_close = hist["Close"].iloc[-1]
        prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else last_close
        daily_change_pct = ((last_close - prev_close) / prev_close) * 100 if prev_close else 0

        history = {
            "1 Day": {"start": float(prev_close), "end": float(last_close)},
            "1 Week": {"start": float(hist["Close"].iloc[-5]), "end": float(last_close)} if len(hist) >= 5 else None,
            "1 Month": {"start": float(hist["Close"].iloc[-22]), "end": float(last_close)} if len(hist) >= 22 else None,
            "1 Year": {"start": float(hist["Close"].iloc[0]), "end": float(last_close)},
        }

        return (
            {
                "ticker": ticker.upper(),
                "last_close": float(last_close),
                "daily_change_pct": round(float(daily_change_pct), 2),
            },
            history,
        )
    except Exception as e:  # pragma: no cover
        console.print(f"[warn] Price fetch failed for {ticker}: {e}")
        return None, None


def fetch_pe_ratio(ticker: str) -> float | str:
    """
    Fetch trailing P/E ratio via yfinance .info (best effort).

    Args:
        ticker: Stock ticker symbol.

    Returns:
        P/E as float if available, else "N/A".
    """
    try:
        stock = yf.Ticker(ticker)
        pe = stock.info.get("trailingPE", "N/A")
        return float(pe) if pe and pe != "N/A" else "N/A"
    except Exception:  # pragma: no cover
        return "N/A"


def fetch_news(ticker: str, max_headlines: int = 5) -> List[Dict[str, Optional[str]]]:
    """
    Fetch recent news headlines from Google News RSS for the given ticker.

    Args:
        ticker: Stock ticker symbol.
        max_headlines: Maximum number of headlines to return.

    Returns:
        A list of dicts with keys {"title", "link"}.
    """
    try:
        url = f"https://news.google.com/rss/search?q={ticker}+stock"
        feed = feedparser.parse(url)
        articles = [{"title": entry.title, "link": entry.link} for entry in feed.entries[:max_headlines]]
        return articles if articles else [{"title": f"No recent news for {ticker}", "link": None}]
    except Exception as e:  # pragma: no cover
        console.print(f"[warn] RSS fetch failed for {ticker}: {e}")
        return [{"title": f"No recent news for {ticker}", "link": None}]


def classify_sentiment(news_items: List[Dict[str, str]] | None) -> List[Dict[str, Any]]:
    """
    Apply a sentiment classifier to each news title.

    Args:
        news_items: List of {"title": str, "link": str|None}

    Returns:
        List of {"title": str, "sentiment": str, "score": float, ["error": str]}
    """
    results: List[Dict[str, Any]] = []
    for item in news_items or []:
        text = item.get("title", "")
        try:
            sentiment = sentiment_pipeline(text)[0]
            results.append(
                {
                    "title": text,
                    "sentiment": sentiment["label"].lower(),
                    "score": float(sentiment["score"]),
                }
            )
        except Exception as e:  # pragma: no cover
            results.append(
                {
                    "title": text,
                    "sentiment": "unknown",
                    "score": 0.0,
                    "error": str(e),
                }
            )
    return results


def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """
    Percent change helper.

    Args:
        a: Start value.
        b: End value.

    Returns:
        Percentage change (float) or None on invalid input.
    """
    try:
        return (b - a) / a * 100 if a else 0.0  # type: ignore[operator]
    except Exception:
        return None


# ===========================
# Rich renderers
# ===========================
def render_history(history: Optional[Dict[str, Optional[Dict[str, float]]]]) -> Table:
    """
    Build a Rich table for the condensed price history snapshot.

    Args:
        history: Dict of period→{"start": float, "end": float} (some entries may be None).

    Returns:
        Rich Table object (not printed).
    """
    table = Table(title="Price History", box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("Range", style="muted")
    table.add_column("Start", justify="right")
    table.add_column("End", justify="right")
    table.add_column("Δ%", justify="right")
    for rng, vals in (history or {}).items():
        if not vals:
            continue
        start, end = vals["start"], vals["end"]
        pct = _pct(start, end)
        s_pct = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else "—"
        color = "ok" if (pct or 0) >= 0 else "err"
        table.add_row(rng, f"{start:,.2f}", f"{end:,.2f}", f"[{color}]{s_pct}[/]")
    return table


def render_news(
    news: Optional[List[Dict[str, Optional[str]]]],
    sentiments: Optional[List[Dict[str, Any]]] = None,
) -> Table:
    """
    Build a Rich table for recent news and (optional) per-headline sentiment.

    Args:
        news: List of news items.
        sentiments: List of sentiment dicts aligned with `news`.

    Returns:
        Rich Table object (not printed).
    """
    table = Table(title="Recent News", box=box.ROUNDED)
    table.add_column("#", justify="right", style="muted", width=3)
    table.add_column("Headline")
    table.add_column("Sentiment", justify="center", style="muted", width=14)
    sent_map: Dict[int, str] = {}
    if sentiments:
        for i, s in enumerate(sentiments):
            label = s.get("sentiment", "?")
            score = s.get("score", 0)
            sent_map[i] = f"{label} ({score:.2f})"
    for i, n in enumerate(news or []):
        label = sent_map.get(i, "—")
        table.add_row(str(i + 1), n.get("title", "—"), label)
    return table


def render_summary(state: Dict[str, Any], ticker: str) -> Panel:
    """
    Build a Rich panel summarizing key scalar metrics and recommendation.

    Args:
        state: Final state dict from the workflow.
        ticker: Stock ticker symbol.

    Returns:
        Rich Panel object (not printed).
    """
    price = state.get("price") or {}
    pe = state.get("pe_ratio", "N/A")
    last = price.get("last_close")
    chg = price.get("daily_change_pct")
    chg_color = "ok" if isinstance(chg, (int, float)) and chg >= 0 else "err"

    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="right")

    # Row 1
    left1 = f"[accent]Ticker:[/] [bold]{ticker.upper()}[/]"
    right1 = f"PE: [bold]{pe}[/]" if isinstance(pe, (int, float)) else f"PE: [muted]{pe}[/]"
    header.add_row(left1, right1)

    # Row 2 (guard types before using :,.2f)
    if isinstance(last, (int, float)):
        left2 = f"Last close: [bold]{last:,.2f}[/]"
    else:
        left2 = "Last close: [muted]—[/]"

    chg_val = chg if isinstance(chg, (int, float)) else 0.0
    right2 = f"Day change: [{chg_color}]{chg_val:+.2f}%[/]"
    header.add_row(left2, right2)

    rec = state.get("recommendation", "—")
    return Panel(
        header,
        title="[accent]Analysis Summary[/]",
        subtitle=f"Recommendation: [bold]{rec}[/]",
        border_style="accent",
    )


def count_sentiments(items: Optional[List[Dict[str, Any]]]) -> Dict[str, int]:
    """
    Aggregate headline-level sentiments into counts.

    Args:
        items: List of sentiment dicts with `sentiment` keys.

    Returns:
        Dict with keys {"positive","negative","neutral","unknown"} -> int counts.
    """
    c = {"positive": 0, "negative": 0, "neutral": 0, "unknown": 0}
    for s in items or []:
        label = (s.get("sentiment") or "unknown").lower()
        if label not in c:
            label = "unknown"
        c[label] += 1
    return c


def render_portfolio_summary(results_dict: Dict[str, Dict[str, Any]]) -> Table:
    """
    Build a Rich table summarizing per-ticker metrics and recommendations.

    Args:
        results_dict: Mapping of ticker -> state dict or {"error": "..."}.

    Returns:
        Rich Table object (not printed).
    """
    table = Table(title="Portfolio Recommendations", box=box.SIMPLE_HEAVY, show_lines=False)
    table.add_column("Ticker", style="accent", justify="center")
    table.add_column("Last Close", justify="right")
    table.add_column("Day Δ%", justify="right")
    table.add_column("PE", justify="right")
    table.add_column("Sentiment P/N/U", justify="center")
    table.add_column("Recommendation", justify="center")

    for ticker, state in (results_dict or {}).items():
        if not isinstance(state, dict) or "error" in state:
            table.add_row(ticker, "—", "—", "—", "—", "[err]ERROR[/]")
            continue

        price = state.get("price") or {}
        last = price.get("last_close")
        chg = price.get("daily_change_pct")
        pe = state.get("pe_ratio", "N/A")
        rec = state.get("recommendation", "—")

        # Format numbers safely
        last_s = f"{last:,.2f}" if isinstance(last, (int, float)) else "—"
        if isinstance(chg, (int, float)):
            chg_color = "ok" if chg >= 0 else "err"
            chg_s = f"[{chg_color}]{chg:+.2f}%[/]"
        else:
            chg_s = "—"
        pe_s = f"{pe:.2f}" if isinstance(pe, (int, float)) else str(pe)

        sc = state.get("sentiment_counts") or count_sentiments(state.get("sentiment"))
        p, n, u = sc.get("positive", 0), sc.get("negative", 0), sc.get("neutral", 0)
        sent_s = f"{p}/{n}/{u}"

        rec_color = (
            "ok"
            if isinstance(rec, str) and rec.lower().startswith("buy")
            else ("warn" if isinstance(rec, str) and rec.lower().startswith("hold") else "err")
        )
        rec_s = f"[{rec_color}]{rec}[/]"
        table.add_row(ticker, last_s, chg_s, pe_s, sent_s, rec_s)

    return table


# ===========================
# LangGraph workflow
# ===========================
def build_graph(ticker: str, max_headlines: int = 5):
    """
    Assemble the stock-analysis workflow as a LangGraph StateGraph.

    Pipeline:
        fetch -> sentiment -> draft -> critique -> final -> END

    Args:
        ticker: Stock ticker symbol.
        max_headlines: Number of news headlines to analyze.

    Returns:
        A compiled LangGraph workflow callable via `invoke({})`.
    """
    graph = StateGraph(dict)

    def fetch_node(state: Dict[str, Any]) -> Dict[str, Any]:
        price, history = fetch_price_and_history(ticker)
        pe_ratio = fetch_pe_ratio(ticker)
        news = fetch_news(ticker, max_headlines=max_headlines)
        state.update({"price": price, "history": history, "pe_ratio": pe_ratio, "news": news})
        return state

    def sentiment_node(state: Dict[str, Any]) -> Dict[str, Any]:
        state["sentiment"] = classify_sentiment(state.get("news", []))
        return state

    def draft_node(state: Dict[str, Any]) -> Dict[str, Any]:
        headlines_text = "\n".join([n.get("title", "") for n in state.get("news", [])])
        prompt = f"Draft a short stock analysis for {ticker} based on these headlines:\n{headlines_text}"
        state["draft"] = hf_generate(prompt, max_new_tokens=200)
        return state

    def critique_node(state: Dict[str, Any]) -> Dict[str, Any]:
        draft = state.get("draft", "No draft")
        prompt = f"Critique the following stock analysis for {ticker}:\n{draft}"
        state["critique"] = hf_generate(prompt, max_new_tokens=150)
        return state

    def final_node(state: Dict[str, Any]) -> Dict[str, Any]:
        draft = state.get("draft", "No draft")
        critique = state.get("critique", "No critique")
        prompt = f"Refine the draft with this critique for {ticker}:\nDraft: {draft}\nCritique: {critique}"
        state["final"] = hf_generate(prompt, max_new_tokens=200)

        sentiments = state.get("sentiment", [])
        positives = sum(1 for s in sentiments if s.get("sentiment") == "positive")
        negatives = sum(1 for s in sentiments if s.get("sentiment") == "negative")
        neutrals = sum(1 for s in sentiments if s.get("sentiment") == "neutral")
        state["sentiment_counts"] = {"positive": positives, "negative": negatives, "neutral": neutrals}

        state["recommendation"] = (
            "Buy - positive sentiment and upward trend"
            if positives > negatives
            else "Hold - mixed or neutral signals"
            if positives == negatives
            else "Sell - negative sentiment dominates"
        )
        return state

    graph.add_node("fetch", fetch_node)
    graph.add_node("sentiment", sentiment_node)
    graph.add_node("draft", draft_node)
    graph.add_node("critique", critique_node)
    graph.add_node("final", final_node)

    graph.set_entry_point("fetch")
    graph.add_edge("fetch", "sentiment")
    graph.add_edge("sentiment", "draft")
    graph.add_edge("draft", "critique")
    graph.add_edge("critique", "final")
    graph.add_edge("final", END)

    return graph.compile()


# ===========================
# Core analysis (pretty CLI)
# ===========================
def _analyze_stock_impl(ticker: str, max_headlines: int = 5) -> Dict[str, Any]:
    """
    Execute the analysis workflow for a single ticker and render pretty sections.

    Args:
        ticker: Stock ticker symbol.
        max_headlines: Number of headlines to analyze.

    Returns:
        Final state dict produced by the workflow (includes 'recommendation').
    """
    console.rule(f"[accent]Analysis • {ticker.upper()}[/]")
    with console.status("Fetching data & running workflow…", spinner="dots"):
        workflow = build_graph(ticker, max_headlines)
        state: Dict[str, Any] = workflow.invoke({})

    state["memory"] = {"last_run": datetime.utcnow().isoformat()}

    # Pretty sections
    console.print(render_summary(state, ticker))
    if state.get("history"):
        console.print(render_history(state["history"]))
    if state.get("news"):
        console.print(render_news(state["news"], state.get("sentiment")))

    # Optional: show generations
    if state.get("draft"):
        console.rule("[accent]Draft")
        console.print(state["draft"])
    if state.get("critique"):
        console.rule("[accent]Critique")
        console.print(state["critique"])
    if state.get("final"):
        console.rule("[accent]Final")
        console.print(state["final"])

    console.rule("[muted]done[/]")
    return state


# ===========================
# MCP tool
# ===========================
@mcp.tool
def analyze_stock(ticker: str, max_headlines: int = 5) -> Dict[str, Any]:
    """
    MCP Tool: Run the full LangGraph-based research workflow for one ticker.

    Args:
        ticker: Stock ticker symbol.
        max_headlines: Number of headlines to analyze.

    Returns:
        The workflow's final state dict (safe to serialize to JSON).
    """
    return _analyze_stock_impl(ticker, max_headlines=max_headlines)


# Optional health route for HTTP/SSE runs
if PlainTextResponse is not None:  # pragma: no cover - only used for http/sse
    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request):
        """
        Lightweight liveness probe for HTTP/SSE transports.

        Returns:
            Plain "OK" text on success.
        """
        return PlainTextResponse("OK")


# ===========================
# Entrypoint
# ===========================
if __name__ == "__main__":
    """
    Script entrypoint. See module docstring for usage examples.

    In `agentic` mode, runs multiple tickers and prints:
      - Per-ticker sections (summary/history/news/drafts)
      - A consolidated "Portfolio Recommendations" table
      - A JSON snapshot of all results (to STDERR via Rich)
    """
    if len(sys.argv) > 1 and sys.argv[1].lower() == "agentic":
        companies = ["AAPL", "TSLA", "MSFT", "GOOGL", "AMZN"]
        results: Dict[str, Dict[str, Any]] = {}
        for ticker in companies:
            try:
                results[ticker] = _analyze_stock_impl(ticker, max_headlines=5)
            except Exception as e:  # pragma: no cover
                results[ticker] = {"error": str(e)}
                console.print(f"[err][ERROR][/err] {ticker} -> {e}")

        # Pretty portfolio recommendations table
        console.rule("[accent]RECOMMENDATIONS")
        console.print(render_portfolio_summary(results))

        # JSON snapshot (useful for logs/diffing; still goes to STDERR)
        console.rule("[accent]RAW RESULTS (JSON)")
        console.print_json(data=results, indent=2, sort_keys=True, ensure_ascii=False)

    else:
        transport = (sys.argv[1] if len(sys.argv) > 1 else "stdio").lower()
        if transport == "http":  # pragma: no cover
            host = os.getenv("HOST", "0.0.0.0")
            port = int(os.getenv("PORT", "8000"))
            mcp.run(transport="http", host=host, port=port)
        elif transport == "sse":  # pragma: no cover
            host = os.getenv("HOST", "0.0.0.0")
            port = int(os.getenv("PORT", "8000"))
            mcp.run(transport="sse", host=host, port=port)
        else:
            # Default: stdio (for local MCP clients). No TTY output on STDOUT.
            mcp.run(transport="stdio")

