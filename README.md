# ezstox

Terminal-based portfolio tracker with AI investment advisor

## Features

- Track portfolio holdings with P&L calculations
- Monitor watchlist stocks
- Live stock prices via OpenBB
- Recent news articles for your stocks
- AI investment advisor powered by OpenAI GPT-4o-mini
- Automatic asset type detection (stocks, ETFs, commodities)
- Interactive menu system

## Setup

### 1. Install Dependencies

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Set Up API Keys

Add these to your shell config (`~/.zshrc` or `~/.bashrc`):

```bash
export OPENAI_API_KEY='your-openai-key'
export FRED_API_KEY='your-fred-key'  # Optional - for VIX data
```

Then reload: `source ~/.zshrc`

### 3. Create Data Files

Create a `data/` directory with your portfolio information:

**data/portfolio.txt**
```
# Format: SYMBOL,SHARES,COST_BASIS
# Example:
AAPL,10,150.00
NVDA,5,200.00
```

**data/watchlist.txt**
```
# Format: One symbol per line
# Example:
TSLA
AMD
META
```

**data/cash.txt**
```
1000
```

### 4. Run

```bash
python main.py
```

This launches the interactive menu where you can:
1. View Portfolio & Watchlist
2. View News for Stocks
3. Get AI Investment Advice (Full Analysis)
4. Edit Portfolio
5. Edit Watchlist
6. Edit Cash Balance
7. Settings & Info

## Usage Notes

- **AI Analysis**: Costs ~$0.003-0.005 per run (~$0.21/month for 2x daily use)
- **Performance**: Portfolio view ~5 seconds, AI analysis ~60-90 seconds
- **Asset Types**: Automatically detects stocks, ETFs, and commodities
- **ETF Handling**: Analyzes ETFs based on underlying index/commodity trends instead of company metrics

## File Structure

```
ezstox/
├── main.py              # Entry point
├── menu.py              # Interactive menu
├── src/
│   ├── data_fetcher.py      # OpenBB API calls
│   ├── llm_advisor.py       # AI analysis
│   ├── portfolio_manager.py # Portfolio data handling
│   └── reporter.py          # Output formatting
└── data/                # Your portfolio data (not in git)
    ├── portfolio.txt
    ├── watchlist.txt
    └── cash.txt
```
