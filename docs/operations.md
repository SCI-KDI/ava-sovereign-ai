# Operations

What it takes to keep AVA running, and what we learned doing it.

## What we run

For more than 2,000 users during the pilot:

| Component | Replicas | Memory | CPU | Storage |
|---|---|---|---|---|
| Open WebUI | 1 | 2 GiB requested, 12 GiB limit | 2 cores limit | 15 GiB volume |
| Apache Tika | 1 | 512 MiB requested, 4 GiB limit | 1 core | None |
| Redis (Valkey) | 1 | Small | Small | None |
| PostgreSQL with pgvector | Shared server | — | — | — |
| Models | Own GPU servers, NVIDIA H200 | — | — | — |

Open WebUI itself needs little CPU: the models do the heavy work. Its memory grows with document processing and concurrent users. Tika needed 4 GiB in production.

We have two environments, test and production, with the same configuration and different hosts.

## Upgrade Open WebUI

Open WebUI releases often, and its internal API, which the delegation tools use, can change. Our routine:

1. **Read the release notes** for every version between yours and the target.
2. **Back up** the database and the data volume.
3. **Upgrade the test environment first**, by changing only `image.tag`.
4. **Run the test questions** (see [evaluation](#evaluate-before-each-change)), including a delegation, a document upload and a sign-in.
5. **Upgrade production** the same way, outside working hours.

Keep one replica and the `Recreate` strategy: database migrations run at start-up, and two versions must never run them at the same time. Expect a minute of downtime.

Pin the exact version. One minor release broke Tika document extraction for us after a few days on a moving tag; since then, we test and pin every version before it reaches production.

Do not let pinning delay security fixes. Follow Open WebUI's security advisories, and fast-track a release that fixes a vulnerability through the same routine.

After an upgrade, **check the valves**. Updating a tool or function's code does not overwrite the valve values already saved in the interface, so a new default does not apply until you change it.

## Run several replicas

We still run one replica. Several replicas need:

- Redis, with `REDIS_URL`, `WEBSOCKET_MANAGER=redis` and `WEBSOCKET_REDIS_URL`, on two database indexes;
- the same `WEBUI_SECRET_KEY` on every replica;
- an external database and vector store: PostgreSQL with pgvector;
- a shared volume for uploaded files, or object storage.

Our first attempts at two replicas, before Redis was in place, failed. Add Redis first, then test with two replicas in your test environment under load.

## Proxies and TLS inspection

Many administrations route outbound traffic through a proxy that inspects TLS. Symptoms: model calls time out, or fail with certificate errors, while they work from your workstation.

- **Trust the proxy's CA.** Mount your CA bundle into the pod and set `SSL_CERT_FILE` and `REQUESTS_CA_BUNDLE` for Python. For Node.js services, set `NODE_EXTRA_CA_CERTS`.
- **Set `HTTP_PROXY`, `HTTPS_PROXY` and `NO_PROXY`** on the pod. Put the cluster's internal domains and `localhost` in `NO_PROXY`.
- **`aiohttp` ignores these variables by default.** In tools, external calls need `aiohttp.ClientSession(trust_env=True)`. `httpx` honours them by default. We lost time on an "aiohttp: Server disconnected" error that was only a missing proxy.
- **Keep calls to Open WebUI itself inside the pod.** The delegation tool calls `http://localhost:8080` with `trust_env=False`, so they never reach the proxy or the ingress.
- **Ask for each external host to be allowed** before you need it: model provider, MCP servers, legal portals.

## Timeouts

| Where | Our value | Why |
|---|---|---|
| Ingress or route of Open WebUI | 10 minutes | Long research and transcriptions |
| Ingress or route of the website gateway | 10 minutes | Long model calls |
| Delegation tool, `max_wait_seconds` | 400 seconds | Legal research with several tool calls |
| Intent router | 20 to 30 seconds | After that, continue without routing |

Long operations should also send a status update at least every 15 seconds, so that no proxy closes an idle connection.

## Sign-in and access

- **SSO only.** We turned off the login form and password sign-in (`ENABLE_LOGIN_FORM=false`, `ENABLE_PASSWORD_AUTH=false`). Check that an administrator can sign in through SSO before you do.
- **Groups from the identity provider** become Open WebUI groups (`ENABLE_OAUTH_GROUP_MANAGEMENT`). Grant models to groups, not to individuals.
- **Secure cookies:** `WEBUI_SESSION_COOKIE_SECURE=true` and `WEBUI_AUTH_COOKIE_SECURE=true`.
- **API keys:** users cannot create them. Service accounts can only reach the endpoints they need (`ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS`).
- **Security headers:** set HSTS, `X-Content-Type-Options` and a referrer policy at your ingress. Test any Content Security Policy carefully: Open WebUI loads code for tools and rendering.

## Backups

Back up three things, and test the restore:

1. **The database:** accounts, chats, models, tools, functions, valves, knowledge metadata and, with pgvector, the document vectors. A nightly `pg_dump` is enough to start.
2. **The data volume** (`/app/backend/data`): uploaded files and caches.
3. **Your extensions and prompts, in git.** Treat the interface as a deployment target, and git as the source of truth. Valves are the exception: they hold secrets, so keep them in your secret manager, and note their non-secret values in your runbook.

## Latency: where the seconds go

Slowness was the main complaint during our pilot. A code review of the legal path, with log timings where we had them, found five layers. The figures are estimates for one turn, not benchmarks:

| Layer | What happens | Cost | What helps |
|---|---|---|---|
| Routing before the answer | One call to a 31B model with a long prompt | About 27 seconds | A keyword fast path for obvious cases, a result cache, skipping greetings, a smaller model |
| Question preparation | An optional rewrite of the delegated question by another model | About 38 seconds | Leave it off unless it measurably improves answers |
| Delegation | The orchestrator's turn, then the specialist's full turn, then polling and cleanup | One extra model turn | Fast polling at the start; use the stream directly when it is complete |
| Tool turns | 3 to 4 sequential tool calls for a simple question, 5 to 8 to prove an absence | 3 to 8 model round trips | Tool budget in the prompt, three answer levels, one grouped search tool instead of three |
| Tool internals | Calls to the legal sources | Small | Parallel calls, and identical calls running at the same time merged into one |

The lesson: **count model round trips before the first word.** Each one costs seconds, whatever the hardware. Our target is a median under 30 seconds for a simple legal question.

To measure, log one line per request with the elapsed time of each step, and nothing else. Our filters log a line such as `[AVA-TIMING] filter=intent_carrier total_ms=… encoder_ms=… cache_hit=…`.

## Evaluate before each change

A prompt, model, tool or version change is a behaviour change. Before each one, rerun the same questions and compare.

**Routing.** We keep a small benchmark. In one message, we ask the orchestrator to describe, for each request, which tools and specialists it would call, in which order, and with which role, without answering. A person then checks each plan. Our ten requests:

| # | Request | What it tests |
|---|---|---|
| 1 | "Who is the Pope?" | A general question that needs no specialist |
| 2 | "When will I receive my next salary?" | An HR question |
| 3 | A sentence with an internal acronym, to be translated into German | Acronym resolution before translation |
| 4 | "Apply the official PowerPoint template", with a PPTX attached | A document tool on an attachment |
| 5 | "Find any sharp price increase in this document", with a document of several hundred pages | A document too large for the context |
| 6 | "I need a press release about the Japanese beetle" | Institutional communication |
| 7 | "Translate this PDF into German" | File translation, rather than translating the text by hand |
| 8 | "Write an official document on renovating buildings in agricultural zones, citing the relevant articles" | Two steps: legal research, then a document |
| 9 | "Transcribe this meeting and translate it into German", with a recording | Two steps: minutes, then translation |
| 10 | "Which MPs voted no in the final vote on the airport law?" | Parliament data |

Our last run scored 10 out of 10 with Qwen3.8 Flash. Write your own requests from real ones, including the ones that went wrong.

**Answers.** For each specialist, keep a golden set of questions with the expected answer and sources. Ours, for the legal specialist, has about twenty questions from real requests, on federal law, cantonal law and case law. Run each question several times: the same question can be routed or answered differently from one run to the next. Check:

- the answer's conclusion, against the expected one;
- every cited article and decision, against the source;
- the closing line, whose absence shows a truncated answer;
- the time to the complete answer.

## Lessons from production

| What happened | What we do now |
|---|---|
| The legal specialist dated its answers a year too early | The delegation tool gives the specialist today's date: temporary chats skip the orchestrator's filters |
| The specialist cited plausible but non-existent cantonal law numbers | Hard rule in the prompt: no number unless seen in a tool result this turn |
| Fedlex links built from memory returned 404, and federal law numbers are sometimes reused | Links come only from tool results; numbers are resolved against current, non-repealed acts |
| A knowledge base searched under an approximate name returned another base's content | The prompt gives the exact knowledge base names |
| A relevance threshold of 0 injected irrelevant passages into every answer | Threshold 0.05, with reranking |
| A prompt referred to tools that did not exist, and the model kept calling them | Prompts and tool names are reviewed together |
| A generated "state of the law on…" line was often wrong | Removed from the answer format |
| A filter variable passed as a boolean crashed requests | Variables are always strings: Open WebUI substitutes them with `str.replace()` |
| The volume size in our values was smaller than the live volume, which blocked deployments | Values match the live size: a volume can grow, never shrink |
