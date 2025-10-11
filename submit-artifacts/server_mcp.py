#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agentic MCP Stock Analysis Server
=================================
Implements an Agentic AI workflow for financial research using:
  • LangGraph for multi-step orchestration
  • Hugging Face Transformers for generation & reasoning
  • YFinance + RSS feeds for retrieval
  • Sentiment analysis and visualization
  • FastMCP integration for server deployment

Modes:
  • `python3 server_mcp.py agentic`   → analyze multiple tickers
  • `python3 server_mcp.py stdio`     → run as MCP server

Workflow:
    fetch → sentiment → reasoning → draft → critique → final → END
"""

# ---------------------------------------------------------------------------
# Imports
# ---------------------------------------------------------------------------
from __future__ import annotations
import os, sys
from typing import Any, Dict
import feedparser, yfinance as yf
from transformers import pipeline
from langgraph.graph import StateGraph, END
from fastmcp import FastMCP
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.theme import Theme
from rich import box
from rich.traceback import install as rich_traceback
import matplotlib.pyplot as plt
import functools as _functools, builtins as _builtins

# ---------------------------------------------------------------------------
# Console & theme setup
# ---------------------------------------------------------------------------
rich_traceback(show_locals=False)
theme = Theme({
    "accent": "bold cyan",
    "ok": "bold green",
    "warn": "yellow",
    "err": "bold red",
    "muted": "grey66"
})
console = Console(theme=theme, stderr=True)
print = _functools.partial(_builtins.print, file=sys.stderr)

# ---------------------------------------------------------------------------
# Model setup
# ---------------------------------------------------------------------------
GEN_MODEL = os.getenv("GEN_MODEL", "google/flan-t5-base")
CRITIC_MODEL = os.getenv("CRITIC_MODEL", GEN_MODEL)
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", None)
DEVICE = os.getenv("DEVICE", "cpu").lower()

if DEVICE in ("cuda", "mps"):
    generator_pipeline = pipeline("text2text-generation", model=GEN_MODEL, device_map="auto")
    critic_pipeline = pipeline("text2text-generation", model=CRITIC_MODEL, device_map="auto")
    embed_pipeline = pipeline("feature-extraction", model=EMBED_MODEL, device_map="auto")
else:
    generator_pipeline = pipeline("text2text-generation", model=GEN_MODEL)
    critic_pipeline = pipeline("text2text-generation", model=CRITIC_MODEL)
    embed_pipeline = pipeline("feature-extraction", model=EMBED_MODEL)

sentiment_pipeline = (
    pipeline("sentiment-analysis", model=SENTIMENT_MODEL)
    if SENTIMENT_MODEL else pipeline("sentiment-analysis")
)

mcp = FastMCP("investment-analysis-langgraph")

# ---------------------------------------------------------------------------
# Visualization utilities
# ---------------------------------------------------------------------------
def ensure_dir(path="visuals"):
    """Ensure the visualization output directory exists."""
    if not os.path.exists(path):
        os.makedirs(path)
    return path


def plot_price_history(ticker: str, history: Dict[str, Any]):
    """Plot and save a 1-year price trend."""
    if not history or "Date" not in history:
        return
    path = ensure_dir()
    plt.figure(figsize=(6, 3))
    plt.plot(history["Date"], history["Close"], linewidth=2)
    plt.title(f"{ticker} 1-Year Price Trend")
    plt.xlabel("Date")
    plt.ylabel("Close ($)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    fname = os.path.join(path, f"price_history_{ticker}.png")
    plt.savefig(fname)
    plt.close()
    console.print(f"[ok] Saved price chart: {fname}")


def plot_sentiment_pie(ticker: str, sentiments: list):
    """Create a pie chart showing sentiment breakdown."""
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    for s in sentiments:
        counts[s["sentiment"]] = counts.get(s["sentiment"], 0) + 1
    path = ensure_dir()
    plt.figure(figsize=(4, 4))
    plt.pie(counts.values(), labels=counts.keys(), autopct="%1.0f%%",
            colors=["green", "red", "gray"])
    plt.title(f"{ticker} Sentiment Split")
    fname = os.path.join(path, f"sentiment_{ticker}.png")
    plt.savefig(fname)
    plt.close()
    console.print(f"[ok] Saved sentiment chart: {fname}")


def plot_portfolio_summary(results: Dict[str, Any]):
    """Plot overall sentiment distribution for all tickers."""
    path = ensure_dir()
    tickers = list(results.keys())
    pos = [v.get("sentiment_counts", {}).get("positive", 0) for v in results.values()]
    neg = [v.get("sentiment_counts", {}).get("negative", 0) for v in results.values()]
    neu = [v.get("sentiment_counts", {}).get("neutral", 0) for v in results.values()]

    plt.figure(figsize=(8, 4))
    plt.bar(tickers, pos, label="Positive", color="green")
    plt.bar(tickers, neg, bottom=pos, label="Negative", color="red")
    plt.bar(tickers, neu, bottom=[p+n for p, n in zip(pos, neg)], label="Neutral", color="gray")
    plt.title("Portfolio Sentiment Overview")
    plt.legend()
    plt.tight_layout()
    fname = os.path.join(path, "portfolio_summary.png")
    plt.savefig(fname)
    plt.close()
    console.print(f"[ok] Saved portfolio chart: {fname}")

# ---------------------------------------------------------------------------
# Data retrieval and sentiment analysis
# ---------------------------------------------------------------------------
def fetch_price_and_history(ticker: str):
    """Retrieve stock price and history."""
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty:
            return None, None
        last, prev = hist["Close"].iloc[-1], hist["Close"].iloc[-2]
        change = ((last - prev) / prev) * 100 if prev else 0
        history = {"Date": hist.index.strftime("%Y-%m-%d").tolist(), "Close": hist["Close"].tolist()}
        return {"ticker": ticker, "last_close": float(last), "daily_change_pct": round(change, 2)}, history
    except Exception as e:
        console.print(f"[warn] Failed to fetch price: {e}")
        return None, None


def fetch_pe_ratio(ticker: str):
    """Return trailing P/E ratio."""
    try:
        return float(yf.Ticker(ticker).info.get("trailingPE", "N/A"))
    except Exception:
        return "N/A"


def fetch_news(ticker: str, max_headlines=5):
    """Fetch recent Google News RSS headlines."""
    try:
        feed = feedparser.parse(f"https://news.google.com/rss/search?q={ticker}+stock")
        return [{"title": e.title, "link": e.link} for e in feed.entries[:max_headlines]]
    except Exception:
        return [{"title": f"No news found for {ticker}", "link": None}]


def classify_sentiment(news: list, ticker: str = ""):
    """Run sentiment analysis for each headline."""
    out = []
    for n in news:
        try:
            r = sentiment_pipeline(n["title"])[0]
            out.append({"title": n["title"], "sentiment": r["label"].lower(), "score": float(r["score"])})
        except Exception as e:
            out.append({"title": n["title"], "sentiment": "unknown", "score": 0, "error": str(e)})
    return out

# ---------------------------------------------------------------------------
# LangGraph workflow
# ---------------------------------------------------------------------------
def build_graph(ticker: str, max_headlines=5):
    """Construct the LangGraph reasoning workflow."""
    g = StateGraph(dict)

    # --- Node 1: Fetch data ---
    def fetch_node(s):
        price, history = fetch_price_and_history(ticker)
        pe = fetch_pe_ratio(ticker)
        news = fetch_news(ticker, max_headlines)
        s.update({"price": price, "history": history, "pe_ratio": pe, "news": news})
        return s

    # --- Node 2: Sentiment ---
    def sentiment_node(s):
        s["sentiment"] = classify_sentiment(s.get("news", []), ticker)
        return s

    # --- Node 3: Reasoning gate ---
    def reasoning_node(s):
        draft_text = "\n".join([n["title"] for n in s.get("news", [])])
        reasoning_prompt = (
            f"Given this stock analysis for {ticker}, decide if further critique is needed.\n"
            f"Answer YES if more analysis (critique/final summary) should continue.\n"
            f"Answer NO if sentiment is clearly negative or conclusion is obvious.\n"
            f"DRAFT:\n{draft_text}"
        )
        decision = generator_pipeline(reasoning_prompt, max_new_tokens=30)[0]["generated_text"].strip()
        s["reasoning_decision"] = decision
        console.print(f"[accent]Reasoning decision for {ticker}: {decision}[/]")
        return s

    # --- Node 4: Draft ---
    def draft_node(s):
        text = "\n".join([n["title"] for n in s.get("news", [])])
        s["draft"] = generator_pipeline(f"Draft stock analysis for {ticker}:\n{text}", max_new_tokens=180)[0]["generated_text"]
        return s

    # --- Node 5: Critique ---
    def critique_node(s):
        s["critique"] = critic_pipeline("Critique:\n" + s.get("draft", ""))[0]["generated_text"]
        return s

    # --- Node 6: Final summary ---
    def final_node(s):
        s["final"] = generator_pipeline("Rewrite summary:\n" + s.get("draft", "") + "\n" + s.get("critique", ""), )[0]["generated_text"]
        sentiments = s.get("sentiment", [])
        pos = sum(1 for x in sentiments if x["sentiment"] == "positive")
        neg = sum(1 for x in sentiments if x["sentiment"] == "negative")
        neu = sum(1 for x in sentiments if x["sentiment"] == "neutral")
        s["sentiment_counts"] = {"positive": pos, "negative": neg, "neutral": neu}
        s["recommendation"] = "Buy" if pos > neg else "Hold" if pos == neg else "Sell"
        return s

    # Graph construction
    for n, f in [("fetch", fetch_node), ("sentiment", sentiment_node),
                 ("reasoning", reasoning_node), ("draft", draft_node),
                 ("critique", critique_node), ("final", final_node)]:
        g.add_node(n, f)

    g.set_entry_point("fetch")
    g.add_edge("fetch", "sentiment")
    g.add_edge("sentiment", "reasoning")
    g.add_conditional_edges(
        "reasoning",
        lambda s: "draft" if s.get("reasoning_decision", "").upper().startswith("Y") else "final",
        {"draft": "draft", "final": "final"},
    )
    g.add_edge("draft", "critique")
    g.add_edge("critique", "final")
    g.add_edge("final", END)
    return g.compile()

# ---------------------------------------------------------------------------
# Core stock analysis (single run)
# ---------------------------------------------------------------------------
def _analyze_stock_impl(ticker: str, max_headlines: int = 5):
    """Execute the graph for one ticker."""
    console.rule(f"[accent]Analysis • {ticker.upper()}[/]")
    wf = build_graph(ticker, max_headlines)
    state = wf.invoke({})

    # Visualization
    if state.get("history"): plot_price_history(ticker, state["history"])
    if state.get("sentiment"): plot_sentiment_pie(ticker, state["sentiment"])

    # Display summary
    console.print(f"[accent]Final Summary:[/] {state.get('final','—')}")
    console.rule("[muted]done[/]")
    return state

# ---------------------------------------------------------------------------
# Entrypoint (dual-mode)
# ---------------------------------------------------------------------------

@mcp.tool
def analyze_stock(ticker:str,max_headlines:int=5)->Dict[str,Any]:
    return _analyze_stock_impl(ticker,max_headlines)

# ---- Entrypoint ----
if __name__=="__main__":
    if len(sys.argv)>1 and sys.argv[1].lower()=="agentic":
        companies=["AAPL","TSLA","MSFT","GOOGL","AMZN"]
        results={}
        for t in companies:
            try:
                results[t]=_analyze_stock_impl(t)
            except Exception as e:
                console.print(f"[err]{t}: {e}")
                results[t]={"error":str(e)}
        plot_portfolio_summary(results)
        console.rule("[accent]Portfolio Summary[/]")
        for t,s in results.items():
            rec=s.get("recommendation","—")
            console.print(f"{t}: [ok]{rec}[/]")
    else:
        transport=(sys.argv[1] if len(sys.argv)>1 else "stdio").lower()
        mcp.run(transport=transport)
