
import json
import matplotlib.pyplot as plt
from datetime import datetime
from collections import Counter
import os
from wordcloud import WordCloud
from matplotlib.backends.backend_pdf import PdfPages

LOG_FILE = "workflow_log.jsonl"
OUT_DIR = "workflow_plots"
PDF_REPORT = os.path.join(OUT_DIR, "workflow_report.pdf")

os.makedirs(OUT_DIR, exist_ok=True)

def load_log():
    """Load log entries grouped by symbol."""
    company_steps = {}
    with open(LOG_FILE, "r") as f:
        for line in f:
            try:
                entry = json.loads(line)
                state = entry.get("state", {})
                symbol = state.get("symbol", "UNKNOWN")
                company_steps.setdefault(symbol, []).append(entry)
            except json.JSONDecodeError:
                continue
    return company_steps

# -------------------------------------------------------------------------
# Chart Functions
# -------------------------------------------------------------------------
def plot_timeline(ax, steps, symbol):
    nodes = [s["node"] for s in steps]
    times = [datetime.fromisoformat(s["timestamp"]) for s in steps]

    ax.plot(times, list(range(len(nodes))), marker="o", linestyle="--")
    for i, node in enumerate(nodes):
        ax.text(times[i], i, f" {node}", verticalalignment="bottom")
    ax.set_title(f"{symbol}: Workflow Execution Timeline")
    ax.set_xlabel("Timestamp")
    ax.set_ylabel("Step Order")
    ax.grid(True)

def plot_state_matrix(ax, steps, symbol):
    all_keys = set()
    for s in steps:
        all_keys.update(s["state"].keys())
    all_keys = sorted(list(all_keys))

    matrix = []
    for s in steps:
        row = []
        for k in all_keys:
            row.append(1 if k in s["state"] and s["state"][k] else 0)
        matrix.append(row)

    im = ax.imshow(matrix, aspect="auto", cmap="Blues")
    ax.set_yticks(range(len(steps)))
    ax.set_yticklabels([s["node"] for s in steps])
    ax.set_xticks(range(len(all_keys)))
    ax.set_xticklabels(all_keys, rotation=45, ha="right")
    ax.set_title(f"{symbol}: State Coverage Across Workflow")
    plt.colorbar(im, ax=ax, label="Field Present")

def plot_sentiment(ax, steps, symbol):
    sentiments = []
    for s in steps:
        state = s.get("state", {})
        if "sentiment" in state and isinstance(state["sentiment"], list):
            for item in state["sentiment"]:
                sentiments.append((item.get("title", "N/A"), item.get("score", 0)))

    if not sentiments:
        ax.set_title(f"{symbol}: Sentiment Analysis")
        ax.text(0.5, 0.5, " No sentiment data found.", ha="center", va="center")
        return

    titles, scores = zip(*sentiments)
    ax.barh(titles, scores, color="skyblue")
    ax.set_title(f"{symbol}: Sentiment Scores per Headline")
    ax.set_xlabel("Score (0=neg, 1=pos)")

def plot_recommendations(ax, steps, symbol):
    recs = []
    for s in steps:
        state = s.get("state", {})
        if "recommendation" in state and state["recommendation"]:
            recs.append(state["recommendation"])

    if not recs:
        ax.set_title(f"{symbol}: Recommendation Distribution")
        ax.text(0.5, 0.5, " No recommendation data found.", ha="center", va="center")
        return

    counts = Counter(recs)
    ax.pie(counts.values(), labels=counts.keys(), autopct="%1.1f%%", startangle=140)
    ax.set_title(f"{symbol}: Recommendation Distribution")

def plot_news_wordcloud(ax, steps, symbol):
    headlines = []
    for s in steps:
        state = s.get("state", {})
        if "news" in state and isinstance(state["news"], list):
            for item in state["news"]:
                if item.get("title"):
                    headlines.append(item["title"])

    if not headlines:
        ax.set_title(f"{symbol}: News Headlines Word Cloud")
        ax.text(0.5, 0.5, " No news data found.", ha="center", va="center")
        return

    text = " ".join(headlines)
    wc = WordCloud(width=800, height=400, background_color="white").generate(text)
    ax.imshow(wc, interpolation="bilinear")
    ax.axis("off")
    ax.set_title(f"{symbol}: News Headlines Word Cloud")

def plot_summary(ax, steps, symbol):
    last_state = steps[-1].get("state", {}) if steps else {}
    last_run = last_state.get("memory", {}).get("last_run", "N/A")
    avg_sent = None
    sentiment_data = last_state.get("sentiment", [])
    if sentiment_data:
        scores = [s["score"] for s in sentiment_data if "score" in s]
        if scores:
            avg_sent = sum(scores) / len(scores)
    rec = last_state.get("recommendation", "N/A")

    # Summary text block
    text_lines = [
        f"📊 Workflow Summary for {symbol}",
        "",
        f"Last Run: {last_run}",
        f"Average Sentiment Score: {avg_sent:.3f}" if avg_sent is not None else "Average Sentiment Score: N/A",
        f"Top Recommendation: {rec}",
        f"Total Steps Recorded: {len(steps)}",
        "",
        "📰 Top Headlines Sentiment:"
    ]

    ax.axis("off")
    ax.text(0.05, 0.95, "\n".join(text_lines), ha="left", va="top", fontsize=11, family="monospace")

    if sentiment_data:
        top5 = sentiment_data[:5]
        cell_text = [[s.get("title", "N/A")[:50], f"{s.get('sentiment','N/A')}", f"{s.get('score',0):.2f}"] for s in top5]
        table = ax.table(cellText=cell_text,
                         colLabels=["Headline", "Sentiment", "Score"],
                         colWidths=[0.6, 0.2, 0.2],
                         cellLoc="left",
                         loc="lower left")
        table.auto_set_font_size(False)
        table.set_fontsize(8)
        table.scale(1, 1.3)

# -------------------------------------------------------------------------
# Multi-company Dashboard
# -------------------------------------------------------------------------
if __name__ == "__main__":
    companies = load_log()
    if not companies:
        print("⚠️ No log data found. Run the MCP workflow first.")
    else:
        with PdfPages(PDF_REPORT) as pdf:
            for symbol, steps in companies.items():
                # 0. Summary Page
                fig, ax = plt.subplots(figsize=(8.5, 11))
                plot_summary(ax, steps, symbol)
                pdf.savefig(fig); plt.close(fig)

                # 1. Timeline
                fig, ax = plt.subplots(figsize=(10, 4))
                plot_timeline(ax, steps, symbol)
                pdf.savefig(fig); plt.close(fig)

                # 2. State Matrix
                fig, ax = plt.subplots(figsize=(10, 6))
                plot_state_matrix(ax, steps, symbol)
                pdf.savefig(fig); plt.close(fig)

                # 3. Sentiment
                fig, ax = plt.subplots(figsize=(10, 4))
                plot_sentiment(ax, steps, symbol)
                pdf.savefig(fig); plt.close(fig)

                # 4. Recommendations
                fig, ax = plt.subplots(figsize=(6, 6))
                plot_recommendations(ax, steps, symbol)
                pdf.savefig(fig); plt.close(fig)

                # 5. News Word Cloud
                fig, ax = plt.subplots(figsize=(10, 5))
                plot_news_wordcloud(ax, steps, symbol)
                pdf.savefig(fig); plt.close(fig)

        print(f"✅ Multi-company Dashboard PDF saved at: {PDF_REPORT}")

