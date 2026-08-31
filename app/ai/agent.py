"""AI Agent orchestrator using LangChain + pluggable LLM provider.

This module sets up the agent that orchestrates tool calls to answer user
questions about their business data. The agent uses a pluggable LLM provider
(default: Groq) and has access to 14 read-only tools + 2 web search tools.

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

You have access to 14 read-only tools for querying business data (products, sales, inventory, customers, finances) and web search for price comparisons.

RULES:
1. READ-ONLY only -- never suggest data has been created/updated.
2. All amounts in Nigerian Naira (NGN/₦).
3. Cite which tools you used.
4. Be concise. Use bullet points for lists.
5. End with actionable recommendations when appropriate.

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

    try:
        parsed = json.loads(content)
        return {
            "answer": parsed.get("answer", content),
            "recommendations": parsed.get("recommendations", []),
        }
    except (json.JSONDecodeError, TypeError):
        return {
            "answer": content,
            "recommendations": [],
        }
