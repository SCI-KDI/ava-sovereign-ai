# Third-party licences

The Apache 2.0 licence in this repository covers only our own work: tools, filters, prompts, documentation and configuration examples. It does not change the licence of anything we rely on. Check each licence, and each service's terms, at the version you deploy.

## Open WebUI

[Open WebUI](https://github.com/open-webui/open-webui) is under the Open WebUI License: BSD-style, with a branding clause, and not OSI-approved.

You may change or remove its branding, including its name and logo, only:

- with 50 end users or fewer in a rolling 30-day period;
- or with written permission from the Open WebUI maintainers;
- or with an enterprise licence.

Our examples leave the branding unchanged. Read [the licence](https://github.com/open-webui/open-webui/blob/main/LICENSE) yourself before changing the name, logo or favicon of your instance.

## Software

| Component | Licence | Used for |
|---|---|---|
| PostgreSQL | PostgreSQL License | Database |
| pgvector | PostgreSQL License | Vector search |
| Apache Tika | Apache 2.0 | Text extraction |
| Valkey | BSD 3-Clause | Shared state and websockets |
| Redis | Varies by version: check before use | Alternative to Valkey |
| Ollama | MIT | Local evaluation only |
| vLLM | Apache 2.0 | Self-hosted models |
| ffmpeg | LGPL or GPL, depending on the build | Audio conversion for meeting minutes |

## Models

Model licences vary, and some restrict use. Read each model card at the version you use.

| Model | Licence, as stated by its publisher | Check |
|---|---|---|
| Qwen3.8 | See the model card of each checkpoint | Licence and any use conditions |
| Gemma 4 | See the model card: Google has used different terms across Gemma generations | Licence and any use restrictions |
| Mistral, Ministral | Varies by model | Each model card |
| `BAAI/bge-multilingual-gemma2` | Based on Gemma 2: Gemma terms apply | The model card |
| `BAAI/bge-reranker-v2-m3` | Apache 2.0 | The model card |
| Whisper | MIT | — |

## Services

These are public services operated by third parties. Their terms apply, and they receive the queries you send them.

| Service | Licence of code and data | Used for |
|---|---|---|
| [Fedlex Connector](https://github.com/JayTheSkier/fedlex-connector), by Jeremy Bacharach | MIT | Swiss federal law over MCP |
| [OpenCaseLaw](https://github.com/jonashertner/opencaselaw) | MIT code, CC0 data | Swiss case law, laws and doctrine over MCP |
| Model providers | Commercial terms | Hosted models |
