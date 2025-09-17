import sys
import asyncio
import yfinance as yf
from transformers import pipeline
from langgraph.graph import StateGraph, MessagesState, END
from langchain.schema import HumanMessage, AIMessage

# ---------------------------
# Hugging Face pipelines
# ---------------------------
generator = pipeline("text2text-generation", model="google/flan-t5-base")
sentiment_analyzer = pipeline("sentiment-analysis", model="ProsusAI/finbert")

def hf_generate(prompt: str, max_new_tokens: int = 200) -> str:
    out = generator(prompt, max_new_tokens=max_new_tokens)
    return out[0]["generated_text"]

# ---------------------------
# Stock helpers
# ---------------------------
def get_stock_summary(ticker: str) -> str:
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="5d")
        latest_close = hist["Close"].iloc[-1]
        prev_close = hist["Close"].iloc[-2]
        change = ((latest_close - prev_close) / prev_close) * 100
        return f"{ticker.upper()} last close: ${latest_close:.2f}, daily change: {change:.2f}%"
    except Exception as e:
        return f"Could not fetch stock data for {ticker}: {e}"

def get_stock_news(ticker: str, limit: int = 3):
    try:
        stock = yf.Ticker(ticker)
        news = stock.news
        if not news:
            return []
        return news[:limit]
    except Exception as e:
        return [{"title": f"Could not fetch news: {e}", "link": ""}]

# ---------------------------
# LangGraph workflow
# ---------------------------
def build_graph():
    workflow = StateGraph(MessagesState)

    def investment_node(state: MessagesState):
        user_msg = state["messages"][-1].content.strip()
        ticker = None
        for w in user_msg.split():
            if w.isupper() and 1 <= len(w) <= 5:
                ticker = w
                break
        if not ticker:
            return {"messages": state["messages"] + [AIMessage(content="No ticker found.")]}

        # --- Stock info ---
        stock_info = get_stock_summary(ticker)

        # --- News ---
        headlines = []
        raw_news = get_stock_news(ticker)
        for item in raw_news:
            title = item.get("title", "")
            link = item.get("link", "")
            if title:
                headlines.append(f"- {title} ({link})")
        if not headlines:
            headlines = ["No recent news available."]

        # --- AI Summary ---
        prompt = (
            f"Write a concise, professional stock research summary for {ticker}. "
            f"Include performance, risks, and outlook. "
            f"Base it on: {stock_info}. "
            f"Maximum 2 sentences, neutral tone."
        )
        summary = hf_generate(prompt)

        # --- Sentiment (prefer news, fallback to summary) ---
        news_text = " ".join([item.get("title", "") for item in raw_news])
        target_text = news_text if news_text.strip() else summary
        sent = sentiment_analyzer(target_text)[0]
        sent_str = f"Sentiment: {sent['label']} (score: {sent['score']:.2f})"

        # --- Build payload ---
        payload = (
            f"[Stock] {stock_info}\n"
            f"[Summary] {summary}\n"
            f"[Sentiment] {sent_str}\n"
            "Recent News:\n" + "\n".join(headlines)
        )
        return {"messages": state["messages"] + [AIMessage(content=payload)]}

    workflow.add_node("investment", investment_node)
    workflow.set_entry_point("investment")
    workflow.add_edge("investment", END)
    return workflow.compile()

graph = build_graph()

# ---------------------------
# MCP Server Loop
# ---------------------------
async def run():
    print("Investment+Sentiment+News MCP Server. Type ticker (e.g. AAPL).", flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        result = graph.invoke({"messages": [HumanMessage(content=line)]})
        ai_msgs = [m for m in result["messages"] if isinstance(m, AIMessage)]
        reply = ai_msgs[-1].content if ai_msgs else "No response."
        for ln in reply.splitlines():
            print("AI:", ln, flush=True)
        print("<<<END>>>", flush=True)

if __name__ == "__main__":
    asyncio.run(run())

