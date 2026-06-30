// Add this at the top of your script
if (typeof AbortSignal.timeout !== 'function') {
  AbortSignal.timeout = function(ms) {
    const controller = new AbortController();
    setTimeout(() => {
      // Modern browsers use 'TimeoutError', but older polyfills 
      // often just trigger a standard abort.
      controller.abort(); 
    }, ms);
    return controller.signal;
  };
}

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
 *     toolbar: 'hebrewspellcheck hebrewspellcheck_clear hebrewspellcheck_toggle_auto | bold italic ...',
 *     extended_valid_elements: 'span[class|data-word|data-suggestions]',
 *     browser_spellcheck: false,
 *
 *     // Plugin-specific options:
 *     hebrewspellcheck_api_url:         'http://localhost:8000',
 *     hebrewspellcheck_language:        'he-IL',
 *     hebrewspellcheck_max_suggestions: 5,
 *     hebrewspellcheck_auto_check:      false,   // auto-check while typing
 *     hebrewspellcheck_logger:          window.console, // optional logger instance
 *   });
 *
 * Toolbar buttons available:
 * ─────────────────────────────────────────────────────────────
 *  • hebrewspellcheck          — run spell-check manually
 *  • hebrewspellcheck_clear    — remove all highlights
 *  • hebrewspellcheck_toggle_auto — toggle auto-check while typing
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
  const AUTO_CHECK_DEBOUNCE_MS = 1500;

  let pluginLogger = null;
  function safeLog(message, payload) {
    const fn = pluginLogger?.info || pluginLogger?.log;
    if (typeof fn !== 'function') return;
    try {
      fn.call(pluginLogger, `[HebrewSpellCheck] ${message}`, payload);
    } catch (_err) {
      // swallow logger failures to avoid breaking plugin
    }
  }

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
        signal: AbortSignal.timeout(15000),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Spell-check API error ${res.status}: ${body}`);
      }
      return res.json();
    },

    async suggestToDictionary(word, context) {
      const body = { word };
      if (context) body.context = context;
      const res = await fetch(`${this._baseUrl}/dictionary/suggest`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) {
        const text = await res.text().catch(() => '');
        throw new Error(`Dictionary API error ${res.status}: ${text}`);
      }
      return res.json();
    },
  };

  // ─── Text extraction ───────────────────────────────────────────────────────

  function extractTextSegments(editorBody) {
    const BLOCK_TAGS = new Set([
      'P', 'DIV', 'H1', 'H2', 'H3', 'H4', 'H5', 'H6',
      'LI', 'TD', 'TH', 'BLOCKQUOTE', 'PRE',
      'ARTICLE', 'SECTION', 'HEADER', 'FOOTER', 'FIGCAPTION', 'CAPTION',
    ]);

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

      const text = node.textContent;
      if (!text) continue;

      const parentEl = node.parentElement;

      if (prevWasBlock && plainText) {
        plainText  += ' ';
        prevWasBlock = false;
      }

      const start   = plainText.length;
      const end     = start + text.length;
      const wrapped = !!(parentEl && parentEl.classList.contains(SPAN_CLASS));

      segments.push({ node, start, end, wrapped });
      plainText += text;
    }

    return { plainText, segments };
  }

  const CONTEXT_BLOCK_SELECTOR = 'p, div, li, td, th, blockquote, h1, h2, h3, h4, h5, h6';
  const CONTEXT_WORD_COUNT = 5;

  function getWordContext(span) {
    const block = (span.closest && span.closest(CONTEXT_BLOCK_SELECTOR)) || span.parentElement;
    if (!block) return undefined;

    const doc = span.ownerDocument;
    const beforeRange = doc.createRange();
    beforeRange.setStart(block, 0);
    beforeRange.setEndBefore(span);
    const beforeWords = beforeRange.toString().trim().split(/\s+/).filter(Boolean).slice(-CONTEXT_WORD_COUNT);

    const afterRange = doc.createRange();
    afterRange.setStartAfter(span);
    afterRange.setEnd(block, block.childNodes.length);
    const afterWords = afterRange.toString().trim().split(/\s+/).filter(Boolean).slice(0, CONTEXT_WORD_COUNT);

    const word = span.textContent.trim();
    const text = [...beforeWords, word, ...afterWords].filter(Boolean).join(' ');
    return text || undefined;
  }

  // ─── Highlighting ──────────────────────────────────────────────────────────

  function clearHighlights(editorBody) {
    editorBody.querySelectorAll(`.${SPAN_CLASS}`).forEach((span) => {
      const parent = span.parentNode;
      if (!parent) return;
      while (span.firstChild) parent.insertBefore(span.firstChild, span);
      parent.removeChild(span);
      parent.normalize();
    });
  }

  function applyHighlights(editor, segments, misspellings) {
    const doc = editor.getDoc();
    // Use Type 2 bookmark. Since we aren't destroying the parent 
    // container, this is very stable.
    const bookmark = editor.selection.getBookmark();
    const segmentMap = new Map();
    for (const miss of misspellings) {
      const seg = segments.find(
        (s) => s.start <= miss.start && s.end >= miss.end
      );
      if (!seg) continue;
      if (!segmentMap.has(seg)) segmentMap.set(seg, []);
      segmentMap.get(seg).push(miss);
    }
  
    for (const [seg, misses] of segmentMap) {
      // CRITICAL: Sort from RIGHT to LEFT (descending).
      // This allows us to split the text node from the end to the start
      // without invalidating the offsets for the words at the beginning.
      misses.sort((a, b) => b.start - a.start);
  
      for (const miss of misses) {
        const localStart = miss.start - seg.start;
        const localEnd = miss.end - seg.start;
  
        // Ensure the node is still valid and has enough length
        if (!seg.node || localStart < 0 || localEnd > seg.node.textContent.length) continue;
  
        try {
          // 1. Split the node at the end of the word
          const afterNode = seg.node.splitText(localEnd);
          // 2. Split the node at the start of the word
          const wordNode = seg.node.splitText(localStart);
  
          // wordNode now contains exactly the misspelled word.
          // seg.node now contains the text BEFORE the word.
  
          // 3. Create the highlight span
          const span = doc.createElement('span');
          span.className = SPAN_CLASS;
          span.dataset.word = miss.word;
          span.dataset.suggestions = JSON.stringify(miss.suggestions || []);
  
          // 4. Wrap the wordNode
          wordNode.parentNode.insertBefore(span, wordNode);
          span.appendChild(wordNode);
        } catch (e) {
          console.warn("Skipping highlight due to node shift:", e);
        }
      }
      seg.wrapped = true;
    }
  
    // 5. Finalize
    editor.nodeChanged();
    editor.selection.moveToBookmark(bookmark);
  }

  // ─── Suggestion popover ────────────────────────────────────────────────────

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

      document.addEventListener('mousedown', (e) => {
        if (this._el && !this._el.contains(e.target)) this.hide();
      }, true);
    },

    show(span, editor, onReplace, onIgnoreAll) {
      this._build();
      this.hide();

      this._currentSpan = span;
      span.classList.add(SPAN_ACTIVE_CLS);

      const word        = span.dataset.word || span.textContent;
      let   suggestions = [];
      try { suggestions = JSON.parse(span.dataset.suggestions || '[]'); } catch (_) {}

      const el = this._el;
      while (el.firstChild) el.removeChild(el.firstChild);

      // Header
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
        row.addEventListener('mousedown', (e) => { e.preventDefault(); onClick(); });
        el.appendChild(row);
        return row;
      };

      // Suggestions
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
            safeLog('click_replace_suggestion', { word, suggestion: sug });
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

      // Divider
      const hr = document.createElement('div');
      Object.assign(hr.style, { margin: '5px 0', borderTop: '1px solid #f3f4f6' });
      el.appendChild(hr);

      // Actions
      addRow('התעלם כאן', () => { this._ignore(span); this.hide(); safeLog('click_ignore_here', { word }); });

      addRow('התעלם בכל המסמך', () => {
        this._removeWordHighlights(word, editor);
        this.hide();
        safeLog('click_ignore_all', { word });
        if (typeof onIgnoreAll === 'function') onIgnoreAll(word);
      });

      addRow('הצע למילון', async () => {
        try {
          const context = getWordContext(span);
          await Api.suggestToDictionary(word, context);
          this._removeWordHighlights(word, editor);
          this.hide();
          safeLog('click_suggest_to_dictionary', { word });
          Notifier.show('ההצעה נשלחה למילון בהצלחה ✓', 'success');
        } catch (err) {
          Notifier.show('שגיאה בשליחת ההצעה — בדוק שהשרת פועל', 'error');
        }
      });

      this._position(span, editor);
    },

    _position(span, editor) {
      const el = this._el;
      el.style.display = 'block';

      const vw = window.innerWidth;
      const vh = window.innerHeight;

      const container = editor.getContainer && editor.getContainer();
      const iframeEl  = container ? container.querySelector('iframe') : null;
      const iframeOff = iframeEl
        ? iframeEl.getBoundingClientRect()
        : { left: 0, top: 0, bottom: 0 };

      const spanRect = span.getBoundingClientRect();

      const spanLeft   = iframeOff.left + spanRect.left;
      const spanBottom = iframeOff.top  + spanRect.bottom;
      const spanTop    = iframeOff.top  + spanRect.top;

      const popW = el.offsetWidth;
      const popH = el.offsetHeight;

      let left = spanLeft;
      let top  = spanBottom + 6;

      if (top + popH > vh - 8) top = spanTop - popH - 6;
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
        position:      'fixed',
        bottom:        '24px',
        right:         '24px',
        background:    BG[type] || BG.info,
        color:         '#fff',
        padding:       '10px 18px',
        borderRadius:  '6px',
        fontFamily:    'system-ui, Arial, sans-serif',
        fontSize:      '13px',
        fontWeight:    '500',
        zIndex:        '2147483647',
        boxShadow:     '0 4px 12px rgba(0,0,0,0.2)',
        direction:     'rtl',
        maxWidth:      '340px',
        pointerEvents: 'none',
      });
      el.textContent = message;
      document.body.appendChild(el);
      setTimeout(() => { if (el.parentNode) el.parentNode.removeChild(el); }, 3500);
    },
  };

  // ─── SVG icon definitions ──────────────────────────────────────────────────
  // Registered once per editor; referenced by name in button/toggleButton defs.

  const ICONS = {
    // Spell-check: "A" with squiggle underline + checkmark (Material spellcheck)
    'hsc-check':
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">' +
      '<path fill="currentColor" d="M12.45 16h2.09L10 5H8L3.46 16h2.09l1.12-3h4.64zm' +
      '-5.02-5 1.57-4.19L10.57 11zm11.13 3-1.41-1.41L15 16.17l-.88-.88-1.41 1.41L15 19z"/>' +
      '</svg>',

    // Clear highlights: eraser
    'hsc-clear':
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">' +
      '<path fill="currentColor" d="M15.14 3c-.51 0-1.02.2-1.41.59L2.59 14.73c-.78.77-.78 2.04 0 ' +
      '2.83L5.03 20H20v-2h-8.36l9.77-9.76c.78-.79.78-2.05 0-2.83l-4.86-4.86c-.4-.39-.9-.55-1.41' +
      '-.55zm-5.64 13L4 10.5l8-8L17.5 8l-8 8z"/>' +
      '</svg>',

    // Auto spell-check toggle: lightning bolt
    'hsc-auto':
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">' +
      '<path fill="currentColor" d="M7 2v11h3v9l7-12h-4l4-8z"/>' +
      '</svg>',
  };

  // ─── Plugin registration ───────────────────────────────────────────────────

  tinymce.PluginManager.add(PLUGIN_NAME, function (editor) {

    // ── STEP 1: Register all options before any get() ──
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
    editor.options.register('hebrewspellcheck_auto_check', {
      processor: 'boolean',
      default:   false,
    });
    editor.options.register('hebrewspellcheck_logger', {
      processor: 'object',
      default:   {},
    });

    // ── STEP 2: Read options ──
    const apiUrl   = editor.options.get('hebrewspellcheck_api_url');
    const language = editor.options.get('hebrewspellcheck_language');
    const maxSug   = editor.options.get('hebrewspellcheck_max_suggestions');
    let   autoCheckEnabled = editor.options.get('hebrewspellcheck_auto_check');
    pluginLogger = editor.options.get('hebrewspellcheck_logger');

    // Words ignored for the lifetime of this editor session (not persisted)
    const sessionIgnored = new Set();

    Api.init(apiUrl, language);

    // ── Register custom SVG icons ──
    Object.entries(ICONS).forEach(([name, svg]) => {
      editor.ui.registry.addIcon(name, svg);
    });

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
        const word = target.dataset.word || target.textContent;
        const suggestions = (() => {
          try { return JSON.parse(target.dataset.suggestions || '[]'); } catch (_err) { return []; }
        })();
        safeLog('click_misspelled_word', { word, suggestions });

        Popover.show(target, editor, () => runSpellCheck(false, true), (word) => {
          sessionIgnored.add(word);
          Notifier.show(`"${word}" יתעלם בכל המסמך לאורך הסשן`, 'info');
        });
      } else {
        Popover.hide();
      }
    });

    // ── Close popover when editor loses focus ──
    editor.on('blur', () => Popover.hide());

    // ── Auto spell-check while typing (debounced) ──
    let autoCheckTimer = null;
    editor.on('input', () => {
      if (!autoCheckEnabled) return;
      clearTimeout(autoCheckTimer);
      autoCheckTimer = setTimeout(() => runSpellCheck(false, true), AUTO_CHECK_DEBOUNCE_MS);
    });

    // ── Core: run the spell check ──
    // silent=true suppresses "found N errors" toasts (used for auto-check)
    async function runSpellCheck(showProgress = true, silent = false) {
      Popover.hide();

      const body = editor.getBody();
      if (!body) return;

      editor.undoManager.transact(() => clearHighlights(body));

      const { plainText, segments } = extractTextSegments(body);

      if (!plainText.trim()) {
        if (!silent) Notifier.show('אין תוכן לבדיקת איות', 'info');
        return;
      }

      if (showProgress) editor.setProgressState(true);

      try {
        const result = await Api.checkText(plainText, maxSug);

        // Filter out words the user chose to ignore for this session
        const misspellings = (result.misspellings || []).filter((m) => {
          const clean = m.word.replace(/[\u0591-\u05C7]/g, '');
          return !sessionIgnored.has(m.word) && !sessionIgnored.has(clean);
        });

        editor.undoManager.transact(() => {
          applyHighlights(editor, segments, misspellings);
        });

        if (!silent) {
          const count = misspellings.length;
          if (count === 0) {
            Notifier.show('לא נמצאו שגיאות איות ✓', 'success');
          } else {
            Notifier.show(
              `נמצאו ${count} שגיאות איות — לחץ על מילה מסומנת לתיקון`,
              'info'
            );
          }
        }
      } catch (err) {
        if (!silent) {
          console.error('[HebrewSpellCheck]', err);
          const msg = err.name === 'TimeoutError'
            ? 'שרת בדיקת האיות לא הגיב בזמן — אנא נסה שוב'
            : 'שגיאה בחיבור לשרת בדיקת האיות — בדוק שהשרת פועל';
          Notifier.show(msg, 'error');
        }
      } finally {
        if (showProgress) editor.setProgressState(false);
      }
    }

    function clearAllHighlights() {
      clearTimeout(autoCheckTimer);
      Popover.hide();
      editor.undoManager.transact(() => clearHighlights(editor.getBody()));
      Notifier.show('סימוני האיות נוקו', 'info');
    }

    // ── Toolbar buttons ──

    editor.ui.registry.addButton('hebrewspellcheck', {
      icon:    'hsc-check',
      tooltip: 'בדיקת איות בעברית',
      onAction: () => {
        safeLog('click_spellcheck_button', {});
        runSpellCheck(true);
      },
    });

    editor.ui.registry.addButton('hebrewspellcheck_clear', {
      icon:    'hsc-clear',
      tooltip: 'נקה סימוני איות',
      onAction: () => {
        safeLog('click_clear_button', {});
        clearAllHighlights();
      },
    });

    // Toggle button — stays "pressed" (highlighted) while auto-check is active
    editor.ui.registry.addToggleButton('hebrewspellcheck_toggle_auto', {
      icon:    'hsc-auto',
      tooltip: 'בדיקת איות אוטומטית תוך כדי הקלדה',
      onSetup: (api) => {
        api.setActive(autoCheckEnabled);
        return () => {};
      },
      onAction: (api) => {
        autoCheckEnabled = !autoCheckEnabled;
        api.setActive(autoCheckEnabled);
        safeLog('click_toggle_auto', { enabled: autoCheckEnabled });
        Notifier.show(
          autoCheckEnabled ? 'בדיקת איות אוטומטית הופעלה' : 'בדיקת איות אוטומטית כובתה',
          'info'
        );
        if (autoCheckEnabled) runSpellCheck(false, true);
      },
    });

    // ── Keyboard shortcuts ──
    // Alt+S  — run spell-check
    // Alt+Shift+C — clear all highlights
    editor.addShortcut('alt+s',       'בדיקת איות בעברית', () => runSpellCheck(true));
    editor.addShortcut('alt+shift+c', 'נקה סימוני איות',   clearAllHighlights);

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
