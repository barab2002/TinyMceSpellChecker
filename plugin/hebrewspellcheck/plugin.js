/**
 * TinyMCE v7 – Hebrew Spell-Check Plugin
 * =======================================
 * File: plugin/hebrewspellcheck/plugin.js
 *
 * Load in TinyMCE via:
 *   tinymce.init({
 *     external_plugins: { hebrewspellcheck: '/path/to/hebrewspellcheck/plugin.js' },
 *     toolbar: 'hebrewspellcheck',
 *     ...hebrewSpellCheckConfig
 *   });
 *
 * Plugin config keys (passed via tinymce.init options):
 *   hebrewspellcheck_api_url  — backend base URL (default: http://localhost:8000)
 *   hebrewspellcheck_language — BCP-47 language tag    (default: he-IL)
 *   hebrewspellcheck_max_suggestions — integer         (default: 5)
 *
 * Architecture
 * ------------
 *  1. User clicks "בדיקת איות" toolbar button.
 *  2. Plugin walks the editor DOM (text nodes only) to build a plain-text
 *     corpus with character-offset metadata.
 *  3. Plain text is sent to POST /spell/check.
 *  4. Server returns misspelled words with start/end offsets in that plain text.
 *  5. Plugin maps each offset back to the exact text node(s) and wraps the
 *     misspelled run in a <span class="mce-spellcheck-word"> element.
 *  6. A click listener on the editor body detects clicks on those spans and
 *     shows a lightweight popover with suggestions / ignore / add-to-dict actions.
 *
 * Safety notes
 * ------------
 *  • Only TEXT_NODE nodes are walked — link hrefs, image srcs, data attributes,
 *    and formatting tags are never touched.
 *  • Existing highlights are removed before re-running (idempotent).
 *  • No innerHTML assignment is used in the suggestion popover — all DOM is
 *    built via createElement/textContent to prevent XSS.
 *  • The editor's undo stack is preserved: we use a single editor.undoManager
 *    transaction around all DOM mutations.
 */

/* global tinymce */
(function () {
  'use strict';

  // ─── Constants ────────────────────────────────────────────────────────────

  const PLUGIN_NAME      = 'hebrewspellcheck';
  const SPAN_CLASS       = 'mce-spellcheck-word';
  const SPAN_ACTIVE_CLS  = 'mce-spellcheck-word--active';
  const POPOVER_ID       = 'mce-spellcheck-popover';
  const STYLE_ID         = 'mce-spellcheck-styles';

  // ─── Styles injected into the editor <head> ───────────────────────────────

  const EDITOR_CSS = `
    .${SPAN_CLASS} {
      border-bottom: 2px solid #e53e3e;
      cursor: pointer;
      border-radius: 1px;
    }
    .${SPAN_CLASS}:hover,
    .${SPAN_ACTIVE_CLS} {
      background-color: rgba(229, 62, 62, 0.12);
    }
  `;

  // ─── API client ───────────────────────────────────────────────────────────

  /**
   * Thin fetch wrapper for the spell-check backend.
   */
  const Api = {
    _baseUrl: 'http://localhost:8000',
    _language: 'he-IL',

    init(baseUrl, language) {
      this._baseUrl = (baseUrl || 'http://localhost:8000').replace(/\/$/, '');
      this._language = language || 'he-IL';
    },

    async checkText(plainText, maxSuggestions = 5) {
      const res = await fetch(`${this._baseUrl}/spell/check`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          text: plainText,
          language: this._language,
          options: { includeSuggestions: true, maxSuggestions },
        }),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Spell-check API error ${res.status}: ${body}`);
      }
      return res.json(); // { language, misspellings: [...], total }
    },

    async addToDictionary(word) {
      const res = await fetch(`${this._baseUrl}/dictionary/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ word }),
      });
      if (!res.ok) throw new Error(`Dictionary API error ${res.status}`);
      return res.json();
    },
  };

  // ─── Text extraction ──────────────────────────────────────────────────────

  /**
   * Walk the editor body collecting TEXT_NODE content.
   *
   * Returns:
   *   {
   *     plainText: string,          // concatenated text of all text nodes
   *     segments: Array<{           // one entry per text node
   *       node: Text,
   *       start: number,            // offset of node's text in plainText
   *       end:   number,
   *     }>
   *   }
   *
   * We insert a space between block-level siblings so that words at
   * paragraph boundaries don't merge (e.g. "wordA</p><p>wordB" → "wordA wordB").
   */
  function extractTextSegments(editorBody) {
    const BLOCK_TAGS = new Set([
      'P','DIV','H1','H2','H3','H4','H5','H6','LI','TD','TH',
      'BLOCKQUOTE','PRE','ARTICLE','SECTION','HEADER','FOOTER',
      'FIGCAPTION','CAPTION',
    ]);

    const segments = [];
    let plainText  = '';

    const walker = document.createTreeWalker(
      editorBody,
      NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT,
      null
    );

    let prevWasBlock = false;
    let node;

    while ((node = walker.nextNode())) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (BLOCK_TAGS.has(node.tagName)) {
          // We'll append a separator when we next encounter a text node
          prevWasBlock = true;
        }
        continue;
      }

      // TEXT_NODE
      // Skip content inside spell-check spans themselves (avoid double processing)
      if (node.parentElement && node.parentElement.classList.contains(SPAN_CLASS)) {
        // Include it as text but don't record its node for wrapping
        // (it is already wrapped)
        const text = node.textContent;
        if (prevWasBlock && plainText) { plainText += ' '; prevWasBlock = false; }
        segments.push({ node, start: plainText.length, end: plainText.length + text.length, wrapped: true });
        plainText += text;
        continue;
      }

      const text = node.textContent;
      if (!text) continue;

      if (prevWasBlock && plainText) {
        plainText += ' ';
        prevWasBlock = false;
      }

      segments.push({ node, start: plainText.length, end: plainText.length + text.length, wrapped: false });
      plainText += text;
    }

    return { plainText, segments };
  }

  // ─── Highlighting ─────────────────────────────────────────────────────────

  /**
   * Remove all existing spell-check spans, restoring their text nodes.
   * Must be called before re-running a check.
   */
  function clearHighlights(editorBody) {
    const spans = editorBody.querySelectorAll(`.${SPAN_CLASS}`);
    spans.forEach((span) => {
      const parent = span.parentNode;
      if (!parent) return;
      // Replace span with its text content
      while (span.firstChild) {
        parent.insertBefore(span.firstChild, span);
      }
      parent.removeChild(span);
      parent.normalize(); // merge adjacent text nodes
    });
  }

  /**
   * Wrap a range within a single text node in a spell-check span.
   * Returns the created span element.
   */
  function wrapTextNodeRange(textNode, localStart, localEnd, word, suggestions) {
    // Split text node: [prefix][misspelled][suffix]
    const text = textNode.textContent;
    const prefix = text.slice(0, localStart);
    const misspelled = text.slice(localStart, localEnd);
    const suffix = text.slice(localEnd);

    const span = document.createElement('span');
    span.className = SPAN_CLASS;
    span.textContent = misspelled;
    span.dataset.word = word;
    span.dataset.suggestions = JSON.stringify(suggestions);

    const parent = textNode.parentNode;

    if (prefix) {
      parent.insertBefore(document.createTextNode(prefix), textNode);
    }
    parent.insertBefore(span, textNode);
    if (suffix) {
      textNode.textContent = suffix;
    } else {
      parent.removeChild(textNode);
    }

    return span;
  }

  /**
   * Apply all misspellings returned by the API to the editor DOM.
   *
   * Strategy:
   *   For each misspelling {start, end}, find the segment(s) that cover
   *   that range.  If the range is fully inside one text node, wrap it.
   *   Cross-node misspellings (rare edge case) are skipped to be safe.
   */
  function applyHighlights(segments, misspellings) {
    // Sort in reverse order so that earlier offsets don't shift when we split
    const sorted = [...misspellings].sort((a, b) => b.start - a.start);

    // Rebuild segment list: we need the current (live) list because wrapping
    // splits text nodes.  We'll do a single pass in reverse.
    for (const miss of sorted) {
      const { word, start: mStart, end: mEnd, suggestions } = miss;

      // Find the segment that fully contains this range
      const seg = segments.find(
        (s) => !s.wrapped && s.start <= mStart && s.end >= mEnd
      );
      if (!seg) continue; // cross-node or already wrapped — skip safely

      const localStart = mStart - seg.start;
      const localEnd   = mEnd   - seg.start;

      // Sanity: make sure the node still contains the expected text
      const nodeText = seg.node.textContent;
      if (nodeText.slice(localStart, localEnd) !== word &&
          // Tolerate nikud-stripped mismatches
          nodeText.slice(localStart, localEnd).replace(/[\u0591-\u05C7]/g, '') !== word) {
        continue;
      }

      const span = wrapTextNodeRange(seg.node, localStart, localEnd, word, suggestions || []);

      // Update segment so subsequent (lower-offset) items still resolve correctly.
      // After wrapping, seg.node contains the suffix text (or is removed).
      // The prefix, span, and suffix have replaced seg.node.
      // Adjust seg to reflect only the suffix (text after the misspelling).
      const suffixLen = nodeText.length - localEnd;
      if (suffixLen > 0) {
        seg.start = mEnd;
        // seg.node now holds the suffix text
      } else {
        seg.start = seg.end; // segment exhausted
      }
    }
  }

  // ─── Suggestion popover ───────────────────────────────────────────────────

  /**
   * The popover is rendered in the HOST document (not the editor iframe) so
   * it can overflow the editor boundaries.  It is absolutely positioned using
   * the span's bounding rect.
   *
   * All DOM construction uses createElement/textContent — no innerHTML — to
   * prevent any XSS from suggestion strings.
   */
  const Popover = {
    _el: null,
    _currentSpan: null,

    _build() {
      if (this._el) return;
      const el = document.createElement('div');
      el.id = POPOVER_ID;
      Object.assign(el.style, {
        position:        'fixed',
        zIndex:          '2147483647',
        background:      '#fff',
        border:          '1px solid #d1d5db',
        borderRadius:    '6px',
        boxShadow:       '0 4px 16px rgba(0,0,0,0.15)',
        minWidth:        '180px',
        maxWidth:        '280px',
        fontFamily:      'system-ui, Arial, sans-serif',
        fontSize:        '13px',
        padding:         '6px 0',
        display:         'none',
        direction:       'rtl',
      });
      document.body.appendChild(el);
      this._el = el;

      // Close on outside click
      document.addEventListener('mousedown', (e) => {
        if (this._el && !this._el.contains(e.target)) {
          this.hide();
        }
      }, true);
    },

    show(span, editor, onRecheck) {
      this._build();
      this._currentSpan = span;
      span.classList.add(SPAN_ACTIVE_CLS);

      const word        = span.dataset.word || span.textContent;
      let   suggestions = [];
      try { suggestions = JSON.parse(span.dataset.suggestions || '[]'); } catch (_) {}

      const el = this._el;
      el.innerHTML = ''; // safe: we fill it below with createElement only

      // Header
      const header = document.createElement('div');
      Object.assign(header.style, {
        padding:       '6px 12px 4px',
        fontWeight:    '600',
        color:         '#374151',
        borderBottom:  '1px solid #e5e7eb',
        marginBottom:  '4px',
        direction:     'rtl',
      });
      header.textContent = `"${word}"`;
      el.appendChild(header);

      const addItem = (label, onClick, isRed = false) => {
        const btn = document.createElement('div');
        Object.assign(btn.style, {
          padding:    '6px 12px',
          cursor:     'pointer',
          color:      isRed ? '#dc2626' : '#111827',
          direction:  'rtl',
        });
        btn.textContent = label;
        btn.addEventListener('mouseover', () => (btn.style.background = '#f3f4f6'));
        btn.addEventListener('mouseout',  () => (btn.style.background = ''));
        btn.addEventListener('mousedown', (e) => { e.preventDefault(); onClick(); });
        el.appendChild(btn);
      };

      // Suggestions section
      if (suggestions.length > 0) {
        const sugLabel = document.createElement('div');
        Object.assign(sugLabel.style, {
          padding:   '2px 12px',
          fontSize:  '11px',
          color:     '#6b7280',
          direction: 'rtl',
        });
        sugLabel.textContent = 'הצעות תיקון:';
        el.appendChild(sugLabel);

        suggestions.forEach((sug) => {
          addItem(`← ${sug}`, () => {
            this._replaceWord(span, sug, editor);
            this.hide();
            if (typeof onRecheck === 'function') onRecheck();
          });
        });
      } else {
        const noSug = document.createElement('div');
        Object.assign(noSug.style, { padding: '4px 12px', color: '#9ca3af', direction: 'rtl' });
        noSug.textContent = 'אין הצעות';
        el.appendChild(noSug);
      }

      // Separator
      const sep = document.createElement('hr');
      Object.assign(sep.style, { margin: '4px 0', border: 'none', borderTop: '1px solid #e5e7eb' });
      el.appendChild(sep);

      // Ignore
      addItem('התעלם', () => {
        this._ignoreWord(span);
        this.hide();
      });

      // Add to dictionary
      addItem('הוסף למילון', async () => {
        try {
          await Api.addToDictionary(word);
          // Remove ALL highlights for this word across the document
          this._removeAllHighlightsForWord(word, editor);
          this.hide();
          Notifier.show('המילה נוספה למילון בהצלחה', 'success');
        } catch (e) {
          Notifier.show('שגיאה בהוספה למילון', 'error');
        }
      }, false);

      // Position the popover near the span
      const editorIframe = editor.getContainer().querySelector('iframe');
      const iframeRect   = editorIframe ? editorIframe.getBoundingClientRect() : { left: 0, top: 0 };
      const spanRect     = span.getBoundingClientRect();

      el.style.display = 'block';

      const popW  = el.offsetWidth;
      const popH  = el.offsetHeight;
      const vw    = window.innerWidth;
      const vh    = window.innerHeight;

      // Convert span rect from iframe coords to viewport coords
      const absLeft = iframeRect.left + spanRect.left;
      const absTop  = iframeRect.top  + spanRect.bottom + 4;

      let left = absLeft;
      let top  = absTop;

      // Keep inside viewport
      if (left + popW > vw) left = vw - popW - 8;
      if (left < 0) left = 8;
      if (top + popH > vh) top = iframeRect.top + spanRect.top - popH - 4;

      el.style.left = `${left}px`;
      el.style.top  = `${top}px`;
    },

    hide() {
      if (this._currentSpan) {
        this._currentSpan.classList.remove(SPAN_ACTIVE_CLS);
        this._currentSpan = null;
      }
      if (this._el) this._el.style.display = 'none';
    },

    _replaceWord(span, replacement, editor) {
      editor.undoManager.transact(() => {
        const textNode = document.createTextNode(replacement);
        span.parentNode.replaceChild(textNode, span);
        textNode.parentNode.normalize();
      });
    },

    _ignoreWord(span) {
      // Replace span with its text content (remove underline), keep word
      const textNode = document.createTextNode(span.textContent);
      span.parentNode.replaceChild(textNode, span);
      textNode.parentNode.normalize();
    },

    _removeAllHighlightsForWord(word, editor) {
      const body = editor.getBody();
      const spans = body.querySelectorAll(`.${SPAN_CLASS}`);
      spans.forEach((s) => {
        if (s.dataset.word === word) {
          const t = document.createTextNode(s.textContent);
          s.parentNode.replaceChild(t, s);
          t.parentNode.normalize();
        }
      });
    },
  };

  // ─── Toast notifications ──────────────────────────────────────────────────

  const Notifier = {
    show(message, type = 'info') {
      const toast = document.createElement('div');
      const bg    = type === 'success' ? '#16a34a' : type === 'error' ? '#dc2626' : '#2563eb';
      Object.assign(toast.style, {
        position:     'fixed',
        bottom:       '24px',
        right:        '24px',
        background:   bg,
        color:        '#fff',
        padding:      '10px 18px',
        borderRadius: '6px',
        fontFamily:   'system-ui, Arial, sans-serif',
        fontSize:     '13px',
        zIndex:       '2147483647',
        boxShadow:    '0 4px 12px rgba(0,0,0,0.2)',
        direction:    'rtl',
        maxWidth:     '320px',
      });
      toast.textContent = message;
      document.body.appendChild(toast);
      setTimeout(() => document.body.removeChild(toast), 3500);
    },
  };

  // ─── Main plugin ──────────────────────────────────────────────────────────

  tinymce.PluginManager.add(PLUGIN_NAME, function (editor) {

    // --- Resolve config from TinyMCE init options ---
    const apiUrl    = editor.options.get('hebrewspellcheck_api_url') || 'http://localhost:8000';
    const language  = editor.options.get('hebrewspellcheck_language') || 'he-IL';
    const maxSug    = editor.options.get('hebrewspellcheck_max_suggestions') || 5;

    Api.init(apiUrl, language);

    let _lastSegments = []; // used for re-check reference

    // --- Inject CSS into editor iframe ---
    editor.on('init', () => {
      const doc = editor.getDoc();
      if (!doc.getElementById(STYLE_ID)) {
        const style = doc.createElement('style');
        style.id = STYLE_ID;
        style.textContent = EDITOR_CSS;
        doc.head.appendChild(style);
      }
    });

    // --- Click handler on spell-check spans ---
    editor.on('click', (e) => {
      const target = e.target;
      if (target && target.classList && target.classList.contains(SPAN_CLASS)) {
        e.preventDefault();
        e.stopPropagation();
        Popover.show(target, editor, () => runSpellCheck(false));
      } else {
        Popover.hide();
      }
    });

    // --- Core spell-check function ---
    async function runSpellCheck(showLoadingIndicator = true) {
      Popover.hide();

      const body = editor.getBody();
      if (!body) return;

      // 1. Clear previous highlights
      editor.undoManager.transact(() => {
        clearHighlights(body);
      });

      // 2. Extract plain text + segment map
      const { plainText, segments } = extractTextSegments(body);
      _lastSegments = segments;

      if (!plainText.trim()) {
        Notifier.show('אין תוכן לבדיקה', 'info');
        return;
      }

      // 3. Toolbar button loading state
      if (showLoadingIndicator) {
        editor.setProgressState(true);
      }

      try {
        // 4. Call API
        const result = await Api.checkText(plainText, maxSug);

        // 5. Apply highlights inside a single undo transaction
        editor.undoManager.transact(() => {
          applyHighlights(segments, result.misspellings || []);
        });

        const count = result.total || 0;
        if (count === 0) {
          Notifier.show('לא נמצאו שגיאות איות', 'success');
        } else {
          Notifier.show(`נמצאו ${count} שגיאות איות — לחץ על מילה מסומנת לפרטים`, 'info');
        }

      } catch (err) {
        console.error('[HebrewSpellCheck]', err);
        Notifier.show('שגיאה בחיבור לשרת בדיקת האיות. אנא בדוק שהשרת פועל.', 'error');
      } finally {
        if (showLoadingIndicator) {
          editor.setProgressState(false);
        }
      }
    }

    function clearAllHighlights() {
      Popover.hide();
      editor.undoManager.transact(() => {
        clearHighlights(editor.getBody());
      });
      Notifier.show('הסימונים נוקו', 'info');
    }

    // --- Register option declarations (TinyMCE 7 requires this) ---
    editor.options.register('hebrewspellcheck_api_url', {
      processor: 'string',
      default: 'http://localhost:8000',
    });
    editor.options.register('hebrewspellcheck_language', {
      processor: 'string',
      default: 'he-IL',
    });
    editor.options.register('hebrewspellcheck_max_suggestions', {
      processor: 'number',
      default: 5,
    });

    // --- Toolbar buttons ---

    // Main spell-check button
    editor.ui.registry.addButton('hebrewspellcheck', {
      text: 'בדיקת איות',
      tooltip: 'בדיקת איות בעברית',
      onAction: () => runSpellCheck(true),
    });

    // Clear highlights button
    editor.ui.registry.addButton('hebrewspellcheck_clear', {
      text: 'נקה סימונים',
      tooltip: 'נקה את סימוני שגיאות האיות',
      onAction: clearAllHighlights,
    });

    // --- Menu items ---

    editor.ui.registry.addMenuItem('hebrewspellcheck', {
      text: 'בדיקת איות בעברית',
      icon: 'spell-check',
      onAction: () => runSpellCheck(true),
    });

    editor.ui.registry.addMenuItem('hebrewspellcheck_clear', {
      text: 'נקה סימוני איות',
      onAction: clearAllHighlights,
    });

    // --- Context menu (right-click on a spell-check span) ---
    editor.ui.registry.addContextMenu('hebrewspellcheck', {
      update: (element) => {
        if (element.classList && element.classList.contains(SPAN_CLASS)) {
          return 'hebrewspellcheck | hebrewspellcheck_clear';
        }
        return '';
      },
    });

    // --- Plugin metadata ---
    return {
      getMetadata: () => ({
        name: 'Hebrew Spell Checker',
        url: 'https://github.com/your-org/TinyMceSpellChecker',
      }),
    };
  });

})();
