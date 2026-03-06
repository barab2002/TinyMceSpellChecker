#!/usr/bin/env bash
# ─── Sample API calls for the Hebrew Spell-Check service ─────────────────────
# Requires: curl, jq (optional, for pretty output)
# Usage: bash sample_api_calls.sh [BASE_URL]
#
# Default base URL: http://localhost:8000
# ──────────────────────────────────────────────────────────────────────────────

BASE_URL="${1:-http://localhost:8000}"
DIVIDER="──────────────────────────────────────────────"

echo "$DIVIDER"
echo " Hebrew Spell-Check API — Sample Calls"
echo " Base URL: $BASE_URL"
echo "$DIVIDER"

# ── 1. Health check ────────────────────────────────────────────────────────────
echo ""
echo "1. GET /health"
curl -s "$BASE_URL/health" | jq . 2>/dev/null || curl -s "$BASE_URL/health"
echo ""

# ── 2. Spell check — basic Hebrew text with intentional errors ─────────────────
echo "$DIVIDER"
echo "2. POST /spell/check — Hebrew text with intentional errors"
curl -s -X POST "$BASE_URL/spell/check" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "שלומ לכולם. אנחנו עוסקים בפיתוח מוצרים ובניהול אורחנוזציה גדולה.",
    "language": "he-IL",
    "options": {
      "includeSuggestions": true,
      "maxSuggestions": 5
    }
  }' | jq . 2>/dev/null || \
curl -s -X POST "$BASE_URL/spell/check" \
  -H "Content-Type: application/json" \
  -d '{"text":"שלומ לכולם","language":"he-IL","options":{"includeSuggestions":true,"maxSuggestions":5}}'
echo ""

# ── 3. Spell check — clean text (no errors expected) ──────────────────────────
echo "$DIVIDER"
echo "3. POST /spell/check — Clean text (no errors expected)"
curl -s -X POST "$BASE_URL/spell/check" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "שלום לכולם. אנחנו עוסקים בפיתוח תוכנה.",
    "language": "he-IL"
  }' | jq . 2>/dev/null
echo ""

# ── 4. Spell check — org-dictionary words should NOT be flagged ────────────────
echo "$DIVIDER"
echo "4. POST /spell/check — Org words (Salesforce, ZoomInfo) should be clean"
curl -s -X POST "$BASE_URL/spell/check" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "אנחנו משתמשים ב-Salesforce וב-ZoomInfo כדי לנהל את הלקוחות שלנו.",
    "language": "he-IL"
  }' | jq . 2>/dev/null
echo ""

# ── 5. Get custom dictionary ───────────────────────────────────────────────────
echo "$DIVIDER"
echo "5. GET /dictionary — list custom words"
curl -s "$BASE_URL/dictionary" | jq . 2>/dev/null
echo ""

# ── 6. Add a word to the dictionary ───────────────────────────────────────────
echo "$DIVIDER"
echo "6. POST /dictionary/add — add 'TestWord'"
curl -s -X POST "$BASE_URL/dictionary/add" \
  -H "Content-Type: application/json" \
  -d '{"word": "TestWord"}' | jq . 2>/dev/null
echo ""

# ── 7. Verify the added word is no longer flagged ──────────────────────────────
echo "$DIVIDER"
echo "7. POST /spell/check — 'TestWord' should no longer be flagged"
curl -s -X POST "$BASE_URL/spell/check" \
  -H "Content-Type: application/json" \
  -d '{
    "text": "השתמשנו ב-TestWord לצורך הבדיקה.",
    "language": "he-IL"
  }' | jq . 2>/dev/null
echo ""

# ── 8. Remove the test word ────────────────────────────────────────────────────
echo "$DIVIDER"
echo "8. POST /dictionary/remove — remove 'TestWord'"
curl -s -X POST "$BASE_URL/dictionary/remove" \
  -H "Content-Type: application/json" \
  -d '{"word": "TestWord"}' | jq . 2>/dev/null
echo ""

echo "$DIVIDER"
echo "Done!"
