"""Provider adapters parse real SDK response types and round-trip history (no network, no key)."""
from types import SimpleNamespace

from anthropic.types import Message
from openai.types.chat import ChatCompletion

from worker.llm import AnthropicLLM, OpenAICompatLLM

TOOLS = [{"name": "finish", "description": "d", "parameters": {"type": "object", "properties": {}}}]


def _stub(adapter_cls, response, captured):
    obj = adapter_cls.__new__(adapter_cls)
    create = lambda **kw: captured.update(kw) or response
    obj.model = "m"
    obj.client = SimpleNamespace(messages=SimpleNamespace(create=create),
                                 chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    return obj


def test_anthropic_turn_and_replay():
    msg = Message.model_validate({
        "id": "m1", "type": "message", "role": "assistant", "model": "m", "stop_reason": "tool_use",
        "content": [{"type": "text", "text": "Checking."},
                    {"type": "tool_use", "id": "tu1", "name": "finish", "input": {"summary": "s"}}],
        "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 90}})
    cap = {}
    turn = _stub(AnthropicLLM, msg, cap).complete("sys", [{"role": "user", "content": "go"}], TOOLS)
    assert turn.text == "Checking." and turn.calls[0].name == "finish" and turn.usage == {"in": 100, "out": 5}
    assert cap["tool_choice"]["disable_parallel_tool_use"] is True
    history = [{"role": "user", "content": "go"},
               {"role": "assistant", "text": turn.text, "calls": [{"id": "tu1", "name": "finish", "args": {}}], "raw": turn.raw},
               {"role": "tool", "results": [{"id": "tu1", "content": "ok", "is_error": False}], "note": "mem"}]
    conv = AnthropicLLM._convert(history)
    assert conv[1]["content"][1]["type"] == "tool_use" and conv[2]["content"][0]["tool_use_id"] == "tu1"


def test_gemini_turn_keeps_thought_signature_and_one_call():
    from google.genai import types
    from worker.llm import GeminiLLM
    sig = b"\x00\xffsig-bytes"
    resp = types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=[
            types.Part(text="Opening the portal."),
            types.Part(function_call=types.FunctionCall(id="fc1", name="browser_open", args={"url": "/portal"}),
                       thought_signature=sig),
            types.Part(function_call=types.FunctionCall(id="fc2", name="finish", args={"summary": "x"}))]))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=11, candidates_token_count=4,
                                                                  thoughts_token_count=6))
    llm = GeminiLLM.__new__(GeminiLLM)
    llm.t, llm.model = types, "m"
    llm.client = SimpleNamespace(models=SimpleNamespace(generate_content=lambda **kw: resp))
    turn = llm.complete("sys", [{"role": "user", "content": "go"}], TOOLS)
    assert [c.name for c in turn.calls] == ["browser_open"] and turn.usage == {"in": 11, "cached": 0, "out": 10}
    history = [{"role": "user", "content": "go"},
               {"role": "assistant", "text": turn.text, "raw": turn.raw,
                "calls": [{"id": "fc1", "name": "browser_open", "args": {"url": "/portal"}}]},
               {"role": "tool", "results": [{"id": "fc1", "content": "page", "is_error": False}], "note": "mem"}]
    conv = llm._convert(history)
    replayed = [p for p in conv[1].parts if p.function_call]
    assert len(replayed) == 1 and replayed[0].thought_signature == sig  # exact bytes survive JSON persistence
    assert conv[2].parts[0].function_response.name == "browser_open"


def test_openai_compat_turn():
    cc = ChatCompletion.model_validate({
        "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None,
            "tool_calls": [{"id": "call1", "type": "function", "function": {"name": "finish", "arguments": "{\"summary\":\"s\"}"}}]}}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}})
    turn = _stub(OpenAICompatLLM, cc, {}).complete("sys", [{"role": "user", "content": "go"}], TOOLS)
    assert turn.calls[0].args == {"summary": "s"} and turn.usage == {"in": 7, "out": 3}
