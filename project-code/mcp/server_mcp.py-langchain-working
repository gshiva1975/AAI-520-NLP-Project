import os
import json
import yfinance as yf
import feedparser
from datetime import datetime, timedelta
from transformers import pipeline
from langchain_community.llms import HuggingFacePipeline
from langchain.prompts import PromptTemplate
from langchain.chains import LLMChain
from fastmcp import FastMCP

# -----------------------
# Setup HuggingFace LLM
# -----------------------
generator_pipeline = pipeline(
    "text-generation",
    model="google/flan-t5-base",
    device=0 if os.environ.get("DEVICE", "mps") == "mps" else -1,
    max_new_tokens=256
)
generator = HuggingFacePipeline(pipeline=generator_pipeline)

# Sentiment analysis
sentiment_pipeline = pipeline("sentiment-analysis")

mcp = FastMCP("investment-analysis-langchain")

# -----------------------
# Helper functions
# -----------------------
def fetch_price_and_history(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")

        if hist.empty:
            return None, None

        last_close = hist["Close"].iloc[-1]
        prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else last_close
        daily_change_pct = ((last_close - prev_close) / prev_close) * 100 if prev_close != 0 else 0

        history = {
            "1 Day": {"start": float(prev_close), "end": float(last_close)},
            "1 Week": {"start": float(hist["Close"].iloc[-5]), "end": float(last_close)} if len(hist) >= 5 else None,
            "1 Month": {"start": float(hist["Close"].iloc[-22]), "end": float(last_close)} if len(hist) >= 22 else None,
            "1 Year": {"start": float(hist["Close"].iloc[0]), "end": float(last_close)}
        }

        return {
            "ticker": ticker,
            "last_close": float(last_close),
            "daily_change_pct": round(daily_change_pct, 2)
        }, history
    except Exception as e:
        print(f"[WARN] Price fetch failed: {e}")
        return None, None

def fetch_pe_ratio(ticker: str):
    try:
        stock = yf.Ticker(ticker)
        return float(stock.info.get("trailingPE", "N/A"))
    except Exception:
        return "N/A"

def fetch_news(ticker: str, max_headlines=5):
    """Use Google News RSS for better headlines."""
    try:
        url = f"https://news.google.com/rss/search?q={ticker}+stock"
        feed = feedparser.parse(url)
        articles = []
        for entry in feed.entries[:max_headlines]:
            articles.append({"title": entry.title, "link": entry.link})
        if not articles:
            return [{"title": f"No recent news for {ticker}", "link": None}]
        return articles
    except Exception as e:
        print(f"[WARN] RSS fetch failed: {e}")
        return [{"title": f"No recent news for {ticker}", "link": None}]

def classify_sentiment(news_items):
    results = []
    for item in news_items:
        text = item["title"]
        sentiment = sentiment_pipeline(text)[0]
        results.append({
            "title": text,
            "sentiment": sentiment["label"].lower(),
            "score": float(sentiment["score"])
        })
    return results

# -----------------------
# LangChain Draft → Critique → Final
# -----------------------
draft_prompt = PromptTemplate(
    input_variables=["symbol", "headlines"],
    template="Draft a short stock analysis for {symbol} based on these headlines:\n{headlines}"
)
critique_prompt = PromptTemplate(
    input_variables=["symbol", "draft"],
    template="Critique the following stock analysis for {symbol}:\n{draft}"
)
final_prompt = PromptTemplate(
    input_variables=["symbol", "draft", "critique"],
    template="Refine the draft with this critique for {symbol}:\nDraft: {draft}\nCritique: {critique}"
)

draft_chain = LLMChain(llm=generator, prompt=draft_prompt, output_key="draft")
critique_chain = LLMChain(llm=generator, prompt=critique_prompt, output_key="critique")
final_chain = LLMChain(llm=generator, prompt=final_prompt, output_key="final")

# -----------------------
# Core Analysis
# -----------------------
def _analyze_stock_impl(ticker: str, max_headlines=5):
    print(f"[START] Running analysis for {ticker}")

    price, history = fetch_price_and_history(ticker)
    pe_ratio = fetch_pe_ratio(ticker)
    news = fetch_news(ticker, max_headlines=max_headlines)
    sentiment = classify_sentiment(news)

    headlines_text = "\n".join([n["title"] for n in news])

    draft = draft_chain.run({"symbol": ticker, "headlines": headlines_text})
    critique = critique_chain.run({"symbol": ticker, "draft": draft})
    final = final_chain.run({"symbol": ticker, "draft": draft, "critique": critique})

    print(f"[END] Finished analysis for {ticker}")

    return {
        "symbol": ticker,
        "price": price,
        "history": history,
        "pe_ratio": pe_ratio,
        "news": news,
        "sentiment": sentiment,
        "draft": draft,
        "critique": critique,
        "final": final,
        "recommendation": "Buy - positive sentiment and upward trend" if any(s["sentiment"] == "positive" for s in sentiment) else "Hold - mixed or neutral signals",
        "memory": {"last_run": datetime.utcnow().isoformat()}
    }

# -----------------------
# MCP Tool
# -----------------------
def analyze_stock(ticker: str, max_headlines: int = 5):
    """Run the full LangChain-based research workflow."""
    return _analyze_stock_impl(ticker, max_headlines=max_headlines)

# Register tool
mcp.tool(analyze_stock)

# -----------------------
# Entrypoint
# -----------------------
if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "test":
        print(json.dumps(_analyze_stock_impl("TSLA"), indent=2))
    else:
        mcp.run(transport="stdio")

