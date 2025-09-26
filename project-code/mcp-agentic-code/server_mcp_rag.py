#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Stock Analysis Server & CLI (with Rich TTY output routed to STDERR)
+ RAG-enhanced sentiment using a local Vector DB (ChromaDB)

What this adds
--------------
- A lightweight **RAG** layer for sentiment:
  1) We embed each headline with a small sentence embedding model.
  2) We **retrieve** similar headlines from a persistent **vector DB** (Chroma).
  3) We ask the LLM to classify sentiment **with retrieved context** and return a label+rationale.
  4) We **upsert** the new (headline, ticker, label, score, time) back into the vector DB for future runs.

If ChromaDB isn't installed, we gracefully fall back to the original classifier-only flow.

Transports:
- `stdio` (default): suitable for local MCP clients (e.g., Claude Desktop)
- `http` / `sse`: optional network transports (health route provided if Starlette is available)

Example usage:
    uv pip install rich yfinance feedparser transformers chromadb  # (chromadb is optional but recommended)
    python server.py agentic    # run pretty CLI for a small portfolio and show recommendations
    python server.py stdio      # run MCP server over stdio (no stdout logs!)
    python server.py http       # run MCP server over HTTP (host/port via env)
    python server.py sse        # run MCP server over SSE (host/port via env)

Environment:
    GEN_MODEL          (default: google/flan-t5-base; text2text-generation)
    CRITIC_MODEL       (optional; defaults to GEN_MODEL)
    EMBED_MODEL        (default: sentence-transformers/all-MiniLM-L6-v2)
    SENTIMENT_MODEL    (optional; transformers pipeline default if unset)
    DEVICE             ("cpu" | "cuda" | "mps"; default "cpu")
    CHROMA_PATH        (directory for Chroma persistence; default ./rag_store)
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

# Optional vector DB (Chroma)
CHROMA_AVAILABLE = True
try:
    import chromadb
    from chromadb.utils.embedding_functions import EmbeddingFunction
except Exception:
    CHROMA_AVAILABLE = False

# Optional: numpy for mean pooling (recommended)
try:
    import numpy as np
except Exception:
    np = None  # we will degrade to pure-Python pooling if needed

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
CRITIC_MODEL = os.getenv("CRITIC_MODEL", GEN_MODEL)
EMBED_MODEL = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
SENTIMENT_MODEL = os.getenv("SENTIMENT_MODEL", None)
DEVICE = os.getenv("DEVICE", "cpu").lower()

# Generation (drafter)
if DEVICE in ("cuda", "mps"):
    generator_pipeline = pipeline("text2text-generation", model=GEN_MODEL, device_map="auto")
    critic_pipeline = pipeline("text2text-generation", model=CRITIC_MODEL, device_map="auto")
    embed_pipeline = pipeline("feature-extraction", model=EMBED_MODEL, device_map="auto")
else:
    generator_pipeline = pipeline("text2text-generation", model=GEN_MODEL)
    critic_pipeline = pipeline("text2text-generation", model=CRITIC_MODEL)
    embed_pipeline = pipeline("feature-extraction", model=EMBED_MODEL)

# Classifier
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
# RAG: Embeddings & Vector DB
# ===========================
def _mean_pool_features(feats: Any) -> List[float]:
    """
    Mean-pool a nested list coming from the 'feature-extraction' pipeline.
    Handles shapes like [seq_len, hidden] or [1, seq_len, hidden].
    """
    # Convert to numpy if available for simplicity
    if np is not None:
        arr = np.array(feats, dtype="float32")
        if arr.ndim == 3:  # [batch, seq, hid]
            arr = arr[0]
        # avoid division by zero
        if arr.size == 0:
            return []
        return arr.mean(axis=0).astype("float32").tolist()

    # Fallback: pure python pooling
    # feats -> list (maybe [1][seq][hidden] or [seq][hidden])
    if isinstance(feats, list) and feats:
        if isinstance(feats[0], list) and feats[0] and isinstance(feats[0][0], list):
            feats = feats[0]  # drop batch
        # feats is now [seq][hidden]
        seq = len(feats)
        hid = len(feats[0]) if seq else 0
        if seq == 0 or hid == 0:
            return []
        # mean across seq for each hidden dim
        sums = [0.0] * hid
        for token in feats:
            for j, val in enumerate(token):
                sums[j] += float(val)
        return [s / seq for s in sums]
    return []


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Embed texts via the HF feature-extraction pipeline + mean pooling.
    """
    vectors: List[List[float]] = []
    for t in texts:
        feats = embed_pipeline(t, truncation=True, max_length=512)
        vec = _mean_pool_features(feats)
        vectors.append(vec)
    return vectors


class HFEmbeddingFn(EmbeddingFunction):
    """
    Chroma EmbeddingFunction that delegates to our HF embedding pipeline.
    """
    def __call__(self, input: List[str]) -> List[List[float]]:
        return embed_texts(input)


class VectorStore:
    """
    Small wrapper around Chroma (if available) for storing and retrieving headlines.
    Each document = {'text': headline, 'metadata': {'ticker', 'sentiment', 'score', 'time'}}.
    """

    def __init__(self, path: str = "./rag_store", collection: str = "finance_news"):
        self.enabled = CHROMA_AVAILABLE
        self.path = path
        self.collection_name = collection
        self.client = None
        self.col = None
        if self.enabled:
            try:
                self.client = chromadb.PersistentClient(path=self.path)  # type: ignore[name-defined]
                self.col = self.client.get_or_create_collection(
                    name=self.collection_name,
                    embedding_function=HFEmbeddingFn(),
                )
                # Seed a few generic exemplars (only if empty)
                if self.col.count() == 0:
                    seed_docs = [
                        ("beats earnings and raises guidance", {"ticker": "GEN", "sentiment": "positive"}),
                        ("SEC probe into accounting practices", {"ticker": "GEN", "sentiment": "negative"}),
                        ("shares flat after mixed results", {"ticker": "GEN", "sentiment": "neutral"}),
                        ("product recall due to safety issues", {"ticker": "GEN", "sentiment": "negative"}),
                        ("announces major partnership expansion", {"ticker": "GEN", "sentiment": "positive"}),
                    ]
                    self.upsert_bulk(
                        texts=[t for t, _ in seed_docs],
                        metadatas=[m | {"score": 1.0, "time": datetime.utcnow().isoformat()} for _, m in seed_docs],
                        ids=[f"seed-{i}" for i in range(len(seed_docs))],
                    )
            except Exception as e:
                self.enabled = False
                console.print(f"[warn] Vector DB disabled (init failed): {e}")

    def upsert(self, text: str, metadata: Dict[str, Any], id_: Optional[str] = None) -> None:
        """
        Upsert a single document.
        """
        if not self.enabled or self.col is None:
            return
        try:
            self.col.upsert(documents=[text], metadatas=[metadata], ids=[id_ or self._make_id(text, metadata)])
        except Exception as e:
            console.print(f"[warn] Vector upsert failed: {e}")

    def upsert_bulk(self, texts: List[str], metadatas: List[Dict[str, Any]], ids: Optional[List[str]] = None) -> None:
        """
        Upsert many documents at once.
        """
        if not self.enabled or self.col is None or not texts:
            return
        try:
            if ids is None:
                ids = [self._make_id(t, metadatas[i]) for i, t in enumerate(texts)]
            self.col.upsert(documents=texts, metadatas=metadatas, ids=ids)
        except Exception as e:
            console.print(f"[warn] Vector bulk upsert failed: {e}")

    def query(self, text: str, ticker: Optional[str] = None, k: int = 5) -> List[Dict[str, Any]]:
        """
        Query similar documents to the given text. Optionally filter by ticker.

        Returns:
            List of {text, metadata, distance}
        """
        if not self.enabled or self.col is None:
            return []
        try:
            where = {"ticker": ticker} if ticker else None
            res = self.col.query(query_texts=[text], n_results=max(1, k), where=where)
            docs = []
            for i in range(len(res.get("ids", [[]])[0])):
                docs.append({
                    "text": res["documents"][0][i],
                    "metadata": res["metadatas"][0][i],
                    "distance": res["distances"][0][i] if "distances" in res and res["distances"] else None,
                })
            return docs
        except Exception as e:
            console.print(f"[warn] Vector query failed: {e}")
            return []

    @staticmethod
    def _make_id(text: str, metadata: Dict[str, Any]) -> str:
        # compact deterministic id
        t = metadata.get("ticker", "NA")
        ts = metadata.get("time", datetime.utcnow().isoformat())
        base = f"{t}-{ts}-{text[:64]}"
        return base.replace(" ", "_").replace("/", "_")


# Global vector store
VSTORE = VectorStore(path=os.getenv("CHROMA_PATH", "./rag_store"))


# ===========================
# Data & generation helpers
# ===========================
def hf_generate(prompt: str, max_new_tokens: int = 256) -> str:
    """
    Generate text from a prompt using the configured transformers pipeline.
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


def hf_critic(prompt: str, max_new_tokens: int = 200) -> str:
    """
    Critic LLM generation (can be a different model than the drafter).
    """
    try:
        out = critic_pipeline(prompt, max_new_tokens=max_new_tokens, do_sample=False)
        text = out[0].get("generated_text") or out[0].get("summary_text")
        return (text or str(out[0])).strip()
    except Exception as e:
        console.print(f"[warn] Critique generation failed: {e}")
        return "[CRITIC FAILED]"


def fetch_price_and_history(ticker: str) -> Tuple[Optional[Dict[str, Any]], Optional[Dict[str, Any]]]:
    """
    Fetch latest close, daily change %, and a 1y history snapshot from yfinance.
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
    """
    try:
        url = f"https://news.google.com/rss/search?q={ticker}+stock"
        feed = feedparser.parse(url)
        articles = [{"title": entry.title, "link": entry.link} for entry in feed.entries[:max_headlines]]
        return articles if articles else [{"title": f"No recent news for {ticker}", "link": None}]
    except Exception as e:  # pragma: no cover
        console.print(f"[warn] RSS fetch failed for {ticker}: {e}")
        return [{"title": f"No recent news for {ticker}", "link": None}]


# ===========================
# RAG-augmented sentiment
# ===========================
def _rag_sentiment(headline: str, ticker: str) -> Dict[str, Any]:
    """
    RAG-enhanced sentiment classification for a single headline.

    Steps:
        1) Base classifier label/score via transformers pipeline.
        2) Retrieve top-k similar headlines from the vector DB (same ticker first, else general).
        3) Ask LLM to re-label with context (label ∈ {positive, negative, neutral}) and give a rationale.
        4) Upsert this headline into the vector DB for future runs.

    Returns:
        {
          "title": str,
          "sentiment": "positive|negative|neutral",
          "score": float,     # from base classifier
          "rag": {
             "used": bool,
             "rationale": str,
             "neighbors": [{"title": str, "sentiment": str, "distance": float}, ...]
          }
        }
    """
    # 1) Base classifier
    base = sentiment_pipeline(headline)[0]
    base_label = base["label"].lower()
    base_score = float(base["score"])

    # 2) Retrieve neighbors
    neighbors = VSTORE.query(headline, ticker=ticker, k=5) if VSTORE.enabled else []
    if not neighbors and VSTORE.enabled:
        # fallback to general pool
        neighbors = VSTORE.query(headline, ticker=None, k=5)

    # 3) LLM re-label with context (if any neighbor exists)
    rag_used = bool(neighbors)
    final_label = base_label
    rationale = ""

    if rag_used:
        bullets = []
        for nb in neighbors:
            nb_lab = (nb.get("metadata", {}) or {}).get("sentiment", "unknown")
            bullets.append(f"- {nb['text']} [sentiment: {nb_lab}]")
        context = "\n".join(bullets[:5])

        prompt = (
            "You are an equity research assistant. Classify the SENTIMENT (positive/negative/neutral) of the "
            "HEADLINE, taking into account similar prior headlines and their labels for context.\n\n"
            f"HEADLINE: {headline}\n\n"
            "SIMILAR HEADLINES:\n"
            f"{context}\n\n"
            "Return JSON with fields: label (one of positive|negative|neutral) and rationale (<= 2 short bullets)."
        )
        out = hf_generate(prompt, max_new_tokens=220)
        # very light parsing: find label
        label = None
        for cand in ("positive", "negative", "neutral"):
            if cand in out.lower():
                label = cand
                break
        final_label = label or base_label
        rationale = out.strip()

    # 4) Upsert this headline (with final label)
    if VSTORE.enabled:
        VSTORE.upsert(
            text=headline,
            metadata={
                "ticker": ticker,
                "sentiment": final_label,
                "score": base_score,
                "time": datetime.utcnow().isoformat(),
            },
        )

    return {
        "title": headline,
        "sentiment": final_label,
        "score": base_score,
        "rag": {
            "used": rag_used,
            "rationale": rationale if rag_used else "",
            "neighbors": [
                {
                    "title": nb["text"],
                    "sentiment": (nb.get("metadata", {}) or {}).get("sentiment", "unknown"),
                    "distance": nb.get("distance"),
                }
                for nb in (neighbors or [])
            ],
        },
    }


def classify_sentiment(news_items: List[Dict[str, str]] | None, ticker: str = "") -> List[Dict[str, Any]]:
    """
    Apply RAG-enhanced sentiment (with vector DB) to each news title.

    If the vector DB is unavailable, falls back to base classifier.
    """
    results: List[Dict[str, Any]] = []
    for item in news_items or []:
        title = item.get("title", "")
        try:
            if VSTORE.enabled:
                results.append(_rag_sentiment(title, ticker))
            else:
                base = sentiment_pipeline(title)[0]
                results.append(
                    {
                        "title": title,
                        "sentiment": base["label"].lower(),
                        "score": float(base["score"]),
                        "rag": {"used": False, "rationale": "", "neighbors": []},
                    }
                )
        except Exception as e:  # pragma: no cover
            results.append(
                {
                    "title": title,
                    "sentiment": "unknown",
                    "score": 0.0,
                    "error": str(e),
                    "rag": {"used": False, "rationale": "", "neighbors": []},
                }
            )
    return results


# ===========================
# Pretty renderers & helpers
# ===========================
def _pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    try:
        return (b - a) / a * 100 if a else 0.0  # type: ignore[operator]
    except Exception:
        return None


def render_history(history: Optional[Dict[str, Optional[Dict[str, float]]]]) -> Table:
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
    table = Table(title="Recent News (RAG-aware)", box=box.ROUNDED)
    table.add_column("#", justify="right", style="muted", width=3)
    table.add_column("Headline")
    table.add_column("Sentiment", justify="center", style="muted", width=20)
    for i, n in enumerate(news or []):
        label = "—"
        if sentiments and i < len(sentiments):
            s = sentiments[i]
            label = f"{s.get('sentiment','?')} ({s.get('score',0):.2f})"
            if s.get("rag", {}).get("used"):
                label += " [RAG]"
        table.add_row(str(i + 1), n.get("title", "—"), label)
    return table


def render_summary(state: Dict[str, Any], ticker: str) -> Panel:
    price = state.get("price") or {}
    pe = state.get("pe_ratio", "N/A")
    last = price.get("last_close")
    chg = price.get("daily_change_pct")
    chg_color = "ok" if isinstance(chg, (int, float)) and chg >= 0 else "err"

    header = Table.grid(expand=True)
    header.add_column(justify="left")
    header.add_column(justify="right")

    left1 = f"[accent]Ticker:[/] [bold]{ticker.upper()}[/]"
    right1 = f"PE: [bold]{pe}[/]" if isinstance(pe, (int, float)) else f"PE: [muted]{pe}[/]"
    header.add_row(left1, right1)

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
    c = {"positive": 0, "negative": 0, "neutral": 0, "unknown": 0}
    for s in items or []:
        label = (s.get("sentiment") or "unknown").lower()
        if label not in c:
            label = "unknown"
        c[label] += 1
    return c


def render_portfolio_summary(results_dict: Dict[str, Dict[str, Any]]) -> Table:
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
        fetch -> sentiment(RAG) -> draft -> critique -> final -> END
    """
    graph = StateGraph(dict)

    def fetch_node(state: Dict[str, Any]) -> Dict[str, Any]:
        price, history = fetch_price_and_history(ticker)
        pe_ratio = fetch_pe_ratio(ticker)
        news = fetch_news(ticker, max_headlines=max_headlines)
        state.update({"price": price, "history": history, "pe_ratio": pe_ratio, "news": news})
        return state

    def sentiment_node(state: Dict[str, Any]) -> Dict[str, Any]:
        state["sentiment"] = classify_sentiment(state.get("news", []), ticker=ticker)
        return state

    def draft_node(state: Dict[str, Any]) -> Dict[str, Any]:
        headlines_text = "\n".join([n.get("title", "") for n in state.get("news", [])])
        prompt = f"Draft a short stock analysis for {ticker} based on these headlines:\n{headlines_text}"
        state["draft"] = hf_generate(prompt, max_new_tokens=200)
        return state

    def critique_node(state: Dict[str, Any]) -> Dict[str, Any]:
        draft = state.get("draft", "No draft")
        prompt = (
            "Act as a senior equity analyst. Provide a concise, bullet-point critique of the draft below. "
            "Focus on evidence, risks, missing context (valuation, catalysts, macro), and factual caution. "
            "Be specific.\n\n=== DRAFT ===\n" + draft
        )
        state["critique"] = hf_critic(prompt, max_new_tokens=180)
        return state

    def final_node(state: Dict[str, Any]) -> Dict[str, Any]:
        draft = state.get("draft", "No draft")
        critique = state.get("critique", "No critique")
        prompt = (
            "Rewrite the draft into a clear, neutral stock note (≤180 words), "
            "incorporating the critique. Include: thesis, key risks, and a cautious stance if evidence is weak.\n\n"
            f"=== DRAFT ===\n{draft}\n\n=== CRITIQUE ===\n{critique}"
        )
        state["final"] = hf_generate(prompt, max_new_tokens=220)

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
        """
        return PlainTextResponse("OK")


# ===========================
# Entrypoint
# ===========================
if __name__ == "__main__":
    """
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

