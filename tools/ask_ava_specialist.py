"""
title: Ask AVA Specialist
description: Delegates a question to a hidden specialist model and returns its answer and sources.
author: State of Valais, Cantonal IT Service (SCI)
license: Apache-2.0
version: 1.0.0
required_open_webui_version: 0.9.0
requirements: aiohttp
"""

# How it works (see docs/architecture.md, "How delegation works"):
#
#   1. Read the specialist's tools, filters and knowledge from its model record.
#   2. Create a temporary chat with the specialist, as the current user.
#   3. Run the completion, relaying citations and progress to the user.
#   4. Wait until the specialist's message is marked done.
#   5. Run the outlet filters, forward the sources, delete the temporary chat.
#
# Make one copy of this file per specialist. In each copy, set the valves,
# rename the public method at the bottom (for example ask_ava_juriste) and
# rewrite its docstring: the orchestrator reads it to decide when to call.

import asyncio
import codecs
import json
import re
import time
import uuid
from datetime import datetime
from typing import Any, Optional

import aiohttp
from pydantic import BaseModel, Field

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

# Reasoning traces and Open WebUI tool-call blocks must not reach the orchestrator.
_HIDDEN_BLOCKS_RE = re.compile(
    r"<(think|reasoning|details)\b[^>]*>.*?</\1>", flags=re.DOTALL | re.IGNORECASE
)

_STATUS = {
    "en": {
        "start": "Asking {name}…",
        "source": "{name} is consulting: {item}",
        "progress": "{name}: {item}",
        "waiting": "Waiting for {name}'s answer…",
        "done": "{name} has answered.",
        "cleanup_failed": "Warning: the temporary chat of {name} could not be deleted.",
    },
    "fr": {
        "start": "Je transmets la question à {name}…",
        "source": "{name} consulte : {item}",
        "progress": "{name} : {item}",
        "waiting": "En attente de la réponse de {name}…",
        "done": "{name} a répondu.",
        "cleanup_failed": "Avertissement : le chat temporaire de {name} n'a pas pu être supprimé.",
    },
    "de": {
        "start": "Ich leite die Frage an {name} weiter…",
        "source": "{name} konsultiert: {item}",
        "progress": "{name}: {item}",
        "waiting": "Warte auf die Antwort von {name}…",
        "done": "{name} hat geantwortet.",
        "cleanup_failed": "Warnung: Der temporäre Chat von {name} konnte nicht gelöscht werden.",
    },
}

# Prepended to the delegated question. The temporary chat does not go through
# the orchestrator's inlet filters, so the specialist would otherwise not know
# the date, and tends to answer in the language of its sources.
_LANGUAGE_DIRECTIVE = {
    "en": "Answer language (mandatory): answer only in English, whatever the language of the sources.",
    "fr": "Langue de la réponse (impératif) : réponds exclusivement en français, quelle que soit la langue des sources.",
    "de": "Sprache der Antwort (zwingend): Antworte ausschliesslich auf Deutsch (Schweizer Rechtschreibung, ss statt ß), unabhängig von der Sprache der Quellen.",
}
_DATE_LINE = {
    "en": "Date context: today is {date}.",
    "fr": "Contexte temporel : nous sommes le {date}.",
    "de": "Zeitlicher Kontext: heute ist der {date}.",
}
_BUDGET_LINE = {
    "en": "Tool budget: at most {n} tool calls. Once reached, stop searching and answer with what you have, saying what could not be checked.",
    "fr": "Budget d'outils : {n} appels au maximum. Une fois atteint, arrête les recherches et réponds avec les éléments disponibles en signalant ce qui n'a pas pu être vérifié.",
    "de": "Werkzeug-Budget: höchstens {n} Werkzeugaufrufe. Danach beende die Recherche und antworte mit den verfügbaren Elementen; nenne, was nicht überprüft werden konnte.",
}

# Deliberately crude: it only has to tell French, German and English apart.
_LANG_HINTS = {
    "fr": (" le ", " la ", " les ", " des ", " est ", " une ", " pour ", " que ", " qui ", " dans ", " quel", " comment "),
    "de": (" der ", " die ", " das ", " und ", " ist ", " nicht ", " mit ", " für ", " eine ", " wie ", " was ", " wann "),
    "en": (" the ", " and ", " is ", " are ", " with ", " for ", " what ", " how ", " which ", " this ", " when "),
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _new_id() -> str:
    return str(uuid.uuid4())


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


def _truncate_middle(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    return text[:head] + "\n…\n" + text[-(max_chars - head - 3) :]


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [part for part in value.split(",")]
    if not isinstance(value, list):
        return []
    return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _detect_language(text: str, default: str) -> str:
    padded = f" {text.lower()} "
    scores = {lang: sum(padded.count(w) for w in words) for lang, words in _LANG_HINTS.items()}
    scores["de"] += sum(padded.count(c) for c in "äöü") * 2
    scores["fr"] += sum(padded.count(c) for c in "éèàçê")
    best = max(scores, key=scores.get)
    return best if scores[best] > 0 else default


def _resolve_token(__user__: Any, __request__: Any) -> str:
    """The current user's own token: the specialist runs with their rights."""
    if isinstance(__user__, dict):
        for key in ("token", "api_key"):
            if __user__.get(key):
                return str(__user__[key])
    if __request__ is not None:
        try:
            auth = __request__.headers.get("Authorization", "")
            if auth.startswith("Bearer "):
                return auth[len("Bearer ") :].strip()
            cookie = __request__.cookies.get("token")
            if cookie:
                return str(cookie)
        except Exception:
            pass
    return ""


def _sanitize_file_ref(item: Any) -> Optional[dict]:
    """Keep only a light reference. Extracted text and chunks are never copied."""
    if not isinstance(item, dict):
        return None
    nested = item.get("file") if isinstance(item.get("file"), dict) else {}
    file_id = str(item.get("id") or nested.get("id") or "").strip()
    if not file_id:
        return None
    ref: dict[str, Any] = {"type": str(item.get("type") or "file"), "id": file_id}
    if ref["type"] == "file":
        name = item.get("name") or nested.get("filename") or nested.get("name")
        if name:
            ref["name"] = str(name)
        ref["url"] = str(item.get("url") or file_id)
    return ref


def _collect_file_refs(__metadata__: Any, __files__: Any) -> list[dict]:
    candidates: list[Any] = []
    for source in (__files__, (__metadata__ or {}).get("files") if isinstance(__metadata__, dict) else None):
        if isinstance(source, list):
            candidates.extend(source)
    refs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for item in candidates:
        ref = _sanitize_file_ref(item)
        if ref and (ref["type"], ref["id"]) not in seen:
            seen.add((ref["type"], ref["id"]))
            refs.append(ref)
    return refs


def _forwarded_messages(
    __messages__: Any, __metadata__: Any, question: str, max_messages: int, max_chars: int
) -> list[dict]:
    """Recent conversation turns, so the specialist understands follow-ups."""
    messages = __messages__
    if not isinstance(messages, list) and isinstance(__metadata__, dict):
        # Written by filters/ava_context_injector.py when __messages__ is unavailable.
        messages = __metadata__.get("forwarded_messages")
    if not isinstance(messages, list) or max_messages <= 0:
        return []
    out: list[dict] = []
    for msg in messages[:-1]:  # the last message is the current request
        if not isinstance(msg, dict) or msg.get("role") not in {"user", "assistant"}:
            continue
        content = _HIDDEN_BLOCKS_RE.sub("", _normalize_content(msg.get("content"))).strip()
        if content and content != question.strip():
            out.append({"role": msg["role"], "content": _truncate_middle(content, max_chars)})
    return out[-max_messages:]


# ---------------------------------------------------------------------------
# HTTP helpers. All calls go to Open WebUI itself, with the user's token.
# ---------------------------------------------------------------------------


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


async def _post_json(session: aiohttp.ClientSession, url: str, token: str, payload: dict) -> Any:
    async with session.post(url, headers=_headers(token), json=payload) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status} on POST {url}: {raw[:500]}")
        return json.loads(raw) if raw.strip() else {}


async def _get_json(session: aiohttp.ClientSession, url: str, token: str, params: Optional[dict] = None) -> Any:
    async with session.get(url, headers=_headers(token), params=params) as resp:
        raw = await resp.text()
        if resp.status >= 400:
            raise RuntimeError(f"HTTP {resp.status} on GET {url}: {raw[:500]}")
        return json.loads(raw)


def _find_task_ids(value: Any, depth: int = 0) -> set[str]:
    found: set[str] = set()
    if depth > 32:
        return found
    if isinstance(value, dict):
        for key, val in value.items():
            if key in {"task_id", "taskId"} and isinstance(val, str) and val:
                found.add(val)
            elif key in {"task_ids", "taskIds"} and isinstance(val, list):
                found.update(str(v) for v in val if v)
            else:
                found |= _find_task_ids(val, depth + 1)
    elif isinstance(value, list):
        for item in value:
            found |= _find_task_ids(item, depth + 1)
    return found


# ---------------------------------------------------------------------------
# Citations
# ---------------------------------------------------------------------------


def _normalize_citation(item: Any) -> Optional[dict]:
    if not isinstance(item, dict):
        return None
    document = item.get("document")
    if not isinstance(document, list):
        document = [str(document)] if document else [""]
    metadata = item.get("metadata")
    if not isinstance(metadata, list):
        metadata = [metadata] if isinstance(metadata, dict) else []
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    first = metadata[0] if metadata and isinstance(metadata[0], dict) else {}
    name = source.get("name") or first.get("source") or first.get("title") or "Source"
    # Many tools put the public link in metadata[0]["source"] rather than source["url"].
    meta_source = str(first.get("source") or "")
    url = source.get("url") or first.get("url") or (meta_source if meta_source.startswith("http") else None)
    normalized_source = {"name": name, **({"url": url} if url else {})}
    return {
        "document": document,
        "metadata": metadata or [{"source": name, **({"url": url} if url else {})}],
        "source": normalized_source,
    }


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------


class Tools:
    class Valves(BaseModel):
        tool_model_id: str = Field(
            default="ava-specialist",
            description="Model ID of the hidden specialist, as shown in Workspace > Models.",
        )
        tool_display_name: str = Field(
            default="AVA Specialist", description="Name shown to the user in progress messages."
        )
        status_language: str = Field(
            default="fr", description="Language of progress messages and fallback answer language: fr, de or en."
        )
        openwebui_base_url: str = Field(
            default="http://localhost:8080",
            description=(
                "How this tool reaches Open WebUI. Keep localhost so that calls stay inside the "
                "container and avoid ingress timeouts. Leave empty to use the request's own URL."
            ),
        )
        forward_conversation_context: bool = Field(
            default=True, description="Send recent conversation turns before the question."
        )
        max_forwarded_messages: int = Field(default=6, description="Number of previous turns sent.")
        max_forwarded_message_chars: int = Field(
            default=20000, description="Each forwarded turn is shortened in the middle above this length."
        )
        add_language_directive: bool = Field(
            default=True, description="Tell the specialist to answer in the language of the question."
        )
        add_date_context: bool = Field(default=True, description="Tell the specialist today's date.")
        timezone: str = Field(default="Europe/Zurich", description="Time zone used for the date.")
        max_tool_calls: int = Field(
            default=0,
            description="If above 0, ask the specialist to stop after this many tool calls. We use 6 for legal research.",
        )
        force_native_function_calling: bool = Field(
            default=True, description="Use the model's native tool calling in the temporary chat."
        )
        pass_model_knowledge_as_files: bool = Field(
            default=False,
            description="Also attach the specialist's knowledge collections as files. Usually not needed.",
        )
        max_wait_seconds: int = Field(
            default=400, description="Longest wait for the specialist. Raise it for heavy research."
        )
        poll_delay_seconds: float = Field(
            default=2.0, description="Delay between checks of the specialist's message, after the first 10 seconds."
        )
        call_chat_completed: bool = Field(
            default=True, description="Run the specialist's outlet filters on its answer."
        )
        delete_delegated_chat: bool = Field(
            default=True, description="Delete the temporary chat afterwards. Users must be allowed to delete chats."
        )
        debug: bool = Field(
            default=False, description="Log timings and identifiers to the server log. Never logs content."
        )

    def __init__(self):
        self.valves = self.Valves()
        # Citations are forwarded explicitly from the specialist's chat.
        self.citation = False

    # -- logging and events -------------------------------------------------

    def _log(self, message: str) -> None:
        if self.valves.debug:
            print(f"[ask_ava:{self.valves.tool_model_id}] {message}")

    def _text(self, key: str, **kwargs: Any) -> str:
        strings = _STATUS.get(self.valves.status_language, _STATUS["en"])
        return strings[key].format(name=self.valves.tool_display_name, **kwargs)

    @staticmethod
    async def _emit(emitter: Any, event: dict) -> None:
        if not emitter:
            return
        try:
            await emitter(event)
        except Exception:
            pass

    async def _status(self, emitter: Any, description: str, done: bool = False, hidden: bool = False) -> None:
        await self._emit(emitter, {"type": "status", "data": {"description": description, "done": done, "hidden": hidden}})

    async def _citations(self, emitter: Any, sources: Any, seen: set) -> None:
        for item in sources if isinstance(sources, list) else []:
            citation = _normalize_citation(item)
            if not citation:
                continue
            key = json.dumps(citation, sort_keys=True, ensure_ascii=False)
            if key not in seen:
                seen.add(key)
                await self._emit(emitter, {"type": "citation", "data": citation})

    # -- question preparation -----------------------------------------------

    def _prepare_question(self, question: str) -> str:
        lang = _detect_language(question, self.valves.status_language)
        if lang not in _LANGUAGE_DIRECTIVE:
            lang = "en"
        lines: list[str] = []
        if self.valves.add_language_directive:
            lines.append(_LANGUAGE_DIRECTIVE[lang])
        if self.valves.add_date_context:
            tz = None
            if ZoneInfo is not None:
                try:
                    tz = ZoneInfo(self.valves.timezone)
                except Exception:
                    tz = None
            lines.append(_DATE_LINE[lang].format(date=datetime.now(tz).strftime("%Y-%m-%d")))
        if self.valves.max_tool_calls > 0:
            lines.append(_BUDGET_LINE[lang].format(n=self.valves.max_tool_calls))
        return "\n".join(lines + ["", question]).strip() if lines else question

    # -- streaming and polling ----------------------------------------------

    async def _run_completion(
        self, session: aiohttp.ClientSession, url: str, token: str, payload: dict, emitter: Any, seen: set
    ) -> dict:
        """Start the completion and read whatever Open WebUI streams back.

        When a chat_id, message id and session_id are given, Open WebUI may run the
        completion as a background task and answer with a task ID instead of a
        stream. Both cases are handled: the final answer is then read from the chat.
        """
        trace: dict[str, Any] = {"content": "", "done": False, "tool_signal": False, "task_ids": set()}
        parts: list[str] = []
        timeout = aiohttp.ClientTimeout(total=self.valves.max_wait_seconds, sock_connect=30)
        headers = {**_headers(token), "Accept": "text/event-stream"}

        async def handle(line: str) -> None:
            line = line.strip()
            if not line.startswith("data:"):
                return
            data = line[5:].strip()
            if data == "[DONE]":
                trace["done"] = True
                return
            if "tool_calls" in data:
                trace["tool_signal"] = True
            try:
                event = json.loads(data)
            except ValueError:
                return
            if not isinstance(event, dict):
                return
            trace["task_ids"] |= _find_task_ids(event)
            obj = event.get("event") if isinstance(event.get("event"), dict) else event
            choices = obj.get("choices")
            if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                delta = choices[0].get("delta") or choices[0].get("message") or {}
                if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                    parts.append(delta["content"])
            elif obj.get("type") in {"message", "replace"}:
                text = obj.get("data")
                text = text.get("content", "") if isinstance(text, dict) else text
                if isinstance(text, str):
                    if obj["type"] == "replace":
                        parts.clear()
                    parts.append(text)
            elif obj.get("type") in {"source", "citation"}:
                await self._citations(emitter, [obj.get("data")], seen)

        async with session.post(url, headers=headers, json=payload, timeout=timeout) as resp:
            if resp.status >= 400:
                raise RuntimeError(f"HTTP {resp.status} on POST {url}: {(await resp.text())[:500]}")
            if "application/json" in resp.headers.get("Content-Type", ""):
                trace["task_ids"] |= _find_task_ids(await resp.json(content_type=None))
                return trace
            # Incremental decoding: accented characters can be split across chunks.
            decoder = codecs.getincrementaldecoder("utf-8")("ignore")
            buffer = ""
            async for chunk in resp.content.iter_any():
                buffer += decoder.decode(chunk)
                *lines, buffer = buffer.split("\n")
                for line in lines:
                    await handle(line)
            if buffer:
                await handle(buffer)

        trace["content"] = "".join(parts)
        return trace

    async def _wait_for_answer(
        self, session: aiohttp.ClientSession, base_url: str, token: str, chat_id: str, message_id: str, emitter: Any
    ) -> dict:
        """Read the specialist's message until it is marked done, or the wait runs out."""
        deadline = time.monotonic() + self.valves.max_wait_seconds
        seen_progress: set[str] = set()
        message: dict = {}
        attempt = 0
        while time.monotonic() < deadline:
            attempt += 1
            try:
                chat = await _get_json(session, f"{base_url}/api/v1/chats/{chat_id}", token)
                history = ((chat.get("chat") or chat).get("history") or {}).get("messages") or {}
                message = history.get(message_id) or {}
            except Exception as exc:
                self._log(f"poll error {type(exc).__name__}")

            # Show the specialist's own progress (tool statuses) to the user.
            for entry in message.get("statusHistory") or []:
                if not isinstance(entry, dict):
                    continue
                data = entry.get("data") if isinstance(entry.get("data"), dict) else entry
                description = str(data.get("description") or "").strip()
                if description and description not in seen_progress:
                    seen_progress.add(description)
                    await self._status(emitter, self._text("progress", item=description[:160]))

            if message.get("done") and (_message_text(message) or message.get("error")):
                return message
            # Poll quickly at first, then at the configured pace.
            await asyncio.sleep(1.0 if attempt <= 10 else max(0.5, self.valves.poll_delay_seconds))
        message["_timed_out"] = True
        return message

    # -- cleanup ------------------------------------------------------------

    async def _cleanup(
        self, session: aiohttp.ClientSession, base_url: str, token: str, chat_id: str, task_ids: set[str]
    ) -> bool:
        """Stop the specialist's remaining tasks, then delete its temporary chat."""
        try:
            listed = await _get_json(session, f"{base_url}/api/tasks/chat/{chat_id}", token)
            task_ids = task_ids | _find_task_ids(listed)
        except Exception:
            pass
        for task_id in task_ids:
            try:
                await _post_json(session, f"{base_url}/api/tasks/stop/{task_id}", token, {})
            except Exception:
                pass

        if not self.valves.delete_delegated_chat:
            return True
        try:
            async with session.delete(f"{base_url}/api/v1/chats/{chat_id}", headers=_headers(token)) as resp:
                if resp.status >= 400:
                    self._log(f"delete failed HTTP {resp.status}")
                    return False
            async with session.get(f"{base_url}/api/v1/chats/{chat_id}", headers=_headers(token)) as resp:
                return resp.status in {401, 403, 404}
        except Exception as exc:
            self._log(f"delete error {type(exc).__name__}")
            return False

    # -- main flow ------------------------------------------------------------

    async def _delegate(
        self,
        question: str,
        __user__: Optional[dict],
        __request__: Any,
        __metadata__: Optional[dict],
        __messages__: Any,
        __files__: Any,
        __event_emitter__: Any,
    ) -> str:
        started = time.monotonic()
        question = str(question or "").strip()
        if not question:
            return "Error: the question is empty."
        token = _resolve_token(__user__, __request__)
        if not token:
            return "Error: the current user's Open WebUI token could not be found."

        base_url = self.valves.openwebui_base_url.strip().rstrip("/")
        if not base_url and __request__ is not None:
            base_url = str(__request__.base_url).rstrip("/")
        model_id = self.valves.tool_model_id.strip()
        emitter = __event_emitter__
        seen_citations: set[str] = set()
        await self._status(emitter, self._text("start"))

        delegated_question = self._prepare_question(question)
        file_refs = _collect_file_refs(__metadata__, __files__)
        user_id, assistant_id, session_id = _new_id(), _new_id(), _new_id()
        now = int(time.time())
        user_message = {
            "id": user_id,
            "role": "user",
            "content": delegated_question,
            "timestamp": now,
            "models": [model_id],
            "childrenIds": [assistant_id],
            **({"files": file_refs} if file_refs else {}),
        }
        assistant_message = {
            "id": assistant_id,
            "role": "assistant",
            "content": "",
            "parentId": user_id,
            "childrenIds": [],
            "model": model_id,
            "modelIdx": 0,
            "done": False,
            "timestamp": now + 1,
        }

        chat_id = ""
        task_ids: set[str] = set()
        answer = ""
        # trust_env=False: these calls go to Open WebUI itself and must not go through a proxy.
        async with aiohttp.ClientSession(trust_env=False) as session:
            try:
                # 1. The specialist's own tools, filters and knowledge.
                record = await _get_json(session, f"{base_url}/api/v1/models/model", token, {"id": model_id})
                meta = (record.get("info") or record).get("meta") or {}
                tool_ids = _string_list(meta.get("toolIds"))
                filter_ids = _string_list(meta.get("filterIds"))
                knowledge = [k for k in meta.get("knowledge") or [] if isinstance(k, dict) and k.get("id")]

                # 2. A temporary chat, owned by the current user.
                created = await _post_json(
                    session,
                    f"{base_url}/api/v1/chats/new",
                    token,
                    {
                        "chat": {
                            "title": f"Delegated to {self.valves.tool_display_name}",
                            "models": [model_id],
                            "messages": [user_message, assistant_message],
                            "history": {
                                "currentId": assistant_id,
                                "messages": {user_id: user_message, assistant_id: assistant_message},
                            },
                            **({"files": file_refs} if file_refs else {}),
                        }
                    },
                )
                chat_id = str(created.get("id") or "")
                if not chat_id:
                    return "Error: the temporary chat could not be created."

                # 3. The completion itself.
                context = (
                    _forwarded_messages(
                        __messages__,
                        __metadata__,
                        question,
                        self.valves.max_forwarded_messages,
                        self.valves.max_forwarded_message_chars,
                    )
                    if self.valves.forward_conversation_context
                    else []
                )
                files = file_refs + (
                    [{"type": "collection", "id": k["id"]} for k in knowledge]
                    if self.valves.pass_model_knowledge_as_files
                    else []
                )
                payload: dict[str, Any] = {
                    "chat_id": chat_id,
                    "id": assistant_id,
                    "session_id": session_id,
                    "model": model_id,
                    "stream": True,
                    "messages": context + [{"role": "user", "content": delegated_question}],
                    # Titles, tags and follow-ups are useless for a chat that will be deleted.
                    "background_tasks": {
                        "title_generation": False,
                        "tags_generation": False,
                        "follow_up_generation": False,
                    },
                    # Tells filters/intent_router.py to skip this request. Must be a string:
                    # Open WebUI substitutes variables with str.replace().
                    "variables": {"carryover_filter_skip": "true"},
                    **({"tool_ids": tool_ids} if tool_ids else {}),
                    **({"filter_ids": filter_ids} if filter_ids else {}),
                    **({"files": files} if files else {}),
                    **({"function_calling": "native"} if self.valves.force_native_function_calling else {}),
                }
                trace = await self._run_completion(
                    session, f"{base_url}/api/chat/completions", token, payload, emitter, seen_citations
                )
                task_ids |= trace["task_ids"]

                # 4. Use the stream directly when it is complete and had no tool calls.
                #    Otherwise the authoritative answer is the one saved in the chat.
                message: dict = {}
                answer = trace["content"]
                if not (answer and trace["done"] and not trace["tool_signal"]):
                    await self._status(emitter, self._text("waiting"))
                    message = await self._wait_for_answer(session, base_url, token, chat_id, assistant_id, emitter)
                    answer = _message_text(message) or answer
                    if not answer and message.get("error"):
                        answer = f"Error from {self.valves.tool_display_name}: {message['error']}"
                await self._citations(emitter, message.get("sources"), seen_citations)

                # 5. Outlet filters (for example spelling fixes) run on the final answer.
                if self.valves.call_chat_completed and answer:
                    try:
                        completed = await _post_json(
                            session,
                            f"{base_url}/api/chat/completed",
                            token,
                            {
                                "chat_id": chat_id,
                                "id": assistant_id,
                                "session_id": session_id,
                                "model": model_id,
                                "messages": [
                                    {"id": user_id, "role": "user", "content": delegated_question},
                                    {"id": assistant_id, "role": "assistant", "content": answer},
                                ],
                                **({"filter_ids": filter_ids} if filter_ids else {}),
                            },
                        )
                        last = (completed.get("messages") or [{}])[-1] if isinstance(completed, dict) else {}
                        if isinstance(last, dict) and last.get("role") == "assistant" and last.get("content"):
                            answer = str(last["content"])
                    except Exception as exc:
                        self._log(f"chat completed error {type(exc).__name__}")

                answer = _HIDDEN_BLOCKS_RE.sub("", answer).strip()
                if message.get("_timed_out") and answer:
                    # A partial but sourced answer is still useful: say so instead of dropping it.
                    answer += f"\n\n[{self.valves.tool_display_name}: answer may be incomplete, time limit reached]"
                await self._status(emitter, self._text("done"), done=True)

            except asyncio.CancelledError:
                answer = f"The delegation to {self.valves.tool_display_name} was cancelled."
            except Exception as exc:
                self._log(f"error {type(exc).__name__}: {exc}")
                answer = f"Error while delegating to {self.valves.tool_display_name}: {type(exc).__name__}."
            finally:
                if chat_id and not await self._cleanup(session, base_url, token, chat_id, task_ids):
                    await self._status(emitter, self._text("cleanup_failed"), done=True)

        self._log(f"done in {int((time.monotonic() - started) * 1000)} ms, {len(answer)} chars")
        return answer or f"No answer was received from {self.valves.tool_display_name}."

    # Rename this method for each specialist (for example ask_ava_juriste) and rewrite
    # the docstring. The orchestrator sees the method name and the docstring, and
    # nothing else, when it decides whether to delegate.
    async def ask_ava_specialist(
        self,
        question: str,
        __user__: Optional[dict] = None,
        __request__: Any = None,
        __metadata__: Optional[dict] = None,
        __messages__: Any = None,
        __files__: Any = None,
        __event_emitter__: Any = None,
    ) -> str:
        """
        Ask the specialist assistant a question that requires its expertise, and get back its sourced answer.
        Use it only for questions in the specialist's domain. Do not use it for general conversation,
        rewriting or translation. Attached files are forwarded automatically: mention their names
        in the question instead of copying their content. Keep the specialist's answer and sources,
        even if the answer is partial; do not rewrite it from general knowledge.

        :param question: The complete, standalone question for the specialist, in the user's language, with all relevant context from the conversation.
        :return: The specialist's final answer.
        """
        return await self._delegate(
            question, __user__, __request__, __metadata__, __messages__, __files__, __event_emitter__
        )


def _message_text(message: dict) -> str:
    """Text of an assistant message. Recent Open WebUI versions store it in output[]."""
    if not isinstance(message, dict):
        return ""
    for item in reversed(message.get("output") or []):
        if isinstance(item, dict) and item.get("type") == "message" and item.get("role", "assistant") == "assistant":
            texts = [
                str(part.get("text") or "").strip()
                for part in item.get("content") or []
                if isinstance(part, dict) and part.get("type") == "output_text"
            ]
            text = "\n\n".join(t for t in texts if t)
            if text:
                return text
    content = message.get("content")
    return content.strip() if isinstance(content, str) else ""
