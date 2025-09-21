
import os
import json
import urllib.parse
from datetime import datetime

import yfinance as yf
import feedparser
from transformers import pipeline
from fastmcp import FastMCP

# ----------------------------------------------------------------------------
# MCP Setup
# ----------------------------------------------------------------------------
mcp = FastMCP("investment-analysis-service")

try:
    sentiment_analyzer = pipeline("sentiment-analysis", model="ProsusAI/finbert")
except Exception as e:
    sentiment_analyzer = None
    print(f"[WARN] Sentiment model unavailable: {e}")

# ----------------------------------------------------------------------------
# Memory
# ----------------------------------------------------------------------------
MEMORY_FILE = "agent_memory.json"

def _load_memory():
    if os.path.exists(MEMORY_FILE):
        try:
            with open(MEMORY_FILE, "r") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def _save_memory(mem):
    with open(MEMORY_FILE, "w") as f:
        json.dump(mem, f, indent=2)

def remember(symbol: str, key: str, value):
    mem = _load_memory()
    mem.setdefault(symbol.upper(), {})[key] = value
    _save_memory(mem)

def recall(symbol: str):
    return _load_memory().get(symbol.upper(), {})

# ----------------------------------------------------------------------------
# Stock Data Helpers
# ----------------------------------------------------------------------------
def get_stock_price(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        last_close = float(hist["Close"].iloc[-1])
        prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else last_close
        change = ((last_close - prev_close) / prev_close) * 100 if prev_close else 0
        return {"ticker": ticker.upper(), "last_close": round(last_close, 2), "daily_change_pct": round(change, 2)}
    except Exception as e:
        print(f"[WARN] Failed to fetch price: {e}")
        return {"ticker": ticker.upper(), "last_close": "N/A", "daily_change_pct": "N/A"}

def get_stock_history(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty:
            return {}
        return {
            "1 Day": {"start": float(hist["Close"].iloc[-2]), "end": float(hist["Close"].iloc[-1])},
            "1 Week": {"start": float(hist["Close"].iloc[-5]), "end": float(hist["Close"].iloc[-1])},
            "1 Month": {"start": float(hist["Close"].iloc[-21]), "end": float(hist["Close"].iloc[-1])},
            "1 Year": {"start": float(hist["Close"].iloc[0]), "end": float(hist["Close"].iloc[-1])},
        }
    except Exception as e:
        print(f"[WARN] Failed to fetch history: {e}")
        return {}

def get_pe_ratio(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        pe = stock.info.get("trailingPE")
        return round(pe, 2) if pe else "N/A"
    except Exception as e:
        print(f"[WARN] Failed to fetch P/E ratio: {e}")
        return "N/A"

# ----------------------------------------------------------------------------
# News
# ----------------------------------------------------------------------------
def get_news(ticker: str, limit: int = 5):
    try:
        stock = yf.Ticker(ticker)
        news_items = getattr(stock, "news", None)
        if news_items:
            parsed = []
            for n in news_items[:limit]:
                title = n.get("title", "").strip()
                link = n.get("link")
                if title and title.lower() != "no title":
                    parsed.append({"title": title, "link": link})
            if parsed:
                return parsed
    except Exception as e:
        print(f"[WARN] yfinance news failed: {e}")

    try:
        query = urllib.parse.quote(f"{ticker} stock")
        rss_url = f"https://news.google.com/rss/search?q={query}"
        feed = feedparser.parse(rss_url)
        if feed.entries:
            return [{"title": entry.title, "link": entry.link} for entry in feed.entries[:limit]]
    except Exception as e:
        print(f"[WARN] Google RSS fetch failed: {e}")

    return [{"title": f"No recent news for {ticker}", "link": None}]

# ----------------------------------------------------------------------------
# Sentiment
# ----------------------------------------------------------------------------
def analyze_sentiment(news_items):
    if not sentiment_analyzer:
        return [{"title": it["title"], "sentiment": "neutral"} for it in news_items]
    out = []
    for it in news_items:
        try:
            res = sentiment_analyzer(it["title"])[0]
            out.append({"title": it["title"], "sentiment": res["label"], "score": res["score"]})
        except Exception:
            out.append({"title": it["title"], "sentiment": "neutral"})
    return out

def make_recommendation(price_info, sentiment_results):
    if not sentiment_results or price_info["daily_change_pct"] == "N/A":
        return "Hold - insufficient data"
    avg_score = sum(x.get("score", 0) for x in sentiment_results if isinstance(x.get("score", 0), (int, float))) / max(len(sentiment_results), 1)
    if avg_score > 0.6 and price_info["daily_change_pct"] > 0:
        return "Buy - positive sentiment and upward trend"
    elif avg_score < 0.4 and price_info["daily_change_pct"] < 0:
        return "Sell - negative sentiment and downward trend"
    else:
        return "Hold - mixed signals"

# ----------------------------------------------------------------------------
# Core Implementation
# ----------------------------------------------------------------------------
def _analyze_stock_impl(ticker: str, max_headlines: int = 5):
    print(f"[START] Running analysis for {ticker}")

    price = get_stock_price(ticker)
    history = get_stock_history(ticker)
    pe_ratio = get_pe_ratio(ticker)
    news = get_news(ticker, max_headlines)
    classified = analyze_sentiment(news)
    recommendation = make_recommendation(price, classified)

    remember(ticker, "last_run", datetime.utcnow().isoformat())

    result = {
        "symbol": ticker.upper(),
        "price": price,
        "history": history,
        "pe_ratio": pe_ratio,
        "news": news,
        "sentiment": classified,
        "recommendation": recommendation,
        "memory": recall(ticker)
    }

    print(f"[END] Finished analysis for {ticker}")
    return result

# ----------------------------------------------------------------------------
# MCP Tool Wrapper
# ----------------------------------------------------------------------------
@mcp.tool()
def analyze_stock(ticker: str, max_headlines: int = 5):
    """Run stock analysis with price, history, P/E, sentiment, and recommendations."""
    return _analyze_stock_impl(ticker, max_headlines)

# ----------------------------------------------------------------------------
# Entrypoint
# ----------------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print(json.dumps(_analyze_stock_impl("TSLA", max_headlines=3), indent=2))
    else:
        mcp.run(transport="stdio")

