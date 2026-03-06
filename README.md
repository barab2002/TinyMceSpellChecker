# Hebrew Spell-Check for TinyMCE v7

A complete, **internal/private-network** Hebrew spell-checking solution:

- **Backend**: Python 3.11 + FastAPI + Hunspell (`pyhunspell`)
- **Plugin**: Vanilla JavaScript, TinyMCE v7 API
- **Deployment**: Docker Compose (single command)
- **Dictionary**: Hunspell `he_IL` + custom organisational word list (JSON file)

No external cloud services. No browser spell-check. Runs entirely on your network.

---

## Table of Contents

1. [Architecture](#architecture)
2. [Tech Stack Decisions](#tech-stack-decisions)
3. [Project Structure](#project-structure)
4. [Quick Start — Docker Compose](#quick-start--docker-compose)
5. [Quick Start — Local Dev (no Docker)](#quick-start--local-dev-no-docker)
6. [TinyMCE Integration](#tinymce-integration)
7. [API Reference](#api-reference)
8. [Custom / Organisational Dictionary](#custom--organisational-dictionary)
9. [Configuration Reference](#configuration-reference)
10. [Logging](#logging)
11. [Known Limitations](#known-limitations)
12. [Recommended Next Improvements](#recommended-next-improvements)

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Browser — TinyMCE v7 editor                            │
│  ┌────────────────────────────────────────────────────┐ │
│  │  hebrewspellcheck plugin (plugin.js)               │ │
│  │  1. Walk DOM text nodes → build plainText corpus   │ │
│  │  2. POST /spell/check  → backend                   │ │
│  │  3. Map offsets back → wrap spans in editor DOM    │ │
│  │  4. Click on span → show suggestion popover        │ │
│  └────────────────────────┬───────────────────────────┘ │
└───────────────────────────┼─────────────────────────────┘
                            │ HTTP / JSON
                            ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI service  (app/main.py)                          │
│  ┌─────────────┐  ┌──────────────────┐  ┌─────────────┐ │
│  │ SpellService │  │ DictionaryService │  │  Routes     │ │
│  │ (Hunspell)   │  │  (JSON file)     │  │ /spell/check│ │
│  └──────┬───────┘  └────────┬─────────┘  │ /dictionary │ │
│         │                   │            │ /health     │ │
│         ▼                   ▼            └─────────────┘ │
│   he_IL.aff/.dic    org_dictionary.json                  │
└──────────────────────────────────────────────────────────┘
```

### Key design decisions

| Concern | Decision | Reason |
|---|---|---|
| Text sent to API | **Plain text**, not HTML | Avoids HTML parsing on server; plugin maps positions back to DOM |
| Highlighting | Wrap text nodes in `<span class="mce-spellcheck-word">` | Safe; touches only text, not markup |
| DOM walk | `TreeWalker(SHOW_TEXT)` | Never touches link hrefs, image src, data attributes |
| Undo safety | All DOM mutations inside `editor.undoManager.transact()` | Single undo step for the whole check |
| XSS in popover | `createElement` / `textContent` only — **no innerHTML** | Suggestion strings from server never executed |
| Custom dictionary priority | Checked before Hunspell | Org words always accepted regardless of base dictionary |

---

## Tech Stack Decisions

### Backend: Python + FastAPI + Hunspell

**Why Python?**
`pyhunspell` (C binding to libhunspell) is the most mature and reliable Hunspell
integration available.  Hebrew support in Hunspell is well-tested via the
`hunspell-he` package.  Node.js alternatives (`nodehun`, `nspell`) are less
maintained and have had issues with non-Latin encodings.

**Why FastAPI?**
Async, typed, auto-generates OpenAPI docs at `/docs`, minimal boilerplate.

**Why not spylls (pure Python Hunspell)?**
Spylls works but is 10–30× slower than the C binding for large documents.
For an enterprise editor, latency matters.

### Plugin: Vanilla JavaScript

No build step.  Load the single file directly. TypeScript would add a compile
step for no practical gain in a single-file plugin.  The code is cleanly
structured with JSDoc comments and separation of concerns.

---

## Project Structure

```
TinyMceSpellChecker/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              ← FastAPI app factory + entry point
│   │   ├── config.py            ← pydantic-settings config (env vars)
│   │   ├── models/
│   │   │   └── schemas.py       ← Pydantic request/response schemas
│   │   ├── services/
│   │   │   ├── spell_service.py     ← Hunspell wrapper + tokeniser
│   │   │   └── dictionary_service.py ← Custom org dictionary (JSON)
│   │   └── routes/
│   │       ├── spell.py         ← POST /spell/check
│   │       └── dictionary.py    ← GET/POST /dictionary/*
│   ├── dictionaries/
│   │   ├── he_IL.aff            ← Hunspell affix file (from hunspell-he)
│   │   ├── he_IL.dic            ← Hunspell dictionary file
│   │   └── custom/
│   │       └── org_dictionary.json  ← Seeded with org-specific words
│   ├── logs/                    ← Structured JSON logs (mounted volume)
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
│
├── plugin/
│   └── hebrewspellcheck/
│       └── plugin.js            ← TinyMCE v7 plugin (single file, no build)
│
├── example/
│   ├── index.html               ← Live demo / integration test page
│   ├── test_data.json           ← Sample test cases
│   └── sample_api_calls.sh      ← curl examples for every endpoint
│
├── scripts/
│   └── download_dictionaries.sh ← One-time dictionary download helper
│
├── docker-compose.yml
├── nginx.conf                   ← Static file server config
├── .env.example
└── README.md
```

---

## Quick Start — Docker Compose

### Prerequisites

- Docker ≥ 24 and Docker Compose v2
- Internet access for initial image build (to install `hunspell-he`)
- After build, runs fully offline

### Steps

```bash
# 1. Clone / enter the project
git clone https://github.com/your-org/TinyMceSpellChecker.git
cd TinyMceSpellChecker

# 2. Copy environment file
cp .env.example .env
# Edit .env if needed (change ports, CORS origins, etc.)

# 3. Build and start
docker compose up -d --build

# 4. Verify the API is healthy
curl http://localhost:8000/health
# Expected: {"status":"ok","hunspell_available":true,...}

# 5. Open the demo page
# http://localhost:3000/example/index.html
```

### Stop / Restart

```bash
docker compose down          # stop
docker compose up -d         # restart (no rebuild)
docker compose up -d --build # rebuild (after code changes)
```

---

## Quick Start — Local Dev (no Docker)

### Backend

```bash
cd backend

# 1. Install system dependencies (Debian/Ubuntu)
sudo apt-get install -y libhunspell-dev hunspell-he

# 2. Create virtual environment
python -m venv .venv
source .venv/bin/activate

# 3. Install Python packages
pip install -r requirements.txt

# 4. Symlink or copy dictionary files
#    hunspell-he installs to /usr/share/hunspell/
ln -sf /usr/share/hunspell/he_IL.aff dictionaries/he_IL.aff
ln -sf /usr/share/hunspell/he_IL.dic dictionaries/he_IL.dic

# 5. Configure
cp .env.example .env
# Set SPELLCHECK_HUNSPELL_DICT_DIR=./dictionaries in .env

# 6. Start the API
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# API docs available at http://localhost:8000/docs
```

### Plugin (no build step)

Serve the `plugin/` directory via any web server or directly from your app's
static file path.  Example using Python's built-in HTTP server:

```bash
# From project root
python -m http.server 3000

# Open: http://localhost:3000/example/index.html
```

---

## TinyMCE Integration

### 1. Include the plugin script

```html
<!-- After the TinyMCE script tag -->
<script src="/path/to/hebrewspellcheck/plugin.js"></script>
```

### 2. Configure `tinymce.init()`

```javascript
tinymce.init({
  selector: '#my-editor',

  // Load plugin
  external_plugins: {
    hebrewspellcheck: '/path/to/plugin/hebrewspellcheck/plugin.js',
  },

  // Add to toolbar
  toolbar: 'hebrewspellcheck hebrewspellcheck_clear | bold italic | ...',

  // Add to menu (optional)
  menu: {
    tools: { title: 'כלים', items: 'hebrewspellcheck hebrewspellcheck_clear' },
  },

  // Spell-check backend URL (required)
  hebrewspellcheck_api_url: 'http://your-spellcheck-server:8000',
  hebrewspellcheck_language: 'he-IL',
  hebrewspellcheck_max_suggestions: 5,

  // RTL layout
  directionality: 'rtl',

  // Allow spell-check spans through TinyMCE content filter
  extended_valid_elements: 'span[class|data-word|data-suggestions]',

  // Disable browser spell-check (we handle it)
  browser_spellcheck: false,
  gecko_spellcheck: false,
});
```

### Plugin options

| Option | Type | Default | Description |
|---|---|---|---|
| `hebrewspellcheck_api_url` | string | `http://localhost:8000` | Base URL of the spell-check backend |
| `hebrewspellcheck_language` | string | `he-IL` | BCP-47 language tag |
| `hebrewspellcheck_max_suggestions` | number | `5` | Max suggestions per word |

### Toolbar buttons registered

| Button name | Label | Action |
|---|---|---|
| `hebrewspellcheck` | בדיקת איות | Run spell check |
| `hebrewspellcheck_clear` | נקה סימונים | Clear all highlights |

---

## API Reference

### `GET /health`

Returns service health status.

```json
{
  "status": "ok",
  "hunspell_available": true,
  "language": "he_IL",
  "custom_dict_words": 18
}
```

---

### `POST /spell/check`

Check text for spelling errors.

**Request:**
```json
{
  "text": "שלומ לכולם. אנחנו עוסקים בפיתוח מוצרים.",
  "language": "he-IL",
  "documentId": "optional-doc-id",
  "options": {
    "includeSuggestions": true,
    "maxSuggestions": 5
  }
}
```

**Response:**
```json
{
  "language": "he-IL",
  "misspellings": [
    {
      "word": "שלומ",
      "start": 0,
      "end": 4,
      "suggestions": ["שלום", "שלמו"],
      "source": "hunspell"
    }
  ],
  "total": 1
}
```

`start` and `end` are **character offsets** in the plain text that was sent.
The plugin maps these back to the editor DOM via the text-node segment map
built during extraction.

---

### `GET /dictionary`

List all words in the organisational dictionary.

```json
{ "words": ["Salesforce", "ZoomInfo", "Snowflake"], "count": 3 }
```

---

### `POST /dictionary/add`

Add a word.

```json
{ "word": "MyNewProduct" }
```

---

### `POST /dictionary/remove`

Remove a word.  Returns 404 if not found.

```json
{ "word": "OldProduct" }
```

---

## Custom / Organisational Dictionary

The dictionary is stored at `backend/dictionaries/custom/org_dictionary.json`
(overridable via `SPELLCHECK_CUSTOM_DICT_PATH`).

It is pre-seeded with common SaaS tool names.  Add your organisation's terms
via the API or by editing the JSON file directly (restart not required for API
edits; the file is read on startup and updated on each add/remove call).

### Adding words in bulk

```bash
# From the project root
for word in "ProductName" "AcronymXYZ" "ClientCo"; do
  curl -s -X POST http://localhost:8000/dictionary/add \
    -H "Content-Type: application/json" \
    -d "{\"word\": \"$word\"}"
done
```

---

## Configuration Reference

All settings use the `SPELLCHECK_` prefix.

| Variable | Default | Description |
|---|---|---|
| `SPELLCHECK_HOST` | `0.0.0.0` | Bind address |
| `SPELLCHECK_PORT` | `8000` | Port |
| `SPELLCHECK_CORS_ORIGINS` | `*` | CORS origins (comma-separated or `*`) |
| `SPELLCHECK_HUNSPELL_DICT_DIR` | `/app/dictionaries` | Directory with `.aff`/`.dic` files |
| `SPELLCHECK_CUSTOM_DICT_PATH` | `/app/dictionaries/custom/org_dictionary.json` | Custom dictionary file |
| `SPELLCHECK_DEFAULT_LANGUAGE` | `he_IL` | Language identifier for Hunspell |
| `SPELLCHECK_MAX_TEXT_LENGTH` | `200000` | Max characters per request |
| `SPELLCHECK_MAX_SUGGESTIONS` | `5` | Max suggestions per word |
| `SPELLCHECK_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

---

## Logging

The backend uses **structured JSON logging** (via `python-json-logger`).
Each log line is a JSON object with `asctime`, `name`, `levelname`, `message`.

In development, you can switch to plain text by changing the formatter in
`app/main.py → _setup_logging()`.

Logs are written to `stdout`.  Docker Compose captures them; tail with:

```bash
docker compose logs -f spellcheck-api
```

Mount `./backend/logs` to persist logs to disk (configured in `docker-compose.yml`).

---

## Known Limitations

1. **Morphological analysis**: Hunspell checks words in isolation.  Hebrew is a
   highly inflected language; some correctly conjugated forms may be flagged if
   not in the dictionary.  Mitigation: add common forms to the custom dictionary.

2. **Cross-text-node misspellings**: If a word is split across two DOM text nodes
   (rare, usually due to formatting applied mid-word), the plugin skips
   highlighting that occurrence safely.  The word still appears in the API result.

3. **Performance on very long documents**: The plugin extracts all text before
   checking.  Documents over ~10,000 words may take 1–2 seconds.  The progress
   indicator covers this case.

4. **Nikud (vowel points)**: Nikud is stripped before Hunspell check.  If a word
   is only misspelled in its nikud, it will pass.  This is the correct behaviour
   for most enterprise use cases.

5. **Mixed Hebrew-English tokens**: A word like `מנהל-CRM` is tokenised as
   `מנהל` (Hebrew, checked) + `CRM` (non-Hebrew, skipped).

6. **Dictionary thread safety**: The JSON-file dictionary is not protected by a
   file lock.  Simultaneous writes from multiple workers may corrupt the file.
   Use `--workers 1` in production or switch to SQLite (see next section).

7. **TinyMCE language pack**: The Hebrew UI language pack for TinyMCE is not
   bundled here.  Download it from the TinyMCE language pack page if needed for
   a fully Hebrew UI.

---

## Recommended Next Improvements

### For production

- **Dictionary storage → SQLite or PostgreSQL**: Replace the JSON file with a
  proper database for concurrent write safety and faster lookup.
  `DictionaryService` is isolated and easy to swap.

- **Authentication**: Add an API key header or OAuth2 integration so the
  spell-check service cannot be used by unauthorised clients on the intranet.

- **Hunspell worker pool**: If running multiple Uvicorn workers, initialise one
  `HunSpell` instance per process (already the case; document it).

- **Rate limiting**: Add `slowapi` or Nginx rate limits to protect against
  accidental document spam.

- **HTTPS**: Terminate TLS at Nginx or your load balancer.

### Multilingual support

The architecture already supports multiple languages.  To add English:

1. Install `hunspell-en-us` (or copy `en_US.aff` / `en_US.dic` to
   `backend/dictionaries/`).
2. The API accepts `"language": "en-US"` — it maps to `en_US` files.
3. In the plugin, add a language selector button or use the document language.
4. Extend `SpellCheckRequest.language_allowed` validator with the new tag.

Each `SpellService` instance handles one language.  For multi-language support,
instantiate multiple services (one per language) and route requests by the
`language` field.

### Dictionary storage → database

```python
# Swap DictionaryService with a SQLAlchemy-backed version:
class SQLDictionaryService:
    def __init__(self, db_url: str): ...
    def contains(self, word: str) -> bool: ...
    def add(self, word: str) -> bool: ...
    def remove(self, word: str) -> bool: ...
    def list_words(self) -> List[str]: ...
```

No changes to routes or the plugin are required.

### Suggestions quality

- Consider integrating **hspell** (Israeli academic Hebrew spell checker) for
  better morphological coverage.
- Add a **frequency-ranked suggestions** step: re-rank Hunspell suggestions by
  word frequency from a Hebrew corpus.
