# Build guide

This guide rebuilds AVA's structure with Open WebUI, in two parts:

- **[Part 1](#part-1-try-it-on-a-laptop)** runs on a laptop in about 30 minutes. You finish with an orchestrator that delegates to one specialist. It is the quickest way to see how the pieces fit.
- **[Part 2](#part-2-build-it-for-production)** builds the production setup on Kubernetes or OpenShift: real models, single sign-on, document search, legal sources and the routing filter.

Build the smallest version first, then add one specialist at a time.

> **Versions.** Everything here was tested with Open WebUI **v0.12.4**. Open WebUI changes quickly: pin one version, and check menus and settings against [its documentation](https://docs.openwebui.com/) for that version. Many settings are stored in the database after first start. If an environment variable seems to have no effect, check **Admin Panel > Settings**.

## Vocabulary

| Term | Meaning in Open WebUI |
|---|---|
| **Workspace model** | A base model plus a system prompt, tools, knowledge and parameters. The orchestrator and each specialist are workspace models |
| **Tool** | Python code the model decides to call. The delegation tools are tools |
| **Function / filter** | Python code that runs on every matching request or response, without the model deciding |
| **Valves** | Settings of a tool or filter, edited in the interface with the gear icon |
| **Knowledge** | A collection of documents, indexed for search, that you attach to a model |

---

## Part 1: Try it on a laptop

**You need:** Docker with Compose, 8 GB of free RAM, and this repository.

### Step 1: Start the stack

```bash
git clone https://github.com/SCI-KDI/AVA.git
cd AVA
cp .env.example .env
```

Edit `.env` and replace both `change-me` values. For the secret key, use the output of `openssl rand -hex 32`. Then start the stack:

```bash
docker compose up -d
docker compose logs -f model-pull    # wait for "success", then Ctrl+C
```

This starts PostgreSQL with pgvector, Ollama with a small model, Apache Tika for document extraction, and Open WebUI.

Open <http://localhost:3000> and create the first account. It becomes the administrator.

**Check:** the model selector at the top of a new chat lists `qwen3:4b`.

### Step 2: Connect a better model (recommended)

A 4B model on a laptop shows the mechanics, but often misses tool calls. If you can, add a stronger model through any OpenAI-compatible API:

1. Go to **Admin Panel > Settings > Connections**.
2. Under **OpenAI API**, click **+**.
3. Enter the base URL and key: your own model server, or a hosted provider. For Infomaniak, a Swiss provider, the URL is `https://api.infomaniak.com/2/ai/<PRODUCT_ID>/openai/v1`; see [models](models.md#use-a-hosted-provider).
4. Save, then check that the new models appear in the model selector.

### Step 3: Install the context injector

1. Go to **Admin Panel > Functions** and click **+**.
2. Paste the content of [`filters/ava_context_injector.py`](../filters/ava_context_injector.py) and save.
3. Turn it on with its switch, then turn on **Global** in its menu.

**Check:** in a new chat, ask "Quel jour sommes-nous ?". The model gives today's date.

### Step 4: Create a specialist

We start with the coding specialist, because it needs no tools or documents.

1. Go to **Workspace > Models** and click **+**.
2. Name it `AVA Coding` and set the model ID to `ava-coding`.
3. Choose your best model as the base model.
4. Paste [`system-prompts/ava-coding.md`](../system-prompts/ava-coding.md) into **System Prompt**.
5. Under **Advanced Params**, set **Function Calling** to **Native**.
6. Save.

**Check:** chat with `AVA Coding` directly and ask for a Python function. The answer follows the prompt's format: solution, code, security points, how to verify.

### Step 5: Create the orchestrator and test the delegation

1. Go to **Workspace > Tools** and click **+**. Paste [`tools/ask_ava_specialist.py`](../tools/ask_ava_specialist.py).
2. At the bottom of the file, rename the method `ask_ava_specialist` to `ask_ava_coding`. Replace the first sentence of its docstring with: "Ask AVA Coding a software development question: writing, reviewing, debugging or securing code, or software architecture."
3. Set the tool ID to `ask_ava_coding` and save.
4. Open the tool's gear icon. Set `tool_model_id` to `ava-coding` and `tool_display_name` to `AVA Coding`.
5. Go to **Workspace > Models** and create a model named `AVA` with the ID `ava`, the same base model, and [`system-prompts/ava.md`](../system-prompts/ava.md) as its system prompt.
6. Under **Tools**, tick `ask_ava_coding`. Under **Advanced Params**, set **Function Calling** to **Native**. Save.

**Check:** select `AVA` and run the two tests below.

| Ask | Expected result |
|---|---|
| "Écris une fonction Python qui valide un IBAN suisse." | A status "Je transmets la question à AVA Coding…", then the specialist's answer |
| "Reformule ce message de manière plus polie : envoie-moi le rapport." | A direct answer, with no delegation |

Afterwards, no "Delegated to AVA Coding" chat should remain in the sidebar.

If the delegation never happens, the base model probably does not call tools reliably: try a stronger model. If it happens but fails, set the tool's `debug` valve to `true` and read `docker compose logs open-webui`.

You now have AVA's core pattern: one visible assistant, a hidden specialist, and a delegation tool between them. To stop the stack, run `docker compose down`. Add `-v` to delete its data as well.

---

## Part 2: Build it for production

**You need:**

- a Kubernetes or OpenShift cluster, with Helm 3;
- a PostgreSQL 15 or later server where you can install pgvector;
- an OIDC identity provider, such as Keycloak or Entra ID;
- an OpenAI-compatible model provider, or GPU servers to host models yourself.

### Step 6: Choose and connect your models

AVA uses these model services. Each is a plain HTTP endpoint with an OpenAI-compatible API:

| Service | Used for | What we use |
|---|---|---|
| Large language model | The orchestrator and specialists | Qwen3.5, on our own H200 GPUs |
| Small language model | Routing, titles, tags | A smaller model on the same servers |
| Embeddings | Indexing documents | `BAAI/bge-multilingual-gemma2` |
| Reranker | Re-ordering search results | `BAAI/bge-reranker-v2-m3` |
| Speech to text (optional) | Meeting minutes | Whisper |

[Models](models.md) explains our choices, how to host the models on your own GPUs, as we do, and how to use a hosted provider instead.

**Check:** each endpoint answers from the cluster, not only from your workstation:

```bash
kubectl run curl --rm -it --image=curlimages/curl -- \
  curl -s -H "Authorization: Bearer $LLM_API_KEY" https://llm.example.org/v1/models
```

The answer lists your models. If it times out, your cluster probably needs a proxy for outbound traffic: see [operations](operations.md#proxies-and-tls-inspection).

### Step 7: Prepare PostgreSQL and Redis

Create a database and a user, then enable pgvector in that database:

```sql
CREATE USER ava WITH PASSWORD '<password>';
CREATE DATABASE ava OWNER ava;
\c ava
CREATE EXTENSION IF NOT EXISTS vector;
```

**Check:** `SELECT extname FROM pg_extension;` includes `vector`.

Deploy Redis or Valkey in the same namespace, with a password. Open WebUI uses it to share state between replicas, for websocket messages, and to stop running tasks. Even with one replica, it makes task cancellation reliable. Use database 0 for state and database 1 for websockets.

**Check:** `redis-cli -u "redis://default:<password>@<redis-host>:6379/0" ping` answers `PONG`.

### Step 8: Install Open WebUI with Helm

1. Create the Secret. Start from [`deploy/secret.example.yaml`](../deploy/secret.example.yaml), or better, create it from your secret manager.
2. Copy [`deploy/values.example.yaml`](../deploy/values.example.yaml) to `values.local.yaml`, which git ignores. Replace every `example.org` value.
3. Compare the key names with the chart version you install:

   ```bash
   helm repo add open-webui https://open-webui.github.io/helm-charts
   helm repo update
   helm show values open-webui/open-webui > upstream-values.yaml
   ```

4. Install:

   ```bash
   kubectl apply -f secret.yaml -n ava
   helm upgrade --install ava open-webui/open-webui -n ava -f values.local.yaml
   kubectl get pods -n ava --watch    # wait until the open-webui pod is Running and ready
   ```

Our values keep one replica and a `Recreate` strategy, so that two versions never run database migrations at the same time. If you run several replicas, Redis and the same `WEBUI_SECRET_KEY` on every replica are mandatory.

On OpenShift, long transcriptions and research need a route timeout above the default. We use `haproxy.router.openshift.io/timeout: 10m`.

**Do not change the Open WebUI branding unless you are entitled to.** Above 50 users, this requires written permission or an enterprise licence. See [third-party licences](third-party-licenses.md).

**Check:** `curl https://ava.example.org/health` answers `{"status":true}`, and the sign-in page opens.

### Step 9: Connect single sign-on

1. In your identity provider, register a confidential OIDC client:
   - redirect URI: `https://ava.example.org/oauth/oidc/callback`;
   - PKCE with `S256`;
   - scopes `openid email profile`;
   - a `groups` claim in the ID token, if you want to control model access by group. In Keycloak, add a "Group Membership" mapper to the client.
2. Put the client ID and secret in the Secret, and check the `OPENID_PROVIDER_URL` value.
3. Restart Open WebUI so that it reads the new Secret: delete its pod with `kubectl delete pod -n ava <open-webui-pod>`, and Kubernetes recreates it.
4. Sign in through SSO **before** relying on `ENABLE_LOGIN_FORM=false`. Then make your SSO account an administrator in **Admin Panel > Users**.

New users get the role set by `DEFAULT_USER_ROLE`, `pending` by default, so an administrator approves each one. Set it to `user` if everyone who can sign in may use AVA.

**Check:** a test user signs in, receives the role you expect, not administrator, and appears in the groups you expect.

### Step 10: Configure document search

Go to **Admin Panel > Settings > Documents**:

1. **Embedding:** engine **OpenAI**, your embedding server's URL and key, model `BAAI/bge-multilingual-gemma2`. Choose it before loading documents: changing it later means re-indexing everything.
2. **Retrieval:** turn on **Hybrid Search**. It combines BM25, for exact references such as article numbers, with embeddings, for meaning.
3. **Reranking:** set `BAAI/bge-reranker-v2-m3`. Either let Open WebUI run it locally, on CPU, or point it to an external rerank endpoint on your model servers.
4. **Values we use:** top K `10`, top K after reranking `5`, relevance threshold `0.05`. A threshold of `0` lets irrelevant passages into every answer.
5. **Content extraction:** engine **Tika**, with the URL of the Tika service the chart created. `kubectl get svc -n ava` shows its name. Tika listens on port 9998.

Then create a knowledge base for each corpus, in **Workspace > Knowledge**. For legal texts, we use one law per file, with the law's number and title in the file name. The specialist can then cite and retrieve laws precisely.

**Check:** upload a document containing an exact reference, such as an article number, to a test knowledge base. Attach the knowledge base to a model and ask about that reference. The answer cites the document.

### Step 11: Connect the legal sources

Two free public MCP servers give access to Swiss law. They need no account:

| Server | URL | Content |
|---|---|---|
| [Fedlex Connector](https://github.com/JayTheSkier/fedlex-connector) | `https://mcp.fedlex-connector.ch` | Swiss federal law: search by title, articles, past versions |
| [OpenCaseLaw](https://github.com/jonashertner/opencaselaw) | `https://mcp.opencaselaw.ch` | Federal and cantonal case law, laws, doctrine |

1. Go to **Admin Panel > Settings > External Tools** and click **+**.
2. Choose the type **MCP (Streamable HTTP)**, enter the URL, and leave authentication empty.
3. Save, then repeat for the second server.

These are third-party services. Each tool call sends the specialist's search terms, which can include details of a case, to them. List them among your external data flows, and have them assessed like any other.

**Check:** in a test chat with the Fedlex tool enabled, ask "Que dit l'art. 1 du Code civil ?". The answer quotes the article and links to fedlex.admin.ch.

### Step 12: Install the extensions and create the models

Install in this order. Part 1 described each action in detail.

1. **Context injector:** [`filters/ava_context_injector.py`](../filters/ava_context_injector.py), as a global function.
2. **One delegation tool per specialist:** a copy of [`tools/ask_ava_specialist.py`](../tools/ask_ava_specialist.py), with its method renamed, its docstring rewritten, and its valves set. The [tools README](../tools/README.md#install-the-delegation-tool) has a complete example.
3. **The specialists**, in **Workspace > Models**:

   | Model ID | System prompt | Tools | Knowledge | Parameters |
   |---|---|---|---|---|
   | `ava-juriste` | [`ava-juriste.md`](../system-prompts/ava-juriste.md) | Fedlex, OpenCaseLaw | Your law collections | Temperature 0, seed 42, native function calling |
   | `ava-rh` | [`ava-rh.md`](../system-prompts/ava-rh.md) | Your intranet search, if any | Your HR directives | Native function calling |
   | `ava-coding` | [`ava-coding.md`](../system-prompts/ava-coding.md) | None | None | Native function calling |
   | `ava-pv` | [`ava-pv.md`](../system-prompts/ava-pv.md) | None | None | See [meeting minutes](meeting-minutes.md) |

   Replace the `[À ADAPTER : …]` blocks in the prompts first; see the [prompts README](../system-prompts/README.md#adapt-before-use).

4. **Specialist access.** A delegation runs with the current user's rights, so **users must be able to use each specialist**. Give your users' group read access to it, under **Access** in the model's settings. Then hide it from the model selector, in **Admin Panel > Settings > Models**, so users only ever see the orchestrator. A private specialist works for administrators and fails for everyone else.
5. **The orchestrator:** a model `ava` with [`ava.md`](../system-prompts/ava.md), every `ask_ava_*` tool ticked, and native function calling. Give your users access to it. Set it as the default model in **Admin Panel > Settings > Models**.

**Check:** sign in as an ordinary user, not an administrator, and run these questions:

| Ask | Expected result |
|---|---|
| "Quel est le délai pour faire recours contre une décision cantonale ?" | Delegation to AVA Juriste, an answer with sources, and the closing line "📃 Ce message est généré par AVA…" |
| "Combien de jours de vacances ai-je à 52 ans ?" | Delegation to AVA RH |
| "Traduis en allemand : merci pour votre retour." | A direct answer, with no delegation |
| A legal question, then "et pour un apprenti ?" | The follow-up is delegated with its context |

Prepare 20 to 30 such questions from real users, with the expected route for each. You will rerun them before every change; see [evaluation](operations.md#evaluate-before-each-change).

### Step 13: Add the intent router (optional)

The [intent router](../filters/README.md#ava-intent-router) rewrites follow-up questions into standalone requests, and suggests a specialist before the orchestrator answers. It costs one small model call per turn.

1. Install [`filters/intent_router.py`](../filters/intent_router.py) in **Admin Panel > Functions** and turn it on, but not as global.
2. Set `llm_base_url`, `llm_api_key` and `llm_model` to a small, fast model.
3. Edit `specialists_json` to list exactly your delegation tools.
4. In the `ava` model, tick the filter under **Filters**.

**Check:** rerun your test questions, with `debug` on, and compare the routing and the latency with Step 12. Keep the router only if routing improves more than latency worsens.

### Step 14: Reduce latency

Slowness was the main complaint during our pilot. In order of effect:

1. **Background tasks.** Each answer triggers title, tag and follow-up generation. Set a small task model in **Admin Panel > Settings > Interface**, or turn off what you do not need.
2. **Pre-answer calls.** Every filter that calls a model adds its full latency to every turn. Use the smallest model that routes correctly, and skip trivial inputs.
3. **Tool turns.** Each tool call is a full model round trip. A tool budget in the prompt and grouped search tools matter more than hardware.
4. **Prompt caching.** Keep the start of the prompt identical across requests: system prompt, then tools, then retrieved content, then the date, then the user message. The context injector puts the date at the end of the system prompt for this reason.
5. **Retrieval.** Fewer, better passages: hybrid search plus reranking, then top K 5.

**Check:** measure the time to the first token and to the complete answer, with and without delegation, using your test questions and several parallel users. Our target is a median under 30 seconds for a simple legal question. [Operations](operations.md#latency-where-the-seconds-go) shows where the seconds went in our audit.

### Step 15: Prepare for production

- [ ] Data classification and permitted uses are defined, and users are told about them
- [ ] Each external data flow is documented, or disabled: model provider, MCP servers, web search, page loading
- [ ] The prompts describe your hosting and data handling truthfully, as validated by your data protection officer
- [ ] Your data protection and security officers have reviewed the setup
- [ ] Only administrators can create tools and functions: they run arbitrary code on the server
- [ ] Users cannot create API keys; service keys are restricted to the endpoints they need
- [ ] Conversation retention is decided, and actually enforced
- [ ] Backups of the database and the data volume have been tested by restoring them
- [ ] A test set of real questions runs before each prompt, model or version change
- [ ] Users are told that answers can be wrong and must be checked
- [ ] The Open WebUI licence is respected, and model licences have been checked
- [ ] Upgrades are tested on a test instance first; see [operations](operations.md#upgrade-open-webui)
