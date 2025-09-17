setup environment
=================




Investment MCP Server — Commands
Environment Setup
# Install uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# Create virtual environment with Python 3.11
uv venv --python 3.11

# Activate environment (macOS/Linux)
source .venv/bin/activate

 Install & Manage Dependencies
# Install dependencies
uv pip install -r requirements.txt

# Freeze dependencies into requirements.txt
uv pip freeze > requirements.txt

 Run the Server
uv run python server.py

 Run Tests
uv run python test_server.py


Or directly with Python:

python ./test-server.py

 Example Test Session
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
