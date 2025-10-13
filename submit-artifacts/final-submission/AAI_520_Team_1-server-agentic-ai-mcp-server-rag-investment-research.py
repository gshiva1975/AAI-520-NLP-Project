#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=====================================================================================
Title: Agentic MCP Server with Retrieval-Augmented Generation (RAG), FAISS, and Visualization
Author:AAI-520 TEAM 1 GROUP 
Course: AAI-520 – Applied AI Systems
Institution: University of San Diego 
Submission Date: Oct,13,2025 
=====================================================================================

Description:
------------
This project implements an **Agentic AI Workflow** for stock market analysis
and financial reasoning using the **Model Context Protocol (MCP)**.

The system integrates:
     **Retrieval-Augmented Generation (RAG)** using FAISS for semantic search  
     **LangGraph** for agentic workflow orchestration  
     **LLMs** (Flan-T5) for text generation, reasoning, and critique  
     **Sentiment Analysis** for real-time news evaluation  
     **Matplotlib** for visualizing stock trends and sentiment split  
     **Rich Console Output** for interactive CLI summaries

Core Objective:
---------------
To develop a self-reasoning, explainable AI agent that can:
    1. Fetch and interpret live stock market data.
    2. Evaluate recent news sentiment for financial instruments.
    3. Retrieve relevant knowledge from local corpora using FAISS.
    4. Generate human-like analysis, critique, and conclusions.
    5. Visualize both quantitative (price) and qualitative (sentiment) results.
    6. Operate both as a **standalone system** and an **MCP server**.

=====================================================================================
"""

# =============================================================================
#  Imports and Environment Setup
# =============================================================================
import os
import sys
import faiss
import glob
import feedparser
import yfinance as yf
import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from transformers import pipeline
from sentence_transformers import SentenceTransformer
from langgraph.graph import StateGraph, END
from fastmcp import FastMCP
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box
from rich.traceback import install as rich_traceback

# =============================================================================
#  Global Configuration and Initialization
# =============================================================================
rich_traceback(show_locals=False)
console = Console(stderr=True)

# ----- Model Configuration -----
GEN_MODEL = os.getenv("GEN_MODEL", "google/flan-t5-base")
CRITIC_MODEL = os.getenv("CRITIC_MODEL", GEN_MODEL)
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", None)
DEVICE = os.getenv("DEVICE", "cpu").lower()

# ----- Pipeline Setup -----
generator_pipeline = pipeline(
    "text2text-generation",
    model=GEN_MODEL,
    device_map="auto" if DEVICE in ("cuda", "mps") else None,
)
critic_pipeline = pipeline(
    "text2text-generation",
    model=CRITIC_MODEL,
    device_map="auto" if DEVICE in ("cuda", "mps") else None,
)
sentiment_pipeline = pipeline(
    "sentiment-analysis",
    model=SENTIMENT_MODEL or "distilbert/distilbert-base-uncased-finetuned-sst-2-english",
)
embed_model = SentenceTransformer(EMBED_MODEL)

# MCP server initialization
mcp = FastMCP("investment-agentic-rag-visual")

# =============================================================================
#  RAG and FAISS Configuration
# =============================================================================
FAISS_INDEX_PATH = "rag_index.faiss"
DOCS_PATH = "./docs"
EMBED_DIM = 384  # Dimension for all-MiniLM-L6-v2 embeddings

def build_faiss_index():
    """
    Build or load a FAISS index from local documents in ./docs folder.

    Each text file is divided into small semantic chunks (~500 characters),
    encoded into embedding vectors, and indexed into FAISS for efficient
    nearest-neighbor search during reasoning.

    Returns:
        index (faiss.IndexFlatL2): FAISS index object
        doc_chunks (list[str]): Document chunks mapped to embeddings
    """
    index = faiss.IndexFlatL2(EMBED_DIM)
    doc_chunks = []

    if not os.path.exists(DOCS_PATH):
        os.makedirs(DOCS_PATH)
        console.print("[yellow]Created empty ./docs folder. Add text files for RAG context.[/]")
        return index, doc_chunks

    text_files = glob.glob(os.path.join(DOCS_PATH, "*.txt"))
    if not text_files:
        console.print("[yellow]No text files found. FAISS index remains empty.[/]")
        return index, doc_chunks

    # Iterate through local files and embed in chunks
    for fpath in text_files:
        with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        chunks = [content[i:i+500] for i in range(0, len(content), 500)]
        emb = embed_model.encode(chunks, convert_to_numpy=True)
        index.add(emb)
        doc_chunks.extend(chunks)

    console.print(f"[green]FAISS index built with {len(doc_chunks)} chunks from {len(text_files)} files.[/]")
    return index, doc_chunks

FAISS_INDEX, DOC_TEXTS = build_faiss_index()

def retrieve_docs(query: str, k: int = 3):
    """
    Retrieve top-k relevant text chunks from FAISS index given a query.

    Args:
        query (str): The query string (e.g., stock draft or reasoning text)
        k (int): Number of chunks to retrieve

    Returns:
        list[str]: Retrieved document texts providing additional context
    """
    if FAISS_INDEX.ntotal == 0:
        return []
    q_vec = embed_model.encode([query], convert_to_numpy=True)
    D, I = FAISS_INDEX.search(q_vec, k)
    return [DOC_TEXTS[i] for i in I[0] if i < len(DOC_TEXTS)]

# =============================================================================
#  Visualization Utilities
# =============================================================================
def ensure_dir(path="visuals"):
    """Ensure the output directory exists for visual charts."""
    if not os.path.exists(path):
        os.makedirs(path)
    return path

def plot_price_chart(ticker: str, history: dict):
    """
    Plot and save a 1-year price trend chart.

    Args:
        ticker (str): Stock symbol
        history (dict): Dictionary with 'Date' and 'Close' lists
    """
    path = ensure_dir()
    plt.figure(figsize=(6, 3))
    plt.plot(history["Date"], history["Close"], linewidth=2)
    plt.title(f"{ticker} 1-Year Price Trend")
    plt.xlabel("Date")
    plt.ylabel("Close ($)")
    plt.xticks(rotation=45)
    plt.tight_layout()
    fname = os.path.join(path, f"price_{ticker}.png")
    plt.savefig(fname)
    plt.close()
    console.print(f"[green]Saved price chart: {fname}[/]")

def plot_sentiment_chart(ticker: str, sentiments: list):
    """
    Plot a pie chart showing sentiment distribution for news.

    Args:
        ticker (str): Stock symbol
        sentiments (list): List of dictionaries with sentiment labels
    """
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    for s in sentiments:
        counts[s["sentiment"]] = counts.get(s["sentiment"], 0) + 1
    labels, vals = list(counts.keys()), list(counts.values())
    path = ensure_dir()
    plt.figure(figsize=(4, 4))
    plt.pie(vals, labels=labels, autopct="%1.0f%%", colors=["green", "red", "gray"])
    plt.title(f"{ticker} Sentiment Split")
    fname = os.path.join(path, f"sentiment_{ticker}.png")
    plt.savefig(fname)
    plt.close()
    console.print(f"[green]Saved sentiment chart: {fname}[/]")

# =============================================================================
#  Data Retrieval and Sentiment Analysis
# =============================================================================
def fetch_stock_data(ticker: str):
    """
    Fetch stock history and price statistics from Yahoo Finance.
    """
    try:
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty:
            return None, None
        last, prev = hist["Close"].iloc[-1], hist["Close"].iloc[-2]
        change = ((last - prev) / prev) * 100
        history = {"Date": hist.index.strftime("%Y-%m-%d").tolist(), "Close": hist["Close"].tolist()}
        return {"ticker": ticker, "last_close": float(last), "daily_change_pct": round(change, 2)}, history
    except Exception as e:
        console.print(f"[yellow]Error fetching data for {ticker}: {e}[/]")
        return None, None

def fetch_pe_ratio(ticker: str):
    """Fetch the stock’s price-to-earnings (P/E) ratio."""
    try:
        return float(yf.Ticker(ticker).info.get("trailingPE", "N/A"))
    except Exception:
        return "N/A"

def fetch_news(ticker: str, max_headlines: int = 5):
    """Retrieve top financial news headlines for a stock via Google News RSS."""
    feed = feedparser.parse(f"https://news.google.com/rss/search?q={ticker}+stock")
    return [{"title": e.title, "link": e.link} for e in feed.entries[:max_headlines]]

def analyze_sentiment(news: list):
    """Perform sentiment classification on news headlines."""
    result = []
    for n in news:
        sentiment = sentiment_pipeline(n["title"])[0]
        result.append({"title": n["title"], "sentiment": sentiment["label"].lower(), "score": sentiment["score"]})
    return result

# =============================================================================
#  LangGraph Agentic Workflow Definition
# =============================================================================
def build_graph(ticker: str, max_headlines: int = 5):
    """
    Define the LangGraph agentic reasoning workflow:
        1. Fetch → 2. Sentiment → 3. Draft → 4. Reasoning (RAG) →
        5. Critique → 6. Final Summary (with visualization)
    """
    g = StateGraph(dict)

    def fetch_node(s):
        price, hist = fetch_stock_data(ticker)
        pe = fetch_pe_ratio(ticker)
        news = fetch_news(ticker, max_headlines)
        s.update({"price": price, "hist": hist, "pe": pe, "news": news})
        return s

    def sentiment_node(s):
        s["sentiment"] = analyze_sentiment(s["news"])
        return s

    def draft_node(s):
        text = "\n".join([n["title"] for n in s["news"]])
        s["draft"] = generator_pipeline(f"Draft stock analysis for {ticker}:\n{text}", max_new_tokens=180)[0]["generated_text"]
        return s

    def reasoning_node(s):
        draft = s.get("draft", "")
        context = "\n".join(retrieve_docs(draft))
        prompt = f"Refine reasoning for {ticker} using context:\n{context}\n\nDraft:\n{draft}"
        s["reasoning"] = generator_pipeline(prompt, max_new_tokens=150)[0]["generated_text"]
        return s

    def critique_node(s):
        s["critique"] = critic_pipeline(f"Critique this reasoning:\n{s.get('reasoning', '')}", max_new_tokens=100)[0]["generated_text"]
        return s

    def final_node(s):
        final_prompt = f"Summarize stock analysis for {ticker}:\n{s.get('critique', '')}"
        s["final"] = generator_pipeline(final_prompt, max_new_tokens=150)[0]["generated_text"]

        sentiments = s.get("sentiment", [])
        pos = sum(1 for x in sentiments if x["sentiment"] == "positive")
        neg = sum(1 for x in sentiments if x["sentiment"] == "negative")
        s["recommendation"] = "Buy" if pos > neg else "Hold" if pos == neg else "Sell"

        # Generate and save visualizations
        if s.get("hist"): plot_price_chart(ticker, s["hist"])
        if s.get("sentiment"): plot_sentiment_chart(ticker, s["sentiment"])
        return s

    # Define workflow sequence
    for name, func in [
        ("fetch", fetch_node),
        ("sentiment", sentiment_node),
        ("draft", draft_node),
        ("reasoning", reasoning_node),
        ("critique", critique_node),
        ("final", final_node)
    ]:
        g.add_node(name, func)

    # Set transitions
    g.set_entry_point("fetch")
    g.add_edge("fetch", "sentiment")
    g.add_edge("sentiment", "draft")
    g.add_edge("draft", "reasoning")
    g.add_edge("reasoning", "critique")
    g.add_edge("critique", "final")
    g.add_edge("final", END)
    return g.compile()

# =============================================================================
#  Execution and MCP Integration
# =============================================================================
def analyze_stock(ticker: str, max_headlines: int = 5):
    """
    Execute full analysis pipeline for one stock:
        - Fetch Data → Sentiment → RAG Reasoning → Critique → Visual Summary
    """
    console.rule(f"[cyan]Analysis • {ticker}[/]")
    wf = build_graph(ticker, max_headlines)
    state = wf.invoke({})
    rec = state.get("recommendation", "—")

    panel = Panel(
        f"Ticker: {ticker}\nRecommendation: {rec}\n\nSummary:\n{state.get('final', '')}",
        title=f"{ticker} Report", border_style="green"
    )
    console.print(panel)
    console.rule("[grey]done[/]")
    return state

@mcp.tool
def mcp_analyze_stock(ticker: str, max_headlines: int = 5):
    """MCP entrypoint for integration with LangGraph and FastMCP protocol."""
    return analyze_stock(ticker, max_headlines)

# =============================================================================
#  Entrypoint
# =============================================================================
if __name__ == "__main__":
    """
    Usage:
      $ python server_mcp_agentic.py agentic
          → Runs multi-stock agentic workflow for default tickers.
      $ python sserve_mcp_agentic.py stdio
          → Runs as an MCP-compatible service.
    """
    if len(sys.argv) > 1 and sys.argv[1].lower() == "agentic":
        for t in ["AAPL", "TSLA", "MSFT", "GOOGL", "AMZN"]:
            analyze_stock(t)
    else:
        transport = (sys.argv[1] if len(sys.argv) > 1 else "stdio").lower()
        mcp.run(transport=transport)

