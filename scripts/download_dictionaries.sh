#!/usr/bin/env bash
# ─── Download Hebrew Hunspell dictionary files ────────────────────────────────
# Run this script ONCE before building the Docker image if you want to bundle
# the dictionary files directly (instead of relying on apt-get hunspell-he).
#
# Sources (try in order):
#   1. Ubuntu/Debian apt package (preferred)
#   2. LibreOffice extension mirror on GitHub
#
# Output: backend/dictionaries/he_IL.aff  +  backend/dictionaries/he_IL.dic
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

DICT_DIR="$(dirname "$0")/../backend/dictionaries"
mkdir -p "$DICT_DIR"

AFF="$DICT_DIR/he_IL.aff"
DIC="$DICT_DIR/he_IL.dic"

if [ -f "$AFF" ] && [ -f "$DIC" ]; then
  echo "✓ Dictionary files already present in $DICT_DIR — skipping download."
  exit 0
fi

echo "Attempting to install hunspell-he via apt-get..."
if command -v apt-get &>/dev/null; then
  if apt-get install -y --no-install-recommends hunspell-he 2>/dev/null; then
    # Copy from system location
    for SYS_DIR in /usr/share/hunspell /usr/share/myspell/dicts; do
      if [ -f "$SYS_DIR/he_IL.aff" ]; then
        cp "$SYS_DIR/he_IL.aff" "$AFF"
        cp "$SYS_DIR/he_IL.dic" "$DIC"
        echo "✓ Copied from $SYS_DIR"
        exit 0
      fi
    done
  fi
fi

echo "apt-get unavailable or hunspell-he not found — trying GitHub mirror..."

# Hebrew dictionary from the LibreOffice extension (maintained mirror)
BASE_URL="https://raw.githubusercontent.com/wooorm/dictionaries/main/dictionaries/he"
curl -fsSL "$BASE_URL/index.aff" -o "$AFF"
curl -fsSL "$BASE_URL/index.dic" -o "$DIC"

# Rename to he_IL.*
echo "✓ Downloaded Hebrew dictionary to $DICT_DIR"
echo "  Files: he_IL.aff ($(wc -l < "$AFF") lines), he_IL.dic ($(wc -l < "$DIC") lines)"
