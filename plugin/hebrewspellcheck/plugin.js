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
 *     toolbar: 'hebrewspellcheck hebrewspellcheck_clear hebrewspellcheck_toggle_auto hebrewspellcheck_dictionary | bold italic ...',
 *     extended_valid_elements: 'span[class|data-word|data-suggestions]',
 *     browser_spellcheck: false,
 *
 *     // Plugin-specific options:
 *     hebrewspellcheck_api_url:         'http://localhost:8000',
 *     hebrewspellcheck_language:        'he-IL',
 *     hebrewspellcheck_max_suggestions: 5,
 *     hebrewspellcheck_auto_check:      false,   // auto-check while typing
 *   });
 *
 * Toolbar buttons available:
 * ─────────────────────────────────────────────────────────────
 *  • hebrewspellcheck          — run spell-check manually
 *  • hebrewspellcheck_clear    — remove all highlights
 *  • hebrewspellcheck_toggle_auto — toggle auto-check while typing
 *  • hebrewspellcheck_dictionary  — open org-dictionary manager
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

    async removeFromDictionary(word) {
      const res = await fetch(`${this._baseUrl}/dictionary/remove`, {
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

    async listWords() {
      const res = await fetch(`${this._baseUrl}/dictionary`, {
        signal: AbortSignal.timeout(5000),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Dictionary API error ${res.status}: ${body}`);
      }
      return res.json(); // { words: [...], count: N }
    },

    exportDictionary() {
      // Trigger browser download — server sends Content-Disposition: attachment
      const a = document.createElement('a');
      a.href     = `${this._baseUrl}/dictionary/export`;
      a.download = 'org_dictionary.csv';
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
    },

    async importDictionary(file) {
      const formData = new FormData();
      formData.append('file', file);
      const res = await fetch(`${this._baseUrl}/dictionary/import`, {
        method: 'POST',
        body:   formData,
        signal: AbortSignal.timeout(15000),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        throw new Error(`Import API error ${res.status}: ${body}`);
      }
      return res.json(); // { added, skipped, errors, total_words }
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

  function wrapTextNodeRange(textNode, localStart, localEnd, word, suggestions) {
    const doc    = textNode.ownerDocument;
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

  function applyHighlights(segments, misspellings) {
    const sorted = [...misspellings].sort((a, b) => b.start - a.start);

    for (const miss of sorted) {
      const { word, start: mStart, end: mEnd, suggestions } = miss;

      const seg = segments.find(
        (s) => !s.wrapped && s.start <= mStart && s.end >= mEnd
      );
      if (!seg) continue;

      const localStart = mStart - seg.start;
      const localEnd   = mEnd   - seg.start;

      const nodeText   = seg.node.textContent;
      const slice      = nodeText.slice(localStart, localEnd);
      const sliceClean = slice.replace(/[\u0591-\u05C7]/g, '');
      if (slice !== word && sliceClean !== word) continue;

      wrapTextNodeRange(seg.node, localStart, localEnd, word, suggestions || []);

      const suffixLen = nodeText.length - localEnd;
      seg.start = suffixLen > 0 ? mEnd : seg.end;
    }
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
      addRow('התעלם כאן', () => { this._ignore(span); this.hide(); });

      addRow('התעלם בכל המסמך', () => {
        this._removeWordHighlights(word, editor);
        this.hide();
        if (typeof onIgnoreAll === 'function') onIgnoreAll(word);
      });

      if (GrowthBook.canAddToDict()) {
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
      }

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

  // ─── Organisation Dictionary Manager ──────────────────────────────────────

  const DictionaryManager = {
    _overlay:  null,
    _listEl:   null,
    _addInput: null,

    _build() {
      if (this._overlay) return;

      // Backdrop
      const overlay = document.createElement('div');
      Object.assign(overlay.style, {
        position:   'fixed',
        inset:      '0',
        background: 'rgba(0,0,0,0.45)',
        zIndex:     '2147483646',
        display:    'none',
      });
      overlay.addEventListener('mousedown', (e) => {
        if (e.target === overlay) this.hide();
      });
      document.body.appendChild(overlay);
      this._overlay = overlay;

      // Dialog panel
      const el = document.createElement('div');
      Object.assign(el.style, {
        position:      'fixed',
        top:           '50%',
        left:          '50%',
        transform:     'translate(-50%, -50%)',
        background:    '#ffffff',
        border:        '1px solid #d1d5db',
        borderRadius:  '10px',
        boxShadow:     '0 8px 32px rgba(0,0,0,0.22)',
        width:         '440px',
        maxWidth:      '95vw',
        maxHeight:     '80vh',
        display:       'flex',
        flexDirection: 'column',
        fontFamily:    'system-ui, -apple-system, Arial, sans-serif',
        direction:     'rtl',
        zIndex:        '2147483647',
        fontSize:      '13px',
      });
      overlay.appendChild(el);

      // ── Header ──
      const header = document.createElement('div');
      Object.assign(header.style, {
        display:        'flex',
        alignItems:     'center',
        justifyContent: 'space-between',
        padding:        '14px 16px',
        borderBottom:   '1px solid #e5e7eb',
        fontWeight:     '700',
        fontSize:       '15px',
        flexShrink:     '0',
      });
      const titleEl = document.createElement('span');
      titleEl.textContent = 'ניהול מילון הארגון';
      const closeBtn = document.createElement('button');
      closeBtn.textContent = '×';
      Object.assign(closeBtn.style, {
        background: 'none',
        border:     'none',
        fontSize:   '22px',
        lineHeight: '1',
        cursor:     'pointer',
        color:      '#6b7280',
        padding:    '0 2px',
      });
      closeBtn.addEventListener('click', () => this.hide());
      header.appendChild(titleEl);
      header.appendChild(closeBtn);
      el.appendChild(header);

      // ── Add-word section (hidden when GrowthBook disables the feature) ──
      if (GrowthBook.canAddToDict()) {
        const addSection = document.createElement('div');
        Object.assign(addSection.style, {
          display:      'flex',
          gap:          '8px',
          padding:      '12px 16px',
          borderBottom: '1px solid #e5e7eb',
          flexShrink:   '0',
        });
        const input = document.createElement('input');
        input.type        = 'text';
        input.placeholder = 'הוסף מילה חדשה...';
        Object.assign(input.style, {
          flex:         '1',
          padding:      '7px 10px',
          border:       '1px solid #d1d5db',
          borderRadius: '5px',
          fontSize:     '13px',
          direction:    'rtl',
          outline:      'none',
        });
        input.addEventListener('focus', () => (input.style.borderColor = '#2563eb'));
        input.addEventListener('blur',  () => (input.style.borderColor = '#d1d5db'));
        this._addInput = input;

        const addBtn = document.createElement('button');
        addBtn.textContent = 'הוסף';
        Object.assign(addBtn.style, {
          padding:      '7px 16px',
          background:   '#2563eb',
          color:        '#fff',
          border:       'none',
          borderRadius: '5px',
          cursor:       'pointer',
          fontSize:     '13px',
          fontWeight:   '600',
          flexShrink:   '0',
        });
        addBtn.addEventListener('mouseover', () => (addBtn.style.background = '#1d4ed8'));
        addBtn.addEventListener('mouseout',  () => (addBtn.style.background = '#2563eb'));
        addBtn.addEventListener('click', () => this._addWord());
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._addWord(); });

        addSection.appendChild(input);
        addSection.appendChild(addBtn);
        el.appendChild(addSection);
      }

      // ── Word list ──
      const listContainer = document.createElement('div');
      Object.assign(listContainer.style, {
        overflowY:  'auto',
        flex:       '1',
        minHeight:  '120px',
      });
      this._listEl = listContainer;
      el.appendChild(listContainer);

      // ── Footer ──
      const footer = document.createElement('div');
      Object.assign(footer.style, {
        padding:        '10px 16px',
        borderTop:      '1px solid #e5e7eb',
        display:        'flex',
        justifyContent: 'space-between',
        alignItems:     'center',
        flexShrink:     '0',
        gap:            '8px',
      });

      // Left side: import / export
      const footerLeft = document.createElement('div');
      Object.assign(footerLeft.style, { display: 'flex', gap: '6px' });

      const _makeSecondaryBtn = (text) => {
        const btn = document.createElement('button');
        btn.textContent = text;
        Object.assign(btn.style, {
          padding:      '6px 12px',
          background:   '#f3f4f6',
          color:        '#374151',
          border:       '1px solid #d1d5db',
          borderRadius: '5px',
          cursor:       'pointer',
          fontSize:     '12px',
        });
        btn.addEventListener('mouseover', () => (btn.style.background = '#e5e7eb'));
        btn.addEventListener('mouseout',  () => (btn.style.background = '#f3f4f6'));
        return btn;
      };

      const exportBtn = _makeSecondaryBtn('ייצוא CSV ↓');
      exportBtn.title = 'הורד את המילון כקובץ CSV';
      exportBtn.addEventListener('click', () => {
        Api.exportDictionary();
        Notifier.show('מוריד קובץ CSV...', 'info');
      });

      // Hidden file input for import
      const fileInput = document.createElement('input');
      fileInput.type   = 'file';
      fileInput.accept = '.csv,.txt,text/plain,text/csv';
      Object.assign(fileInput.style, { display: 'none' });
      fileInput.addEventListener('change', async () => {
        const file = fileInput.files && fileInput.files[0];
        if (!file) return;
        fileInput.value = '';  // reset so same file can be re-imported
        try {
          const result = await Api.importDictionary(file);
          await this._refresh();
          Notifier.show(
            `ייבוא הושלם: ${result.added} מילים נוספו, ${result.skipped} כבר קיימות`,
            result.errors.length > 0 ? 'info' : 'success'
          );
        } catch (_err) {
          Notifier.show('שגיאה בייבוא הקובץ — בדוק שהשרת פועל', 'error');
        }
      });
      document.body.appendChild(fileInput);

      const importBtn = _makeSecondaryBtn('ייבוא CSV ↑');
      importBtn.title = 'טען מילים מקובץ CSV או טקסט';
      importBtn.addEventListener('click', () => fileInput.click());

      footerLeft.appendChild(importBtn);
      footerLeft.appendChild(exportBtn);
      footer.appendChild(footerLeft);

      // Right side: close
      const closeFooterBtn = document.createElement('button');
      closeFooterBtn.textContent = 'סגור';
      Object.assign(closeFooterBtn.style, {
        padding:      '7px 22px',
        background:   '#f3f4f6',
        color:        '#374151',
        border:       '1px solid #d1d5db',
        borderRadius: '5px',
        cursor:       'pointer',
        fontSize:     '13px',
      });
      closeFooterBtn.addEventListener('click', () => this.hide());
      footer.appendChild(closeFooterBtn);
      el.appendChild(footer);
    },

    async open() {
      this._build();
      this._overlay.style.display = 'block';
      if (this._addInput) {
        this._addInput.value = '';
        this._addInput.focus();
      }
      await this._refresh();
    },

    hide() {
      if (this._overlay) this._overlay.style.display = 'none';
    },

    async _refresh() {
      const listEl = this._listEl;
      while (listEl.firstChild) listEl.removeChild(listEl.firstChild);

      // Loading state
      const loading = document.createElement('div');
      Object.assign(loading.style, { padding: '24px', textAlign: 'center', color: '#9ca3af' });
      loading.textContent = 'טוען...';
      listEl.appendChild(loading);

      try {
        const data  = await Api.listWords();
        const words = data.words || [];
        while (listEl.firstChild) listEl.removeChild(listEl.firstChild);

        if (words.length === 0) {
          const empty = document.createElement('div');
          Object.assign(empty.style, {
            padding:   '32px 16px',
            textAlign: 'center',
            color:     '#9ca3af',
          });
          empty.textContent = 'המילון הארגוני ריק — הוסף מילים למעלה';
          listEl.appendChild(empty);
          return;
        }

        // Count label
        const countLabel = document.createElement('div');
        Object.assign(countLabel.style, {
          padding:    '8px 16px 4px',
          fontSize:   '11px',
          color:      '#6b7280',
          fontWeight: '500',
        });
        countLabel.textContent = `${words.length} מילים במילון`;
        listEl.appendChild(countLabel);

        words.forEach((word) => {
          const row = document.createElement('div');
          Object.assign(row.style, {
            display:        'flex',
            alignItems:     'center',
            justifyContent: 'space-between',
            padding:        '8px 16px',
            borderBottom:   '1px solid #f3f4f6',
          });
          row.addEventListener('mouseover', () => (row.style.background = '#f9fafb'));
          row.addEventListener('mouseout',  () => (row.style.background = ''));

          const wordText = document.createElement('span');
          wordText.textContent = word;
          Object.assign(wordText.style, { color: '#111827' });

          const delBtn = document.createElement('button');
          delBtn.textContent = 'מחק';
          Object.assign(delBtn.style, {
            padding:      '3px 10px',
            background:   '#fee2e2',
            color:        '#dc2626',
            border:       'none',
            borderRadius: '4px',
            cursor:       'pointer',
            fontSize:     '12px',
            fontWeight:   '500',
          });
          delBtn.addEventListener('mouseover', () => (delBtn.style.background = '#fecaca'));
          delBtn.addEventListener('mouseout',  () => (delBtn.style.background = '#fee2e2'));
          delBtn.addEventListener('click', () => this._removeWord(word));

          row.appendChild(wordText);
          row.appendChild(delBtn);
          listEl.appendChild(row);
        });

      } catch (_err) {
        while (listEl.firstChild) listEl.removeChild(listEl.firstChild);
        const errEl = document.createElement('div');
        Object.assign(errEl.style, {
          padding:   '24px 16px',
          textAlign: 'center',
          color:     '#dc2626',
        });
        errEl.textContent = 'שגיאה בטעינת המילון — בדוק שהשרת פועל';
        listEl.appendChild(errEl);
      }
    },

    async _addWord() {
      const word = this._addInput.value.trim();
      if (!word) return;
      try {
        await Api.addToDictionary(word);
        this._addInput.value = '';
        await this._refresh();
        Notifier.show(`"${word}" נוספה למילון ✓`, 'success');
      } catch (_err) {
        Notifier.show('שגיאה בהוספת המילה — בדוק שהשרת פועל', 'error');
      }
    },

    async _removeWord(word) {
      try {
        await Api.removeFromDictionary(word);
        await this._refresh();
        Notifier.show(`"${word}" הוסרה מהמילון`, 'info');
      } catch (_err) {
        Notifier.show('שגיאה בהסרת המילה', 'error');
      }
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

    // Dictionary manager: open book
    'hsc-dict':
      '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24">' +
      '<path fill="currentColor" d="M21 5c-1.11-.35-2.33-.5-3.5-.5-1.95 0-4.05.4-5.5 1.5' +
      '-1.45-1.1-3.55-1.5-5.5-1.5S2.45 4.9 1 6v14.65c0 .25.25.5.5.5.1 0 .15-.05.25-.05' +
      'C3.1 20.45 5.05 20 6.5 20c1.95 0 4.05.4 5.5 1.5 1.35-.85 3.8-1.5 5.5-1.5 1.65 0 ' +
      '3.35.3 4.75 1.05.1.05.15.05.25.05.25 0 .5-.25.5-.5V6c-.6-.45-1.25-.75-2-1zm0 13.5' +
      'c-1.1-.35-2.3-.5-3.5-.5-1.7 0-4.15.65-5.5 1.5V8c1.35-.85 3.8-1.5 5.5-1.5 1.2 0 ' +
      '2.4.15 3.5.5v11.5z"/>' +
      '</svg>',
  };

  // ─── GrowthBook feature-flag client ────────────────────────────────────────
  //
  // Queries the GrowthBook Features API to decide whether the "Add to
  // dictionary" action should be visible.  Defaults to *enabled* (fail-open)
  // if GrowthBook is not configured or the network call fails.
  //
  // Config options (passed via tinymce.init):
  //   hebrewspellcheck_growthbook_client_key  — GrowthBook SDK client key
  //   hebrewspellcheck_growthbook_feature_key — feature flag name to check
  //       (default: "hebrew-spellcheck-add-to-dictionary")
  //   hebrewspellcheck_growthbook_api_url     — base URL for the Features API
  //       (default: "https://cdn.growthbook.io/api/features")

  const GrowthBook = {
    _addToDictEnabled: true, // default: enabled (fail-open)

    /**
     * Fetch the feature flags from GrowthBook and cache whether
     * "add to dictionary" is enabled.  Safe to call without await;
     * the flag stays true (enabled) until the request resolves.
     *
     * @param {string} clientKey  - GrowthBook SDK client key
     * @param {string} featureKey - the feature flag name to check
     * @param {string} apiUrl     - base URL (without trailing slash)
     */
    async init(clientKey, featureKey, apiUrl) {
      if (!clientKey) return; // not configured — leave default (enabled)
      try {
        const url = `${apiUrl}/${encodeURIComponent(clientKey)}`;
        const res = await fetch(url, { signal: AbortSignal.timeout(5000) });
        if (!res.ok) return;
        const data = await res.json();
        const feature = (data.features || {})[featureKey];
        if (feature && typeof feature.defaultValue !== 'undefined') {
          this._addToDictEnabled = feature.defaultValue !== false;
        }
      } catch (_err) {
        // Network error or timeout — keep default (enabled / fail-open)
      }
    },

    /** Returns true when "add to dictionary" should be shown. */
    canAddToDict() {
      return this._addToDictEnabled;
    },
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
    editor.options.register('hebrewspellcheck_growthbook_client_key', {
      processor: 'string',
      default:   '',
    });
    editor.options.register('hebrewspellcheck_growthbook_feature_key', {
      processor: 'string',
      default:   'hebrew-spellcheck-add-to-dictionary',
    });
    editor.options.register('hebrewspellcheck_growthbook_api_url', {
      processor: 'string',
      default:   'https://cdn.growthbook.io/api/features',
    });

    // ── STEP 2: Read options ──
    const apiUrl   = editor.options.get('hebrewspellcheck_api_url');
    const language = editor.options.get('hebrewspellcheck_language');
    const maxSug   = editor.options.get('hebrewspellcheck_max_suggestions');
    let   autoCheckEnabled = editor.options.get('hebrewspellcheck_auto_check');

    // ── GrowthBook: fetch feature flags async (fail-open if unreachable) ──
    GrowthBook.init(
      editor.options.get('hebrewspellcheck_growthbook_client_key'),
      editor.options.get('hebrewspellcheck_growthbook_feature_key'),
      editor.options.get('hebrewspellcheck_growthbook_api_url'),
    ).catch(() => {});

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
          applyHighlights(segments, misspellings);
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
      onAction: () => runSpellCheck(true),
    });

    editor.ui.registry.addButton('hebrewspellcheck_clear', {
      icon:    'hsc-clear',
      tooltip: 'נקה סימוני איות',
      onAction: clearAllHighlights,
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
        Notifier.show(
          autoCheckEnabled ? 'בדיקת איות אוטומטית הופעלה' : 'בדיקת איות אוטומטית כובתה',
          'info'
        );
        if (autoCheckEnabled) runSpellCheck(false, true);
      },
    });

    // Dictionary manager button
    editor.ui.registry.addButton('hebrewspellcheck_dictionary', {
      icon:    'hsc-dict',
      tooltip: 'ניהול מילון הארגון',
      onAction: () => DictionaryManager.open(),
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

    editor.ui.registry.addMenuItem('hebrewspellcheck_dictionary', {
      text:    'ניהול מילון הארגון',
      onAction: () => DictionaryManager.open(),
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
