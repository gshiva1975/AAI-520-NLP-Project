
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Stock Analysis Server + Visualization + Console Output
----------------------------------------------------------
✓ Full RAG + LangGraph + MCP + Rich UI
✓ Visual charts saved to ./visuals/
✓ Printed console tables for each ticker and portfolio summary
"""

from __future__ import annotations
import os, sys
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
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

# ---- Setup ----
rich_traceback(show_locals=False)
theme = Theme({"accent":"bold cyan","ok":"bold green","warn":"yellow","err":"bold red","muted":"grey66"})
console = Console(theme=theme, stderr=True)
import functools as _functools, builtins as _builtins
print = _functools.partial(_builtins.print, file=sys.stderr)

# ---- Models ----
GEN_MODEL = os.getenv("GEN_MODEL","google/flan-t5-base")
CRITIC_MODEL = os.getenv("CRITIC_MODEL",GEN_MODEL)
EMBED_MODEL = os.getenv("EMBED_MODEL","sentence-transformers/all-MiniLM-L6-v2")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL",None)
DEVICE = os.getenv("DEVICE","cpu").lower()

if DEVICE in ("cuda","mps"):
    generator_pipeline = pipeline("text2text-generation", model=GEN_MODEL, device_map="auto")
    critic_pipeline = pipeline("text2text-generation", model=CRITIC_MODEL, device_map="auto")
    embed_pipeline = pipeline("feature-extraction", model=EMBED_MODEL, device_map="auto")
else:
    generator_pipeline = pipeline("text2text-generation", model=GEN_MODEL)
    critic_pipeline = pipeline("text2text-generation", model=CRITIC_MODEL)
    embed_pipeline = pipeline("feature-extraction", model=EMBED_MODEL)

sentiment_pipeline = pipeline("sentiment-analysis", model=SENTIMENT_MODEL) if SENTIMENT_MODEL else pipeline("sentiment-analysis")
mcp = FastMCP("investment-analysis-langgraph")

# ---- Visualization helpers ----
def ensure_dir(path="visuals"):
    if not os.path.exists(path):
        os.makedirs(path)
    return path

def plot_price_history(ticker, history):
    if not history or "Date" not in history: return
    path = ensure_dir()
    plt.figure(figsize=(6,3))
    plt.plot(history["Date"], history["Close"], linewidth=2)
    plt.title(f"{ticker} 1Y Price Trend")
    plt.xlabel("Date"); plt.ylabel("Close ($)")
    plt.xticks(rotation=45); plt.tight_layout()
    fname = os.path.join(path, f"price_history_{ticker}.png")
    plt.savefig(fname); plt.close()
    console.print(f"[ok] Saved price chart: {fname}")

def plot_sentiment_pie(ticker, sentiments):
    counts = {"positive":0,"negative":0,"neutral":0}
    for s in sentiments:
        counts[s["sentiment"]] = counts.get(s["sentiment"],0)+1
    labels,vals = list(counts.keys()), list(counts.values())
    path = ensure_dir()
    plt.figure(figsize=(4,4))
    plt.pie(vals,labels=labels,autopct="%1.0f%%",colors=["green","red","gray"])
    plt.title(f"{ticker} Sentiment Split")
    fname = os.path.join(path, f"sentiment_{ticker}.png")
    plt.savefig(fname); plt.close()
    console.print(f"[ok] Saved sentiment chart: {fname}")

def plot_portfolio_summary(results):
    path = ensure_dir()
    tickers=list(results.keys())
    pos=[v.get("sentiment_counts",{}).get("positive",0) for v in results.values()]
    neg=[v.get("sentiment_counts",{}).get("negative",0) for v in results.values()]
    neu=[v.get("sentiment_counts",{}).get("neutral",0) for v in results.values()]
    plt.figure(figsize=(8,4))
    plt.bar(tickers,pos,label="Positive",color="green")
    plt.bar(tickers,neg,bottom=pos,label="Negative",color="red")
    plt.bar(tickers,neu,bottom=[p+n for p,n in zip(pos,neg)],label="Neutral",color="gray")
    plt.title("Portfolio Sentiment Overview"); plt.legend(); plt.tight_layout()
    fname = os.path.join(path,"portfolio_summary.png")
    plt.savefig(fname); plt.close()
    console.print(f"[ok] Saved portfolio chart: {fname}")

# ---- Data fetch ----
def fetch_price_and_history(ticker):
    try:
        stock=yf.Ticker(ticker)
        hist=stock.history(period="1y")
        if hist.empty: return None,None
        last,prev=hist["Close"].iloc[-1],hist["Close"].iloc[-2]
        change=((last-prev)/prev)*100 if prev else 0
        history={"Date":hist.index.strftime("%Y-%m-%d").tolist(),"Close":hist["Close"].tolist()}
        return {"ticker":ticker,"last_close":float(last),"daily_change_pct":round(change,2)},history
    except Exception as e:
        console.print(f"[warn] price fetch fail: {e}"); return None,None

def fetch_pe_ratio(ticker):
    try: return float(yf.Ticker(ticker).info.get("trailingPE","N/A"))
    except Exception: return "N/A"

def fetch_news(ticker,max_headlines=5):
    try:
        feed=feedparser.parse(f"https://news.google.com/rss/search?q={ticker}+stock")
        return [{"title":e.title,"link":e.link} for e in feed.entries[:max_headlines]]
    except Exception:
        return [{"title":f"No recent news for {ticker}","link":None}]

# ---- Sentiment ----
def classify_sentiment(news,ticker=""):
    out=[]
    for n in news:
        try:
            base=sentiment_pipeline(n["title"])[0]
            out.append({"title":n["title"],"sentiment":base["label"].lower(),"score":float(base["score"])})
        except Exception as e:
            out.append({"title":n["title"],"sentiment":"unknown","score":0,"error":str(e)})
    return out

# ---- Console Rich Renderers ----
def render_summary(price,pe,recommendation,ticker):
    panel = Panel(
        f"[accent]Ticker:[/] [bold]{ticker}[/]\n"
        f"Last Close: [bold]{price.get('last_close','—')}[/]\n"
        f"Daily Change: [bold]{price.get('daily_change_pct','—')}%[/]\n"
        f"P/E Ratio: [bold]{pe}[/]\n"
        f"Recommendation: [ok]{recommendation}[/]",
        title=f"[accent]{ticker} Summary[/]", border_style="accent"
    )
    console.print(panel)

def render_news_table(news,sentiments):
    t=Table(title="Recent News & Sentiment",box=box.SIMPLE)
    t.add_column("#",justify="right",style="muted")
    t.add_column("Headline")
    t.add_column("Sentiment",justify="center")
    for i,n in enumerate(news):
        s=sentiments[i] if i<len(sentiments) else {"sentiment":"?"}
        t.add_row(str(i+1),n["title"],s["sentiment"])
    console.print(t)

# ---- LangGraph ----
def build_graph(ticker,max_headlines=5):
    g=StateGraph(dict)
    def fetch_node(s):
        price,history=fetch_price_and_history(ticker)
        pe=fetch_pe_ratio(ticker); news=fetch_news(ticker,max_headlines)
        s.update({"price":price,"history":history,"pe_ratio":pe,"news":news}); return s
    def sentiment_node(s):
        s["sentiment"]=classify_sentiment(s.get("news",[]),ticker); return s
    def draft_node(s):
        t="\n".join([n["title"] for n in s.get("news",[])])
        s["draft"]=generator_pipeline(f"Draft stock analysis for {ticker}:\n{t}",max_new_tokens=180)[0]["generated_text"]
        return s
    def critique_node(s):
        s["critique"]=critic_pipeline("Critique:\n"+s.get("draft",""))[0]["generated_text"]; return s
    def final_node(s):
        s["final"]=generator_pipeline("Rewrite summary:\n"+s.get("draft","")+"\n"+s.get("critique",""))[0]["generated_text"]
        sentiments=s.get("sentiment",[])
        p=sum(1 for x in sentiments if x["sentiment"]=="positive")
        n=sum(1 for x in sentiments if x["sentiment"]=="negative")
        u=sum(1 for x in sentiments if x["sentiment"]=="neutral")
        s["sentiment_counts"]={"positive":p,"negative":n,"neutral":u}
        s["recommendation"]="Buy" if p>n else "Hold" if p==n else "Sell"
        return s
    for n,f in [("fetch",fetch_node),("sentiment",sentiment_node),("draft",draft_node),("critique",critique_node),("final",final_node)]:
        g.add_node(n,f)
    g.set_entry_point("fetch")
    g.add_edge("fetch","sentiment"); g.add_edge("sentiment","draft"); g.add_edge("draft","critique")
    g.add_edge("critique","final"); g.add_edge("final",END)
    return g.compile()

# ---- Core ----
def _analyze_stock_impl(ticker,max_headlines=5):
    console.rule(f"[accent]Analysis • {ticker.upper()}[/]")
    wf=build_graph(ticker,max_headlines)
    state=wf.invoke({})
    price,pe,news,sent=state.get("price",{}),state.get("pe_ratio","N/A"),state.get("news",[]),state.get("sentiment",[])
    rec=state.get("recommendation","—")
    render_summary(price,pe,rec,ticker)
    render_news_table(news,sent)
    if state.get("history"): plot_price_history(ticker,state["history"])
    if sent: plot_sentiment_pie(ticker,sent)
    console.print(f"[accent]Draft:[/] {state.get('draft','—')}")
    console.print(f"[accent]Critique:[/] {state.get('critique','—')}")
    console.print(f"[accent]Final Summary:[/] {state.get('final','—')}")
    console.rule("[muted]done[/]")
    return state

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

