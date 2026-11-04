# Models

AVA only uses open-weight models, through OpenAI-compatible APIs. That lets the same Open WebUI configuration, extensions and prompts work with a hosted provider or with your own servers: only URLs, keys and model IDs change.

## What we use

The State of Valais runs AVA's inference on its own GPU servers, with NVIDIA H200 GPUs. Prompts, documents and answers stay on the State's infrastructure. See [host the models yourself](#host-the-models-yourself).

For organisations without GPUs, a Swiss hosted provider is an alternative: see [use a hosted provider](#use-a-hosted-provider).

The models we use:

| Role | Model | Notes |
|---|---|---|
| Orchestrator and specialists | `Qwen 3.8 Flash` | Mixture of experts: large model quality, 17B active parameters per token. Reliable tool calling in French and German |
| Document tools | `Qwen 3.8 Flash` | Anonymisation, translation, document conversion |
| Routing and classification | `google/gemma-4-31B-it`, `mistralai/Ministral-3-14B-Instruct-2512` | Short JSON decisions before the answer |
| Vision fallback for scans | `mistral-small-3.2-24b-instruct-2506` | When text extraction and OCR find nothing |
| Embeddings | `BAAI/bge-multilingual-gemma2` | Good in French and German, the languages of our documents |
| Reranker | `BAAI/bge-reranker-v2-m3` | Multilingual |
| Speech to text | Whisper 3 | Meeting minutes |

Open-weight models are released often. Test before switching: a model change is a behaviour change, so it goes through the same [evaluation](operations.md#evaluate-before-each-change) as a prompt change.

## Choose a main model

The orchestrator and the specialists need a model that:

1. **Calls tools reliably.** Delegation, legal search and document tools all depend on it. Test with Open WebUI's **native** function calling, with several tools enabled at once.
2. **Writes well in your languages.** For us: French, and Swiss Standard German without ß.
3. **Follows long system prompts.** The legal prompt is about 30,000 characters.
4. **Has enough context.** A delegation sends recent turns, retrieved passages and tool results. Aim for 128k tokens.

Small models are useful for routing and titles, not for specialists. Our 10-question routing benchmark scored 10 out of 10 with the 397B model; test any smaller model on it before relying on it. See [evaluation](operations.md#evaluate-before-each-change).

### Reasoning models

Qwen3.5 can "think" before answering. Thinking improves hard legal questions, but costs time and tokens:

- For short JSON calls, such as routing or classification, turn it off. On vLLM-based servers, send `"chat_template_kwargs": {"enable_thinking": false}`. The [intent router](../filters/intent_router.py) does this by default.
- For direct API calls with a token limit, reasoning can use the whole limit and leave an empty answer. Turn reasoning off with `reasoning_effort: "none"` where the provider supports it, or raise `max_tokens`. We use 16,000 tokens.
- Remove leaked `<think>` blocks before passing an answer on. The delegation tool does.

## Use a hosted provider

This is the fastest way to start, and an option for Swiss organisations that do not run GPUs. The example uses [Infomaniak](https://www.infomaniak.com/), a Swiss provider that operates its own data centres in Switzerland, through its AI Tools API. Any OpenAI-compatible provider works the same way.

1. In the provider's console, create an AI Tools product and an API token. Store the token in your secret manager.
2. Find your product ID:

   ```bash
   curl -s -H "Authorization: Bearer $INFOMANIAK_TOKEN" https://api.infomaniak.com/1/ai
   ```

   The answer lists your products with their `product_id`.
3. Check that the OpenAI-compatible endpoint works:

   ```bash
   BASE="https://api.infomaniak.com/2/ai/$PRODUCT_ID/openai/v1"
   curl -s -H "Authorization: Bearer $INFOMANIAK_TOKEN" "$BASE/models"
   ```

4. In Open WebUI, go to **Admin Panel > Settings > Connections** and add an **OpenAI API** connection with `$BASE` and the token. Its models appear in the model selector.
5. Use the same base URL and token for the embeddings, in **Admin Panel > Settings > Documents**.

Check the provider's current documentation: the v1 routes are deprecated in favour of the v2 routes shown here.

The product ID identifies your account. Treat it as configuration, not as a secret, but do not publish it.

### Data protection with a provider

A hosted provider receives every prompt, retrieved passage and tool result. Before production, check:

- where the data is processed and stored, including backups and logs;
- whether prompts and answers are logged, and for how long;
- whether they are used to train models;
- the data processing agreement, sub-processors, and the legal basis for this processing under your data protection law;
- what your prompts tell users. If the prompt says "data never leaves our premises", it must be true.

## Host the models yourself

This is how the State of Valais runs AVA, on NVIDIA H200 GPUs. The examples below use [vLLM](https://docs.vllm.ai/), which exposes the same OpenAI-compatible API as a hosted provider: Open WebUI does not notice the difference.

### Main model

```bash
vllm serve Qwen/Qwen3.5-122B-A10B-FP8 \
  --served-model-name ava-llm \
  --tensor-parallel-size 4 \
  --max-model-len 131072 \
  --enable-prefix-caching \
  --enable-auto-tool-choice \
  --tool-call-parser <parser for this model> \
  --reasoning-parser <parser for this model> \
  --api-key "$VLLM_API_KEY"
```

- `--enable-auto-tool-choice` and the right `--tool-call-parser` are mandatory for delegation. The model card and vLLM's tool calling documentation name the parser for each model family.
- `--enable-prefix-caching` makes long, stable system prompts much cheaper on every request after the first.
- `--tensor-parallel-size` is the number of GPUs the model is split across. As an order of magnitude, an FP8 model needs about 1 GB of GPU memory per billion parameters, plus memory for the context cache. With 80 GB GPUs, the 122B model fits on four, and the 397B model needs eight. An H200 has 141 GB, so the same models need about half as many GPUs; check that enough memory remains for the context cache at your expected number of parallel users.

**Check:** `curl -H "Authorization: Bearer $VLLM_API_KEY" http://<vllm-host>:8000/v1/models` lists `ava-llm`. Then ask a question in Open WebUI with a tool enabled, and check in the vLLM log that the request contains `tool_calls`.

### Embeddings and reranker

Both models run on vLLM as pooling models, on a single GPU. Check vLLM's documentation for the pooling options of your version. Open WebUI's built-in reranker can also run `BAAI/bge-reranker-v2-m3` on CPU, which is slower but needs no extra service.

### Speech

| Need | Open-source option | API |
|---|---|---|
| Speech to text | [Speaches](https://github.com/speaches-ai/speaches), with faster-whisper | OpenAI `/v1/audio/transcriptions` |
| Speech to text, better French | Voxtral (Mistral, Apache 2.0) on vLLM | OpenAI `/v1/audio/transcriptions` |
| Text to speech | [Kokoro-FastAPI](https://github.com/remsky/Kokoro-FastAPI) | OpenAI `/v1/audio/speech` |

Set them in **Admin Panel > Settings > Audio**.

## Hosted or self-hosted?

| | Hosted provider | Self-hosted |
|---|---|---|
| Start | Hours | Weeks to months, including hardware procurement |
| Cost | Per token | GPUs, power, operations staff |
| Data control | Contract, and trust in the provider | Full |
| Model catalogue | The provider's | Any open-weight model your GPUs can hold |
| Operations | Provider | You: drivers, upgrades, capacity, monitoring |

A hosted provider is a sensible way to start and to learn what users need before buying hardware. The State of Valais now runs its inference on its own H200 GPUs. Because everything goes through OpenAI-compatible APIs and open-weight models, Open WebUI, the extensions and the prompts stay the same either way: only URLs, keys and model IDs change.
