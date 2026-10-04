/* mapseditor.js: click-to-paint terrain for a battle map.
 *
 * The engine owns the rules. This page paints squares and sends rectangles; the
 * server replays them onto the map's cells and re-derives features[] as a cover
 * of disjoint rectangles (scripts/tactics/mapeditor.py), so painting over
 * terrain replaces it rather than stacking on it. Nothing here merges anything:
 * if this file disagreed with the server about merge order the GM would see
 * their strokes land in the wrong place, which is why the preview is a local
 * cell matrix and the save is the server's answer, not this page's.
 *
 * One map, one terrain type at a time, click-drag for a rectangle. That is the
 * whole tool. No build step: the project has none.
 */
(function () {
  'use strict';

  const C = 32;                                   // SVG units per square, as tactics.js
  const SVGNS = 'http://www.w3.org/2000/svg';
  const state = window.MAP_EDITOR;
  if (!state) return;

  // cells[y][x] is the terrain a GM has placed; strokes is the ordered list of
  // rectangles they drew that produced it. Undo pops a stroke and rebuilds
  // cells from the ones before it, so there is no second copy of the merge.
  let cells = state.cells.map(r => r.slice());
  let strokes = [];
  let type = null;
  let drag = null;
  // The alignment the GM is testing, or null when the board is showing what is
  // saved. Held apart from `state.alignment` on purpose: typing in the box must
  // not look like a save, and `adopt()` replaces the saved one wholesale after
  // the server answers.
  let trial = null;

  const el = {
    board: document.getElementById('me-board'),
    palette: document.getElementById('me-palette'),
    name: document.getElementById('me-map-name'),
    status: document.getElementById('me-status'),
    undo: document.getElementById('me-undo'),
    clear: document.getElementById('me-clear'),
    save: document.getElementById('me-save'),
    cellPx: document.getElementById('me-cell-px'),
    offsetX: document.getElementById('me-offset-x'),
    offsetY: document.getElementById('me-offset-y'),
    alignRead: document.getElementById('me-align-read'),
    alignSave: document.getElementById('me-align-save'),
  };

  function svg(tag, attrs, parent) {
    const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(n);
    return n;
  }

  function say(msg, kind) {
    el.status.textContent = msg || '';
    el.status.className = 'me-status' + (kind ? ' me-' + kind : '');
  }

  function fillFor(name) {
    // A map-specific type paints in the built-in colour its terrain block names,
    // which is the same fallback tactics.js uses (meta.colors).
    const hit = state.palette.find(p => p.type === name);
    return 'var(--tx-' + ((hit && hit.color) || 'floor') + ')';
  }

  // Where the map's artwork goes. A deliberate mirror of tactics.js `artAttrs`,
  // not a copy of the idea: k = cell / cell_px on both axes, positioned at
  // -offset * k, cropped to the board by the caller's viewBox. Both files need
  // it and neither can import the other, so the pair is pinned by
  // tests/test_mapseditor_alignment.py, which checks this file's arithmetic
  // against the server's own `maps.art_geometry` on the same inputs -- so the two
  // cannot drift into agreeing on the wrong pitch.
  //
  // `align` overrides what is drawn, so the same function draws the *saved*
  // alignment while a GM is testing another one. That is the whole live overlay:
  // one function, two inputs, so the preview and the saved picture are drawn by
  // the same line of code.
  function artAttrs(st, W, H, cell, align) {
    const legacy = { x: 0, y: 0, width: W * cell, height: H * cell,
                     preserveAspectRatio: 'none' };
    const a = align || st.alignment || {};
    const size = a.image_px;
    if (!st.image || !size || size.length !== 2 || !(a.cell_px > 0)) return legacy;
    const k = cell / a.cell_px;
    return { x: -(a.offset_x || 0) * k, y: -(a.offset_y || 0) * k,
             width: size[0] * k, height: size[1] * k };
  }

  // ── the board ──
  function draw() {
    const W = state.width, H = state.height;
    const box = el.board.getBoundingClientRect();
    // Fit the larger dimension to whichever axis is tighter, so a wide map uses
    // the width it has and a tall one uses the height. A GM aiming at a square
    // needs the square to be as big as the page allows.
    const byWidth = Math.floor((box.width - 18) / W);
    const byHeight = Math.floor((box.height - 18) / H);
    const cell = Math.max(10, Math.min(64, byWidth, byHeight));
    const s = svg('svg', { viewBox: `0 0 ${W * C} ${H * C}`, width: W * cell, height: H * cell,
                           role: 'group', 'aria-label': `${state.name}, ${W} by ${H} squares` });
    el.board.innerHTML = '';
    el.board.appendChild(s);

    // Artwork under the terrain, the same layer order renderBoard() uses, so
    // the GM is aiming at what the players will actually see.
    //
    // Drawn through the same arithmetic as tactics.js `artAttrs`: when the map
    // records a picture size, the art scales by C / cell_px on both axes and sits
    // at -offset * k, clipped to the board. Before #143 the editor always
    // stretched, which meant a GM lining a map up in this page was lining it up
    // against a different picture than the one the players would see -- so the
    // alignment could be saved here and land wrong there.
    if (state.image) {
      svg('image', Object.assign(
        { href: '/maps/' + state.image, class: 'me-art' },
        artAttrs(state, W, H, C, trial)), s);
    }
    const terrain = svg('g', { class: 'me-terrain' }, s);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      svg('rect', { x: x * C, y: y * C, width: C, height: C,
                    style: 'fill:' + fillFor(cells[y][x]), 'data-t': cells[y][x] }, terrain);
    }
    const grid = svg('g', { class: 'me-grid' }, s);
    for (let i = 0; i <= W; i++) svg('line', { x1: i * C, y1: 0, x2: i * C, y2: H * C }, grid);
    for (let j = 0; j <= H; j++) svg('line', { x1: 0, y1: j * C, x2: W * C, y2: j * C }, grid);
    for (const z of state.zones || []) {
      svg('line', { x1: z * C, y1: 0, x2: z * C, y2: H * C, class: 'me-zone' }, s);
    }
    // Map labels, drawn the way tactics.js draws them: anchored at the centre of
    // the region they name (maps.py publishes that centre and the region's w),
    // and shortened to the region's own width. Same clamp, same constants, and
    // the same reason -- a GM editing a map is aiming at the squares the players
    // will see, so a label that lies about where its region starts is worse in
    // the editor than on the display.
    for (const l of state.labels || []) {
      const t = svg('text', { x: (l.w ? l.x : l.x + 0.5) * C, y: (l.h ? l.y : l.y + 0.5) * C,
                              class: 'me-lbl' }, s);
      svg('title', {}, t).textContent = l.text;
      t.appendChild(document.createTextNode(l.text));
      if (l.w) t.setAttribute('data-w', l.w);
    }
    svg('g', { class: 'me-preview', id: 'me-preview' }, s);

    s.addEventListener('pointerdown', onDown);
    s.addEventListener('pointermove', onMove);
    s.addEventListener('pointerup', onUp);
    s.addEventListener('pointercancel', cancel);
    s.addEventListener('pointerleave', () => { if (drag) preview(drag); });
    // After the append and after the labels exist: fitLabels measures the DOM,
    // and getComputedTextLength throws for a detached element.
    fitLabels(s, W);
  }

  // 0.56 of an 11px sans, the average advance. Only reached when the DOM cannot
  // measure (no layout yet); a rough clamp beats the unclamped label.
  const LBL_EST_ADVANCE = 6.2;

  // Shorten every map label to the width of the feature region it names.
  //
  // This is tactics.js's fitLabels and labelBudget, kept here rather than shared.
  // The two files have no module system and no build step, and tactics.js's
  // helpers are inside its IIFE; a third file to export two functions would be a
  // larger change than the duplication, and the constants that must agree
  // (LBL_EST_ADVANCE, and the two budgets below) are asserted to be equal to
  // tactics.js's by tests/test_mapseditor.py.
  function fitLabels(s, W) {
    for (const t of s.querySelectorAll('.me-lbl')) {
      const full = (t.querySelector('title') || {}).textContent || '';
      const node = t.lastChild;
      if (!node) continue;
      const cx = parseFloat(t.getAttribute('x')) || 0;
      const cells = Math.max(1, Number(t.getAttribute('data-w')) || 1);
      const room = Math.max(4, Math.min(cx, W * C - cx) - 4);
      const budget = Math.min(Math.max(cells * C - 8, 4 * C - 8), room * 2);
      node.textContent = full;
      let live = true;
      try { t.getComputedTextLength(); } catch (e) { live = false; }
      const measure = live
        ? str => { node.textContent = str; return t.getComputedTextLength(); }
        : str => str.length * LBL_EST_ADVANCE;
      node.textContent = fitPrefix(full, budget, measure);
    }
  }

  // The longest prefix of `text` that fits `budget`, with an ellipsis when the
  // whole thing does not. Binary search, because a per-character loop would ask
  // the browser for a layout once per character per redraw, and the editor
  // redraws on every pointermove.
  function fitPrefix(text, budget, measure) {
    if (!text || !(budget > 0)) return '';
    if (measure(text) <= budget) return text;
    let lo = 0, hi = text.length;
    while (lo < hi) {
      const mid = (lo + hi + 1) >> 1;
      if (measure(text.slice(0, mid) + '…') <= budget) lo = mid; else hi = mid - 1;
    }
    return lo > 0 ? text.slice(0, lo) + '…' : '';
  }

  function cellAt(ev) {
    const s = el.board.querySelector('svg');
    if (!s) return null;
    const r = s.getBoundingClientRect();
    const x = Math.floor((ev.clientX - r.left) / r.width * state.width);
    const y = Math.floor((ev.clientY - r.top) / r.height * state.height);
    if (x < 0 || y < 0 || x >= state.width || y >= state.height) return null;
    return { x, y };
  }

  function onDown(ev) {
    if (!type || ev.button !== 0) return;
    const c = cellAt(ev);
    if (!c) return;
    ev.target.setPointerCapture(ev.pointerId);
    drag = { x0: c.x, y0: c.y, x1: c.x, y1: c.y };
    preview(drag);
  }

  function onMove(ev) {
    if (!drag) return;
    const c = cellAt(ev);
    if (!c) return;
    drag.x1 = c.x; drag.y1 = c.y;
    preview(drag);
  }

  function norm(d) {
    return {
      x: Math.min(d.x0, d.x1), y: Math.min(d.y0, d.y1),
      w: Math.abs(d.x1 - d.x0) + 1, h: Math.abs(d.y1 - d.y0) + 1,
    };
  }

  function preview(d) {
    const layer = document.getElementById('me-preview');
    if (!layer) return;
    layer.innerHTML = '';
    const r = norm(d);
    svg('rect', { x: r.x * C, y: r.y * C, width: r.w * C, height: r.h * C }, layer);
  }

  function cancel() {
    if (!drag) return;
    drag = null;
    const layer = document.getElementById('me-preview');
    if (layer) layer.innerHTML = '';
  }

  function onUp() {
    if (!drag) return;
    const r = Object.assign(norm(drag), { type });
    drag = null;
    strokes.push(r);
    paint(r);
    draw();
    say(`${strokes.length} stroke${strokes.length > 1 ? 's' : ''} not yet saved.`, null);
    refreshButtons();
  }

  // The local preview only. The server re-does this from the strokes; the two
  // agree because both paint rectangles in order over the same cells.
  function paint(r) {
    for (let y = r.y; y < r.y + r.h; y++) for (let x = r.x; x < r.x + r.w; x++) cells[y][x] = r.type;
  }

  function rebuild() {
    cells = state.cells.map(row => row.slice());
    for (const r of strokes) paint(r);
  }

  // ── the palette ──
  function buildPalette() {
    for (const p of state.palette) {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'me-swatch';
      b.setAttribute('role', 'radio');
      b.setAttribute('aria-checked', 'false');
      b.dataset.type = p.type;
      b.title = `${p.type}, ${p.move}, sight ${p.sight}${p.cover ? ', ' + p.cover : ''}`;
      const sw = document.createElement('i');
      sw.style.background = `var(--tx-${p.color})`;
      const name = document.createElement('span');
      name.className = 'me-name';
      name.textContent = p.type;
      const meta = document.createElement('span');
      meta.className = 'me-meta';
      meta.textContent = `${p.move} · ${p.sight}${p.cover ? ' · ' + p.cover : ''}`;
      const label = document.createElement('span');
      label.append(name, document.createElement('br'), meta);
      b.append(sw, label);
      b.addEventListener('click', () => {
        type = p.type;
        for (const other of el.palette.children) {
          other.setAttribute('aria-checked', String(other.dataset.type === type));
        }
        say(`Painting ${type}.`, null);
      });
      el.palette.appendChild(b);
    }
    if (state.palette.length) el.palette.firstElementChild.click();
  }

  function refreshButtons() {
    el.undo.disabled = !strokes.length;
    el.clear.disabled = !strokes.length;
    el.save.disabled = !strokes.length;
  }

  // ── saving ──
  async function save(confirm) {
    el.save.disabled = true;
    say('Saving…', null);
    let res;
    try {
      res = await fetch(`/maps/${encodeURIComponent(state.slug)}/features`, {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' },
                               token() ? { 'X-DND-Token': token() } : {}),
        body: JSON.stringify({ strokes, confirm: !!confirm }),
      });
    } catch (e) {
      say('The display app did not answer. Is it still running?', 'bad');
      refreshButtons();
      return;
    }
    const out = await res.json().catch(() => ({}));
    if (!res.ok) {
      // 409 is the save gate: the map already has terrain, so the server wants
      // the GM to say yes on purpose. Ask once and retry, never silently.
      if (res.status === 409 && !confirm) {
        if (window.confirm(out.error + '\n\nSave anyway?')) return save(true);
        say(out.error || 'Not saved.', 'bad');
        refreshButtons();
        return;
      }
      say(out.error || `Save failed (HTTP ${res.status}).`, 'bad');
      refreshButtons();
      return;
    }
    adopt(out.state);
    say(`Saved ${out.features.length} rectangle${out.features.length === 1 ? '' : 's'}` +
        (out.backup ? `, original kept as ${out.backup}` : '') + '.', 'good');
    refreshButtons();
  }

  function adopt(next) {
    // The server's answer replaces the local model wholesale, so the editor can
    // never drift from what the file says, including the rectangle count, which
    // is the visible proof that strokes merged rather than piled up.
    strokes = [];
    for (const k of ['cells', 'labels', 'zones', 'image', 'has_features', 'width', 'height']) {
      if (next[k] !== undefined) state[k] = next[k];
    }
    rebuild();
    draw();
  }

  function token() {
    const m = document.querySelector('meta[name="dnd-token"]');
    return m ? m.content : '';
  }

  // ── grid alignment ──
  //
  // Three numbers, and the only reason this box exists: a map's artwork and its
  // 5 ft squares have to agree, and nothing else here can work that out. The
  // picture is a JPEG; the browser is not going to find its pitch in it.
  //
  // The overlay is *local arithmetic only*, never a save. It draws the artwork at
  // the number being typed so a GM can see the grid sit on the squares; nothing
  // reaches the file until Save, which goes to the server's route and comes back
  // with the map's own answer. Same contract as the terrain painting above: the
  // preview is this page's guess and the file is the server's.
  function readAlignment() {
    return { cell_px: Number(el.cellPx.value), offset_x: Number(el.offsetX.value),
             offset_y: Number(el.offsetY.value), image_px: (state.alignment || {}).image_px || [] };
  }

  function showAlignment() {
    const a = readAlignment();
    // Cleared first, so a refusal cannot leave a stale red on a sentence that has
    // since become an acceptance.
    el.alignRead.removeAttribute('data-bad');
    const size = (state.alignment || {}).image_px;
    if (!(a.cell_px > 0)) {
      el.alignRead.textContent = 'Pixels per square must be greater than zero.';
      el.alignRead.setAttribute('data-bad', '1');
      el.alignSave.disabled = true;
      return;
    }
    if (!state.image) {
      el.alignRead.textContent = 'This map has no artwork yet. The numbers are kept for when it has some.';
      el.alignSave.disabled = false;
      return;
    }
    if (!size || size.length !== 2) {
      el.alignRead.textContent = 'This map has artwork but no recorded picture size, so there is nothing to line up yet. Use scripts/art_attach.py.';
      el.alignSave.disabled = false;
      return;
    }
    // The same arithmetic the server will do, so the readout and the refusal
    // cannot disagree. scripts/art_attach.py's check_fit is the authority and
    // will have the last word on save.
    const ox = a.offset_x || 0, oy = a.offset_y || 0;
    const cellsW = Math.floor((size[0] - ox) / a.cell_px);
    const cellsH = Math.floor((size[1] - oy) / a.cell_px);
    const leftX = (size[0] - ox) - cellsW * a.cell_px;
    const leftY = (size[1] - oy) - cellsH * a.cell_px;
    const exact = leftX === 0 && leftY === 0;
    const parts = [`${cellsW}x${cellsH} squares from ${size[0]}x${size[1]}px`];
    parts.push(exact
      ? 'exactly, nothing cropped'
      : `${leftX}px cropped right, ${leftY}px cropped down`);
    if (!exact) el.alignRead.setAttribute('data-bad', '1');
    const matches = exact && cellsW === state.width && cellsH === state.height;
    parts.push(matches
      ? '\u2014 matches this board'
      : `\u2014 this board is ${state.width}x${state.height}, so the save will be refused`);
    el.alignRead.textContent = parts.join(' ');
    if (!matches) el.alignRead.setAttribute('data-bad', '1');
    // Save is offered only when it will be accepted. Refusing here as well as on
    // the server is not distrust of the server; it is not making the GM learn a
    // failure by pressing a button.
    el.alignSave.disabled = !matches;
  }

  function nudge(field, delta) {
    const input = el[field];
    const now = Number(input.value) || 0;
    input.value = String(now + delta);
    trial = readAlignment();
    showAlignment();
    draw();
  }

  async function saveAlignment() {
    el.alignSave.disabled = true;
    say('Saving alignment…', null);
    let res;
    try {
      res = await fetch(`/maps/${encodeURIComponent(state.slug)}/grid`, {
        method: 'POST',
        headers: Object.assign({ 'Content-Type': 'application/json' },
                               token() ? { 'X-DND-Token': token() } : {}),
        body: JSON.stringify(readAlignment()),
      });
    } catch (e) {
      say('The display app did not answer. Is it still running?', 'bad');
      showAlignment();
      return;
    }
    const out = await res.json().catch(() => ({}));
    if (!res.ok) {
      say(out.error || `Save failed (HTTP ${res.status}).`, 'bad');
      showAlignment();
      return;
    }
    // Adopt the server's answer wholesale, for the reason `adopt` below gives:
    // this page can then never disagree with the file about what is recorded.
    state.alignment = out.alignment;
    trial = null;
    fillAlignment();
    draw();
    showAlignment();
    say(`Saved alignment${out.backup ? `, original kept as ${out.backup}` : ''}. ` +
        'Terrain untouched.', 'good');
  }

  function fillAlignment() {
    const a = state.alignment || {};
    el.cellPx.value = a.cell_px || '';
    el.offsetX.value = a.offset_x || 0;
    el.offsetY.value = a.offset_y || 0;
  }

  // ── wiring ──
  el.name.textContent = `${state.name}, ${state.width}×${state.height}` +
    (state.image ? ' (with artwork)' : '');
  el.undo.addEventListener('click', () => {
    strokes.pop();
    rebuild();
    draw();
    refreshButtons();
    say(`${strokes.length} stroke${strokes.length === 1 ? '' : 's'} not yet saved.`, null);
  });
  el.clear.addEventListener('click', () => {
    strokes = [];
    rebuild();
    draw();
    refreshButtons();
    say('Strokes cleared. The file is untouched.', null);
  });
  el.save.addEventListener('click', () => save(false));

  // Live preview: typing redraws the board at the number being tried, and typing
  // is not a save. `draw()` reads `trial`, so this *is* the overlay.
  for (const field of ['cellPx', 'offsetX', 'offsetY']) {
    el[field].addEventListener('input', () => {
      trial = readAlignment();
      showAlignment();
      draw();
    });
  }

  // Arrow-key nudge, which is how a GM actually aligns a picture: by eye, a pixel
  // at a time, watching the grid settle onto the art. Shift is ten.
  //
  // Bound on the three inputs rather than the document, so an arrow key nudges
  // the field it is in and Esc still cancels a terrain stroke. Ctrl/Cmd is
  // deliberately not a modifier: the browser's own undo belongs there.
  const NUDGE = { ArrowLeft: ['offsetX', -1], ArrowRight: ['offsetX', 1],
                  ArrowUp: ['offsetY', -1], ArrowDown: ['offsetY', 1] };
  for (const field of ['cellPx', 'offsetX', 'offsetY']) {
    el[field].addEventListener('keydown', (e) => {
      const step = NUDGE[e.key];
      if (!step) return;
      e.preventDefault();                 // do not also move the caret
      const [target, delta] = step;
      nudge(target, e.shiftKey ? delta * 10 : delta);
    });
  }
  el.alignSave.addEventListener('click', saveAlignment);

  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cancel(); });
  window.addEventListener('resize', draw);

  buildPalette();
  fillAlignment();
  draw();
  showAlignment();
  refreshButtons();
  if (state.has_features) {
    say('This map already has terrain. Saving merges your strokes into it; ' +
        'the file you have now is kept as a .bak (only the first save).', null);
  }
})();
