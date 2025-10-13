# Agentic AI Workflow for Scalable Investment Research and Analysis

### AAI-520 Team 1

This repository implements a **LangGraph + MCP + RAG-powered Agentic AI Workflow** for scalable investment research and explainable financial analytics.

The system models reasoning as a **Directed Acyclic Graph (DAG)** with seven cognitive nodes — each representing a stage of thought or analysis — and integrates a **visualization console** for human interpretability.

---

##  Overview

**Goal:** Create an interpretable, modular, and scalable workflow for investment research.  
**Core Features:**
- LangGraph for reasoning orchestration
- MCP for scalable, multi-agent execution
- Retrieval-Augmented Generation (RAG) for contextual grounding
- Visualization layer for console and graphical explainability

---

##  System Architecture

The workflow DAG executes the following reasoning nodes:

fetch → sentiment → draft → critique → reasoning → final → visualization


**Node Descriptions:**

| Node | Function |
|------|-----------|
| **fetch** | Collects financial and news data for tickers (AAPL, TSLA, MSFT, etc.) |
| **sentiment** | Classifies tone using transformer pipelines + RAG context |
| **draft** | Synthesizes an investment narrative |
| **critique** | Refines logic and factual accuracy |
| **reasoning** | Validates structured decision logic |
| **final** | Generates the interpretive investment summary |
| **visualization** | Produces charts and console summaries |

---

##  Execution Flow

1. Fetch live or cached financial data.  
2. Run transformer-based sentiment classification.  
3. Generate a draft investment report.  
4. Perform critique and factual correction.  
5. Execute reasoning validation (LangGraph node check).  
6. Produce the final structured recommendation.  
7. Render charts and sentiment visualizations.

---

##  Visualization Outputs

The system generates console and graphical results such as:

- `price_history_AAPL.png` — Price trends and momentum  
- `sentiment_pie_AAPL.png` — Sentiment distribution  
- `portfolio_summary.png` — Portfolio-level recommendations  

**Example Output Table:**

| Ticker | Close | % Change | P/E | Rec |
|---------|--------|----------|-----|------|
| AAPL | 245.27 | -3.45% | 37.16 | SELL |
| TSLA | 413.49 | -5.06% | 243.23 | SELL |
| MSFT | 510.96 | -2.19% | 37.46 | SELL |
| GOOGL | 236.57 | -2.05% | 25.25 | SELL |
| AMZN | 216.37 | -4.99% | 32.98 | SELL |

---

##  Results Summary

- **Reasoning node** ensures logic validation before generation.  
- **Draft–Critique–Final loop** enhances factual grounding.  
- **RAG integration** reduces hallucinations.  
- **Visualization & console panels** enhance transparency.

---

##  Future Work

- Add live market API integration (e.g., Yahoo Finance).  
- Implement user-in-the-loop critique feedback.  
- Introduce multi-sector portfolio filters.  
- Quantitatively benchmark reasoning accuracy.

---



##  Installation & Run

uv run AAI_520_Team_1-server-agentic-ai-mcp-server-rag-investment-research.py agentic

to 
cp claude_desktop-config.json ~/Library/Application\ Support/Claude/claude_desktop_config.json
run in mcp-mode ( rename AAI_520_Team_1-server-agentic-ai-mcp-server-rag-investment-research.py to mcp-server.py) 

