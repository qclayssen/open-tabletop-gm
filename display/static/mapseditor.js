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

  const el = {
    board: document.getElementById('me-board'),
    palette: document.getElementById('me-palette'),
    name: document.getElementById('me-map-name'),
    status: document.getElementById('me-status'),
    undo: document.getElementById('me-undo'),
    clear: document.getElementById('me-clear'),
    save: document.getElementById('me-save'),
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
    if (state.image) {
      svg('image', { x: 0, y: 0, width: W * C, height: H * C, preserveAspectRatio: 'none',
                     href: '/maps/' + state.image, class: 'me-art' }, s);
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
    for (const l of state.labels || []) {
      const t = svg('text', { x: l.x * C + 4, y: l.y * C + 13, class: 'me-lbl' }, s);
      t.textContent = l.text;
    }
    svg('g', { class: 'me-preview', id: 'me-preview' }, s);

    s.addEventListener('pointerdown', onDown);
    s.addEventListener('pointermove', onMove);
    s.addEventListener('pointerup', onUp);
    s.addEventListener('pointercancel', cancel);
    s.addEventListener('pointerleave', () => { if (drag) preview(drag); });
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
      b.innerHTML = `<i style="background:var(--tx-${p.color})"></i>` +
        `<span><span class="me-name">${p.type}</span><br>` +
        `<span class="me-meta">${p.move} · ${p.sight}${p.cover ? ' · ' + p.cover : ''}</span></span>`;
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
        (out.backup ? `, previous file kept as ${out.backup}` : '') + '.', 'good');
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
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') cancel(); });
  window.addEventListener('resize', draw);

  buildPalette();
  draw();
  refreshButtons();
  if (state.has_features) {
    say('This map already has terrain. Saving merges your strokes into it; ' +
        'the file you have now is kept as a .bak.', null);
  }
})();
