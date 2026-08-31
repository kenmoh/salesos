"""Web search tools for the AI assistant.

This module provides two web search tools:
    1. compare_product_prices: Uses SerpAPI to search for product prices online.
    2. search_product_info: Uses Wikipedia to search for product information.

Both tools are READ-ONLY and return search results without modifying any data.

Abbreviations Used in This Module
----------------------------------
- API: Application Programming Interface -- a set of endpoints for interaction.
- JSON: JavaScript Object Notation -- a lightweight data interchange format.
"""

import json
import logging

logger = logging.getLogger("app.ai.web")


async def compare_product_prices(product_name: str) -> str:
    """Search the web for product prices using SerpAPI.

    Searches Google Shopping for the product name and returns price
    comparisons from multiple retailers.

    Args:
        product_name: The product name to search for.

    Returns:
        JSON string with price comparison results from web retailers.
    """
    try:
        from app.core.config import get_settings

        settings = get_settings()
        serpapi_key = getattr(settings, "serpapi_key", None)

        if serpapi_key:
            return await _serpapi_search(product_name, serpapi_key)

        return await _fallback_price_search(product_name)

    except Exception as e:
        logger.warning("compare_product_prices failed: %s", e)
        return json.dumps({
            "product": product_name,
            "results": [],
            "note": "Price comparison unavailable. Try searching directly on Jumia or Konga.",
            "error": str(e),
        })


async def _serpapi_search(product_name: str, api_key: str) -> str:
    """Search using SerpAPI for Google Shopping results."""
    import httpx

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            "https://serpapi.com/search.json",
            params={
                "q": f"{product_name} price Nigeria",
                "engine": "google_shopping",
                "api_key": api_key,
                "gl": "ng",
                "hl": "en",
            },
        )
        resp.raise_for_status()
        data = resp.json()

    results = []
    for item in data.get("shopping_results", [])[:8]:
        results.append({
            "title": item.get("title", ""),
            "price": item.get("price", ""),
            "extracted_price": item.get("extracted_price"),
            "source": item.get("source", ""),
            "link": item.get("link", ""),
        })

    return json.dumps({
        "product": product_name,
        "results": results,
        "note": "Prices from Google Shopping. Verify before making decisions.",
    })


async def _fallback_price_search(product_name: str) -> str:
    """Fallback price search using a simple web scraping approach."""
    return json.dumps({
        "product": product_name,
        "results": [],
        "note": (
            f"For current prices on '{product_name}', check these Nigerian retailers:\n"
            "- Jumia: https://www.jumia.com.ng\n"
            "- Konga: https://www.konga.com\n"
            "- Slot: https://www.slot.ng\n"
            "- Pointek: https://www.pointek.com"
        ),
    })


async def search_product_info(query: str) -> str:
    """Search Wikipedia for product information.

    Returns product descriptions, specifications, and background information.

    Args:
        query: The search query (product name or topic).

    Returns:
        JSON string with Wikipedia search results.
    """
    try:
        import wikipedia

        results = wikipedia.search(query, results=3)

        if not results:
            return json.dumps({
                "query": query,
                "results": [],
                "note": f"No Wikipedia articles found for '{query}'.",
            })

        try:
            page = wikipedia.page(results[0], auto_suggest=False)
            summary = wikipedia.summary(results[0], sentences=5)
        except (wikipedia.DisambiguationError, wikipedia.PageError):
            try:
                page = wikipedia.page(results[1], auto_suggest=False)
                summary = wikipedia.summary(results[1], sentences=5)
            except Exception:
                summary = "No detailed information available."
                page = None

        return json.dumps({
            "query": query,
            "title": page.title if page else results[0],
            "summary": summary,
            "url": page.url if page else None,
            "related_articles": results[:3],
        })

    except ImportError:
        logger.warning("wikipedia package not installed")
        return json.dumps({
            "query": query,
            "results": [],
            "note": "Wikipedia search not available. Package not installed.",
        })
    except Exception as e:
        logger.warning("search_product_info failed: %s", e)
        return json.dumps({
            "query": query,
            "results": [],
            "error": str(e),
        })
