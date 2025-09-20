import yfinance as yf
from transformers import pipeline
from fastmcp import FastMCP

mcp = FastMCP("investment-analysis")

# Hugging Face Pipelines
generator = pipeline("text2text-generation", model="google/flan-t5-base")
sentiment_analyzer = pipeline("sentiment-analysis", model="ProsusAI/finbert")

def hf_generate(prompt: str, max_new_tokens: int = 200) -> str:
    out = generator(prompt, max_new_tokens=max_new_tokens)
    return out[0]["generated_text"]

def get_stock_price(ticker: str):
    stock = yf.Ticker(ticker)
    hist = stock.history(period="5d")
    if hist.empty:
        return {"error": f"No data for {ticker}"}
    last_close = hist["Close"].iloc[-1]
    prev_close = hist["Close"].iloc[-2] if len(hist) > 1 else last_close
    change = ((last_close - prev_close) / prev_close) * 100 if prev_close else 0
    return {"ticker": ticker, "last_close": round(float(last_close), 2), "daily_change": round(change, 2)}

def get_sentiment(text: str):
    result = sentiment_analyzer(text)[0]
    return {"label": result["label"], "score": round(float(result["score"]), 3)}


import yfinance as yf
import feedparser
import urllib.parse

def get_news(ticker: str, limit: int = 5):
    results = []

    # Try Yahoo Finance first
    try:
        stock = yf.Ticker(ticker)
        news_items = getattr(stock, "news", None)

        if news_items:
            results = [
                {"title": n.get("title", "No title"), "link": n.get("link")}
                for n in news_items[:limit]
                if n.get("title") and n.get("link")
            ]
    except Exception as e:
        print(f"[WARN] Yahoo Finance news failed: {e}")

    # If no results, fallback to Google News RSS
    if not results:
        try:
            query = f"{ticker} stock"
            encoded_query = urllib.parse.quote(query)  # Encode spaces, etc.
            rss_url = f"https://news.google.com/rss/search?q={encoded_query}"
            feed = feedparser.parse(rss_url)

            results = [
                {"title": entry.title, "link": entry.link}
                for entry in feed.entries[:limit]
            ]

            if not results:
                results = [{"title": "No news available", "link": None}]
        except Exception as e:
            print(f"[ERROR] Google News fallback failed: {e}")
            results = [{"title": f"Error fetching news: {e}", "link": None}]

    return results

@mcp.tool()
def stock_summary(ticker: str):
    """Get stock summary (last close and daily % change)."""
    return get_stock_price(ticker)

@mcp.tool()
def sentiment(text: str):
    """Run financial sentiment analysis with FinBERT."""
    return get_sentiment(text)

@mcp.tool()
def stock_news(ticker: str):
    """Get latest news for a ticker."""
    return get_news(ticker)

@mcp.tool()
def generate(prompt: str, max_new_tokens: int = 200):
    """Generate text using Flan-T5."""
    return {"output": hf_generate(prompt, max_new_tokens)}

if __name__ == "__main__":
    mcp.run(transport="stdio")

