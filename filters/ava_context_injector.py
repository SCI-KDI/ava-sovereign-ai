"""
title: AVA Context Injector
description: Adds the user's name and today's date to the system prompt, and shares a snapshot of the conversation with later filters and tools.
author: State of Valais, Cantonal IT Service (SCI)
license: Apache-2.0
version: 1.1.0
required_open_webui_version: 0.9.0
"""

# Install as a global filter (Admin Panel > Functions), so it runs for every model.
# It must run before the other AVA filters: keep its priority at -100.
#
# What it does on each request:
#   1. Replaces name placeholders in system messages, for example {{USER_FIRSTNAME}}.
#      The names are derived from the e-mail address (firstname.lastname@...),
#      or from the profile name as a fallback.
#   2. Appends the current date to the END of the first system message. Keeping the
#      changing content late in the prompt preserves prompt caching on the model server.
#   3. Copies the recent messages to metadata["forwarded_messages"], for code that
#      receives the metadata but not the request body.

import copy
import re
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, Field

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

_MONTHS = {
    "fr": ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"),
    "de": ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September", "Oktober", "November", "Dezember"),
    "en": ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"),
}
_WEEKDAYS = {
    "fr": ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"),
    "de": ("Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"),
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
}
_DATE_TEMPLATES = {
    "fr": ("## Contexte temporel", "Nous sommes le {weekday} {day} {month} {year}", ", il est {hour:02d}:{minute:02d}", " ({tz})."),
    "de": ("## Zeitlicher Kontext", "Heute ist {weekday}, der {day}. {month} {year}", ", {hour:02d}:{minute:02d} Uhr", " ({tz})."),
    "en": ("## Date context", "Today is {weekday} {day} {month} {year}", ", {hour:02d}:{minute:02d}", " ({tz})."),
}
_CONTEXT_MARKER = "<!-- ava-context -->"

# A bare greeting needs no rewriting, search or delegation. Later filters read
# metadata["_ava_ctx"]["is_trivial_input"] to skip their model calls.
_TRIVIAL_INPUT_RE = re.compile(
    r"^(?:bonjour|bonsoir|salut|merci(?:\s+beaucoup)?|au\s+revoir|d'accord|ok|okay|parfait|super|"
    r"hello|hi|hey|thanks|thank\s+you|hallo|gr[üu]ezi|danke(?:\s+sch[öo]n)?|tsch[üu]ss)[\s!.?,;:)]*$",
    re.IGNORECASE,
)


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


def _titlecase(value: str) -> str:
    # jean-pierre -> Jean-Pierre, de la tour -> De La Tour
    return re.sub(r"([^\s\-']+)", lambda m: m.group(1).capitalize(), value)


def _names_from_user(user: dict) -> tuple[str, str]:
    """Split firstname.lastname@domain into (Firstname, Lastname).

    '.' is the separator; '-' and '_' are used only when there is no '.', so that
    compound first names stay intact: marie-anne.dupont -> ('Marie-Anne', 'Dupont').
    """
    email = str(user.get("email") or "")
    first = last = ""
    if "@" in email:
        local = re.sub(r"\d+$", "", email.split("@", 1)[0])
        if "." in local:
            first, _, last = local.partition(".")
        else:
            match = re.match(r"^([^\-_]+)[\-_](.+)$", local)
            first, last = (match.group(1), match.group(2)) if match else (local, "")
        first, last = _titlecase(first), _titlecase(re.sub(r"[._]", " ", last))
    if not first or not last:
        parts = str(user.get("name") or "").strip().split(None, 1)
        first = first or (parts[0] if parts else "")
        last = last or (parts[1] if len(parts) > 1 else "")
    return first.strip(), last.strip()


def _map_system_text(messages: list, transform) -> bool:
    """Apply transform() to the text of every system message. Returns True if one exists."""
    found = False
    for msg in messages:
        if not isinstance(msg, dict) or msg.get("role") != "system":
            continue
        found = True
        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = transform(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and block.get("type") in {"text", "input_text"}:
                    block["text"] = transform(str(block.get("text") or ""))
    return found


def _resolve_language(fallback: str, __request__: Any, __user__: Optional[dict]) -> str:
    candidates: list[str] = []
    if __request__ is not None:
        try:
            candidates += re.split(r"[,;]", __request__.headers.get("Accept-Language", ""))
        except Exception:
            pass
    if isinstance(__user__, dict):
        candidates += [str(__user__.get(k) or "") for k in ("locale", "language")]
    for tag in candidates + [fallback]:
        lang = tag.strip().split("-")[0].lower()
        if lang in _MONTHS:
            return lang
    return "en"


class Filter:
    class Valves(BaseModel):
        priority: int = Field(default=-100, description="Lower runs first. Keep this filter first.")
        default_language: str = Field(default="fr", description="Used when the browser and profile give no supported language (fr, de, en).")
        replace_name_placeholders: bool = Field(
            default=True,
            description="Replace {{USER_FIRSTNAME}}, {{USER_LASTNAME}}, {{USER_FULLNAME}} (and {{USER_PRENOM}}, {{USER_NOM}}, {{USER_EMAIL}}) in system messages.",
        )
        add_date_context: bool = Field(default=True, description="Append today's date to the system prompt.")
        include_time: bool = Field(
            default=False,
            description="Also give the time. This changes the prompt every minute, which reduces prompt caching.",
        )
        timezone_name: str = Field(default="Europe/Zurich", description="IANA time zone.")
        forward_messages: bool = Field(default=True, description="Copy recent messages to metadata['forwarded_messages'].")
        max_forwarded_messages: int = Field(default=20, description="0 copies all messages; 20 to 50 is enough in practice.")
        debug: bool = Field(default=False, description="Log what was applied. Never logs message content.")

    def __init__(self):
        self.valves = self.Valves()

    def _date_text(self, lang: str) -> str:
        now = datetime.now(timezone.utc)
        tz_name = self.valves.timezone_name
        if ZoneInfo is not None:
            try:
                now = now.astimezone(ZoneInfo(tz_name))
            except Exception:
                tz_name = "UTC"
        header, date_part, time_part, tz_part = _DATE_TEMPLATES[lang]
        values = {
            "weekday": _WEEKDAYS[lang][now.weekday()],
            "day": now.day,
            "month": _MONTHS[lang][now.month - 1],
            "year": now.year,
            "hour": now.hour,
            "minute": now.minute,
            "tz": tz_name,
        }
        text = date_part + (time_part if self.valves.include_time else "") + tz_part
        return f"{_CONTEXT_MARKER}\n{header}\n{text.format(**values)}"

    async def inlet(
        self,
        body: dict,
        __user__: Optional[dict] = None,
        __request__: Any = None,
        __task__: Any = None,
        __chat_id__: Optional[str] = None,
        **kwargs: Any,
    ) -> dict:
        # Title, tag and follow-up generation do not need any of this.
        if __task__ or not isinstance(body, dict):
            return body

        # Mutate the existing metadata dict in place: Open WebUI passes this same
        # object to the tools, which is how they see forwarded_messages.
        metadata = body.setdefault("metadata", {})
        if not isinstance(metadata, dict) or metadata.get("_ava_ctx_applied"):
            return body
        messages = body.get("messages")
        if not isinstance(messages, list):
            return body

        lang = _resolve_language(self.valves.default_language, __request__, __user__)
        user = __user__ or {}
        first, last = _names_from_user(user) if self.valves.replace_name_placeholders else ("", "")
        full = f"{first} {last}".strip()

        if self.valves.replace_name_placeholders:
            mapping = {
                "{{USER_FIRSTNAME}}": first,
                "{{USER_LASTNAME}}": last,
                "{{USER_FULLNAME}}": full,
                "{{USER_PRENOM}}": first,
                "{{USER_NOM}}": last,
                "{{USER_EMAIL}}": str(user.get("email") or ""),
            }

            def replace(text: str) -> str:
                for placeholder, value in mapping.items():
                    text = text.replace(placeholder, value)
                return text

            _map_system_text(messages, replace)

        # The snapshot is taken before the date is added, so it holds only the real conversation.
        if self.valves.forward_messages:
            limit = self.valves.max_forwarded_messages
            snapshot = messages[-limit:] if limit > 0 else messages
            metadata["forwarded_messages"] = copy.deepcopy(snapshot)

        if self.valves.add_date_context:
            date_text = self._date_text(lang)
            appended = {"done": False}

            # Only the first system message is extended, so the date sits after the
            # stable system prompt and before the conversation.
            def append_once(text: str) -> str:
                if appended["done"] or _CONTEXT_MARKER in text:
                    return text
                appended["done"] = True
                return f"{text.rstrip()}\n\n{date_text}"

            if not _map_system_text(messages, append_once):
                messages.insert(0, {"role": "system", "content": date_text})

        last_user = next(
            (_normalize_content(m.get("content")) for m in reversed(messages) if isinstance(m, dict) and m.get("role") == "user"),
            "",
        )
        metadata["_ava_ctx"] = {
            "language": lang,
            "chat_id": __chat_id__,
            "user_firstname": first,
            "user_lastname": last,
            "user_fullname": full,
            "is_trivial_input": len(last_user) <= 40 and bool(_TRIVIAL_INPUT_RE.match(last_user)),
        }
        metadata["_ava_ctx_applied"] = True

        if self.valves.debug:
            print(
                f"[AVA Context Injector] chat={__chat_id__ or '-'} lang={lang} "
                f"names={'yes' if full else 'no'} forwarded={len(metadata.get('forwarded_messages') or [])}"
            )
        return body
