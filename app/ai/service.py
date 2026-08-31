"""AI assistant service layer -- agent orchestration and conversation management.

This module contains the core orchestration logic that:
    1. Receives user messages.
    2. Invokes relevant read-only tools based on the message.
    3. Passes tool results to the LLM for synthesis.
    4. Returns a structured response with answer and recommendations.

Abbreviations Used in This Module
----------------------------------
- LLM: Large Language Model -- the AI model that generates responses.
- AR: Accounts Receivable -- money owed TO the business by customers.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
"""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationMessage,
    ConversationResult,
    Recommendation,
    ToolCall,
)

logger = logging.getLogger("app.ai.service")


def select_tools(message: str) -> list[str]:
    """Select which tools to invoke based on the user's message.

    Uses keyword matching to determine which tools are relevant.
    This is a simple heuristic -- the LLM can request additional tools
    if needed via a second pass.

    Args:
        message: The user's natural language message.

    Returns:
        A list of tool names to invoke.
    """
    message_lower = message.lower()
    tools = []

    # Product tools
    if any(w in message_lower for w in ["search", "find", "look for", "product"]):
        tools.append("search_products")
    if any(w in message_lower for w in ["product details", "about", "tell me about"]):
        tools.append("get_product_details")
    if any(w in message_lower for w in ["stock", "inventory", "how many", "available"]):
        tools.append("check_stock")

    # Sales tools
    if any(w in message_lower for w in ["sales", "revenue", "how much", "total"]):
        tools.append("get_sales_summary")
    if any(w in message_lower for w in ["top", "best", "popular", "selling"]):
        tools.append("get_top_products")
    if any(w in message_lower for w in ["trend", "over time", "daily", "growth"]):
        tools.append("get_revenue_trend")
    if any(w in message_lower for w in ["recent", "transactions", "last sales"]):
        tools.append("get_recent_transactions")

    # Customer tools
    if any(w in message_lower for w in ["customer", "client", "who buys", "top buyer"]):
        tools.append("get_customer_insights")

    # Inventory tools
    if any(w in message_lower for w in ["low stock", "reorder", "running out", "alert"]):
        tools.append("get_inventory_alerts")

    # Financial tools
    if any(w in message_lower for w in ["profit", "loss", "p&l", "earnings"]):
        tools.append("get_profit_loss")
    if any(w in message_lower for w in ["expense", "spending", "costs"]):
        tools.append("get_expenses_by_category")
    if any(w in message_lower for w in ["receivable", "unpaid", "outstanding", "owed"]):
        tools.append("get_accounts_receivable")

    # Web tools
    if any(w in message_lower for w in ["price", "cost", "compare", "how much does"]):
        tools.append("compare_product_prices")
    if any(w in message_lower for w in ["what is", "tell me about", "explain"]):
        tools.append("search_product_info")

    if not tools:
        tools.append("get_sales_summary")

    return tools


async def invoke_tools(
    tools: list[str],
    session: AsyncSession,
    tenant_id: UUID,
    message: str,
    tool_args: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Invoke the selected tools and return their results.

    Args:
        tools: List of tool names to invoke.
        session: The async SQLAlchemy database session.
        tenant_id: The business tenant to filter by.
        message: The original user message (for context).
        tool_args: Optional pre-resolved tool arguments.

    Returns:
        A list of tool call records with results.
    """
    from app.ai.tools import TOOL_REGISTRY
    from app.ai.web import compare_product_prices, search_product_info

    results = []
    for tool_name in tools:
        try:
            if tool_name == "compare_product_prices":
                product_name = _extract_product_name(message)
                result_str = await compare_product_prices(product_name)
                result_summary = f"Found price comparison data for '{product_name}'"
            elif tool_name == "search_product_info":
                product_name = _extract_product_name(message)
                result_str = await search_product_info(product_name)
                result_summary = f"Found Wikipedia info for '{product_name}'"
            elif tool_name in TOOL_REGISTRY:
                func = TOOL_REGISTRY[tool_name]
                kwargs: dict[str, Any] = {"session": session, "tenant_id": tenant_id}

                if tool_name == "search_products":
                    kwargs["query"] = _extract_search_query(message)
                elif tool_name == "get_product_details":
                    product_id = (tool_args or {}).get("product_id")
                    if product_id:
                        kwargs["product_id"] = product_id
                    else:
                        continue
                elif tool_name == "check_stock":
                    if tool_args and tool_args.get("product_id"):
                        kwargs["product_id"] = tool_args["product_id"]
                    elif tool_args and tool_args.get("product_name"):
                        kwargs["product_name"] = tool_args["product_name"]
                elif tool_name == "get_sales_summary":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_top_products":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_revenue_trend":
                    kwargs["days"] = 30
                elif tool_name == "get_recent_transactions":
                    kwargs["limit"] = 10
                elif tool_name == "get_customer_insights":
                    kwargs["limit"] = 10
                elif tool_name == "get_inventory_alerts":
                    kwargs["threshold"] = 10
                elif tool_name == "get_profit_loss":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_expenses_by_category":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_accounts_receivable":
                    pass

                result_str = await func(**kwargs)
                result_summary = f"Retrieved data from {tool_name}"
            else:
                continue

            results.append({
                "tool": tool_name,
                "arguments": tool_args or {},
                "result": result_str,
                "result_summary": result_summary,
            })
        except Exception as e:
            logger.warning("Tool %s failed: %s", tool_name, e)
            results.append({
                "tool": tool_name,
                "arguments": tool_args or {},
                "result": json.dumps({"error": str(e)}),
                "result_summary": f"Tool failed: {e}",
            })

    return results


def build_response(
    tool_results: list[dict[str, Any]],
    llm_response: dict[str, Any],
    conversation_id: UUID,
) -> ChatResponse:
    """Build the final ChatResponse from tool results and LLM response.

    Args:
        tool_results: Results from tool invocations.
        llm_response: The parsed LLM response.
        conversation_id: The conversation UUID.

    Returns:
        A structured ChatResponse.
    """
    tool_calls = [
        ToolCall(
            tool=r["tool"],
            arguments=r["arguments"],
            result_summary=r["result_summary"],
        )
        for r in tool_results
    ]

    recommendations = []
    for rec in llm_response.get("recommendations", []):
        if isinstance(rec, dict):
            recommendations.append(Recommendation(
                action=rec.get("action", ""),
                description=rec.get("description", ""),
                target_id=rec.get("target_id"),
                target_type=rec.get("target_type"),
                api_endpoint=rec.get("api_endpoint"),
                api_method=rec.get("api_method", "POST"),
                api_body=rec.get("api_body", {}),
            ))

    return ChatResponse(
        conversation_id=conversation_id,
        answer=llm_response.get("answer", "No response generated."),
        tool_calls=tool_calls,
        recommendations=recommendations,
        confidence=0.8,
        created_at=datetime.now(UTC),
    )


def _extract_product_name(message: str) -> str:
    """Extract a product name from the user's message."""
    prefixes = [
        "what is", "tell me about", "how much does", "compare",
        "search for", "find", "look for", "show me", "what are",
        "get", "check",
    ]
    result = message.lower()
    for prefix in prefixes:
        if result.startswith(prefix):
            result = result[len(prefix):].strip()

    result = result.rstrip("?!.,").strip()
    words = result.split()
    return " ".join(words[:5]) if words else message


def _extract_search_query(message: str) -> str:
    """Extract a search query from the user's message."""
    return _extract_product_name(message)


def _extract_period(message: str) -> str:
    """Extract a time period from the user's message."""
    message_lower = message.lower()
    if "today" in message_lower:
        return "today"
    elif "yesterday" in message_lower:
        return "yesterday"
    elif "week" in message_lower or "7 days" in message_lower:
        return "week"
    elif "year" in message_lower or "12 months" in message_lower:
        return "year"
    else:
        return "month"
