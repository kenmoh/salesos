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
import re
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
    """Select which read-only tools to invoke based on the user's message.

    Uses keyword matching as a fast first pass. When nothing matches, the
    caller can fall back to the LLM planner (see ``plan_tools``).

    Args:
        message: The user's natural language message.

    Returns:
        A list of tool names to invoke. Empty when no keyword matched.
    """
    message_lower = message.lower()
    tools: list[str] = []

    def add(name: str) -> None:
        if name not in tools:
            tools.append(name)

    # Product tools
    if any(w in message_lower for w in ["search", "find", "look for", "product"]):
        add("search_products")
    if any(w in message_lower for w in ["product details", "about", "tell me about"]):
        add("get_product_details")

    # Expiry
    if any(
        w in message_lower
        for w in [
            "expire",
            "expiring",
            "expiry",
            "expired",
            "expires soon",
            "best before",
            "near expiry",
            "out of date",
        ]
    ):
        add("get_expiring_products")

    # Stock level vs stock history
    if any(w in message_lower for w in ["stock", "inventory", "how many", "available"]):
        add("check_stock")
    if any(
        w in message_lower
        for w in [
            "stock history",
            "stock movement",
            "stock movements",
            "movement history",
            "why did stock",
            "why is stock",
            "stock change",
            "stock changes",
            "opening stock",
            "audit trail",
            "adjustment history",
            "stock adjustment",
            "stock adjustments",
            "moved stock",
            "stock moved",
        ]
    ):
        add("get_stock_history")

    # Sales tools
    if any(w in message_lower for w in ["sales", "revenue", "how much", "total"]):
        add("get_sales_summary")
    if any(w in message_lower for w in ["top", "best", "popular", "selling"]):
        add("get_top_products")
    if any(w in message_lower for w in ["trend", "over time", "daily", "growth"]):
        add("get_revenue_trend")
    if any(w in message_lower for w in ["recent", "transactions", "last sales"]):
        add("get_recent_transactions")

    # Customer tools
    if any(w in message_lower for w in ["customer", "client", "who buys", "top buyer"]):
        add("get_customer_insights")

    # Inventory tools
    if any(w in message_lower for w in ["low stock", "reorder", "running out", "alert"]):
        add("get_inventory_alerts")

    # Financial tools
    if any(w in message_lower for w in ["profit", "loss", "p&l", "earnings"]):
        add("get_profit_loss")
    if any(w in message_lower for w in ["expense", "spending", "costs"]):
        add("get_expenses_by_category")
    if any(w in message_lower for w in ["receivable", "unpaid", "outstanding", "owed"]):
        add("get_accounts_receivable")
    if any(
        w in message_lower
        for w in [
            "payable",
            "payables",
            "accounts payable",
            "vendor bill",
            "supplier bill",
            "owe vendor",
            "owe supplier",
            "owe the vendor",
            "bill due",
            "bills due",
            "unpaid bills",
        ]
    ):
        add("get_accounts_payable")

    # Store tools
    if any(w in message_lower for w in ["store", "shops", "branches", "outlets"]):
        add("list_stores")
    if any(w in message_lower for w in ["store analytics", "store performance", "store sales"]):
        add("get_store_analytics")

    # Employee tools
    if any(w in message_lower for w in ["employee", "staff", "team", "worker", "cashier"]):
        add("get_employees")

    # Expense list
    if any(w in message_lower for w in ["expense list", "recent expenses", "expenses detail"]):
        add("get_expenses_list")

    # Advanced analytics
    if any(w in message_lower for w in ["performance", "margin", "slow-moving", "dead stock", "profit margin"]):
        add("get_product_performance")
    if any(w in message_lower for w in ["customer detail", "customer history", "purchase history", "bought"]):
        add("get_customer_detail")
    if any(w in message_lower for w in ["inventory value", "stock value", "total inventory"]):
        add("get_inventory_value")
    if any(
        w in message_lower
        for w in [
            "payment method",
            "payment methods",
            "how do people pay",
            "payment breakdown",
            "payment mix",
            "paid by",
        ]
    ):
        add("get_payment_methods")
    if any(w in message_lower for w in ["category", "categories", "product type"]):
        add("get_category_performance")
    if any(w in message_lower for w in ["today", "daily", "how am i doing", "summary"]):
        add("get_daily_summary")
    if any(w in message_lower for w in ["compare", "vs", "better", "worse", "last month", "last week"]):
        add("get_comparison")
    if any(w in message_lower for w in ["tax", "vat", "tax summary"]):
        add("get_tax_summary")
    if any(w in message_lower for w in ["void", "refund", "voids"]):
        add("get_void_summary")
    if any(w in message_lower for w in ["health", "score", "overall", "how is business"]):
        add("get_business_health")

    # Payment status / pending / settlement / receipts
    payment_status_cues = [
        "payment status",
        "payment confirmed",
        "confirm payment",
        "did they pay",
        "did he pay",
        "did she pay",
        "did the customer pay",
        "has the customer paid",
        "has he paid",
        "has she paid",
        "payment received",
        "transfer status",
        "transfer confirmed",
        "transaction status",
        "tx_ref",
        "txn_ref",
        "payment reference",
        "check payment",
        "payment for",
    ]
    if any(w in message_lower for w in payment_status_cues) or re.search(
        r"\b(?:did|was|is|has|have)\b.+\b(?:pay|paid|payment)\b", message_lower
    ):
        add("get_payment_status")
    if any(
        w in message_lower
        for w in [
            "pending payment",
            "pending payments",
            "unpaid payment",
            "failed payment",
            "abandoned payment",
            "payment pending",
            "payment failed",
            "not completed",
            "awaiting payment",
            "stuck payment",
            "payment not received",
        ]
    ):
        add("get_pending_payments")
    if any(
        w in message_lower
        for w in [
            "settlement",
            "settlements",
            "settled",
            "unsettled",
            "payout",
            "payouts",
            "gateway balance",
            "money in bank",
            "has the money landed",
            "what has been settled",
        ]
    ):
        add("get_settlement_summary")
    if "receipt" in message_lower:
        add("get_receipt")
    if _extract_sale_number(message) or any(
        w in message_lower
        for w in [
            "sale detail",
            "sale details",
            "details of sale",
            "details of the sale",
            "show me sale",
            "show the sale",
            "invoice detail",
            "invoice details",
            "what was sold",
            "sale breakdown",
        ]
    ):
        add("get_sale_detail")

    # Stock transfers vs money transfers
    if "transfer" in message_lower:
        payment_cues = [
            "payment",
            "paid",
            "pay ",
            "money",
            "bank",
            "settle",
            "settled",
            "confirm",
            "received",
            "reference",
            "gateway",
            "tx",
            "account",
        ]
        stock_cues = [
            "stock",
            "store",
            "branch",
            "inventory",
            "request",
            "supply",
            "supplying",
            "product",
            "warehouse",
        ]
        has_payment_cue = any(w in message_lower for w in payment_cues)
        has_stock_cue = any(w in message_lower for w in stock_cues)
        if has_stock_cue or not has_payment_cue:
            add("get_transfer_requests")
        if has_payment_cue:
            add("get_payment_status")

    # Web tools
    if any(w in message_lower for w in ["price", "cost", "compare", "how much does"]):
        add("compare_product_prices")
    if any(w in message_lower for w in ["what is", "tell me about", "explain"]):
        add("search_product_info")

    return tools


_SAFE_TOOL_ERRORS = {
    "Product not found",
    "Sale not found",
    "Provide a sale number or sale id",
}


def _sanitize_tool_result(result: str) -> str:
    """Hide internal error details before they reach the LLM or client."""
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return result
    if isinstance(data, dict) and data.get("error"):
        message = str(data["error"])
        if message not in _SAFE_TOOL_ERRORS:
            data["error"] = "Something went wrong"
        return json.dumps(data)
    return result


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

    provided = tool_args or {}
    message_lower = message.lower()
    results: list[dict[str, Any]] = []
    seen: set[str] = set()

    for tool_name in tools:
        if tool_name in seen:
            continue
        seen.add(tool_name)
        call_arguments: dict[str, Any] = {}

        try:
            if tool_name == "compare_product_prices":
                product_name = provided.get("product_name") or _extract_product_name(message)
                call_arguments = {"product_name": product_name}
                result_str = await compare_product_prices(product_name)
                result_summary = f"Found price comparison data for '{product_name}'"
            elif tool_name == "search_product_info":
                product_name = provided.get("product_name") or _extract_product_name(message)
                call_arguments = {"product_name": product_name}
                result_str = await search_product_info(product_name)
                result_summary = f"Found Wikipedia info for '{product_name}'"
            elif tool_name in TOOL_REGISTRY:
                func = TOOL_REGISTRY[tool_name]
                kwargs: dict[str, Any] = {"session": session, "tenant_id": tenant_id}

                if tool_name == "search_products":
                    kwargs["query"] = provided.get("query") or _extract_search_query(message)
                elif tool_name == "get_product_details":
                    product_id = provided.get("product_id")
                    if product_id:
                        kwargs["product_id"] = product_id
                    else:
                        continue
                elif tool_name == "check_stock":
                    if provided.get("product_id"):
                        kwargs["product_id"] = provided["product_id"]
                    elif provided.get("product_name"):
                        kwargs["product_name"] = provided["product_name"]
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
                elif tool_name == "list_stores":
                    pass
                elif tool_name == "get_store_analytics":
                    if provided.get("store_id"):
                        kwargs["store_id"] = provided["store_id"]
                elif tool_name == "get_employees":
                    pass
                elif tool_name == "get_expenses_list":
                    kwargs["limit"] = 20
                elif tool_name == "get_product_performance":
                    kwargs["period"] = _extract_period(message)
                    kwargs["limit"] = 10
                elif tool_name == "get_customer_detail":
                    name = (
                        provided.get("customer_name")
                        or _extract_customer_name(message)
                        or _extract_search_query(message)
                    )
                    if name:
                        kwargs["customer_name"] = name
                    else:
                        continue
                elif tool_name == "get_inventory_value":
                    pass
                elif tool_name == "get_payment_methods":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_category_performance":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_daily_summary":
                    pass
                elif tool_name == "get_comparison":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_tax_summary":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_void_summary":
                    kwargs["period"] = _extract_period(message)
                elif tool_name == "get_business_health":
                    pass
                elif tool_name == "get_expiring_products":
                    kwargs["days"] = provided.get("days") or _extract_days(message, 30)
                    kwargs["include_expired"] = bool(provided.get("include_expired")) or any(
                        word in message_lower
                        for word in ["already expired", "include expired", "expired products"]
                    )
                    store_id = provided.get("store_id") or _extract_store_id(message)
                    if store_id:
                        kwargs["store_id"] = store_id
                elif tool_name == "get_stock_history":
                    if provided.get("product_id"):
                        kwargs["product_id"] = provided["product_id"]
                    else:
                        product_name = provided.get("product_name") or _extract_product_phrase(message)
                        if product_name:
                            kwargs["product_name"] = product_name
                    store_id = provided.get("store_id") or _extract_store_id(message)
                    if store_id:
                        kwargs["store_id"] = store_id
                    if provided.get("movement_type"):
                        kwargs["movement_type"] = provided["movement_type"]
                    kwargs["days"] = provided.get("days") or _extract_days(message, 30)
                    kwargs["limit"] = 20
                elif tool_name == "get_transfer_requests":
                    kwargs["status"] = provided.get("status") or _extract_transfer_status(message)
                    store_id = provided.get("store_id") or _extract_store_id(message)
                    if store_id:
                        kwargs["store_id"] = store_id
                    kwargs["days"] = provided.get("days") or _extract_days(message, 30)
                elif tool_name == "get_payment_status":
                    sale_number = provided.get("sale_number") or _extract_sale_number(message)
                    reference = provided.get("reference") or _extract_reference(message)
                    customer_name = provided.get("customer_name") or _extract_customer_name(message)
                    if sale_number:
                        kwargs["sale_number"] = sale_number
                    if reference:
                        kwargs["reference"] = reference
                    if customer_name:
                        kwargs["customer_name"] = customer_name
                    kwargs["limit"] = 10
                elif tool_name == "get_pending_payments":
                    age_minutes = 30
                    age_match = re.search(
                        r"(\d+)\s*(minute|minutes|hour|hours)", message, re.IGNORECASE
                    )
                    if age_match:
                        value = int(age_match.group(1))
                        age_minutes = value * 60 if age_match.group(2).lower().startswith("hour") else value
                    elif "today" in message_lower:
                        age_minutes = 1440
                    kwargs["age_minutes"] = provided.get("age_minutes") or age_minutes
                elif tool_name == "get_settlement_summary":
                    period = provided.get("period") or _extract_period(message)
                    kwargs["period"] = period if period in {"week", "month", "year"} else "week"
                elif tool_name == "get_receipt":
                    receipt_number = provided.get("receipt_number") or _extract_receipt_number(message)
                    sale_number = provided.get("sale_number") or _extract_sale_number(message)
                    customer_name = provided.get("customer_name") or _extract_customer_name(message)
                    if receipt_number:
                        kwargs["receipt_number"] = receipt_number
                    if sale_number:
                        kwargs["sale_number"] = sale_number
                    if customer_name:
                        kwargs["customer_name"] = customer_name
                elif tool_name == "get_accounts_payable":
                    status = provided.get("status") or _extract_payable_status(message)
                    if status:
                        kwargs["status"] = status
                    vendor_name = provided.get("vendor_name")
                    if not vendor_name:
                        vendor_match = re.search(
                            r"\b(?:vendor|supplier)\s+([A-Z][\w&'-]*(?:\s+[A-Z][\w&'-]*)?)",
                            message,
                        )
                        if vendor_match:
                            vendor_name = vendor_match.group(1)
                    if vendor_name:
                        kwargs["vendor_name"] = vendor_name
                elif tool_name == "get_sale_detail":
                    sale_number = provided.get("sale_number") or _extract_sale_number(message)
                    sale_id = provided.get("sale_id")
                    if not sale_id and not sale_number:
                        uuid_match = _UUID_RE.search(message)
                        if uuid_match:
                            sale_id = uuid_match.group(0)
                    if sale_number:
                        kwargs["sale_number"] = sale_number
                    elif sale_id:
                        kwargs["sale_id"] = sale_id

                call_arguments = {
                    key: value
                    for key, value in kwargs.items()
                    if key not in {"session", "tenant_id"}
                }
                result_str = await func(**kwargs)
                result_summary = f"Retrieved data from {tool_name}"
            else:
                continue

            results.append(
                {
                    "tool": tool_name,
                    "arguments": call_arguments,
                    "result": _sanitize_tool_result(result_str),
                    "result_summary": result_summary,
                }
            )
        except Exception as e:
            logger.warning("Tool %s failed: %s", tool_name, e)
            results.append(
                {
                    "tool": tool_name,
                    "arguments": call_arguments,
                    "result": json.dumps({"error": "Something went wrong"}),
                    "result_summary": "Data unavailable",
                }
            )

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


async def plan_tools(message: str) -> list[str]:
    """Ask the LLM to pick tools when keyword routing finds nothing.

    The planner only selects tool names; arguments are still resolved
    deterministically by ``invoke_tools``.
    """
    try:
        import anyio

        from app.ai.llm import create_provider
        from app.ai.tools import TOOL_DESCRIPTIONS, TOOL_REGISTRY
        from app.ai.web import compare_product_prices, search_product_info

        catalogue = "\n".join(
            f"- {name}: {description}"
            for name, description in TOOL_DESCRIPTIONS.items()
        )
        prompt = [
            {
                "role": "system",
                "content": (
                    "You select read-only business tools for a retail assistant. "
                    "Pick at most 3 tools that can answer the user's question. "
                    "Respond with JSON only in the form {\"tools\": [\"tool_name\"]}. "
                    "Never invent tool names."
                ),
            },
            {
                "role": "user",
                "content": f"Question: {message}\n\nAvailable tools:\n{catalogue}",
            },
        ]
        provider = create_provider()
        raw = await anyio.to_thread.run_sync(lambda: provider.generate(prompt))
        text_response = raw if isinstance(raw, str) else str(raw)
        start = text_response.find("{")
        end = text_response.rfind("}")
        if start == -1 or end <= start:
            return []
        payload = json.loads(text_response[start : end + 1])
        selected = payload.get("tools") or payload.get("tool") or []
        if isinstance(selected, str):
            selected = [selected]
        allowed = set(TOOL_REGISTRY) | {
            compare_product_prices.__name__,
            search_product_info.__name__,
        }
        return [name for name in selected if isinstance(name, str) and name in allowed][:3]
    except Exception as e:
        logger.warning("Tool planner failed: %s", e)
        return []


_SALE_NUMBER_RE = re.compile(r"\bSL-\d{8}-[A-Fa-f0-9]{8}\b", re.IGNORECASE)
_RECEIPT_NUMBER_RE = re.compile(r"\bRCP-\d{8}-[A-Fa-f0-9]{8}\b", re.IGNORECASE)
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_REFERENCE_RE = re.compile(
    r"\b(?:tx[_ -]?ref|txn[_ -]?ref|reference|ref)[:\s#-]*"
    r"([A-Za-z0-9][A-Za-z0-9._-]{3,})\b",
    re.IGNORECASE,
)


def _extract_sale_number(message: str) -> str | None:
    match = _SALE_NUMBER_RE.search(message)
    return match.group(0).upper() if match else None


def _extract_receipt_number(message: str) -> str | None:
    match = _RECEIPT_NUMBER_RE.search(message)
    return match.group(0).upper() if match else None


def _extract_store_id(message: str) -> str | None:
    match = _UUID_RE.search(message)
    return match.group(0) if match else None


def _extract_reference(message: str) -> str | None:
    match = _REFERENCE_RE.search(message)
    if not match:
        return None
    value = match.group(1).strip(".,?!")
    if value.lower() in {"number", "for", "the", "please", "status"}:
        return None
    return value


def _extract_days(message: str, default: int = 30) -> int:
    match = re.search(r"(\d+)\s*days?", message, re.IGNORECASE)
    if match:
        return max(1, min(3650, int(match.group(1))))
    message_lower = message.lower()
    if "today" in message_lower:
        return 1
    if "last week" in message_lower or "past week" in message_lower:
        return 7
    if "last month" in message_lower or "past month" in message_lower:
        return 30
    if "last year" in message_lower or "past year" in message_lower:
        return 365
    return default


def _extract_transfer_status(message: str) -> str:
    message_lower = message.lower()
    for status in ("pending", "approved", "rejected", "fulfilled"):
        if status in message_lower:
            return status
    if "all" in message_lower or "every" in message_lower:
        return "all"
    return "pending"


def _extract_payable_status(message: str) -> str | None:
    message_lower = message.lower()
    for status in ("overdue", "partial", "paid", "pending"):
        if status in message_lower:
            return status
    return None


def _extract_customer_name(message: str) -> str | None:
    patterns = [
        r"\bdid\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)\s+pay\b",
        r"\bpayment\s+from\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)",
        r"\bcustomer\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)",
        r"\bpaid\s+by\s+([A-Z][\w'-]+(?:\s+[A-Z][\w'-]+)?)",
    ]
    for pattern in patterns:
        match = re.search(pattern, message)
        if match:
            return match.group(1)
    return None


def _extract_product_phrase(message: str) -> str | None:
    match = re.search(
        r"\b(?:for|of|on)\s+(?:the\s+)?([A-Za-z0-9][\w\s&'-]{1,60}?)"
        r"(?=\s+(?:in|at|from|over|during|last|this|today|yesterday|"
        r"between|across|and|with)\b|[?.!,]|$)",
        message,
        re.IGNORECASE,
    )
    if not match:
        return None
    phrase = match.group(1).strip()
    phrase = re.split(
        r"\b(?:change|changed|changes|drop|dropped|increase|increased|"
        r"decrease|decreased|move|moved|history|adjustment|adjustments)\b",
        phrase,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    if not phrase or len(phrase.split()) > 5:
        return None
    return phrase


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
