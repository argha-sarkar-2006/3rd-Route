# AGENT.md — Sovereign AI Workbench

Operational guide to the repository: what it does, how it is wired, how to run it,
and how to test it. Written to be read cold by a person or an agent picking this
up with no prior context.

---

## 1. What this is

A private, multi-model agentic router exposed as a terminal application. You can
first ingest company PDF/text knowledge into the local searchable database, then
give it **an image and a request**, or **a text request**, and it decides which
specialist model should handle it, then chains the stages together:

- a **vision** model reads the image and describes the problem it poses,
- a **reasoning** model researches that problem (live web search + a local
  knowledge base) and classifies the ask as a plain question or a build request,
- a **coding** model writes the program, but only when the ask was for code.

Everything runs on free-tier hosted models. There is no GPU and no local model
weight download in the pipeline path.

The canonical worked example, and the one the test suite is built around: **photograph
a printed word-search puzzle, ask for a solver, and get back a runnable program that
finds all 22 hidden product names.**

---

## 2. File structure

```
MMAR(off-chain)/
├── modelrouter.py        # ENTRY POINT — orchestrator + CLI + interactive shell
├── config.py             # keys (lazy), model chains, timeouts, retry tuning
├── vision.py             # stage 1 — read an image, describe the problem
├── reasoning.py          # stage 2 — research, summarise, classify query vs code
├── coding.py             # stage 3 — turn a specification into working code
├── llm.py                # shared OpenRouter transport (used by vision + reasoning)
├── knowledge.py          # shared local knowledge base (SQLite + FTS5) + PDF ingest
├── requirements.txt      # pinned direct dependencies
├── README.md             # user-facing setup and usage overview
├── tests/                # deterministic regression tests
│
├── .env                  # real keys  — GITIGNORED, never commit
├── .env.example          # placeholder keys — the template to copy
├── .gitignore            # excludes .env, knowledge.db, __pycache__, test/, .DS_Store
├── knowledge.db          # SQLite knowledge base — GITIGNORED, regenerated on use
│
├── error/                # test images used throughout the test cases below
│   ├── test2.jpeg        # the word-search photograph (960 × 1280)
│   ├── er1.jpg           # earlier error-case image
│   └── workflow.png      # structure/flow diagram for this repo
├── workflow.png          # architecture diagram (entry box → stages → externals)
├── testrun.py            # PDF/text ingestion entrypoint, then launches modelrouter chat
├── test/                 # Python 3.14.7 virtualenv — GITIGNORED
└── brag-output/          # launch-video work product (not part of the app)
```

### Standalone ingestion entrypoint

`testrun.py` is a standalone ingestion-to-chat entry point, not imported by the
model-router pipeline. It uses the shared knowledge database and OpenRouter client;
credentials are read from the environment rather than embedded in the script.

---

## 3. End-to-end workflow

`modelrouter.py` is the primary chat entry point. `testrun.py` ingests PDF/text
knowledge into `knowledge.db`, then launches the same modelrouter chat loop.
Every arrow below is a real call in the code.

```
                    ┌──────────────────────────┐
   image + prompt   │                          │   text-only prompt
   ────────────────►│      modelrouter.py      │◄────────────────
                    │   (orchestrator / CLI)   │
                    └────────────┬─────────────┘
                                 │
              image present?  ───┴───  no image → ask the Needle router
                     │                              │
                  yes│                              │  select_model(task_type)
                     │                              ▼
                     │                        ┌───────────┐
                     │                        │  needle   │  local small model
                     │                        │  router   │  → coding | reasoning | vision
                     │                        └─────┬─────┘
                     ▼                              │
            ┌────────────────┐                      │
   ROUTE =  │   vision.py    │◄─────────────────────┘  (a "vision" verdict with no
   "vision" │  read the image│                          image attached falls back to reasoning)
            └────────┬───────┘
                     │  image_description (str)
                     ▼
            ┌────────────────────────────┐
            │       reasoning.py         │  ◄── knowledge.py (local FTS5 retrieval)
            │  research → summarise →    │  ◄── OpenRouter web plugin (live search)
            │  classify query vs code    │
            └────────┬───────────────────┘
                     │  {intent, summary, problem_statement, language, sources}
                     │
        intent == "code"  OR  router chose coding?
                     │
          ┌──────────┴───────────┐
         no                     yes
          │                       │
          ▼                       ▼
   print the summary      ┌────────────────┐
   + citations to the     │   coding.py    │  Ollama Cloud
   terminal               │  spec → code   │  (gpt-oss:20b)
   (INTENT: QUERY)        └────────┬───────┘
                                   ▼
                          print summary + code
                          (INTENT: CODE)
```

### Routing rules, precisely

| Situation | Route taken | Why |
| --- | --- | --- |
| An image path was supplied | `vision`, always | An attached image is unambiguous; spending a router call on it is waste. |
| Text only | the Needle router decides | `route_text()` calls `select_model` as a tool. |
| Router says `vision` but no image exists | downgraded to `reasoning` | Nothing to look at. |
| Router says `coding` | `coding` stage runs regardless of `reasoning`'s verdict | The router's verdict is an explicit user-intent override and wins. |
| Router unreachable | defaults to `reasoning` | Degrade, don't crash. |

### The two-key decision on the code path

The coding stage runs when `intent == "code"` **or** the router forced it. Two
subtleties in `modelrouter.run()`:

1. **Context poisoning guard.** If the reasoning stage fell back to prose (no
   structured JSON, so no `problem_statement`), its `summary` is the model's own
   rambling reasoning, not research. Handing that to the coding model makes it
   *continue the monologue* instead of writing code. The router therefore passes
   `summary=""` in that case and lets the original prompt stand as the spec.
2. **Ragged-input tolerance.** Any literal data the coding model hardcodes from a
   transcription must be normalised at startup (pad/trim rows to a common width)
   rather than strictly validated. Transcriptions are routinely ragged; a solver
   that aborts on a ragged grid is worse than one that repairs it.

---

## 4. Module reference

### `modelrouter.py` — orchestrator / entry point

Public surface:

- `select_model(task_type)` — a `needle.tool`-decorated function; the only tool the
  router model may call. Argument is a `Literal["coding", "reasoning", "vision"]`.
- `route_text(prompt, log)` → `"coding" | "reasoning" | "vision"` (defaults to
  `"reasoning"` if the router is unavailable or returns nothing usable).
- `run(image_path=None, prompt="", use_web=True, log=print)` → result dict.
- `print_report(result)` — renders the terminal report.
- `parse_args()` / `banner()` / `interactive()` / `main()`.

The Needle agent is built lazily by `_get_agent()` and cached in a module global.
`NEEDLE_TELEMETRY=0` is set before the agent is constructed.

CLI flags: `--image`, `--text`, `--prompt`, `--no-web`. With neither `--image` nor
`--text` it drops into an interactive loop.

### `config.py` — central configuration

Keys are read **lazily through functions** so importing the module never fails
because one unrelated key is missing. `_require(name)` raises a clear
`RuntimeError` when a value is missing or still starts with `PASTE_`.

Accessors: `openrouter_api_key()`, `openrouter_api_key_alt()`,
`openrouter_api_keys()`, and `ollama_api_key()`. The plural accessor returns
unique primary/alternate keys in order so a revoked key can fall through to the
other configured key. Loads `.env` from the project root via `python-dotenv`.

### `vision.py` — stage 1

`analyze_image(image_path, prompt=None, log=print) -> str`

- Loads the image, checks the MIME type via `mimetypes`, rejects missing/empty files.
- Inlines the bytes as a base64 data URL (no file upload — uploads leak and re-upload
  on every retry).
- Walks `config.VISION_OPENROUTER_MODELS`, retrying retryable failures with
  exponential backoff and moving to the next model when one is retired.
- `config.VISION_TOTAL_BUDGET_SECONDS` caps the whole stage so a saturated provider
  cannot retry forever.

Error classification (`_classify(error)` → `"retry" | "next" | "raise"`):

- **`"next"`** — the model is gone (404 / "not found" / "no longer available").
  Checked **first**, on purpose: a retired model answers 404 with prose like
  "this model is unavailable for free", which also matches the `unavailable` retry
  marker; retrying a model that no longer exists just burns the budget.
- **`"retry"`** — transient (5xx, 429, `resource_exhausted` in either spelling,
  rate-limited, connection reset, timeout).
- **`"raise"`** — anything else; a hard `VisionError`.

`EmptyResponseError` subclasses `VisionError`: a reasoning-capable vision model can
spend its whole output budget on hidden reasoning and emit no text, leaving
`content` empty. That is retryable, not fatal.

**Prompt design note.** `DEFAULT_PROMPT` deliberately does **not** say "count the
letters in each row and re-read any row that does not match". Small free reasoning
models read that as licence to re-derive the grid while writing every row and spend
the entire output budget in a repetition loop without ever emitting the description.
Stating the deliverable up front and forbidding narration is what makes a single
clean pass come back. This was a real, reproduced failure, not a theoretical one.

Standalone CLI: `python vision.py <image> [-p PROMPT]`, or no argument for a REPL.

### `reasoning.py` — stage 2

`reason(prompt, image_description=None, use_web=True, use_knowledge=True, db_path=None, log=print) -> dict`

Flow: retrieve knowledge-base context → build a system+user message → call the model
with the OpenRouter web plugin → parse JSON → normalise.

Returns exactly these keys:

```python
{
  "intent":            "query" | "code",
  "summary":           str,   # stands alone; is the answer when intent == "query"
  "problem_statement": str,   # self-contained spec for the coding model; "" when query
  "language":          str,   # "" when query
  "sources":           list[str],
}
```

Robustness behaviours, each of which exists because of an observed failure:

- `_parse_json` tolerates markdown fences and leading prose by falling back to the
  outermost `{...}` span; parses with `strict=False` because OpenRouter responses
  routinely contain raw control characters inside JSON strings.
- A response cut off at `finish_reason == "length"` is retried once with **2×** the
  token budget.
- If parsing still fails, the prose is returned as `summary` with `intent` recovered
  from the prompt via `_guess_intent()` and `problem_statement=""`. Defaulting that
  case to `"query"` would silently drop the code path for a user who asked for code
  — a needless coding call wastes a request, a missed one fails outright.
- `sources` prefers the plugin's real `url_citation` annotations over URLs the model
  typed from memory. The system prompt forbids inventing URLs.
- `_call_model` converts the base `llm.LLMError` into `ReasoningError`, so a timeout
  surfaces as a clean message rather than a traceback escaping the router.

`CODE_REQUEST_MARKERS` / `_guess_intent(prompt)` — the keyword fallback used only
when structured output is unusable.

Standalone CLI: `python reasoning.py <prompt> [--image-description TEXT] [--no-web] [--no-knowledge]`.

### `coding.py` — stage 3

`generate_code(problem_statement, language="", summary="", model=None, log=print) -> str`

Runs on Ollama Cloud via `ollama.Client(host=config.OLLAMA_HOST, headers={Authorization: Bearer ...})`.
The system prompt requires: a `FILE: name.py` line before the block, one fenced block
with a complete program, no placeholder ellipses, all imports, a runnable entry point,
and the ragged-data normalisation rule from §3.

`chat()` / `main()` are a legacy interactive chatbot kept working, not used by the router.

### `llm.py` — shared OpenRouter transport

- `data_url(data, mime_type)` — base64 data URL for inline images.
- `openrouter_chat(messages, model, max_tokens=None, plugins=None, timeout=None)` —
  POSTs to `config.OPENROUTER_URL`; retries HTTP 401/403 once with the configured
  alternate key, then raises `LLMError` on authentication, timeout, transport,
  other non-200, or unparseable JSON failures. Parses with `strict=False`.
- `extract_message(data) -> (text, finish_reason, annotations)` — **falls back to the
  `reasoning` field when `content` is empty**, because a reasoning model that exhausts
  its budget returns `content: null` with the text sitting in `reasoning`.
- `citation_urls(annotations)` — pulls real `url_citation` URLs out of web-plugin
  annotations, de-duplicated.

Default request settings: `temperature: 0.2`, `max_tokens: config.LLM_MAX_TOKENS`.

### `knowledge.py` — local knowledge base

SQLite with an **FTS5 external-content** table plus insert/delete/update triggers to
keep the index in sync with `documents`.

- `search(query, limit=5)` — builds a safe FTS5 MATCH expression from arbitrary user
  text, then **falls back to a `LIKE` substring scan** when MATCH returns nothing
  (FTS matches whole tokens only).
- `_fts_expression(text)` quotes every word as a **prefix query** and ORs them,
  because raw user input cannot go into MATCH directly (punctuation like quotes,
  asterisks and parentheses are FTS5 operators and raise `OperationalError`), and
  FTS5 does no stemming.
- `context_for(query, limit)` — formats hits for injection into a prompt.
- `extract_pdf_text(path)` / `ingest_pdf(path)` — PyMuPDF text extraction with a
  pytesseract OCR fallback for scanned pages, then `chunk_text` (8000-char chunks).
  `pymupdf` / `pytesseract` / `PIL` are imported **inside** the functions so that
  querying an existing knowledge base does not require them.

**Design decision (deliberate):** retrieval happens here, in Python, and the top hits
are *injected into the prompt* rather than exposed to the model as a callable tool.
Free-tier models invoke tools unreliably, and a dropped call degrades the answer with
no visible error. Local retrieval is reliable and cheaper.

Standalone CLI: `python knowledge.py --ingest FILE.pdf` / `--search "QUERY" [-n N]`.

### `testrun.py` — ingestion-to-chat entry point

`python testrun.py` prompts for a PDF or text file, stores extracted/chunked
content in `knowledge.db`, then starts `modelrouter.interactive()`. The router's
reasoning stage retrieves matching knowledge-base context for each chat request.

---

## 5. Model chain and external services

| Stage | Model / service | Notes |
| --- | --- | --- |
| Router | `cactus-needle` (local package, imports as `needle`) | Not an API call. Telemetry disabled. |
| Vision | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` via OpenRouter | Free tier. The only configured vision model; no Gemini or provider fallback. |
| Reasoning | `nex-agi/nex-n2.5-pro:free` via OpenRouter | Free tier, supports the `web` plugin. |
| Coding | `gpt-oss:20b` on Ollama Cloud | `https://ollama.com`, Bearer auth. |
| Knowledge | local `knowledge.db` (SQLite + FTS5) | No network. |

### Vocabulary that bites

- Vision uses one explicit free OpenRouter model; no other vision-provider SDK or
  fallback path is configured.
- A `:free` OpenRouter slug can 404 with *"unavailable for free… use this slug instead:
  &lt;paid slug&gt;"*. That is a retired free tier, not a transient error.
- The account's **free-model cap is 50 requests/day**; free vision models also hit
  provider-side upstream limits ("Worker local total request limit reached") that have
  nothing to do with this account.

---

## 6. Configuration reference (`config.py`)

| Constant | Value | Meaning |
| --- | --- | --- |
| `VISION_OPENROUTER_MODELS` | `["nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free"]` | Single free OpenRouter vision model. |
| `REASONING_MODEL` | `nex-agi/nex-n2.5-pro:free` | |
| `CODING_MODEL` | `gpt-oss:20b` | |
| `OPENROUTER_URL` | `https://openrouter.ai/api/v1/chat/completions` | |
| `OLLAMA_HOST` | `https://ollama.com` | |
| `OPENROUTER_APP_TITLE` | `Sovereign AI Workbench` | Sent as `X-Title`. |
| `VISION_ATTEMPTS_PER_MODEL` | `2` | Retry attempts for the configured vision model. |
| `VISION_BACKOFF_SECONDS` | `2.0` | Base for exponential backoff (`base × 2^(attempt-1)`). |
| `VISION_TIMEOUT_SECONDS` | `150.0` | Per vision request. |
| `VISION_TOTAL_BUDGET_SECONDS` | `360.0` | Hard ceiling on the whole vision stage. |
| `OPENROUTER_TIMEOUT_SECONDS` | `180` | Default transport timeout (reasoning). |
| `LLM_MAX_TOKENS` | `16000` | Deliberately generous — reasoning models spend tokens on hidden thinking first. |
| `KNOWLEDGE_DB` | `<root>/knowledge.db` | |

---

## 7. Setup

```bash
# 1. Keys
cp .env.example .env        # then fill in the three values

# 2. Virtualenv (already present here as ./test, Python 3.14.7)
./test/bin/pip install -r requirements.txt
#   or, from scratch:
python3 -m venv test && ./test/bin/pip install -r requirements.txt

# 3. System dependency that pip cannot install
brew install tesseract      # required only for PDF/OCR ingestion

# 4. Optional: verify the router package imports
./test/bin/python3 -c "import needle, ollama, requests, dotenv; print('ok')"
```

`.env` variables: `OPENROUTER_API_KEY`, `OPENROUTER_API_KEY_ALT`, and
`OLLAMA_API_KEY`.

---

## 8. How to run

```bash
# Full pipeline — image + request
./test/bin/python3 modelrouter.py --image error/test2.jpeg --prompt "solve this"

# Full pipeline — text only
./test/bin/python3 modelrouter.py --text "explain what a word-search solver must do"

# Interactive shell (no flags)
./test/bin/python3 modelrouter.py
#   prompt 1: Image path (Enter to skip)   → e.g. error/test2.jpeg
#   prompt 2: Your request                 → e.g. write a solver for this puzzle

# Disable web search for the reasoning stage
./test/bin/python3 modelrouter.py --text "..." --no-web

# Individual stages
./test/bin/python3 testrun.py                 # ingest PDF/text, then start chat
./test/bin/python3 vision.py error/test2.jpeg
./test/bin/python3 reasoning.py "your question" [--no-web]
./test/bin/python3 knowledge.py --search "charger" -n 5
./test/bin/python3 knowledge.py --ingest /path/to/file.pdf

# Knowledge base inspection
./test/bin/python3 -c "import knowledge; print(knowledge.document_count())"
```

Expected banner on startup:

```
============================================================
            SOVEREIGN AI WORKBENCH
               NEEDLE ROUTER
============================================================
Vision:    nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free
Reasoning: nex-agi/nex-n2.5-pro:free
Coding:    gpt-oss:20b
Knowledge: N document(s)
```

---

## 9. Testing phase

The suite is layered so that the cheap, deterministic checks run first and the
expensive, network-dependent ones run last. Run the phases in order; a failure in an
earlier phase invalidates the later ones.

### Phase 0 — Static (no network, seconds)

Compile-check every module and confirm import-time facts. This catches syntax errors,
stale references (e.g. code still importing a model list that was deleted), and
signature drift.

### Phase 1 — Unit (no network, milliseconds)

Pure functions with no I/O: `vision._classify`, `reasoning._parse_json`,
`reasoning._guess_intent`, `knowledge._fts_expression`, `llm.citation_urls`.
These encode the specific bugs that were fixed, so they are the regression net.

### Phase 2 — Local integration (no network)

Knowledge-base round-trip against a temporary SQLite file: schema creation, insert,
search, LIKE fallback, delete-trigger index sync. Optionally PDF ingestion if
`tesseract` is on PATH.

### Phase 3 — Live stage tests (network, free tier)

Each stage driven directly with the real APIs. **These consume the free-tier daily
quota and can fail for environmental reasons that are not code defects** — a 429 or
an upstream "worker request limit reached" means the provider is saturated, not that
the stage is broken. Re-run later, and treat a network failure as inconclusive rather
than a failing test.

### Phase 4 — End-to-end (network, slowest)

`modelrouter.py` against `error/test2.jpeg`, both the query and the code path, plus
executing the generated solver.

### Phase 5 — Secrets audit (no network)

Confirm no key material is present in the working tree **or in git history**.

---

## 10. Test cases

Legend: **[U]** unit, no network · **[L]** local, no network · **[N]** live network call

### Phase 0 — Static

| ID | Test | Command | Expected |
| --- | --- | --- | --- |
| TC-00.1 | All modules compile | `./test/bin/python3 -m py_compile modelrouter.py config.py vision.py reasoning.py coding.py llm.py knowledge.py` | exit 0 |
| TC-00.2 | Vision configuration has no external SDK fallback | `./test/bin/python3 -c "import config; assert len(config.VISION_OPENROUTER_MODELS) == 1; assert config.VISION_OPENROUTER_MODELS[0].endswith(':free'); print('ok')"` | `ok` |
| TC-00.3 | Config is importable without keys | `./test/bin/python3 -c "import config; print(config.VISION_OPENROUTER_MODELS)"` | prints the chain; does **not** raise even with `.env` absent |
| TC-00.4 | Vision chain remains OpenRouter-only | `./test/bin/python3 -c "import config; assert not hasattr(config, 'gemini_api_key'); print('ok')"` | `ok` |
| TC-00.5 | Public signatures | `./test/bin/python3 -c "import vision,reasoning,coding,inspect; print(inspect.signature(vision.analyze_image)); print(inspect.signature(reasoning.reason)); print(inspect.signature(coding.generate_code))"` | matches §4 |
| TC-00.6 | Vision configuration regression test | `./test/bin/python3 -m unittest tests/test_vision_configuration.py` | `OK` |

### Phase 1 — Unit

| ID | Test | Input | Expected |
| --- | --- | --- | --- |
| TC-01.1 | `_classify` retries a 503 | `RuntimeError("503 UNAVAILABLE")` | `"retry"` |
| TC-01.2 | `_classify` retries a 429 | `RuntimeError("429 rate limited")` | `"retry"` |
| TC-01.3 | `_classify` **skips** a retired model | `RuntimeError("404 this model is unavailable for free; use this slug instead: x/y")` | `"next"` — **not** `"retry"`. This is the marker-ordering regression. |
| TC-01.4 | `_classify` retries the camel-case upstream spelling | `RuntimeError("ResourceExhausted: Worker local total request limit reached (348/16)")` | `"retry"` — the underscore-only marker used to miss this and abort the stage. |
| TC-01.5 | `_classify` retries a connection reset | `RuntimeError("Connection reset by peer")` | `"retry"` |
| TC-01.6 | `_classify` raises on an unknown error | `RuntimeError("bad request")` | `"raise"` |
| TC-01.7 | `EmptyResponseError` is retryable | `EmptyResponseError("no text")` | `"retry"` |
| TC-01.8 | `_guess_intent` detects a build request | `"write a python script to solve this word search"` | `"code"` |
| TC-01.9 | `_guess_intent` leaves a question alone | `"what is a word search?"` | `"query"` |
| TC-01.10 | `_parse_json` strips markdown fences | ``"```json\n{\"intent\":\"code\"}\n```"`` | dict with `intent == "code"` |
| TC-01.11 | `_parse_json` tolerates leading prose | `"Sure! Here you go: {\"intent\":\"query\"} hope that helps"` | dict with `intent == "query"` |
| TC-01.12 | `_parse_json` tolerates raw control characters | JSON containing a literal newline inside a string | parses (because `strict=False`) |
| TC-01.13 | `_parse_json` returns `None` on garbage | `"no json at all"` | `None` |
| TC-01.14 | `_fts_expression` neutralises FTS5 operators | `'"; DROP'` or `'a*b(c)'` | quoted prefix terms, no `OperationalError` on use |
| TC-01.15 | `_fts_expression` returns `None` for punctuation-only input | `"!!!"` | `None` |
| TC-01.16 | `citation_urls` de-duplicates | annotations with the same URL twice | one entry |
| TC-01.17 | `extract_message` falls back to the `reasoning` field | body with `content: null`, `reasoning: "text"` | returns `"text"` |

### Phase 2 — Local integration

| ID | Test | Command / approach | Expected |
| --- | --- | --- | --- |
| TC-02.1 | Schema creation is idempotent | `knowledge.init_db(tmp)` twice | no error |
| TC-02.2 | Insert + FTS search round-trip | `add_document("src","t","product charger")` then `search("charger")` | one hit, title `t` |
| TC-02.3 | Prefix matching works (no stemming) | insert `"products"`, search `"product"` | the row is found via `"product"*`. Prefix matching is **one-directional**: the reverse (insert `"product"`, search `"products"`) does **not** match — verified. |
| TC-02.4 | LIKE fallback fires | search a substring FTS cannot tokenise, e.g. `"arg"` against `"charger"` | returns rows via the `LIKE` branch |
| TC-02.5 | `search("")` is safe | `search("")` | `[]`, no error |
| TC-02.6 | Delete trigger keeps the index in sync | insert, delete the row, search again | no stale hit |
| TC-02.7 | Empty documents are refused | `add_document("s","t","   ")` | raises `ValueError` |
| TC-02.8 | `context_for` formats hits | `context_for("charger")` | numbered `[1] title (source: …)` blocks |
| TC-02.9 | PDF ingestion *(needs tesseract)* | `ingest_pdf("path.pdf")` | ≥1 stored chunk; `document_count()` increases |

Suggested Phase 2 harness (creates its own temp DB, never touches `knowledge.db`):

```bash
./test/bin/python3 - <<'PY'
import tempfile, os, knowledge

db = os.path.join(tempfile.mkdtemp(), "t.db")
knowledge.init_db(db)
knowledge.add_document("src", "spec", "the charger product is here", db_path=db)

assert knowledge.search("charger", db_path=db), "FTS MISS"
assert knowledge.search("products", db_path=db), "PREFIX MISS"
assert knowledge.search("arg", db_path=db), "LIKE FALLBACK MISS"
assert knowledge.search("", db_path=db) == [], "EMPTY QUERY"
assert knowledge.context_for("charger", db_path=db).startswith("[1]"), "FORMAT"

try:
    knowledge.add_document("src", "empty", "   ", db_path=db)
    raise SystemExit("EMPTY DOC NOT REFUSED")
except ValueError:
    pass

print("PHASE 2 OK")
PY
```

### Phase 3 — Live stage tests

| ID | Test | Command | Expected | Cost |
| --- | --- | --- | --- | --- |
| TC-03.1 | Vision reads the puzzle | `./test/bin/python3 vision.py error/test2.jpeg` | a description containing a 20-row letter grid, each row 20 characters | free |
| TC-03.2 | Vision grid is rectangular | parse row strings out of TC-03.1 stdout | all rows the same width | free |
| TC-03.3 | Vision is not a repetition loop | inspect TC-03.1 output | a real description, not the same line repeated hundreds of times | free |
| TC-03.4 | Vision rejects a non-image | `./test/bin/python3 vision.py requirements.txt` | `VisionError: Not a recognised image type` | none |
| TC-03.5 | Vision rejects a missing file | `./test/bin/python3 vision.py nope.jpeg` | `VisionError: Image file does not exist` | none |
| TC-03.6 | Reasoning, query path | `./test/bin/python3 reasoning.py "what is the latest Python release?"` | `INTENT: QUERY`, a summary, and real `SOURCES` URLs | free |
| TC-03.7 | Reasoning, code path | `./test/bin/python3 reasoning.py "write a python script to solve a word search"` | `INTENT: CODE` and a non-empty `PROBLEM STATEMENT` | free |
| TC-03.8 | Reasoning survives a transport failure | monkeypatch `llm.openrouter_chat` to raise `llm.LLMError` | raises `ReasoningError` (not a bare `LLMError`) — the router only catches the former | none |
| TC-03.9 | Reasoning off-web | add `--no-web` to TC-03.6 | still returns a summary, with no/zero sources | free |
| TC-03.10 | Coding generates code | `./test/bin/python3 -c "import coding; print(coding.generate_code('write a function that reverses a string', 'python')[:200])"` | code containing `def`, no `...` placeholders | paid-ish |

### Phase 4 — End-to-end

| ID | Test | Command | Expected |
| --- | --- | --- | --- |
| TC-04.1 | Router banner renders | `./test/bin/python3 modelrouter.py` then `exit` | banner with all four model lines + knowledge count |
| TC-04.2 | Image routes deterministically to vision | `./test/bin/python3 modelrouter.py --image error/test2.jpeg --prompt "solve this"` | log shows `[vision] reading …`, then `[reasoning] researching...` |
| TC-04.3 | Code request reaches the coding stage | as TC-04.2 with a request for a solver | log shows `[coding] generating code...` and `INTENT: CODE` |
| TC-04.4 | Query request does **not** reach coding | `./test/bin/python3 modelrouter.py --text "what is a word search?"` | `INTENT: QUERY`, no `CODE` section |
| TC-04.5 | Prose fallback does not poison the coding context | force a JSON parse failure, then request code | coding still emits code, not a continuation of prose (router passes `summary=""`) |
| TC-04.6 | Generated solver runs | save the emitted `FILE:` block to `solver.py`, run it with the transcribed grid | exit 0, reports every product found |
| TC-04.7 | Ragged grid does not abort the solver | run the solver against a grid whose rows differ in length | the solver normalises and still solves, rather than raising `ValueError` |

### Phase 5 — Secrets audit

| ID | Test | Command | Expected |
| --- | --- | --- | --- |
| TC-05.1 | No key material in the working tree | `git grep -nI -E "sk-or-v1-|AIza" -- .` | no matches in tracked files |
| TC-05.2 | No key material at HEAD | `git grep -nI -E "sk-or-v1-|AIza" HEAD -- '*.py'` | **currently FAILS** — see §11 |
| TC-05.3 | `.env` is ignored | `git check-ignore -v .env` | matches the `.gitignore` rule |
| TC-05.4 | `.env` is untracked | `git ls-files .env` | empty output |

---

## 11. Known issues and gotchas

1. **Leaked keys in git history (open).** Older repository history may contain
   OpenRouter keys. Moving keys into `.env` does not un-expose history. **Rotate any
   exposed keys on the OpenRouter dashboard**; history rewriting is a separate,
   destructive operation and is not part of this repo's current state.
2. **Free-tier quota.** 50 requests/day across free models, plus provider-side
   upstream limits that are outside this account's control. Live tests can fail for
   environmental reasons; check the error text before assuming a code defect.
3. **Data URLs re-send the whole image on every retry.** Fine at 150 KB; a problem for
   large images.
4. **The vision path has one configured free model.** If that slug is retired or
   rate-limited, the stage reports the failure; no alternate provider is attempted.
5. **`testrun.py` is a standalone legacy entry point**, not imported by the pipeline;
   it now uses the shared transport and configured key candidates.
6. **Interactive `vision.py` asks for the prompt before it validates the image path**,
   so an empty path still prompts once. Cosmetic; deliberately left as-is.

---

## 12. Security notes

- Never print, echo, or commit the contents of `.env`. Reference key *locations*
  (file:line), never key *values*.
- `.gitignore` excludes `.env`, `knowledge.db`, `__pycache__/`, `test/`, `.DS_Store`.
- Keys are read lazily, so an agent can import and inspect any module without a valid
  key present. Only an actual API call raises.
- The knowledge base is local-only; nothing in `knowledge.py` makes a network request.
- `user prompt → model` is the only path by which arbitrary text reaches a provider.
  Image bytes go to OpenRouter as an inline data URL.
