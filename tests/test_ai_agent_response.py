"""Tests for parse_agent_response content normalization (app/ai/agent.py)."""

import json
from types import SimpleNamespace

from app.ai.agent import parse_agent_response


class TestParseAgentResponse:
    def test_string_content_passthrough(self):
        resp = SimpleNamespace(content="Hello, how can I help?")
        result = parse_agent_response(resp)
        assert result == {"answer": "Hello, how can I help?", "recommendations": []}

    def test_json_envelope_string(self):
        payload = json.dumps({"answer": "You made 5 sales", "recommendations": []})
        resp = SimpleNamespace(content=payload)
        result = parse_agent_response(resp)
        assert result["answer"] == "You made 5 sales"

    def test_langchain_block_list_joined(self):
        resp = SimpleNamespace(
            content=[
                {"type": "text", "text": "Part one. "},
                {"type": "text", "text": "Part two."},
            ]
        )
        result = parse_agent_response(resp)
        assert result["answer"] == "Part one. Part two."
        assert result["recommendations"] == []

    def test_langchain_block_list_with_non_text_blocks(self):
        resp = SimpleNamespace(
            content=[
                {"type": "tool_use", "id": "x"},
                {"type": "text", "text": "The answer"},
            ]
        )
        result = parse_agent_response(resp)
        assert result["answer"] == "The answer"

    def test_block_list_with_json_envelope(self):
        resp = SimpleNamespace(
            content=[{"type": "text", "text": json.dumps({"answer": "Hi", "recommendations": ["a"]})}]
        )
        result = parse_agent_response(resp)
        assert result == {"answer": "Hi", "recommendations": ["a"]}

    def test_empty_block_list_falls_back_to_json(self):
        resp = SimpleNamespace(content=[])
        result = parse_agent_response(resp)
        assert result["answer"] == "[]"

    def test_non_string_content_coerced(self):
        resp = SimpleNamespace(content=42)
        result = parse_agent_response(resp)
        assert result["answer"] == "42"
