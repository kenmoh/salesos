"""Pydantic schemas for the AI assistant service.

This module defines the input (Request) and output (Response) schemas for the
agentic AI assistant. The assistant answers natural language questions about
business data using 14 read-only tools and web search.

Abbreviations Used in This Module
----------------------------------
- AR: Accounts Receivable -- money owed TO the business by customers.
- COA: Chart of Accounts -- the complete list of all financial accounts.
- UUID: Universally Unique Identifier -- a 128-bit identifier for primary keys.
- NGN: Nigerian Naira -- the base currency for all financial amounts.
- LLM: Large Language Model -- the AI model that generates responses.
- SSE: Server-Sent Events -- a protocol for streaming responses to the client.
- SQL: Structured Query Language -- the language used to query databases.

Response Format:
    The AI assistant returns a structured response with:
    - answer: The natural language answer to the user's question.
    - tool_calls: List of tools that were invoked (for transparency).
    - recommendations: Optional list of suggested actions (NOT auto-executed).
    - confidence: How confident the AI is in its answer (0.0-1.0).

Action Policy:
    The AI NEVER executes write operations. It only:
    1. Reads data via the 14 read-only tools.
    2. Searches the web for price comparisons.
    3. Returns recommendations that the user must approve.
    4. The frontend calls separate APIs to execute approved actions.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


# --- Request Schemas -------------------------------------------------------------------


class ChatRequest(BaseModel):
    """Request to send a message to the AI assistant.

    The assistant processes the message, invokes relevant read-only tools,
    and returns a structured response with an answer and optional recommendations.

    Attributes:
        message: The user's natural language question or request.
        conversation_id: Optional UUID to continue an existing conversation.
            If None, a new conversation is started.
        stream: Whether to stream the response via SSE (default: False).
    """

    message: str = Field(..., min_length=1, max_length=2000)
    conversation_id: UUID | None = None
    stream: bool = False


class ConversationHistoryRequest(BaseModel):
    """Request to retrieve conversation history.

    Attributes:
        conversation_id: The UUID of the conversation to retrieve.
        limit: Maximum number of messages to return (default: 50).
    """

    conversation_id: UUID
    limit: int = Field(default=50, ge=1, le=200)


# --- Response Schemas ------------------------------------------------------------------


class ToolCall(BaseModel):
    """Record of a tool that was invoked during the AI's reasoning.

    This provides transparency into which tools the AI used to answer
    the question. All tools are READ-ONLY -- no data is modified.

    Attributes:
        tool: The name of the tool that was called.
        arguments: The arguments passed to the tool.
        result_summary: A brief summary of what the tool returned.
    """

    tool: str
    arguments: dict = Field(default_factory=dict)
    result_summary: str = ""


class Recommendation(BaseModel):
    """A suggested action the AI recommends the user take.

    Recommendations are suggestions ONLY -- they are never auto-executed.
    The user must approve the recommendation, and the frontend calls
    the appropriate API endpoint to execute it.

    Attributes:
        action: The type of action recommended (e.g., "reorder_stock", "send_reminder").
        description: Human-readable description of what this action would do.
        target_id: Optional UUID of the entity this action relates to.
        target_type: Optional type of entity (e.g., "product", "invoice", "customer").
        api_endpoint: The API endpoint the frontend should call to execute this action.
        api_method: The HTTP method for the API endpoint (default: POST).
        api_body: The request body to send to the API endpoint.
    """

    action: str
    description: str
    target_id: str | None = None
    target_type: str | None = None
    api_endpoint: str | None = None
    api_method: str = "POST"
    api_body: dict = Field(default_factory=dict)


class ChatResponse(BaseModel):
    """Response from the AI assistant.

    Contains the natural language answer, records of tools invoked,
    and optional recommendations for the user to approve.

    Attributes:
        conversation_id: UUID of the conversation (new or existing).
        answer: The AI's natural language answer to the user's question.
        tool_calls: List of tools that were invoked (for transparency).
        recommendations: Optional list of suggested actions.
        confidence: How confident the AI is in its answer (0.0-1.0).
        created_at: Timestamp when this response was generated (UTC).
    """

    conversation_id: UUID
    answer: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    recommendations: list[Recommendation] = Field(default_factory=list)
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)
    created_at: datetime | None = None


class ConversationMessage(BaseModel):
    """A single message in a conversation.

    Attributes:
        role: The role of the message sender ("user" or "assistant").
        content: The text content of the message.
        tool_calls: Tools invoked by the assistant (if any).
        created_at: Timestamp when this message was sent (UTC).
    """

    role: str
    content: str
    tool_calls: list[ToolCall] = Field(default_factory=list)
    created_at: datetime | None = None


class ConversationResult(BaseModel):
    """Full conversation with message history.

    Attributes:
        conversation_id: UUID of the conversation.
        messages: List of messages in chronological order.
        created_at: Timestamp when the conversation was started (UTC).
    """

    conversation_id: UUID
    messages: list[ConversationMessage] = Field(default_factory=list)
    created_at: datetime | None = None


class ConversationListItem(BaseModel):
    """A single conversation in a list response.

    Attributes:
        id: UUID of the conversation.
        title: Auto-generated title from the first user message.
        created_at: Timestamp when the conversation was started (UTC).
        updated_at: Timestamp when the conversation was last updated (UTC).
    """

    id: UUID
    title: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ConversationListResult(BaseModel):
    """Paginated list of conversations.

    Attributes:
        items: List of conversation items.
        page: Current page number (1-indexed).
        page_size: Number of items per page.
    """

    items: list[ConversationListItem] = Field(default_factory=list)
    page: int = 1
    page_size: int = 20
