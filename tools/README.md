# Tools

Python tools that the model can call. Open WebUI runs them on its server, so read the code before installing one.

| File | What it does | Needs |
|---|---|---|
| [`ask_ava_specialist.py`](ask_ava_specialist.py) | Delegates a question to a hidden specialist model and returns its answer and sources | `aiohttp` (already in the Open WebUI image) |

## Install the delegation tool

You install one copy per specialist. For a legal specialist with the model ID `ava-juriste`:

1. In **Workspace > Tools**, click **+**, then paste the content of `ask_ava_specialist.py`.
2. At the bottom of the file, rename the method `ask_ava_specialist` to `ask_ava_juriste`.
3. Rewrite its docstring. The orchestrator reads only the method name and this text when it decides to delegate, so say precisely what belongs to this specialist, and what does not. Example:

   ```python
   """
   Ask AVA Juriste a question that needs precise legal analysis: interpretation or application
   of Swiss federal or cantonal law, case law, legal bases, procedures, deadlines, remedies,
   authority competence, or legal drafting that depends on legal analysis.
   Prefer ask_ava_rh for simple operational HR questions. Do not use it for general conversation,
   rewriting or translation without legal analysis, or software questions.
   Attached files are forwarded automatically: mention their names instead of copying their content.
   Keep the answer and its sources, even if partial; do not rewrite it from general knowledge.

   :param question: The complete, standalone legal question, in the user's language, with the relevant facts.
   :return: AVA Juriste's answer, with its sources.
   """
   ```

4. Give the tool the ID `ask_ava_juriste` and save it.
5. Open the gear icon and set the valves below.

### Valves

| Valve | Default | Set it to |
|---|---|---|
| `tool_model_id` | `ava-specialist` | The specialist's model ID, for example `ava-juriste` |
| `tool_display_name` | `AVA Specialist` | The name shown in progress messages, for example `AVA Juriste` |
| `status_language` | `fr` | `fr`, `de` or `en` |
| `openwebui_base_url` | `http://localhost:8080` | Keep it, unless Open WebUI listens on another port in its container |
| `max_tool_calls` | `0` | A research budget, for example `6` for legal research. `0` adds no instruction |
| `max_wait_seconds` | `400` | Longest wait. Raise it for heavy research, and check your ingress timeout too |
| `forward_conversation_context` | `true` | Sends the last `max_forwarded_messages` turns, so follow-up questions make sense |
| `delete_delegated_chat` | `true` | Keeps the user's chat list clean. Users must have permission to delete chats |
| `debug` | `false` | Logs timings and IDs, never content |

### Check that it works

Before attaching the tool to the orchestrator, check that:

- as an administrator, the specialist answers correctly when you chat with it directly;
- in a chat with any model, with only this tool enabled, a question in the specialist's domain shows "Je transmets la question à AVA Juriste…", then an answer with sources;
- no "Delegated to AVA Juriste" chat remains in the sidebar afterwards.

## What the tool sends

The temporary chat belongs to the current user, and runs with their permissions. The specialist receives:

- the question written by the orchestrator, preceded by a language directive, today's date and the optional tool budget;
- the last turns of the conversation, without reasoning traces;
- references to the attached files, never their extracted content;
- its own tools, filters and knowledge, read from its model record.

The details are in [the architecture](../docs/architecture.md#how-delegation-works).

## Tools we have not published yet

These run in our production. We will publish them once their configuration is generic:

| Tool | What it does | Until then |
|---|---|---|
| Fedlex search | Swiss federal law by title, article and version | Connect the public [Fedlex Connector](https://github.com/JayTheSkier/fedlex-connector) MCP server |
| OpenCaseLaw search | Swiss case law, laws and doctrine | Connect the public [OpenCaseLaw](https://github.com/jonashertner/opencaselaw) MCP server |
| `retrieve_exact_law` | Full text of a cantonal article, from a knowledge base with one law per file | Use Open WebUI's knowledge search |
| Grand Council search | Data of the Valais Parliament | — |
| `ask_ava_pv` | Transcribes a recording, then delegates the minutes | See [meeting minutes](../docs/meeting-minutes.md) |

[Step 11 of the build guide](../docs/build-guide.md#step-11-connect-the-legal-sources) explains how to connect the two MCP servers.

## File conventions

Start each file with the Open WebUI header:

```python
"""
title: Tool name
description: One sentence on what it does
author: State of Valais, Cantonal IT Service (SCI)
license: Apache-2.0
version: 1.0.0
required_open_webui_version: 0.9.0
requirements: package-a, package-b
"""
```

- Configure everything specific to a deployment through valves: URLs, model IDs, certificates and keys. Never put a secret or an internal hostname in the code.
- Mark secret valves with `json_schema_extra={"input": {"type": "password"}}`.
- Never log message content or personal data, even in debug mode.
- Calls to Open WebUI itself use `aiohttp.ClientSession(trust_env=False)`, so they do not go through a corporate proxy. Calls to external services use `trust_env=True`.
