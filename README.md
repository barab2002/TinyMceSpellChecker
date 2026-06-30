# Hebrew Spell-Check for TinyMCE v7

A complete, **internal/private-network** Hebrew spell-checking solution.

| Component | Technology |
|---|---|
| Backend API | Python 3.11 + FastAPI + spylls (pure-Python Hunspell) |
| Dictionary | Bundled `he_IL` (469k words, hspell 1.4) + custom org word list |
| Plugin | Vanilla JavaScript, TinyMCE v7 API (single file, no build step) |
| Deployment | Docker Compose — one command |

**No cloud services. No browser spell-check. No system dependencies. Runs entirely on your network.**

---

## Table of Contents

1. [Embedding in Your App](#embedding-in-your-app) ← **Start here if you already have TinyMCE**
2. [Architecture](#architecture)
3. [Project Structure](#project-structure)
4. [Running the Backend](#running-the-backend)
5. [Plugin Options Reference](#plugin-options-reference)
6. [API Reference](#api-reference)
7. [Custom / Organisational Dictionary](#custom--organisational-dictionary)
8. [CORS Configuration](#cors-configuration)
9. [Environment Variables](#environment-variables)
10. [Logging](#logging)
11. [Known Limitations](#known-limitations)
12. [Future Improvements](#future-improvements)

---

## Embedding in Your App

This section covers everything you need to add Hebrew spell-checking to an **existing** app that already uses TinyMCE v7.

### Overview — 3 things you need

```
1. The backend API running somewhere your browser can reach
2. The plugin file (plugin/hebrewspellcheck/plugin.js) served as a static file
3. Three lines added to your tinymce.init() config
```

---

### Step 1 — Run the backend API

The API is a small Python service. Run it with Docker (easiest) or directly.

#### Option A: Docker (recommended)

```bash
# Clone this repo (or just copy the backend/ folder to your infrastructure)
git clone https://github.com/your-org/TinyMceSpellChecker.git
cd TinyMceSpellChecker

cp .env.example .env          # uses port 8000 by default

docker compose up -d --build  # builds and starts the API

# Verify it's running:
curl http://localhost:8000/health
# → {"status":"ok","hunspell_available":true,"language":"he_IL","custom_dict_words":18}
```

The API is now available at `http://localhost:8000`.
Interactive API docs (Swagger UI): **http://localhost:8000/docs**

#### Option B: Run directly (no Docker)

```bash
cd TinyMceSpellChecker/backend

# Create a virtual environment (recommended)
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install dependencies (pure Python — no system libs needed)
pip install -r requirements.txt

# Start the API
uvicorn app.main:app --host 0.0.0.0 --port 8000

# Swagger UI: http://localhost:8000/docs
```

> **The dictionary files (`he_IL.aff` / `he_IL.dic`) are already bundled** in
> `backend/dictionaries/` — no download step needed.

---

### Step 2 — Serve the plugin file

Copy the plugin file to your project's static assets and serve it via your web server.

```bash
# Copy the plugin into your project's static folder
# (adjust the destination path to match your project)

cp -r TinyMceSpellChecker/plugin/hebrewspellcheck  your-project/static/plugins/
```

After this, the file should be accessible at a URL like:
```
http://your-app/static/plugins/hebrewspellcheck/plugin.js
```

**The plugin is a single `.js` file — no npm install, no build step.**

---

### Step 3 — Add to your TinyMCE init

Make these changes to wherever you call `tinymce.init()`:

#### Minimal integration (copy-paste this)

```html
<!-- 1. Load TinyMCE (self-hosted or CDN) -->
<script src="/tinymce/tinymce.min.js"></script>

<!-- 2. Load the spell-check plugin AFTER tinymce.min.js -->
<script src="/static/plugins/hebrewspellcheck/plugin.js"></script>

<script>
tinymce.init({
  selector: '#my-editor',

  // ── Required: load the plugin ──────────────────────────────────────────
  external_plugins: {
    hebrewspellcheck: '/static/plugins/hebrewspellcheck/plugin.js',
  },

  // ── Required: add buttons to the toolbar ───────────────────────────────
  // "hebrewspellcheck" = run spell check
  // "hebrewspellcheck_clear" = clear all highlights
  toolbar: 'hebrewspellcheck hebrewspellcheck_clear | bold italic underline | ...',

  // ── Required: point to your running backend ────────────────────────────
  hebrewspellcheck_api_url: 'http://localhost:8000',

  // ── Required: allow the plugin's highlight spans through the sanitiser ─
  extended_valid_elements: 'span[class|data-word|data-suggestions]',

  // ── Recommended: disable browser spell-check (we handle it ourselves) ──
  browser_spellcheck: false,
  gecko_spellcheck: false,
});
</script>
```

That's the minimum. The plugin works immediately once the backend is running.

---

### Step 4 — Full production-ready init (all options)

```javascript
tinymce.init({
  selector: '#my-editor',

  // ── Plugin ─────────────────────────────────────────────────────────────
  external_plugins: {
    hebrewspellcheck: '/static/plugins/hebrewspellcheck/plugin.js',
  },

  // ── Built-in TinyMCE plugins you may already have ──────────────────────
  plugins: ['lists', 'link', 'image', 'table', 'code'],

  // ── Toolbar ────────────────────────────────────────────────────────────
  toolbar: [
    'hebrewspellcheck hebrewspellcheck_clear | ' +
    'undo redo | blocks | bold italic underline | ' +
    'alignright aligncenter alignleft | bullist numlist | link'
  ],

  // ── Optional: add spell-check items to the Tools menu ──────────────────
  menu: {
    tools: { title: 'כלים', items: 'hebrewspellcheck hebrewspellcheck_clear' },
  },

  // ── Spell-check plugin options ──────────────────────────────────────────
  hebrewspellcheck_api_url:         'http://your-api-server:8000', // ← change this
  hebrewspellcheck_language:        'he-IL',                        // only he-IL supported currently
  hebrewspellcheck_max_suggestions: 5,                              // 1–20

  // ── Content filter — allow spell-check spans ───────────────────────────
  extended_valid_elements: 'span[class|data-word|data-suggestions]',

  // ── Disable competing browser spell-check ──────────────────────────────
  browser_spellcheck: false,
  gecko_spellcheck:   false,

  // ── RTL / Hebrew layout (add if your content is Hebrew) ────────────────
  directionality: 'rtl',

  // ── Editor font for Hebrew readability ─────────────────────────────────
  content_style: `
    body {
      font-family: Arial, "David CLM", "Frank Ruehl CLM", sans-serif;
      font-size: 15px;
      line-height: 1.75;
      direction: rtl;
      text-align: right;
    }
  `,
});
```

---

### Step 5 — Verify it works

1. Open your app and find the editor
2. Click **"בדיקת איות"** in the toolbar
3. Type or paste some Hebrew text with a deliberate mistake (e.g. `שלומ` instead of `שלום`)
4. Run the spell check — misspelled words appear with a **red underline**
5. Click a red word — a suggestion popover appears
6. Click a suggestion to replace the word

**Health check URL** (check in browser or curl):
```
http://your-api-server:8000/health
```

**Swagger UI** (interactive API docs):
```
http://your-api-server:8000/docs
```

---

### Framework-specific notes

#### React

```jsx
import { useEffect, useRef } from 'react';

export function HebrewEditor() {
  const editorRef = useRef(null);

  useEffect(() => {
    // Make sure plugin.js is loaded before this runs
    // (add <script src="/plugins/hebrewspellcheck/plugin.js"> in index.html)
    tinymce.init({
      selector: '#hebrew-editor',
      external_plugins: {
        hebrewspellcheck: '/plugins/hebrewspellcheck/plugin.js',
      },
      toolbar: 'hebrewspellcheck hebrewspellcheck_clear | bold italic',
      hebrewspellcheck_api_url: process.env.REACT_APP_SPELLCHECK_URL || 'http://localhost:8000',
      extended_valid_elements: 'span[class|data-word|data-suggestions]',
      browser_spellcheck: false,
    });

    return () => tinymce.remove('#hebrew-editor');
  }, []);

  return <textarea id="hebrew-editor" />;
}
```

#### Vue 3

```vue
<template>
  <textarea id="hebrew-editor"></textarea>
</template>

<script setup>
import { onMounted, onUnmounted } from 'vue';

onMounted(() => {
  tinymce.init({
    selector: '#hebrew-editor',
    external_plugins: {
      hebrewspellcheck: '/plugins/hebrewspellcheck/plugin.js',
    },
    toolbar: 'hebrewspellcheck hebrewspellcheck_clear | bold italic',
    hebrewspellcheck_api_url: import.meta.env.VITE_SPELLCHECK_URL || 'http://localhost:8000',
    extended_valid_elements: 'span[class|data-word|data-suggestions]',
    browser_spellcheck: false,
  });
});

onUnmounted(() => tinymce.remove('#hebrew-editor'));
</script>
```

#### Next.js (App Router)

```tsx
// components/HebrewEditor.tsx
'use client';
import { useEffect } from 'react';

export function HebrewEditor() {
  useEffect(() => {
    // Dynamic import keeps TinyMCE out of the server bundle
    import('tinymce').then(() => {
      tinymce.init({
        selector: '#hebrew-editor',
        external_plugins: {
          hebrewspellcheck: '/plugins/hebrewspellcheck/plugin.js',
        },
        toolbar: 'hebrewspellcheck hebrewspellcheck_clear | bold italic',
        hebrewspellcheck_api_url: process.env.NEXT_PUBLIC_SPELLCHECK_URL ?? 'http://localhost:8000',
        extended_valid_elements: 'span[class|data-word|data-suggestions]',
        browser_spellcheck: false,
      });
    });
    return () => { tinymce.remove('#hebrew-editor'); };
  }, []);

  return <textarea id="hebrew-editor" />;
}
```

> **Note:** Copy `plugin.js` into your `public/plugins/hebrewspellcheck/` folder so
> Next.js serves it as a static file.

---

### Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Clicking "בדיקת איות" shows error toast | Backend not reachable from browser | Check `hebrewspellcheck_api_url` and that the API is running |
| Misspelled words not highlighted | `extended_valid_elements` missing | Add `extended_valid_elements: 'span[class|data-word|data-suggestions]'` |
| Org product names flagged as errors | Not in custom dictionary | Suggest it via the "הצע למילון" button and have it approved, or call `POST /dictionary/approve` directly |
| CORS error in browser console | Backend rejecting your app's origin | See [CORS Configuration](#cors-configuration) below |
| `option 'hebrewspellcheck_api_url' not registered` in console | Old TinyMCE version | Requires TinyMCE **v7** — check `tinymce.majorVersion` |
| Plugin not loading at all | Wrong file path | Verify `plugin.js` is served at the URL you pass to `external_plugins` |
| No toolbar buttons appear | Plugin path in `external_plugins` doesn't match `<script src>` | Use the same URL in both places |

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Browser — Your App with TinyMCE v7                     │
│  ┌────────────────────────────────────────────────────┐ │
│  │  hebrewspellcheck plugin  (plugin.js)              │ │
│  │  1. Walk editor DOM text nodes (TreeWalker)        │ │
│  │  2. Build plain-text corpus + character offset map │ │
│  │  3. POST /spell/check  ──────────────────────────► │ │
│  │  4. Map offsets back → wrap spans in DOM           │ │
│  │  5. Click span → suggestion popover                │ │
│  └────────────────────────────────────────────────────┘ │
└─────────────────────────────┬───────────────────────────┘
                              │ HTTP JSON  (internal network only)
                              ▼
┌──────────────────────────────────────────────────────────┐
│  FastAPI  (backend/app/main.py)                          │
│                                                          │
│  POST /spell/check     ──► SpellService (spylls + he_IL) │
│  POST /dictionary/suggest ► forwards to APPROVEIT_URL    │
│  POST /dictionary/approve ◄ callback from APPROVEIT_URL  │
│  GET /health                                             │
│                                                          │
│  he_IL.aff + he_IL.dic (bundled) + custom dictionary    │
│  (MongoDB)                                               │
└──────────────────────────────────────────────────────────┘
```

### Key design decisions

| Concern | Decision | Why |
|---|---|---|
| Text sent to API | **Plain text only** | Server never sees HTML; avoids HTML-injection risks |
| Highlighting | `<span class="mce-spellcheck-word">` around text nodes | Only text is touched — links, images, attributes untouched |
| DOM walk | `TreeWalker(SHOW_TEXT)` using `editorBody.ownerDocument` | Correct in both iframe and inline TinyMCE modes |
| Undo safety | `editor.undoManager.transact()` | Entire check is one undo step |
| XSS prevention | Popover built with `createElement`/`textContent` only | Suggestion strings from server are never executed |
| Dictionary priority | Custom dict checked **before** Hunspell | Org words always accepted |

---

## Project Structure

```
TinyMceSpellChecker/
│
├── plugin/
│   └── hebrewspellcheck/
│       └── plugin.js            ← THE FILE YOU COPY INTO YOUR APP
│
├── backend/
│   ├── app/
│   │   ├── main.py              ← FastAPI entry point
│   │   ├── config.py            ← All config via SPELLCHECK_* env vars
│   │   ├── models/schemas.py    ← Pydantic request/response schemas
│   │   ├── services/
│   │   │   ├── spell_service.py     ← spylls wrapper + Hebrew tokeniser
│   │   │   └── dictionary_service.py ← MongoDB-backed custom word list
│   │   └── routes/
│   │       ├── spell.py         ← POST /spell/check
│   │       └── dictionary.py    ← POST /dictionary/suggest, POST /dictionary/approve
│   ├── dictionaries/
│   │   ├── he_IL.aff            ← Bundled Hebrew Hunspell affix rules
│   │   ├── he_IL.dic            ← Bundled Hebrew word list (469k words)
│   │   └── custom/
│   │       └── org_dictionary.json  ← One-time seed for MongoDB (empty-DB only)
│   ├── requirements.txt
│   └── Dockerfile
│
├── example/
│   ├── index.html               ← Full working demo page
│   ├── test_data.json           ← Sample test cases with Hebrew errors
│   └── sample_api_calls.sh      ← curl examples for all endpoints
│
├── docker-compose.yml           ← API + static file server
├── nginx.conf
├── .env.example
└── README.md
```

---

## Running the Backend

### Docker Compose (API + static demo server)

```bash
cp .env.example .env
docker compose up -d --build

# API:         http://localhost:8000
# Swagger UI:  http://localhost:8000/docs
# Demo page:   http://localhost:3000/example/index.html
```

### Docker (API only — no static server)

```bash
cd backend
docker build -t hebrew-spellcheck .
docker run -d \
  -p 8000:8000 \
  -v $(pwd)/dictionaries/custom:/app/dictionaries/custom \
  -e SPELLCHECK_CORS_ORIGINS="http://your-app.com" \
  --name hebrew-spellcheck \
  hebrew-spellcheck
```

### Local Python

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### Stop / restart

```bash
docker compose down            # stop
docker compose up -d           # start again (no rebuild)
docker compose up -d --build   # rebuild (after code changes)
docker compose logs -f         # tail logs
```

---

## Plugin Options Reference

Pass these in your `tinymce.init()` call alongside the plugin registration.

| Option | Type | Default | Description |
|---|---|---|---|
| `hebrewspellcheck_api_url` | `string` | `http://localhost:8000` | Base URL of the spell-check API. **Change this in production.** |
| `hebrewspellcheck_language` | `string` | `he-IL` | BCP-47 language tag. Currently only `he-IL` is supported. |
| `hebrewspellcheck_max_suggestions` | `number` | `5` | Max suggestions returned per misspelled word (1–20). |

### Toolbar buttons

| Button ID | Hebrew label | Action |
|---|---|---|
| `hebrewspellcheck` | בדיקת איות | Run spell check on the full document |
| `hebrewspellcheck_clear` | נקה סימונים | Remove all red underlines |

### In-editor popover actions (when you click a red word)

| Action | What it does |
|---|---|
| `• suggestion` | Replaces the word with that suggestion |
| התעלם | Removes the highlight, keeps the original word |
| הצע למילון | Calls `POST /dictionary/suggest`, forwards `{word, context}` to the external `APPROVEIT_URL` service for review |

---

## API Reference

Interactive docs with runnable examples: **`http://localhost:8000/docs`**

---

### `GET /health`

Returns service health. Use this to confirm the backend is running and the
dictionary is loaded.

```bash
curl http://localhost:8000/health
```

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

Check text for Hebrew spelling errors.

```bash
curl -X POST http://localhost:8000/spell/check \
  -H "Content-Type: application/json" \
  -d '{
    "text": "שלומ לכולם. אנחנו עוסקים בפיתוח תוכנה.",
    "language": "he-IL",
    "options": { "includeSuggestions": true, "maxSuggestions": 5 }
  }'
```

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

`start` / `end` are **character offsets** in the text you sent. The plugin uses
these to find and highlight the exact text node in the editor.

---

### `POST /dictionary/suggest`

Forward a word (and optional surrounding context) to the external approval
service configured via `APPROVEIT_URL`. The plugin calls this when the user
clicks **"הצע למילון"** on a misspelled word.

```bash
curl -X POST http://localhost:8000/dictionary/suggest \
  -H "Content-Type: application/json" \
  -d '{"word": "MyProduct", "context": "We use MyProduct for our CRM."}'
```

```json
{ "status": "ok" }
```

Returns `500` if `APPROVEIT_URL` is not configured, or `502` if the approval
service is unreachable or returns an error.

---

### `POST /dictionary/approve`

Callback for your external approval service. Call this once a reviewer
approves a word suggested via `/dictionary/suggest`, and it's added to the
organisational dictionary immediately — every worker picks it up on its next
refresh cycle (`SPELLCHECK_DICT_REFRESH_INTERVAL_MINUTES`).

```bash
curl -X POST http://localhost:8000/dictionary/approve \
  -H "Content-Type: application/json" \
  -d '{"word": "MyProduct"}'
```

```json
{ "added": true }
```

`added` is `false` if the word was already present. Returns `400` for an
invalid word, `503` if MongoDB is unavailable.

This endpoint has **no authentication** — only expose it on a network your
approval service can reach but the public internet cannot.

---

## Custom / Organisational Dictionary

Custom words are stored in **MongoDB** (`DictionaryService`,
`backend/app/services/dictionary_service.py`), with an in-memory cache per
worker that refreshes on `SPELLCHECK_DICT_REFRESH_INTERVAL_MINUTES`.
`backend/dictionaries/custom/org_dictionary.json` is only used as a **one-time
seed** the first time the database is empty.

Words you add here are **always accepted** — they take priority over Hunspell.
Use it for: product names, customer names, internal acronyms, technical terms.

Words are added automatically by the approval loop: a user suggests a word via
the **"הצע למילון"** button (`POST /dictionary/suggest`, forwarded to your
external `APPROVEIT_URL` service), and once a reviewer approves it there, your
approval service calls `POST /dictionary/approve` back into the API to add it.

---

## CORS Configuration

If your app and the API run on **different origins** (different host or port),
configure CORS before deploying.

### In `.env`

```bash
# Single origin
CORS_ORIGINS=https://your-app.example.com

# Multiple origins (comma-separated)
CORS_ORIGINS=https://app.example.com,https://cms.example.com

# Internal network (any origin) — fine for intranet-only deployments
CORS_ORIGINS=*
```

### In `docker compose up`

```bash
CORS_ORIGINS=https://your-app.example.com docker compose up -d
```

### How to tell if CORS is the problem

Open your browser's developer tools → **Console** tab.
Look for a message like:

```
Access to fetch at 'http://localhost:8000/spell/check' from origin
'http://your-app.com' has been blocked by CORS policy
```

Fix: add `http://your-app.com` to `CORS_ORIGINS`.

---

## Environment Variables

All backend settings use the `SPELLCHECK_` prefix.

| Variable | Default | Description |
|---|---|---|
| `SPELLCHECK_HOST` | `0.0.0.0` | Bind address |
| `SPELLCHECK_PORT` | `8000` | API port |
| `SPELLCHECK_CORS_ORIGINS` | `*` | Allowed CORS origins (comma-separated or `*`) |
| `SPELLCHECK_HUNSPELL_DICT_DIR` | `/app/dictionaries` | Path to the directory with `he_IL.aff` / `he_IL.dic` |
| `SPELLCHECK_CUSTOM_DICT_PATH` | `/app/dictionaries/custom/org_dictionary.json` | One-time seed file, loaded into MongoDB only if the collection is empty |
| `SPELLCHECK_MONGO_URI` | `mongodb://mongo:27017` | MongoDB connection string for the custom dictionary store |
| `SPELLCHECK_MONGO_DB` | `spellcheck` | MongoDB database name |
| `SPELLCHECK_MONGO_COLLECTION` | `custom_dictionary` | MongoDB collection name |
| `SPELLCHECK_DICT_REFRESH_INTERVAL_MINUTES` | `5` | How often each worker reloads the dictionary cache from MongoDB (`0` disables) |
| `SPELLCHECK_DEFAULT_LANGUAGE` | `he_IL` | Spell-check language (must match a `.aff`/`.dic` pair) |
| `SPELLCHECK_MAX_TEXT_LENGTH` | `200000` | Maximum characters accepted per request |
| `SPELLCHECK_MAX_SUGGESTIONS` | `5` | Default max suggestions per word |
| `SPELLCHECK_LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |
| `SPELLCHECK_APPROVEIT_URL` | _(empty)_ | External service URL that receives `{word, context}` from `POST /dictionary/suggest` |

Copy `.env.example` to `.env` and edit as needed before starting with Docker.

---

## Logging

The backend uses structured **JSON logging** (via `python-json-logger`).
Each line is a JSON object with `asctime`, `name`, `levelname`, `message`.

```bash
# Tail logs in Docker
docker compose logs -f spellcheck-api

# Example log line
{"asctime": "2024-01-15 10:23:41", "levelname": "INFO",
 "message": "spell/check lang=he_IL text_len=342"}
```

Set `SPELLCHECK_LOG_LEVEL=DEBUG` for verbose output during development.

---

## Known Limitations

1. **Hebrew morphology**: Hunspell checks isolated word forms. Hebrew is highly
   inflected — some valid conjugated forms may be flagged. Mitigation: add them
   to the custom dictionary.

2. **Word split across DOM nodes**: If a word is split across two text nodes
   (rare — caused by formatting applied mid-word), the plugin skips that
   occurrence safely. The word still appears in the API result.

3. **Nikud (vowel points)**: Stripped before checking. A word misspelled only
   in its nikud will pass — correct for most enterprise use cases.

4. **Mixed Hebrew-English tokens**: `מנהל-CRM` is tokenised as `מנהל` (checked)
   + `CRM` (skipped as non-Hebrew).

5. **`/dictionary/approve` has no authentication**: Anyone who can reach the
   endpoint can add words. Only expose it on a network your approval service
   can reach but the public internet cannot.

6. **TinyMCE Hebrew UI language pack**: Not bundled. Download separately from
   the TinyMCE website if you want the editor's own UI (menus, dialogs) in Hebrew.

---

## Future Improvements

### Production hardening

- **Authenticate `/dictionary/approve`** — add a shared-secret header check so
  only your approval service can call it.
- **API key authentication** — add a header check so only authorised apps can
  call the spell-check service on your intranet.
- **HTTPS** — terminate TLS at your Nginx/load balancer in front of the API.
- **Rate limiting** — add `slowapi` middleware or Nginx limits.

### Adding more languages

1. Copy `.aff` / `.dic` files for the new language into `backend/dictionaries/`.
   For English: `apt-get install hunspell-en-us` then copy `en_US.*`.
2. Add the language tag to the validator whitelist in `backend/app/models/schemas.py`.
3. Pass `hebrewspellcheck_language: 'en-US'` in your `tinymce.init()`.

### Suggestions quality

Integrate **hspell** (Israeli academic Hebrew spell checker) for better
morphological coverage, or add a word-frequency re-ranking step.
