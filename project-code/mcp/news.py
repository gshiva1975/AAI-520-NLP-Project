import yfinance as yf
import feedparser


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

if __name__ == "__main__":
    news = get_news("AAPL")  # Apple news
    for n in news:
        print(f"- {n['title']} ({n['link']})")
