"""
Azure AI Language (Cognitive Services) sentiment analysis over real
ezstox news headlines.

ezstox doesn't persist headlines anywhere (data_fetcher.get_stock_news
fetches live and returns them in-memory each time), so this script
fetches real, current headlines for your portfolio/watchlist symbols
using the existing fetcher, then sends them to Azure's Language
service sentiment analysis REST endpoint.

Setup:
    export AZURE_LANGUAGE_ENDPOINT="https://<your-resource-name>.cognitiveservices.azure.com"
    export AZURE_LANGUAGE_KEY="<key 1 from the Azure portal>"

Run:
    python azure_sentiment.py
    python azure_sentiment.py AAPL NVDA TSLA   # optional explicit symbols
"""

import os
import sys

import requests

from src.data_fetcher import get_stock_news
from src.portfolio_manager import Portfolio

AZURE_LANGUAGE_ENDPOINT = os.environ.get("AZURE_LANGUAGE_ENDPOINT", "").rstrip("/")
AZURE_LANGUAGE_KEY = os.environ.get("AZURE_LANGUAGE_KEY", "")
API_VERSION = "2023-04-01"


def analyze_sentiment(documents):
    """
    Call Azure's Language service sentiment analysis endpoint.

    documents: list of {"id": str, "text": str, "language": "en"}
    Returns: list of per-document result dicts from Azure.
    """
    if not AZURE_LANGUAGE_ENDPOINT or not AZURE_LANGUAGE_KEY:
        raise RuntimeError(
            "Set AZURE_LANGUAGE_ENDPOINT and AZURE_LANGUAGE_KEY environment "
            "variables (Azure portal -> your Language resource -> Keys and Endpoint)."
        )

    url = f"{AZURE_LANGUAGE_ENDPOINT}/language/:analyze-text?api-version={API_VERSION}"
    headers = {
        "Ocp-Apim-Subscription-Key": AZURE_LANGUAGE_KEY,
        "Content-Type": "application/json",
    }
    body = {
        "kind": "SentimentAnalysis",
        "parameters": {"modelVersion": "latest"},
        "analysisInput": {"documents": documents},
    }

    response = requests.post(url, headers=headers, json=body, timeout=30)
    response.raise_for_status()
    return response.json()["results"]["documents"]


def gather_headlines(symbols, limit_per_symbol=3):
    """Fetch real, current headlines for each symbol via ezstox's existing fetcher."""
    documents = []
    for symbol in symbols:
        articles = get_stock_news(symbol, limit=limit_per_symbol)
        for i, article in enumerate(articles):
            documents.append({"id": f"{symbol}-{i}", "text": article["title"], "language": "en"})
    return documents


def main():
    symbols = sys.argv[1:]
    if not symbols:
        portfolio = Portfolio(silent=True)
        symbols = portfolio.get_all_symbols() or ["AAPL", "TSLA", "NVDA"]

    print(f"Fetching real headlines for: {', '.join(symbols)}")
    documents = gather_headlines(symbols)

    if not documents:
        print("No headlines were fetched - nothing to analyze.")
        return

    print(f"Sending {len(documents)} headlines to Azure Language sentiment analysis...\n")
    results = analyze_sentiment(documents)

    doc_by_id = {d["id"]: d["text"] for d in documents}
    for result in results:
        doc_id = result["id"]
        text = doc_by_id[doc_id]
        sentiment = result["sentiment"]
        scores = result["confidenceScores"]
        print(f"[{doc_id}] {text}")
        print(
            f"    -> {sentiment} "
            f"(positive={scores['positive']:.2f}, "
            f"neutral={scores['neutral']:.2f}, "
            f"negative={scores['negative']:.2f})"
        )
        print()


if __name__ == "__main__":
    main()
