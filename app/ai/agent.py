"""AI Agent orchestrator using LangChain + pluggable LLM provider.

This module sets up the agent that orchestrates tool calls to answer user
questions about their business data. The agent uses a pluggable LLM provider
(default: Groq) and has access to 35 read-only tools + 2 web search tools.

Agent Constraints:
    - STRICTLY READ-ONLY: No INSERT, UPDATE, or DELETE operations.
    - Multi-tenant: All tool queries are filtered by tenant_id.
    - Recommendations only: The agent suggests actions but never executes them.
    - The frontend calls separate APIs to execute approved actions.

Abbreviations Used in This Module
----------------------------------
- LLM: Large Language Model -- the AI model that generates responses.
- RAG: Retrieval Augmented Generation -- using retrieved data to inform responses.
- AR: Accounts Receivable -- money owed TO the business by customers.
- COA: Chart of Accounts -- the complete list of all financial accounts.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
"""

import json
import logging
from typing import Any

logger = logging.getLogger("app.ai.agent")

SYSTEM_PROMPT = """You are StoreFlow AI, an intelligent business assistant for Nigerian small businesses.

You have access to 37 read-only tools for querying business data (products, sales, inventory, stock history, expiring stock, transfers, customers, stores, employees, expenses, payments, settlement, receipts, payables, tax, voids, comparisons, health scores) and web search for price comparisons.

RULES:
1. READ-ONLY only -- never suggest data has been created/updated.
2. All amounts in Nigerian Naira (NGN/₦).
3. Never mention tool names, internal systems, database queries, or raw error messages. Speak only about the business data.
4. If the data is missing or a lookup failed, say the information is not available right now and offer to try again. Never invent numbers.
5. Only say a payment or transfer is confirmed when the data shows confirmed=true or payment status "completed". A virtual account being issued is not proof of payment.
6. Be concise. Use bullet points for lists.
7. End with actionable recommendations when appropriate.
8. Use markdown tables for structured data when presenting multiple items.
9. When comparing periods, highlight positive and negative changes clearly.
10. For health scores, explain each factor and give improvement suggestions.

Nigerian Business Context:
- Payment: cash, card, transfer, USSD
- VAT: 7.5%
- Common products: electronics, fashion, groceries, beauty
"""


def get_system_prompt() -> str:
    """Return the system prompt for the AI agent."""
    return SYSTEM_PROMPT


def build_agent_prompt(
    messages: list[dict[str, str]],
    tool_results: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build the full prompt including system message, history, and tool results.

    Args:
        messages: Conversation history (list of {"role": "user"/"assistant", "content": "..."}).
        tool_results: Results from tool invocations to include in context.

    Returns:
        A list of message dicts ready for the LLM.
    """
    prompt_messages: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]

    for msg in messages[-10:]:
        prompt_messages.append({"role": msg["role"], "content": msg["content"]})

    if tool_results:
        context = "\n\n## Tool Results:\n"
        for result in tool_results:
            context += f"\n### {result['tool']}({json.dumps(result['arguments'])}):\n"
            context += f"{result['result']}\n"
        prompt_messages.append({"role": "system", "content": context})

    return prompt_messages


def parse_agent_response(response: Any) -> dict[str, Any]:
    """Parse the LLM response into a structured format.

    Args:
        response: The raw response from the LLM provider.

    Returns:
        A dict with 'answer' and optional 'recommendations'.
    """
    content = response.content if hasattr(response, "content") else str(response)

    # LangChain providers may return content as a list of typed blocks
    # (e.g. Gemini: [{"type": "text", "text": "..."}]). Normalize to text.
    if isinstance(content, list):
        text = "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
        content = text or json.dumps(content)
    elif not isinstance(content, str):
        content = str(content)

    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        parsed = None
    if not isinstance(parsed, dict):
        return {
            "answer": content,
            "recommendations": [],
        }
    return {
        "answer": parsed.get("answer", content),
        "recommendations": parsed.get("recommendations", []),
    }
