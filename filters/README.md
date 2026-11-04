# Filters

Python code that Open WebUI runs on each request (`inlet`) or response (`outlet`). A filter delays every request it applies to: keep filters fast, and skip work when it is not needed.

| File | Priority | Install as | What it does |
|---|---|---|---|
| [`ava_context_injector.py`](ava_context_injector.py) | -100 | Global | Replaces name placeholders, adds today's date, shares the conversation with tools |
| [`intent_router.py`](intent_router.py) | 5 | On the orchestrator | Rewrites follow-ups into standalone requests and suggests a specialist |

Lower priorities run first. The context injector must run before the router, because the router reads the language and the greeting flag that the injector computes.

## Install a filter

1. In **Admin Panel > Functions**, click **+**, paste the file, and save.
2. Turn the function on with its switch.
3. Either turn on **Global**, from the function's menu, to apply it to every model, or leave it off and select it in each model's **Filters** section.
4. Open the gear icon and set the valves.

## AVA Context Injector

Install it as **Global**. It needs no valves to work.

- In system prompts, it replaces `{{USER_FIRSTNAME}}`, `{{USER_LASTNAME}}`, `{{USER_FULLNAME}}`, and the French aliases `{{USER_PRENOM}}` and `{{USER_NOM}}`. The names come from the e-mail address, `firstname.lastname@…`, or from the profile name. Open WebUI's own variables, such as `{{USER_NAME}}` and `{{CURRENT_DATE}}`, keep working.
- It appends today's date to the end of the first system message. The time is off by default, because it would change the prompt every minute and defeat prompt caching.
- It copies the recent messages to `metadata["forwarded_messages"]`.

**Check:** add `Tu parles à {{USER_FULLNAME}}.` to a model's system prompt, and ask it "Comment je m'appelle, et quel jour sommes-nous ?".

## AVA Intent Router

Attach it to the orchestrator. It calls a model before each answer, and that delay is added to every turn. Choose a small model that answers in a few seconds. Our production version used a 31B model with a much longer prompt, and cost about 27 seconds per turn; that was the largest single source of latency in our [performance audit](../docs/operations.md#latency-where-the-seconds-go).

| Valve | Set it to |
|---|---|
| `llm_base_url`, `llm_api_key`, `llm_model` | An OpenAI-compatible endpoint and a small model. Without them, the filter does nothing |
| `specialists_json` | Your delegation tools and one line on each domain. Tool names must match exactly |
| `organisation_context` | One sentence on who your users are |
| `audio_tool_name` | The tool forced when a recording is attached, or empty |
| `min_confidence` | `0.6`. Below it, the turn continues unchanged |
| `disable_thinking` | `true` for vLLM-based servers; turn it off if your provider returns an error |

What it does to a request:

1. It skips tasks such as title generation, greetings, very long inputs, and the specialists' temporary chats.
2. It sends the last few turns to the routing model, which returns JSON.
3. If the model is confident enough, it appends a block to the user's message, for this request only:

   ```text
   [AVA_ROUTING]
   Requête canonique pour ce tour uniquement :
   Quel est le délai de recours contre une décision de l'administration cantonale ?

   Spécialiste(s) suggéré(s), dans cet ordre : ask_ava_juriste. Délègue-leur la requête, sauf si elle ne relève clairement pas de leur domaine.
   [/AVA_ROUTING]
   ```

4. It stores the decision in `metadata["ava_routing"]`.

The orchestrator prompt in [`system-prompts/ava.md`](../system-prompts/ava.md) explains this block to the model.

**Check:** with `debug` on, ask a legal question, then "et en allemand ?". The server log shows the decision and its latency, never the content.

**You can do without it.** Tool docstrings and the orchestrator prompt are enough to route most questions. The router helps with follow-up questions ("et pour un apprenti ?") and recordings. Measure both before deciding.

## Filters we have not published yet

| Filter | What it does |
|---|---|
| File extraction | Builds a map of the text Open WebUI extracted from uploads, with OCR and a vision-model fallback for scans |
| Voice trigger | "Ava, enregistre" starts a recording in the browser; see [meeting minutes](../docs/meeting-minutes.md) |
| Specialist router | Switches the request directly to the specialist when exactly one is chosen, which saves the orchestrator's second pass |
| Reasoning sanitizer | Removes stray reasoning tags and unparsed tool calls from answers |
| Swiss German spelling | Replaces ß with ss in answers (outlet) |

Filters use the same file header and rules as [tools](../tools/README.md#file-conventions).
