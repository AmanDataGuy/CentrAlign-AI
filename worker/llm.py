"""Provider adapters. One neutral message format; each adapter translates.

Neutral messages:
  {"role": "user", "content": str}
  {"role": "assistant", "text": str, "calls": [{"id", "name", "args"}], "raw": provider blocks (optional, replayed verbatim)}
  {"role": "tool", "results": [{"id": str, "content": str, "is_error": bool}], "note": str}
"""
import json
import os
import time
from dataclasses import dataclass, field


@dataclass
class ToolCall:
    id: str
    name: str
    args: dict


@dataclass
class Turn:
    text: str = ""
    calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"in": 0, "out": 0})
    raw: list | None = None


class AnthropicLLM:
    def __init__(self, model: str):
        import anthropic
        self.client, self.model = anthropic.Anthropic(max_retries=4), model

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        r = self.client.messages.create(
            model=self.model, max_tokens=4096,
            system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools],
            tool_choice={"type": "auto", "disable_parallel_tool_use": True},  # browser actions have side effects
            messages=self._convert(messages),
        )
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, b.input) for b in r.content if b.type == "tool_use"]
        return Turn(text, calls, {"in": r.usage.input_tokens + (r.usage.cache_read_input_tokens or 0),
                                  "out": r.usage.output_tokens}, [b.model_dump(exclude_none=True) for b in r.content])

    @staticmethod
    def _convert(messages):
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                # replay raw blocks so thinking/tool_use ids stay intact across turns
                blocks = m.get("raw") or ([{"type": "text", "text": m["text"]}] if m["text"] else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in m["calls"]]
                out.append({"role": "assistant", "content": blocks})
            else:
                blocks = [{"type": "tool_result", "tool_use_id": r["id"], "content": r["content"], "is_error": r["is_error"]}
                          for r in m["results"]]
                if m.get("note"):
                    blocks.append({"type": "text", "text": m["note"]})
                out.append({"role": "user", "content": blocks})
        return out


class OpenAICompatLLM:
    """OpenAI, Groq, Gemini (/v1beta/openai/), Ollama (/v1) -- anything speaking chat.completions + tools."""

    def __init__(self, model: str):
        from openai import OpenAI
        self.client, self.model = OpenAI(base_url=os.environ.get("OPENAI_BASE_URL") or None, max_retries=4), model

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        r = self.client.chat.completions.create(
            model=self.model, messages=[{"role": "system", "content": system}, *self._convert(messages)],
            tools=[{"type": "function", "function": t} for t in tools])
        msg = r.choices[0].message
        calls = [ToolCall(c.id, c.function.name, _args(c.function.arguments)) for c in msg.tool_calls or []]
        usage = {"in": r.usage.prompt_tokens, "out": r.usage.completion_tokens} if r.usage else {"in": 0, "out": 0}
        return Turn(msg.content or "", calls[:1], usage)  # ponytail: one call per turn, same as Anthropic path

    @staticmethod
    def _convert(messages):
        out = []
        for m in messages:
            if m["role"] == "user":
                out.append({"role": "user", "content": m["content"]})
            elif m["role"] == "assistant":
                msg = {"role": "assistant", "content": m["text"] or ""}
                if m["calls"]:  # many endpoints reject "tool_calls": null
                    msg["tool_calls"] = [{"id": c["id"], "type": "function",
                                          "function": {"name": c["name"], "arguments": json.dumps(c["args"])}}
                                         for c in m["calls"]]
                out.append(msg)
            else:
                out += [{"role": "tool", "tool_call_id": r["id"], "content": r["content"]} for r in m["results"]]
                if m.get("note"):
                    out.append({"role": "user", "content": m["note"]})
        return out


class GeminiLLM:
    """Native Gemini via google-genai. Replays the model's own Content (incl. thought signatures) verbatim."""

    def __init__(self, model: str):
        from google import genai
        from google.genai import errors, types
        self.client, self.model, self.t, self.errors = genai.Client(api_key=os.environ["GEMINI_API_KEY"]), model, types, errors

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Turn:
        t = self.t
        config = t.GenerateContentConfig(
            system_instruction=system,
            tools=[t.Tool(function_declarations=[t.FunctionDeclaration(
                name=x["name"], description=x["description"], parameters_json_schema=x["parameters"]) for x in tools])],
            automatic_function_calling=t.AutomaticFunctionCallingConfig(disable=True))
        for attempt in range(4):  # 429 / 5xx: back off; anything else is a real error
            try:
                r = self.client.models.generate_content(model=self.model, contents=self._convert(messages), config=config)
                break
            except self.errors.APIError as e:
                if e.code not in (429, 500, 503) or attempt == 3:
                    raise
                time.sleep(2 ** attempt * 2)
        content = r.candidates[0].content if r.candidates and r.candidates[0].content else t.Content(role="model", parts=[])
        parts, kept, call = content.parts or [], [], None
        for p in parts:  # execute ONE call per turn (side effects); drop extra calls from the replayed history too
            if p.function_call:
                if call:
                    continue
                call = ToolCall(p.function_call.id or f"call_{len(messages)}", p.function_call.name, dict(p.function_call.args or {}))
            kept.append(p)
        u = r.usage_metadata
        usage = {"in": (u and u.prompt_token_count) or 0, "cached": (u and u.cached_content_token_count) or 0,
                 "out": ((u and u.candidates_token_count) or 0) + ((u and u.thoughts_token_count) or 0)}
        text = "".join(p.text for p in kept if p.text and not p.thought)
        raw = json.loads(t.Content(role="model", parts=kept).model_dump_json(exclude_none=True))
        return Turn(text, [call] if call else [], usage, raw)

    def _convert(self, messages):
        t, out, names = self.t, [], {}
        for m in messages:
            if m["role"] == "user":
                out.append(t.Content(role="user", parts=[t.Part(text=m["content"])]))
            elif m["role"] == "assistant":
                names.update({c["id"]: c["name"] for c in m["calls"]})
                if m.get("raw") is not None:  # JSON round-trip: base64 thought_signature decodes back to exact bytes
                    out.append(t.Content.model_validate_json(json.dumps(m["raw"])))
                else:
                    out.append(t.Content(role="model", parts=[t.Part(text=m["text"])] * bool(m["text"]) + [
                        t.Part(function_call=t.FunctionCall(id=c["id"], name=c["name"], args=c["args"])) for c in m["calls"]]))
            else:
                parts = [t.Part(function_response=t.FunctionResponse(
                    id=r["id"], name=names.get(r["id"], "unknown"),
                    response={"error" if r["is_error"] else "result": r["content"]})) for r in m["results"]]
                if m.get("note"):
                    parts.append(t.Part(text=m["note"]))
                out.append(t.Content(role="user", parts=parts))
        return out


def _args(raw: str | None) -> dict:
    """Weaker models emit broken JSON; hand it to the executor as a bad-args error instead of crashing the run."""
    try:
        parsed = json.loads(raw or "{}")
        return parsed if isinstance(parsed, dict) else {"_invalid_json": raw}
    except json.JSONDecodeError:
        return {"_invalid_json": raw}


class ScriptedLLM:
    """Test double: replays a fixed list of turns, or calls fn(messages) -> Turn. Never used for demos."""

    def __init__(self, script):
        if isinstance(script, list):
            it = iter(script)
            script = lambda messages: next(it)
        self.script = script

    def complete(self, system, messages, tools) -> Turn:
        return self.script(messages)


def from_env():
    provider = os.environ.get("WORKER_PROVIDER", "gemini")
    if provider == "gemini":
        return GeminiLLM(os.environ.get("WORKER_MODEL", "gemini-flash-latest"))
    if provider == "anthropic":
        if not os.environ.get("WORKER_MODEL"):
            raise SystemExit("Set WORKER_MODEL to an Anthropic model id for WORKER_PROVIDER=anthropic")
        return AnthropicLLM(os.environ["WORKER_MODEL"])
    if provider == "openai":
        return OpenAICompatLLM(os.environ.get("WORKER_MODEL", "gpt-4.1"))
    raise SystemExit(f"Unknown WORKER_PROVIDER={provider!r} (use gemini|anthropic|openai)")
