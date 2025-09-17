setup environment
=================

**install uv **
curl -LsSf https://astral.sh/uv/install.sh | sh


uv venv --python 3.11

source .venv/bin/activate   # macOS/Linux

uv pip install -r requirements.txt

uv pip freeze > requirements.txt

uv run python server.py
uv run python test_server.py





================================================================
(sep16) gshiva@Gangadhars-MacBook-Pro sep16 % python ./test-server.py
=== Running Investment+Sentiment+News Tests ===

You: AAPL
Device set to use mps:0
Device set to use mps:0

Investment+Sentiment+News MCP Server. Type ticker (e.g. AAPL).

AI: [Stock] AAPL last close: $238.15, daily change: 0.61%

AI: [Summary] AAPL (NASDAQ: AAPL) is a technology company headquartered in San Francisco, California.

AI: [Sentiment] Sentiment: neutral (score: 0.94)

AI: Recent News:

AI: No recent news available.

--------------------------------------------------
You: TSLA

AI: [Stock] TSLA last close: $421.62, daily change: 2.82%

AI: [Summary] TSLA (TSLA) is a publicly traded company headquartered in Los Angeles, California.

AI: [Sentiment] Sentiment: neutral (score: 0.95)

AI: Recent News:

AI: No recent news available.

--------------------------------------------------
You: MSFT

AI: [Stock] MSFT last close: $509.04, daily change: -1.23%

--------------------------------------------------

=== Tests Complete ===
