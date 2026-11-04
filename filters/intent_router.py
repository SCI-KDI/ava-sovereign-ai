"""
title: AVA Intent Router
description: Rewrites follow-up questions into standalone requests and suggests which specialist to delegate to, using one call to a small model.
author: State of Valais, Cantonal IT Service (SCI)
license: Apache-2.0
version: 1.0.0
required_open_webui_version: 0.9.0
requirements: aiohttp
"""

# Attach this filter to the orchestrator model only (Workspace > Models > AVA > Filters),
# or make it global: it skips specialists' temporary chats on its own.
#
# On each user turn it sends the recent conversation to a small, fast model, which
# returns JSON: the user's actual request written as a standalone instruction,
# and the specialist tools that should handle it. The result is:
#   - appended to the last user message, between [AVA_ROUTING] markers. The
#     message is changed only for this request: the stored chat keeps the original;
#   - stored in metadata["ava_routing"] for later filters and tools.
#
# This is a simplified version of the filter we run in production. Ours also
# decides on web search, the staff directory and acronyms, and has a keyword
# fast path. Use a small model: this call happens before every answer, so its
# latency is added to every turn.

import json
import re
import time
from datetime import date
from typing import Any, Optional

import aiohttp
from pydantic import BaseModel, Field

_MARKER_START = "[AVA_ROUTING]"
_MARKER_END = "[/AVA_ROUTING]"
_BLOCK_RE = re.compile(re.escape(_MARKER_START) + r".*?" + re.escape(_MARKER_END), re.DOTALL)
_REASONING_RE = re.compile(r"<(think|reasoning|details)\b[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE)
_AUDIO_VIDEO_EXTENSIONS = (".mp3", ".m4a", ".wav", ".ogg", ".opus", ".webm", ".flac", ".aac", ".mp4", ".mov", ".mkv")

_DEFAULT_SPECIALISTS = {
    "ask_ava_juriste": "Swiss federal and cantonal law: legal interpretation, rights and obligations, procedures, deadlines, remedies, case law, legal drafting.",
    "ask_ava_rh": "Human resources for staff: salary, leave, absences, working time, contracts, internal staff rules.",
    "ask_ava_coding": "Software development: code, debugging, architecture, tests, security.",
    "ask_ava_pv": "Transcribe an attached audio or video recording and draft meeting minutes.",
}

_STRINGS = {
    "fr": {
        "routing": "Je transmets ta question à {name}…",
        "request": "Requête canonique pour ce tour uniquement :",
        "suggested": "Spécialiste(s) suggéré(s), dans cet ordre : {tools}. Délègue-leur la requête, sauf si elle ne relève clairement pas de leur domaine.",
        "none": "Aucun spécialiste n'est nécessaire pour ce tour.",
    },
    "de": {
        "routing": "Ich leite deine Frage an {name} weiter…",
        "request": "Kanonische Anfrage nur für diese Runde:",
        "suggested": "Vorgeschlagene Spezialisten, in dieser Reihenfolge: {tools}. Delegiere die Anfrage an sie, ausser sie gehört eindeutig nicht in ihr Gebiet.",
        "none": "Für diese Runde ist kein Spezialist nötig.",
    },
    "en": {
        "routing": "Passing your question to {name}…",
        "request": "Canonical request for this turn only:",
        "suggested": "Suggested specialist(s), in this order: {tools}. Delegate the request to them unless it clearly falls outside their domain.",
        "none": "No specialist is needed for this turn.",
    },
}

_ENCODER_PROMPT = """You are the intent encoder of an AI assistant. {organisation_context}
Do not answer the user and do not perform the task. In one pass:
1. recover the user's actual intent from the conversation, resolving follow-ups and references;
2. decide which specialists, if any, should handle it;
3. write the request as one clear, standalone query for the main assistant.

Rules:
- Write canonical_user_request and rewritten_user_message in the language of the last user message.
- Preserve the user's intent exactly. Do not invent facts, constraints or goals.
- Preserve exactly: legal references, article numbers, acronyms, quotations, file names, code.
- Never expand or guess the meaning of an acronym.
- Write a request, not an execution plan, and not instructions on how to answer.

Specialists (tool name: domain):
{specialists}

Specialist routing:
- Select no specialist when the main assistant can answer alone (general questions, rewriting, translation, summaries of supplied text).
- Select one specialist when the task needs that domain's authority.
- Select several only when the task genuinely spans distinct domains, in execution order.
- Route from the actual task, never from keywords alone.
- An attached audio or video recording always requires "{audio_tool}".

Return only minified JSON, no markdown:
{{"confidence": 0.0-1.0, "reason": "under 10 words", "canonical_user_request": "concise actual task", "rewritten_user_message": "complete standalone request", "specialist_tools": []}}"""


def _normalize_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return "\n".join(
            str(block.get("text", ""))
            for block in content
            if isinstance(block, dict) and block.get("type") in {"text", "input_text"}
        ).strip()
    return str(content or "").strip()


def _truthy(value: Any) -> bool:
    return value is True or str(value).strip().lower() in {"1", "true", "yes"}


def _extract_json(text: str) -> Optional[dict]:
    text = _REASONING_RE.sub("", text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _file_names(body: dict, metadata: dict) -> list[str]:
    names: list[str] = []
    for source in (body.get("files"), metadata.get("files")):
        for item in source if isinstance(source, list) else []:
            if not isinstance(item, dict):
                continue
            nested = item.get("file") if isinstance(item.get("file"), dict) else {}
            name = item.get("name") or nested.get("filename") or nested.get("name")
            if name and str(name) not in names:
                names.append(str(name))
    return names


def _append_to_message(message: dict, block: str) -> None:
    content = message.get("content")
    if isinstance(content, list):
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"text", "input_text"}:
                part["text"] = f"{_BLOCK_RE.sub('', str(part.get('text') or '')).rstrip()}\n\n{block}"
                return
        content.insert(0, {"type": "text", "text": block})
    else:
        message["content"] = f"{_BLOCK_RE.sub('', str(content or '')).rstrip()}\n\n{block}"


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=5, description="Runs after the context injector (-100).")
        llm_base_url: str = Field(
            default="", description="OpenAI-compatible base URL of the routing model, ending in /v1."
        )
        llm_api_key: str = Field(
            default="", description="API key of the routing model.", json_schema_extra={"input": {"type": "password"}}
        )
        llm_model: str = Field(default="", description="A small, fast model that follows JSON instructions well.")
        disable_thinking: bool = Field(
            default=True,
            description="Send chat_template_kwargs.enable_thinking=false (vLLM and compatible servers). Turn off if your provider rejects it.",
        )
        timeout_seconds: int = Field(default=20, description="If the routing model is slower, the turn continues unrouted.")
        max_tokens: int = Field(default=600)
        organisation_context: str = Field(
            default="Its users are staff of a public administration.",
            description="One or two sentences about who the users are. Do not put personal data here.",
        )
        specialists_json: str = Field(
            default=json.dumps(_DEFAULT_SPECIALISTS, ensure_ascii=False),
            description="JSON object: delegation tool name -> one-line description of its domain. Must match your installed ask_ava_* tools.",
        )
        audio_tool_name: str = Field(default="ask_ava_pv", description="Specialist forced when a recording is attached. Empty disables this.")
        min_confidence: float = Field(default=0.6, description="Below this, the turn continues unchanged.")
        max_context_messages: int = Field(default=6, description="Previous messages sent to the routing model.")
        max_message_chars: int = Field(default=2000, description="Each message is shortened above this length.")
        max_input_chars: int = Field(
            default=12000, description="Very long inputs (pasted documents) are not rewritten."
        )
        show_status: bool = Field(default=True, description="Show 'Passing your question to …' when delegating.")
        debug: bool = Field(default=False, description="Log timings and decisions. Never logs message content.")

    def __init__(self):
        self.valves = self.Valves()

    def _specialists(self) -> dict[str, str]:
        try:
            parsed = json.loads(self.valves.specialists_json or "{}")
            return {str(k): str(v) for k, v in parsed.items()} if isinstance(parsed, dict) else {}
        except ValueError:
            return {}

    def _log(self, message: str) -> None:
        if self.valves.debug:
            print(f"[AVA Intent Router] {message}")

    async def _call_model(self, prompt: str, payload: dict) -> str:
        request = {
            "model": self.valves.llm_model,
            "stream": False,
            "temperature": 0.0,
            "max_tokens": self.valves.max_tokens,
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
        }
        if self.valves.disable_thinking:
            request["chat_template_kwargs"] = {"enable_thinking": False}
        timeout = aiohttp.ClientTimeout(total=self.valves.timeout_seconds)
        # trust_env=True: honour HTTP(S)_PROXY when the model is outside your network.
        async with aiohttp.ClientSession(timeout=timeout, trust_env=True) as session:
            async with session.post(
                f"{self.valves.llm_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {self.valves.llm_api_key}"},
                json=request,
            ) as resp:
                if resp.status >= 400:
                    raise RuntimeError(f"HTTP {resp.status}")
                data = await resp.json(content_type=None)
        return _normalize_content(data["choices"][0]["message"]["content"])

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __metadata__: Optional[dict] = None,
        __event_emitter__: Any = None,
        __task__: Any = None,
        **kwargs: Any,
    ) -> dict:
        if __task__ or not isinstance(body, dict):
            return body
        metadata = body.setdefault("metadata", {})
        if not isinstance(metadata, dict):
            return body
        variables = (__metadata__ or {}).get("variables") or metadata.get("variables") or body.get("variables") or {}
        # Temporary chats created by ask_ava_* tools carry this flag: their question is already canonical.
        if _truthy(metadata.get("carryover_filter_skip")) or (
            isinstance(variables, dict) and _truthy(variables.get("carryover_filter_skip"))
        ):
            return body
        if not (self.valves.llm_base_url and self.valves.llm_model):
            return body

        messages = body.get("messages") or []
        last_user = next(
            (m for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"), None
        )
        if last_user is None:
            return body
        original = _BLOCK_RE.sub("", _normalize_content(last_user.get("content"))).strip()
        if not original or len(original) > self.valves.max_input_chars:
            return body
        if (metadata.get("_ava_ctx") or {}).get("is_trivial_input"):
            return body

        specialists = self._specialists()
        files = _file_names(body, metadata)
        audio_tool = self.valves.audio_tool_name.strip()
        has_recording = bool(audio_tool) and any(n.lower().endswith(_AUDIO_VIDEO_EXTENSIONS) for n in files)

        history = []
        for msg in messages[:-1][-self.valves.max_context_messages :]:
            if isinstance(msg, dict) and msg.get("role") in {"user", "assistant"}:
                text = _BLOCK_RE.sub("", _REASONING_RE.sub("", _normalize_content(msg.get("content")))).strip()
                if text:
                    history.append({"role": msg["role"], "content": text[: self.valves.max_message_chars]})
        prompt = _ENCODER_PROMPT.format(
            organisation_context=self.valves.organisation_context.strip(),
            specialists="\n".join(f'- "{name}": {desc}' for name, desc in specialists.items()) or "- (none)",
            audio_tool=audio_tool or "(none)",
        )
        payload = {
            "recent_messages": history,
            "last_user_message": original,
            "attached_files": files,
            "today": date.today().isoformat(),
        }

        started = time.monotonic()
        try:
            decision = _extract_json(await self._call_model(prompt, payload)) or {}
        except Exception as exc:
            self._log(f"routing model failed: {type(exc).__name__}")
            decision = {}
        elapsed_ms = int((time.monotonic() - started) * 1000)

        try:
            confidence = float(decision.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        tools = [t for t in decision.get("specialist_tools") or [] if t in specialists]
        if has_recording and audio_tool not in tools:
            # A recording means meeting minutes, even on a bare upload or if the model missed it.
            tools.insert(0, audio_tool)
            confidence = max(confidence, self.valves.min_confidence)
        rewritten = str(decision.get("rewritten_user_message") or decision.get("canonical_user_request") or "").strip()

        metadata["ava_routing"] = {
            "confidence": confidence,
            "reason": str(decision.get("reason") or ""),
            "specialist_tools": tools,
            "elapsed_ms": elapsed_ms,
        }
        metadata["canonical_user_request"] = str(decision.get("canonical_user_request") or original)
        self._log(f"elapsed={elapsed_ms}ms confidence={confidence:.2f} tools={tools}")

        if confidence < self.valves.min_confidence or (not tools and (not rewritten or rewritten == original)):
            return body

        lang = (metadata.get("_ava_ctx") or {}).get("language") or "fr"
        strings = _STRINGS.get(lang, _STRINGS["en"])
        lines = [_MARKER_START]
        if rewritten and rewritten != original:
            lines += [strings["request"], rewritten, ""]
        lines.append(strings["suggested"].format(tools=", ".join(tools)) if tools else strings["none"])
        lines.append(_MARKER_END)
        # Appended to the user message rather than the system prompt: the system
        # prompt stays identical across turns, which keeps prompt caching effective.
        _append_to_message(last_user, "\n".join(lines))

        if tools and self.valves.show_status and __event_emitter__:
            name = tools[0].removeprefix("ask_").replace("_", " ").title().replace("Ava", "AVA")
            try:
                await __event_emitter__(
                    {"type": "status", "data": {"description": strings["routing"].format(name=name), "done": False}}
                )
            except Exception:
                pass
        return body
