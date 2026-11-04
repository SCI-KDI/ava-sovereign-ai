# System prompts

One Markdown file per model, ready to paste into the model's **System Prompt** field. They are in French, as in production. The models answer in the user's language, French or German.

| File | Model ID | Role | Tools it expects |
|---|---|---|---|
| [`ava.md`](ava.md) | `ava` | Orchestrator: general help, routing to specialists | The `ask_ava_*` delegation tools |
| [`ava-juriste.md`](ava-juriste.md) | `ava-juriste` | Swiss federal and Valais law, case law | Fedlex and OpenCaseLaw (MCP), a cantonal law knowledge base, `retrieve_exact_law` |
| [`ava-rh.md`](ava-rh.md) | `ava-rh` | Human resources and personnel law | An HR knowledge base, optionally an intranet search tool |
| [`ava-pv.md`](ava-pv.md) | `ava-pv` | Meeting minutes from a transcript | None: the transcript arrives in the message |
| [`ava-coding.md`](ava-coding.md) | `ava-coding` | Software development help | None |

The communication and Grand Council specialists depend on internal sources, so we have not published their prompts.

## Adapt before use

1. **Replace every `[À ADAPTER : …]` block.** In `ava.md` and `ava-juriste.md`, these blocks hold what the assistant says about hosting and personal data. Write only facts that your data protection officer has validated. A prompt that promises "data never leaves our premises" while a model runs at a provider is a compliance problem, not a wording problem.
2. **Match the tool names.** The prompts name tools such as `retrieve_exact_law` or `search_opencaselaw_bundle`. Rename them to your tools, or delete the passages about tools you do not have. A prompt that refers to missing tools makes the model call tools that fail.
3. **Match the knowledge base names.** `ava-juriste.md` names two example knowledge bases, `Recueil cantonal` and `Recueil fédéral`. Replace them with the exact names of your bases, or the search can resolve to another base.
4. **Change the institutions.** References to Valais law (RS/VS, lex.vs.ch) and to Valais institutions are examples. A specialist in your domain should rewrite them.
5. **Test with real questions** before and after each change. See [evaluation](../docs/operations.md#evaluate-before-each-change).

## Conventions that matter

These rules came from production incidents. Keep them when you adapt the prompts:

- **A fixed closing line** ("Ce message est généré par AVA…") lets you, and the orchestrator, see when an answer was cut off.
- **"Never cite a number not seen in a tool result this turn."** Without this rule, the legal specialist produced plausible but invented law numbers.
- **A tool budget** (6 calls) and three answer levels (N1, N2, N3) keep simple questions fast.
- **The orchestrator keeps partial specialist answers.** Otherwise it may throw away a sourced partial answer and rewrite one from general knowledge.
- **No date line in the answer format.** A generated "state of the law on…" line was often wrong. The date is given to the model as context instead.

Placeholders such as `{{USER_NAME}}` and `{{CURRENT_DATE}}` are filled by Open WebUI. `{{USER_PRENOM}}` and `{{USER_NOM}}` are filled by [`ava_context_injector.py`](../filters/ava_context_injector.py).

These prompts do not make a model's answers legal or professional advice.
