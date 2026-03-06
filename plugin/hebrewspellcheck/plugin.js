/**
 * TinyMCE v7 – Hebrew Spell-Check Plugin
 * =======================================
 * File: plugin/hebrewspellcheck/plugin.js
 *
 * Zero-dependency, single-file plugin. No build step required.
 *
 * Integration (tinymce.init):
 * ─────────────────────────────────────────────────────────────
 *   tinymce.init({
 *     external_plugins: {
 *       hebrewspellcheck: '/plugin/hebrewspellcheck/plugin.js',
 *     },
 *     toolbar: 'hebrewspellcheck hebrewspellcheck_clear | bold italic ...',
 *     extended_valid_elements: 'span[class|data-word|data-suggestions]',
 *     browser_spellcheck: false,
 *
 *     // Plugin-specific options:
 *     hebrewspellcheck_api_url:         'http://localhost:8000',
 *     hebrewspellcheck_language:        'he-IL',
 *     hebrewspellcheck_max_suggestions: 5,
 *   });
 *
 * Architecture
 * ─────────────────────────────────────────────────────────────
 *  1. "בדיקת איות" button clicked.
 *  2. Walk editor DOM text nodes → build plain-text corpus + segment map.
 *  3. POST /spell/check with plain text.
 *  4. Map returned offsets back to DOM text nodes.
 *  5. Wrap misspelled runs in <span class="mce-spellcheck-word">.
 *  6. Click on span → show suggestion popover (createElement only, no innerHTML).
 *
 * Safety
 * ─────────────────────────────────────────────────────────────
 *  • Only TEXT_NODE nodes are ever touched — hrefs, src, data-* untouched.
 *  • All DOM creation uses ownerDocument (correct in iframe mode).
 *  • Popover built with createElement/textContent — XSS impossible.
 *  • All DOM mutations inside editor.undoManager.transact() (single undo step).
 */

/* global tinymce */
(function () {
  'use strict';

  // ─── Constants ─────────────────────────────────────────────────────────────

  const PLUGIN_NAME     = 'hebrewspellcheck';
  const SPAN_CLASS      = 'mce-spellcheck-word';
  const SPAN_ACTIVE_CLS = 'mce-spellcheck-word--active';
  const POPOVER_ID      = 'mce-spellcheck-popover';
  const STYLE_ID        = 'mce-spellcheck-styles';

  // ─── CSS injected into editor content document ─────────────────────────────

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

  // ─── API client ────────────────────────────────────────────────────────────

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
        signal: AbortSignal.timeout(15000),  // 15 s timeout
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
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Dictionary API error ${res.status}: ${body}`);
      }
      return res.json();
    },
  };

  // ─── Text extraction ───────────────────────────────────────────────────────

  /**
   * Walk the editor body and collect all TEXT_NODE content.
   *
   * Returns { plainText: string, segments: Array<Segment> } where each
   * Segment is:
   *   { node: Text, start: number, end: number, wrapped: boolean }
   *
   * A space is inserted between block-level siblings so that
   * "wordA</p><p>wordB" becomes "wordA wordB" in plainText.
   *
   * @param {Element} editorBody - editor.getBody()
   */
  function extractTextSegments(editorBody) {
    const BLOCK_TAGS = new Set([
      'P', 'DIV', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
      'LI', 'TD', 'TH', 'BLOCKQUOTE', 'PRE',
      'ARTICLE', 'SECTION', 'HEADER', 'FOOTER', 'FIGCAPTION', 'CAPTION',
    ]);

    // Use the editor's own document for the TreeWalker (correct in iframe mode)
    const editorDoc = editorBody.ownerDocument;
    const walker    = editorDoc.createTreeWalker(
      editorBody,
      NodeFilter.SHOW_TEXT | NodeFilter.SHOW_ELEMENT,
      null
    );

    const segments  = [];
    let   plainText = '';
    let   prevWasBlock = false;
    let   node;

    while ((node = walker.nextNode())) {
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (BLOCK_TAGS.has(node.tagName)) prevWasBlock = true;
        continue;
      }

      // TEXT_NODE
      const text = node.textContent;
      if (!text) continue;

      const parentEl = node.parentElement;

      // Insert space at block boundary so words don't merge across paragraphs
      if (prevWasBlock && plainText) {
        plainText  += ' ';
        prevWasBlock = false;
      }

      const start   = plainText.length;
      const end     = start + text.length;
      // "wrapped" means this node is already inside a spell-check span
      const wrapped = !!(parentEl && parentEl.classList.contains(SPAN_CLASS));

      segments.push({ node, start, end, wrapped });
      plainText += text;
    }

    return { plainText, segments };
  }

  // ─── Highlighting ──────────────────────────────────────────────────────────

  /**
   * Remove all spell-check spans from the editor body, restoring plain text.
   * Call this before every spell-check run (idempotent).
   */
  function clearHighlights(editorBody) {
    editorBody.querySelectorAll(`.${SPAN_CLASS}`).forEach((span) => {
      const parent = span.parentNode;
      if (!parent) return;
      while (span.firstChild) parent.insertBefore(span.firstChild, span);
      parent.removeChild(span);
      parent.normalize();
    });
  }

  /**
   * Wrap a sub-range of a text node in a spell-check span.
   *
   * IMPORTANT: uses textNode.ownerDocument.createElement so this works
   * correctly inside TinyMCE's iframe (different document from host page).
   *
   * @param {Text}     textNode
   * @param {number}   localStart - start within textNode.textContent
   * @param {number}   localEnd   - end   within textNode.textContent
   * @param {string}   word
   * @param {string[]} suggestions
   * @returns {HTMLSpanElement}
   */
  function wrapTextNodeRange(textNode, localStart, localEnd, word, suggestions) {
    const doc    = textNode.ownerDocument; // ← use the correct (iframe) document
    const text   = textNode.textContent;
    const before = text.slice(0, localStart);
    const middle = text.slice(localStart, localEnd);
    const after  = text.slice(localEnd);

    const span = doc.createElement('span');
    span.className            = SPAN_CLASS;
    span.textContent          = middle;
    span.dataset.word         = word;
    span.dataset.suggestions  = JSON.stringify(suggestions);

    const parent = textNode.parentNode;

    if (before) parent.insertBefore(doc.createTextNode(before), textNode);
    parent.insertBefore(span, textNode);

    if (after) {
      textNode.textContent = after;
    } else {
      parent.removeChild(textNode);
    }

    return span;
  }

  /**
   * Map API misspelling offsets back to DOM text nodes and wrap them.
   *
   * Processes in reverse offset order so earlier wraps don't shift later offsets.
   */
  function applyHighlights(segments, misspellings) {
    const sorted = [...misspellings].sort((a, b) => b.start - a.start);

    for (const miss of sorted) {
      const { word, start: mStart, end: mEnd, suggestions } = miss;

      // Find the one segment that fully contains this misspelling range
      const seg = segments.find(
        (s) => !s.wrapped && s.start <= mStart && s.end >= mEnd
      );
      if (!seg) continue; // spans a node boundary or already wrapped — skip safely

      const localStart = mStart - seg.start;
      const localEnd   = mEnd   - seg.start;

      // Sanity check: does the node actually contain the expected text?
      const nodeText = seg.node.textContent;
      const slice    = nodeText.slice(localStart, localEnd);
      // Allow nikud-stripped matches (API strips nikud, DOM may have it)
      const sliceClean = slice.replace(/[\u0591-\u05C7]/g, '');
      if (slice !== word && sliceClean !== word) continue;

      wrapTextNodeRange(seg.node, localStart, localEnd, word, suggestions || []);

      // After wrapping, seg.node holds only the suffix text (or was removed).
      // Advance seg.start so lower-offset segments still resolve correctly.
      const suffixLen = nodeText.length - localEnd;
      seg.start = suffixLen > 0 ? mEnd : seg.end;
    }
  }

  // ─── Suggestion popover ────────────────────────────────────────────────────

  /**
   * Floating suggestion popover rendered in the HOST document so it can
   * overflow the editor iframe boundaries.
   *
   * Built 100% with createElement/textContent — no innerHTML anywhere.
   */
  const Popover = {
    _el:          null,
    _currentSpan: null,

    _build() {
      if (this._el) return;
      const el = document.createElement('div');
      el.id = POPOVER_ID;
      Object.assign(el.style, {
        position:     'fixed',
        zIndex:       '2147483647',
        background:   '#ffffff',
        border:       '1px solid #d1d5db',
        borderRadius: '8px',
        boxShadow:    '0 4px 20px rgba(0,0,0,0.18)',
        minWidth:     '190px',
        maxWidth:     '300px',
        fontFamily:   'system-ui, -apple-system, Arial, sans-serif',
        fontSize:     '13px',
        padding:      '6px 0',
        display:      'none',
        direction:    'rtl',
        lineHeight:   '1.4',
      });
      document.body.appendChild(el);
      this._el = el;

      // Close when clicking anywhere outside the popover
      document.addEventListener('mousedown', (e) => {
        if (this._el && !this._el.contains(e.target)) this.hide();
      }, true);
    },

    /**
     * @param {HTMLSpanElement} span       - the clicked spell-check span
     * @param {object}          editor     - TinyMCE editor instance
     * @param {Function}        onReplace  - called after a word is replaced
     */
    show(span, editor, onReplace) {
      this._build();
      this.hide(); // close any existing popover first

      this._currentSpan = span;
      span.classList.add(SPAN_ACTIVE_CLS);

      const word        = span.dataset.word || span.textContent;
      let   suggestions = [];
      try { suggestions = JSON.parse(span.dataset.suggestions || '[]'); } catch (_) {}

      const el = this._el;
      // Clear previous content (safe: will be rebuilt below with createElement)
      while (el.firstChild) el.removeChild(el.firstChild);

      // ── Header: the misspelled word ──
      const header = document.createElement('div');
      Object.assign(header.style, {
        padding:      '7px 14px 5px',
        fontWeight:   '700',
        color:        '#b91c1c',
        borderBottom: '1px solid #f3f4f6',
        marginBottom: '4px',
        direction:    'rtl',
      });
      header.textContent = `⚠ ${word}`;
      el.appendChild(header);

      // Helper: add a clickable row
      const addRow = (label, onClick) => {
        const row = document.createElement('div');
        Object.assign(row.style, {
          padding:    '7px 14px',
          cursor:     'pointer',
          color:      '#111827',
          direction:  'rtl',
          whiteSpace: 'nowrap',
          overflow:   'hidden',
          textOverflow: 'ellipsis',
        });
        row.textContent = label;
        row.addEventListener('mouseover', () => (row.style.background = '#f9fafb'));
        row.addEventListener('mouseout',  () => (row.style.background = ''));
        // mousedown (not click) so focus doesn't leave the editor before action
        row.addEventListener('mousedown', (e) => { e.preventDefault(); onClick(); });
        el.appendChild(row);
        return row;
      };

      // ── Suggestions ──
      if (suggestions.length > 0) {
        const label = document.createElement('div');
        Object.assign(label.style, {
          padding:  '2px 14px 3px',
          fontSize: '11px',
          color:    '#6b7280',
          direction: 'rtl',
        });
        label.textContent = 'הצעות תיקון:';
        el.appendChild(label);

        suggestions.forEach((sug) => {
          addRow(`• ${sug}`, () => {
            this._replace(span, sug, editor);
            this.hide();
            if (typeof onReplace === 'function') onReplace();
          });
        });
      } else {
        const none = document.createElement('div');
        Object.assign(none.style, { padding: '5px 14px', color: '#9ca3af', direction: 'rtl' });
        none.textContent = '(אין הצעות)';
        el.appendChild(none);
      }

      // ── Divider ──
      const hr = document.createElement('div');
      Object.assign(hr.style, { margin: '5px 0', borderTop: '1px solid #f3f4f6' });
      el.appendChild(hr);

      // ── Actions ──
      addRow('התעלם', () => { this._ignore(span); this.hide(); });

      addRow('הוסף למילון', async () => {
        try {
          await Api.addToDictionary(word);
          this._removeWordHighlights(word, editor);
          this.hide();
          Notifier.show('המילה נוספה למילון בהצלחה ✓', 'success');
        } catch (err) {
          Notifier.show('שגיאה בהוספה למילון — בדוק שהשרת פועל', 'error');
        }
      });

      // ── Position ──
      this._position(span, editor);
    },

    /**
     * Position the popover below the span, staying within the viewport.
     * Works for both iframe mode and inline mode.
     */
    _position(span, editor) {
      const el = this._el;
      el.style.display = 'block';

      const vw = window.innerWidth;
      const vh = window.innerHeight;

      // The span lives inside the editor which may be an iframe.
      // span.getBoundingClientRect() returns coords relative to the
      // iframe's viewport. We need host-document viewport coords.
      const container = editor.getContainer && editor.getContainer();
      const iframeEl  = container ? container.querySelector('iframe') : null;
      const iframeOff = iframeEl
        ? iframeEl.getBoundingClientRect()
        : { left: 0, top: 0, bottom: 0 };

      const spanRect = span.getBoundingClientRect();

      // Convert iframe-relative → host-document-relative
      const spanLeft   = iframeOff.left + spanRect.left;
      const spanBottom = iframeOff.top  + spanRect.bottom;
      const spanTop    = iframeOff.top  + spanRect.top;

      const popW = el.offsetWidth;
      const popH = el.offsetHeight;

      let left = spanLeft;
      let top  = spanBottom + 6;

      // Flip above if below-fold
      if (top + popH > vh - 8) top = spanTop - popH - 6;
      // Clamp horizontal
      if (left + popW > vw - 8) left = vw - popW - 8;
      if (left < 8) left = 8;

      el.style.left = `${Math.round(left)}px`;
      el.style.top  = `${Math.round(top)}px`;
    },

    hide() {
      if (this._currentSpan) {
        this._currentSpan.classList.remove(SPAN_ACTIVE_CLS);
        this._currentSpan = null;
      }
      if (this._el) this._el.style.display = 'none';
    },

    _replace(span, replacement, editor) {
      // ownerDocument ensures we create the text node in the correct document
      const textNode = span.ownerDocument.createTextNode(replacement);
      editor.undoManager.transact(() => {
        span.parentNode.replaceChild(textNode, span);
        textNode.parentNode.normalize();
      });
    },

    _ignore(span) {
      const textNode = span.ownerDocument.createTextNode(span.textContent);
      span.parentNode.replaceChild(textNode, span);
      textNode.parentNode.normalize();
    },

    _removeWordHighlights(word, editor) {
      const body = editor.getBody();
      body.querySelectorAll(`.${SPAN_CLASS}`).forEach((s) => {
        if (s.dataset.word !== word) return;
        const t = s.ownerDocument.createTextNode(s.textContent);
        s.parentNode.replaceChild(t, s);
        t.parentNode.normalize();
      });
    },
  };

  // ─── Toast notifications ───────────────────────────────────────────────────

  const Notifier = {
    show(message, type = 'info') {
      const BG = { success: '#16a34a', error: '#dc2626', info: '#2563eb' };
      const el  = document.createElement('div');
      Object.assign(el.style, {
        position:     'fixed',
        bottom:       '24px',
        right:        '24px',
        background:   BG[type] || BG.info,
        color:        '#fff',
        padding:      '10px 18px',
        borderRadius: '6px',
        fontFamily:   'system-ui, Arial, sans-serif',
        fontSize:     '13px',
        fontWeight:   '500',
        zIndex:       '2147483647',
        boxShadow:    '0 4px 12px rgba(0,0,0,0.2)',
        direction:    'rtl',
        maxWidth:     '340px',
        pointerEvents: 'none',
      });
      el.textContent = message;
      document.body.appendChild(el);
      setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 3500);
    },
  };

  // ─── Plugin registration ───────────────────────────────────────────────────

  tinymce.PluginManager.add(PLUGIN_NAME, function (editor) {

    // ── STEP 1: Register options FIRST (required by TinyMCE v7 before any get()) ──
    editor.options.register('hebrewspellcheck_api_url', {
      processor: 'string',
      default:   'http://localhost:8000',
    });
    editor.options.register('hebrewspellcheck_language', {
      processor: 'string',
      default:   'he-IL',
    });
    editor.options.register('hebrewspellcheck_max_suggestions', {
      processor: 'number',
      default:   5,
    });

    // ── STEP 2: Read options (safe now that they're registered) ──
    const apiUrl   = editor.options.get('hebrewspellcheck_api_url');
    const language = editor.options.get('hebrewspellcheck_language');
    const maxSug   = editor.options.get('hebrewspellcheck_max_suggestions');

    Api.init(apiUrl, language);

    // ── Inject spell-check CSS into the editor content document ──
    editor.on('init', () => {
      const doc = editor.getDoc();
      if (doc && !doc.getElementById(STYLE_ID)) {
        const style      = doc.createElement('style');
        style.id         = STYLE_ID;
        style.textContent = EDITOR_CSS;
        doc.head.appendChild(style);
      }
    });

    // ── Click handler: open popover when a misspelled word is clicked ──
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

    // ── Close popover when editor loses focus ──
    editor.on('blur', () => Popover.hide());

    // ── Core: run the spell check ──
    async function runSpellCheck(showProgress = true) {
      Popover.hide();

      const body = editor.getBody();
      if (!body) return;

      // Clear existing highlights first
      editor.undoManager.transact(() => clearHighlights(body));

      // Extract plain text + segment map AFTER clearing highlights
      const { plainText, segments } = extractTextSegments(body);

      if (!plainText.trim()) {
        Notifier.show('אין תוכן לבדיקת איות', 'info');
        return;
      }

      if (showProgress) editor.setProgressState(true);

      try {
        const result = await Api.checkText(plainText, maxSug);

        editor.undoManager.transact(() => {
          applyHighlights(segments, result.misspellings || []);
        });

        const count = result.total || 0;
        if (count === 0) {
          Notifier.show('לא נמצאו שגיאות איות ✓', 'success');
        } else {
          Notifier.show(
            `נמצאו ${count} שגיאות איות — לחץ על מילה מסומנת לתיקון`,
            'info'
          );
        }
      } catch (err) {
        console.error('[HebrewSpellCheck]', err);
        const msg = err.name === 'TimeoutError'
          ? 'שרת בדיקת האיות לא הגיב בזמן — אנא נסה שוב'
          : 'שגיאה בחיבור לשרת בדיקת האיות — בדוק שהשרת פועל';
        Notifier.show(msg, 'error');
      } finally {
        if (showProgress) editor.setProgressState(false);
      }
    }

    function clearAllHighlights() {
      Popover.hide();
      editor.undoManager.transact(() => clearHighlights(editor.getBody()));
      Notifier.show('סימוני האיות נוקו', 'info');
    }

    // ── Toolbar buttons ──

    editor.ui.registry.addButton('hebrewspellcheck', {
      text:    'בדיקת איות',
      tooltip: 'הפעל בדיקת איות בעברית',
      onAction: () => runSpellCheck(true),
    });

    editor.ui.registry.addButton('hebrewspellcheck_clear', {
      text:    'נקה סימונים',
      tooltip: 'הסר את כל סימוני האיות',
      onAction: clearAllHighlights,
    });

    // ── Menu items ──

    editor.ui.registry.addMenuItem('hebrewspellcheck', {
      text:    'בדיקת איות בעברית',
      onAction: () => runSpellCheck(true),
    });

    editor.ui.registry.addMenuItem('hebrewspellcheck_clear', {
      text:    'נקה סימוני איות',
      onAction: clearAllHighlights,
    });

    // ── Context menu on right-click of a misspelled word ──
    editor.ui.registry.addContextMenu('hebrewspellcheck', {
      update: (element) =>
        element.classList && element.classList.contains(SPAN_CLASS)
          ? 'hebrewspellcheck | hebrewspellcheck_clear'
          : '',
    });

    // ── Plugin metadata ──
    return {
      getMetadata: () => ({
        name: 'Hebrew Spell Checker',
        url:  'https://github.com/your-org/TinyMceSpellChecker',
      }),
    };
  });

})();
