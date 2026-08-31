"""AI assistant HTTP endpoints.

Provides REST API for the AI assistant service: chat (sync/streaming),
conversation listing, detail, and deletion.

Security: All endpoints require authentication, permission, and tenant isolation.
The AI service is STRICTLY READ-ONLY -- no data is modified.
"""

import json
import logging
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from app.ai.schemas import (
    ChatRequest,
    ChatResponse,
    ConversationListResult,
    ConversationListItem,
    ConversationResult,
    ConversationMessage,
)
from app.core.dependencies import TenantDep, require_permission
from app.core.responses import DataResponse, DataMessageResponse, ok

logger = logging.getLogger("app.ai")
router = APIRouter(prefix="/ai", tags=["AI"])


def _ai_db():
    from app.common.bridge import _get_sdb
    return _get_sdb("ai")


async def _prepare_chat(payload: ChatRequest, tenant_id: UUID, user_id: UUID):
    from app.ai.models import Conversation, ConversationMessage
    from app.ai.repository import (
        create_conversation,
        create_conversation_message,
        get_conversation,
        get_conversation_messages,
        update_conversation_title,
    )
    from app.ai.service import invoke_tools, select_tools
    from app.ai.agent import build_agent_prompt

    sdb = _ai_db()
    async with sdb.session() as session:
        conversation_id = payload.conversation_id
        if conversation_id:
            conv = await get_conversation(session, conversation_id)
            if not conv or str(conv.tenant_id) != str(tenant_id):
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            conversation_id = uuid4()
            title = payload.message[:200]
            conv = Conversation(
                id=conversation_id,
                tenant_id=tenant_id,
                user_id=user_id,
                title=title,
            )
            await create_conversation(session, conv)
            await update_conversation_title(session, conversation_id, title)

        user_msg = ConversationMessage(
            id=uuid4(),
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            role="user",
            content=payload.message,
        )
        await create_conversation_message(session, user_msg)

        history_msgs = await get_conversation_messages(session, conversation_id, limit=20)
        history = [{"role": m.role, "content": m.content} for m in history_msgs]

        tools = select_tools(payload.message)
        tool_results = await invoke_tools(tools, session, tenant_id, payload.message)

        prompt = build_agent_prompt(history, tool_results)

        return conversation_id, prompt, tool_results


async def _save_assistant_message(
    conversation_id: UUID, tenant_id: UUID, response: ChatResponse
):
    from app.ai.models import ConversationMessage as ConvMsgModel
    from app.ai.repository import create_conversation_message

    sdb = _ai_db()
    async with sdb.session() as session:
        assistant_msg = ConvMsgModel(
            id=uuid4(),
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            role="assistant",
            content=response.answer,
            tool_calls=[tc.model_dump() for tc in response.tool_calls] if response.tool_calls else None,
        )
        await create_conversation_message(session, assistant_msg)
        await session.commit()


def _sse_event(event_type: str, data: dict | str) -> str:
    payload = json.dumps(data) if isinstance(data, dict) else data
    return f"event: {event_type}\ndata: {payload}\n\n"


@router.post(
    "/chat",
    response_model=DataResponse[ChatResponse],
    dependencies=[Depends(require_permission("ai:read"))],
)
async def chat(payload: ChatRequest, ctx: TenantDep):
    tenant_id = UUID(ctx.user.business_id)
    user_id = UUID(ctx.user.user_id)

    conversation_id, prompt, tool_results = await _prepare_chat(payload, tenant_id, user_id)

    if payload.stream:
        return StreamingResponse(
            _stream_response(conversation_id, prompt, tool_results, tenant_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    try:
        import anyio
        from app.ai.llm import create_provider
        from app.ai.agent import parse_agent_response
        from app.ai.service import build_response

        provider = create_provider()
        llm_response = await anyio.to_thread.run_sync(
            lambda: provider.generate(prompt)
        )
        parsed = parse_agent_response(llm_response)
    except ValueError as e:
        parsed = {
            "answer": (
                f"I found the following information:\n\n"
                + "\n".join(f"**{r['tool']}**: {r['result_summary']}" for r in tool_results)
                + f"\n\n*Note: AI response generation unavailable ({e}). Showing raw tool results.*"
            ),
            "recommendations": [],
        }
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
        parsed = {
            "answer": f"I encountered an error processing your request: {e}",
            "recommendations": [],
        }

    response = build_response(tool_results, parsed, conversation_id)
    await _save_assistant_message(conversation_id, tenant_id, response)
    return ok(response)


async def _stream_response(
    conversation_id: UUID, prompt: list[dict[str, str]], tool_results: list[dict], tenant_id: UUID
):
    full_answer = []

    try:
        import anyio
        from app.ai.llm import create_provider

        provider = create_provider()

        def _sync_stream():
            return list(provider.stream(prompt))

        chunks = await anyio.to_thread.run_sync(_sync_stream)
        for chunk in chunks:
            full_answer.append(chunk)
            yield _sse_event("token", {"text": chunk})

    except ValueError as e:
        fallback = f"AI response generation unavailable ({e}). Showing raw tool results instead."
        full_answer.append(fallback)
        yield _sse_event("token", {"text": fallback})

    except Exception as e:
        logger.warning("LLM stream failed: %s", e)
        error_msg = f"Error: {e}"
        full_answer.append(error_msg)
        yield _sse_event("token", {"text": error_msg})

    answer = "".join(full_answer)
    from app.ai.service import build_response

    response = build_response(tool_results, {"answer": answer, "recommendations": []}, conversation_id)
    await _save_assistant_message(conversation_id, tenant_id, response)

    yield _sse_event("metadata", {"data": response.model_dump(mode="json")})
    yield _sse_event("done", {"conversation_id": str(conversation_id)})


@router.get(
    "/conversations",
    response_model=DataResponse[ConversationListResult],
    dependencies=[Depends(require_permission("ai:read"))],
)
async def list_conversations(ctx: TenantDep, page: int = 1, page_size: int = 20):
    from app.ai.repository import list_conversations as repo_list

    sdb = _ai_db()
    async with sdb.session() as session:
        items = await repo_list(
            session,
            tenant_id=UUID(ctx.user.business_id),
            limit=page_size,
            offset=(page - 1) * page_size,
        )
        result = ConversationListResult(
            items=[
                ConversationListItem(
                    id=c.id,
                    title=c.title,
                    created_at=c.created_at,
                    updated_at=c.updated_at,
                )
                for c in items
            ],
            page=page,
            page_size=page_size,
        )
        return ok(result)


@router.get(
    "/conversations/{conversation_id}",
    response_model=DataResponse[ConversationResult],
    dependencies=[Depends(require_permission("ai:read"))],
)
async def get_conversation_detail(conversation_id: str, ctx: TenantDep):
    from app.ai.repository import (
        get_conversation as repo_get,
        get_conversation_messages as repo_msgs,
    )

    sdb = _ai_db()
    async with sdb.session() as session:
        conv = await repo_get(session, UUID(conversation_id))
        if not conv or str(conv.tenant_id) != ctx.user.business_id:
            raise HTTPException(status_code=404, detail="Conversation not found")

        messages = await repo_msgs(session, UUID(conversation_id))
        result = ConversationResult(
            conversation_id=conv.id,
            messages=[
                ConversationMessage(
                    role=m.role,
                    content=m.content,
                    tool_calls=m.tool_calls or [],
                    created_at=m.created_at,
                )
                for m in messages
            ],
            created_at=conv.created_at,
        )
        return ok(result)


@router.delete(
    "/conversations/{conversation_id}",
    response_model=DataMessageResponse,
    dependencies=[Depends(require_permission("ai:read"))],
)
async def delete_conversation_endpoint(conversation_id: str, ctx: TenantDep):
    from app.ai.repository import (
        get_conversation as repo_get,
        delete_conversation as repo_delete,
    )

    sdb = _ai_db()
    async with sdb.session() as session:
        conv = await repo_get(session, UUID(conversation_id))
        if not conv or str(conv.tenant_id) != ctx.user.business_id:
            raise HTTPException(status_code=404, detail="Conversation not found")

        await repo_delete(session, UUID(conversation_id))
        await session.commit()
        return ok(None, message="Conversation deleted")
