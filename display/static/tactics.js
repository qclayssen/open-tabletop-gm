/* tactics.js: the grid combat panel for the display companion.
 *
 * The engine owns the rules; this page only draws its snapshots and sends the
 * player's clicks. Every click goes to POST /combat/do, which runs the same
 * command the GM would run and queues the result for the GM to narrate.
 *
 * Snapshots arrive as the `combat` SSE event (see sync.snapshot in
 * scripts/tactics/sync.py). The panel appears when combat starts and hides
 * when it ends; with the display off nothing here runs.
 *
 * Modes (ui.mode): move, attack (weapon), cast (the spell list), aim (an area
 * spell following the pointer), spell (a single-target spell), darts (Magic
 * Missile), help and ready. Escape cancels any of them.
 */
(function () {
  'use strict';

  const C = 32;                                   // SVG units per 5 ft square
  const LEGEND = { '.': 'floor', '#': 'wall', ',': 'difficult', '~': 'water',
                   '^': 'hazard', 'o': 'feature', '_': 'void' };
  const BUILTIN = ['floor', 'wall', 'difficult', 'water', 'hazard', 'feature', 'void'];
  const SVGNS = 'http://www.w3.org/2000/svg';
  const MAX_DARTS = 3;
  const SPELL_MODES = ['cast', 'aim', 'spell', 'darts'];

  let snap = null, prev = null;
  const ui = { mode: null, kind: null, reach: null, targets: null, attack: null, preview: {},
               hover: null, armed: null, busy: false, lastMove: null, toastTimer: 0, hoverTimer: 0,
               spells: null, spell: null, singles: null, darts: null, dartsText: '',
               helpTarget: null, readyWhat: null, readyStep: null };
  const el = {};

  // ── helpers ──────────────────────────────────────────────────────────────
  const colLabel = x => { let s = ''; x += 1; while (x) { const r = (x - 1) % 26; s = String.fromCharCode(65 + r) + s; x = Math.floor((x - 1) / 26); } return s; };
  const label = (x, y) => colLabel(x) + (y + 1);
  const parseSq = s => { const m = /^([A-Z]+)(\d+)$/.exec(s || ''); if (!m) return null;
    let x = 0; for (const ch of m[1]) x = x * 26 + (ch.charCodeAt(0) - 64); return [x - 1, +m[2] - 1]; };
  const esc = s => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
  const svg = (tag, attrs, parent) => { const n = document.createElementNS(SVGNS, tag);
    for (const k in attrs) n.setAttribute(k, attrs[k]); if (parent) parent.appendChild(n); return n; };
  const tokenById = id => ((snap && snap.tokens) || []).find(t => t.id === id);
  const current = () => snap && tokenById(snap.current);
  const myTurn = () => { const t = current(); return !!(snap && snap.status === 'active' && t && t.controller === 'player' && !t.dead); };
  const reduced = () => window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches;
  const hostile = (a, b) => (a.side === 'enemy') !== (b.side === 'enemy');
  const adjacent = (a, b) => Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y)) <= 1;
  const living = () => ((snap && snap.tokens) || []).filter(t => !t.dead);
  const sqOf = t => label(t.x, t.y);

  function headers() {
    const h = {};
    try { Object.assign(h, typeof _authHeaders === 'function' ? _authHeaders() : {}); } catch (e) { /* no auth helper */ }
    h['Content-Type'] = 'application/json';
    return h;
  }

  async function call(cmd, args, extra) {
    try {
      const r = await fetch('/combat/do', { method: 'POST', headers: headers(),
        body: JSON.stringify(Object.assign({ cmd, args }, extra || {})) });
      if (r.status === 429) return { error: 'Too many actions at once. Try again in a moment.' };
      const body = await r.json().catch(() => null);
      return body || { error: 'The display could not reach the engine (HTTP ' + r.status + ').' };
    } catch (e) {
      return { error: 'The display could not reach the engine.' };
    }
  }

  // ── panel ────────────────────────────────────────────────────────────────
  function build() {
    if (el.panel) return;
    const p = document.createElement('section');
    p.id = 'tx-panel'; p.hidden = true; p.setAttribute('aria-label', 'Battle map');
    p.innerHTML =
      '<header class="tx-head"><div class="tx-title"><span id="tx-map"></span> <span class="tx-sub">Round <span id="tx-round">1</span></span></div>' +
      '<div id="tx-banner" role="status" aria-live="polite"></div>' +
      '<button class="tx-btn" id="tx-min" type="button" aria-expanded="true">Hide map</button></header>' +
      '<div id="tx-strip" class="tx-strip" aria-label="Initiative order"></div>' +
      '<div class="tx-body"><div id="tx-board" class="tx-board"></div>' +
      '<div class="tx-side"><div id="tx-info" class="tx-info" aria-live="polite"></div>' +
      '<div id="tx-actions" class="tx-actions" role="toolbar" aria-label="Actions"></div>' +
      '<div id="tx-prompt" class="tx-prompt" hidden></div>' +
      '<ol id="tx-log" class="tx-log" aria-label="Combat log"></ol></div></div>' +
      '<div id="tx-toast" class="tx-toast" role="status" hidden></div>';
    document.body.appendChild(p);
    for (const id of ['map', 'round', 'banner', 'min', 'strip', 'board', 'info', 'actions', 'prompt', 'log', 'toast'])
      el[id] = document.getElementById('tx-' + id);
    el.panel = p;
    el.min.addEventListener('click', () => {
      const min = document.body.classList.toggle('tx-min');
      el.min.textContent = min ? 'Show map' : 'Hide map';
      el.min.setAttribute('aria-expanded', String(!min));
    });
    document.addEventListener('keydown', e => { if (e.key === 'Escape' && ui.mode) { clearMode(); render(); } });
    const ts = document.getElementById('text-scroll');
    if (ts) new MutationObserver(syncSidebar).observe(ts, { attributes: true, attributeFilter: ['class'] });
    syncSidebar();
  }

  function syncSidebar() {
    const ts = document.getElementById('text-scroll');
    document.body.classList.toggle('tx-sidebar-hidden', !!(ts && ts.classList.contains('sidebar-hidden')));
  }

  function toast(text, kind) {
    el.toast.textContent = text; el.toast.hidden = false;
    el.toast.className = 'tx-toast' + (kind === 'error' ? ' tx-error' : '');
    clearTimeout(ui.toastTimer);
    ui.toastTimer = setTimeout(() => { el.toast.hidden = true; }, kind === 'error' ? 6000 : 4500);
  }

  function clearMode() {
    ui.mode = ui.kind = ui.reach = ui.targets = ui.attack = ui.hover = ui.armed = null;
    ui.spell = ui.singles = ui.darts = ui.helpTarget = ui.readyWhat = ui.readyStep = null;
    ui.dartsText = '';
    ui.preview = {};
    clearTimeout(ui.hoverTimer);
  }

  // ── snapshots ────────────────────────────────────────────────────────────
  function update(next) {
    build();
    if (!next || next.status !== 'active') {
      if (snap && snap.status === 'active') {
        el.banner.textContent = 'Combat over'; flash();
        setTimeout(() => { if (!snap || snap.status !== 'active') hide(); }, 3500);
      }
      prev = snap; snap = null;
      return;
    }
    prev = snap; snap = next;
    el.panel.hidden = false;
    document.body.classList.add('tx-on');
    if (!prev || prev.current !== snap.current) {
      clearMode(); ui.spells = null;
      const t = current();
      el.banner.textContent = t ? (t.controller === 'player' ? 'Your turn, ' + t.name : t.name + "'s turn") : '';
      flash();
    }
    render();
    animate();
    announceRolls();
  }

  function hide() { el.panel.hidden = true; document.body.classList.remove('tx-on', 'tx-min'); clearMode(); }
  function flash() { el.banner.classList.remove('tx-flash'); void el.banner.offsetWidth; el.banner.classList.add('tx-flash'); }

  function announceRolls() {
    const last = (snap.log || []).slice(-1)[0];
    const before = prev && (prev.log || []).slice(-1)[0];
    if (!last || !(last.rolls || []).length) return;
    if (before && before.text === last.text && before.round === last.round) return;
    toast(last.rolls.map(r =>
      `${r.label}: ${r.notation}${r.dice.length > 1 && r.advantage !== 'normal' ? ' [' + r.dice.join(', ') + ']' : ''} = ${r.total}` +
      (r.source !== 'engine' ? ` (${r.source})` : '')).join(' · '));
  }

  // ── rendering ────────────────────────────────────────────────────────────
  function render() {
    if (!snap) return;
    const meta = snap.meta || {};
    el.map.textContent = meta.name || (snap.grid && snap.grid.name) || 'Battle';
    el.round.textContent = snap.round;
    renderStrip(); renderBoard(); renderSide();
  }

  function tags(t) {
    // Conditions plus concentration, effects and a readied action, for chips and labels.
    const out = [...(t.conditions || [])];
    if (t.concentration) out.push('concentrating: ' + t.concentration);
    for (const e of t.effects || []) out.push(e);
    if (t.readied) out.push('readied: ' + t.readied);
    return out;
  }

  function renderStrip() {
    el.strip.innerHTML = '';
    for (const id of snap.order || []) {
      const t = tokenById(id); if (!t) continue;
      const pct = Math.max(0, Math.round(100 * t.hp / Math.max(1, t.max_hp)));
      const tg = tags(t);
      const c = document.createElement('div');
      c.className = 'tx-chip' + (id === snap.current ? ' tx-now' : '') + (t.dead ? ' tx-dead' : '');
      if (tg.length) c.title = tg.join(', ');
      c.innerHTML = `<span>${esc(t.name)}</span><span class="tx-hpbar" role="img" aria-label="${t.hp} of ${t.max_hp} HP"><i class="${pct <= 25 ? 'tx-low' : ''}" style="width:${pct}%"></i></span>` +
        `<span>${t.dead ? 'dead' : t.hp + '/' + t.max_hp + ' HP'}${tg.length ? ' · ' + esc(tg.join(', ')) : ''}</span>`;
      el.strip.appendChild(c);
    }
  }

  function terrainOf(ch) {
    const g = snap.grid || {};
    return (g.legend && g.legend[ch]) || LEGEND[ch] || 'floor';
  }
  function fillFor(name) {
    const base = BUILTIN.includes(name) ? name : (((snap.meta || {}).colors || {})[name] || 'floor');
    return 'var(--tx-' + base + ')';
  }

  function renderBoard() {
    const rows = (snap.grid && snap.grid.rows) || [];
    const H = rows.length, W = H ? rows[0].length : 0;
    ui.W = W; ui.H = H;
    const avail = Math.max(240, el.board.clientWidth - 4);
    const cell = Math.max(28, Math.min(40, Math.floor(avail / Math.max(1, W))));
    const s = svg('svg', { viewBox: `0 0 ${W * C} ${H * C}`, width: W * cell, height: H * cell,
                           role: 'group', 'aria-label': `Battle map, ${W} by ${H} squares` });
    if (ui.mode === 'aim') s.classList.add('tx-aiming');
    const terrain = svg('g', {}, s);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const name = terrainOf(rows[y][x]);
      svg('rect', { x: x * C, y: y * C, width: C, height: C, style: 'fill:' + fillFor(name) }, terrain);
      if (name === 'difficult') svg('path', { d: `M${x * C + 9},${y * C + 23} l6,-10 l6,10`, style: 'stroke:var(--tx-ink);stroke-opacity:.3;fill:none' }, terrain);
    }
    const grid = svg('g', { style: 'stroke:var(--tx-grid)' }, s);
    for (let i = 0; i <= W; i++) svg('line', { x1: i * C, y1: 0, x2: i * C, y2: H * C }, grid);
    for (let j = 0; j <= H; j++) svg('line', { x1: 0, y1: j * C, x2: W * C, y2: j * C }, grid);
    for (const z of (snap.meta && snap.meta.zones) || [])
      svg('line', { x1: z * C, y1: 0, x2: z * C, y2: H * C, style: 'stroke:var(--tx-brass);stroke-width:3;stroke-dasharray:8 6' }, s);
    for (const l of (snap.meta && snap.meta.labels) || []) {
      const t = svg('text', { x: l.x * C + 5, y: l.y * C + 14, class: 'tx-cell-lbl', style: 'stroke:var(--tx-paper)' }, s);
      t.textContent = l.text;
    }
    ui.overlay = svg('g', {}, s);
    ui.tokenLayer = svg('g', {}, s);
    ui.markLayer = svg('g', { 'aria-hidden': 'true' }, s);
    ui.floatLayer = svg('g', {}, s);
    ui.svg = s;
    for (const t of snap.tokens || []) drawToken(t);
    drawOverlay();
    s.addEventListener('pointermove', onHover);
    s.addEventListener('click', onBoardClick);
    s.addEventListener('pointerleave', () => {
      if ((ui.mode === 'move' || ui.mode === 'aim') && ui.armed !== ui.hover) { ui.hover = null; drawOverlay(); renderInfo(); }
    });
    el.board.innerHTML = ''; el.board.appendChild(s);
    floaters();
  }

  // What the current mode says about a token: a ring class, a badge and words for screen readers.
  function markFor(t) {
    const me = current();
    if (!me || t.dead) return null;
    if (ui.mode === 'attack') {
      const r = bestTarget(t.id);
      return r ? { cls: 'tx-target', badge: r.legal ? r.hit_percent + '%' : null,
                   say: r.legal ? `${r.hit_percent}% to hit` : '' } : null;
    }
    if (ui.mode === 'spell' && ui.singles && t.id in ui.singles) {
      const pv = ui.singles[t.id];
      if (!pv) return { cls: 'tx-pick', say: 'checking' };
      if (!pv.legal) return { cls: 'tx-off', say: 'not a valid target' };
      const row = (pv.affected || []).find(a => a.id === t.id) || {};
      const b = 'hit_percent' in row ? row.hit_percent + '%' : 'fail_percent' in row ? row.fail_percent + '%' : '';
      return { cls: 'tx-target', badge: b || null, ally: !!row.ally,
               say: 'hit_percent' in row ? `${row.hit_percent}% to hit` :
                    'fail_percent' in row ? `${row.fail_percent}% to fail the save` : 'valid target' };
    }
    if (ui.mode === 'darts' && t.id !== me.id && hostile(me, t)) {
      const n = (ui.darts || []).filter(id => id === t.id).length;
      return { cls: 'tx-target', badge: n ? '×' + n : null, say: n ? `${n} dart${n > 1 ? 's' : ''}` : 'can be picked' };
    }
    if (ui.mode === 'help') {
      if (!ui.helpTarget && hostile(me, t) && adjacent(me, t)) return { cls: 'tx-target', say: 'can be helped against' };
      if (ui.helpTarget === t.id) return { cls: 'tx-target', badge: 'vs', say: 'chosen enemy' };
      if (ui.helpTarget && t.id !== me.id && !hostile(me, t)) return { cls: 'tx-pick', say: 'ally to help' };
    }
    if (ui.mode === 'ready' && ui.readyStep === 'target' && t.id !== me.id)
      return { cls: 'tx-pick', say: 'can be the readied target' };
    return null;
  }

  function badge(layer, t, text, kind) {
    svg('rect', { x: t.x * C + C - 22, y: t.y * C - 6, width: 28, height: 14, rx: 3, class: 'tx-pct-bg' + (kind ? ' ' + kind : '') }, layer);
    const p = svg('text', { x: t.x * C + C - 8, y: t.y * C + 5, 'text-anchor': 'middle', class: 'tx-pct' }, layer);
    p.textContent = text;
  }

  function drawToken(t) {
    const cx = t.x * C + C / 2, cy = t.y * C + C / 2;
    const cls = ['tx-tok'];
    if (t.id === snap.current) cls.push('tx-now');
    if (t.dead) cls.push('tx-dead');
    if (t.hidden) cls.push('tx-hidden');
    const mark = markFor(t);
    if (mark && mark.cls) cls.push(...mark.cls.split(' '));
    const tg = tags(t);
    const g = svg('g', { class: cls.join(' '), 'data-id': t.id, tabindex: t.dead ? -1 : 0, role: 'button',
      'aria-label': `${t.name}, ${t.dead ? 'dead' : t.hp + ' of ' + t.max_hp + ' HP'}, ${label(t.x, t.y)}` +
        (tg.length ? ', ' + tg.join(', ') : '') + (mark && mark.say ? ', ' + mark.say : '') }, ui.tokenLayer);
    const col = t.side === 'enemy' ? 'var(--tx-danger)' : t.side === 'pc' ? 'var(--tx-quan)' : 'var(--tx-lore)';
    svg('circle', { cx, cy, r: C / 2 - 1, class: 'tx-ring' }, g);
    svg('circle', { cx, cy, r: C / 2 - 4, style: `fill:${col};stroke:var(--tx-panel);stroke-width:2` }, g);
    const initials = t.name.split(/\s+/).map(w => /^\d+$/.test(w) ? w : w[0]).join('').slice(0, 3);
    const tx = svg('text', { x: cx, y: cy + 4, 'text-anchor': 'middle', style: 'fill:#fff;font:700 11px Figtree,sans-serif' }, g);
    tx.textContent = initials;
    if (!t.dead) {
      const pct = Math.max(0, t.hp / Math.max(1, t.max_hp));
      svg('rect', { x: t.x * C + 4, y: t.y * C + C - 5, width: C - 8, height: 3, style: 'fill:var(--tx-line)' }, g);
      svg('rect', { x: t.x * C + 4, y: t.y * C + C - 5, width: (C - 8) * pct, height: 3,
                    style: 'fill:' + (pct <= .25 ? 'var(--tx-danger)' : 'var(--tx-heal)') }, g);
      // Small corner markers: C = concentrating (top left), R = a readied action (top right).
      if (t.concentration) marker(g, t.x * C + 5, t.y * C + 5, 'C', 'tx-conc');
      if (t.readied) marker(g, t.x * C + C - 5, t.y * C + 5, 'R', 'tx-ready');
    }
    if (mark && mark.badge) badge(g, t, mark.badge, mark.ally ? 'tx-ally' : '');
    const title = svg('title', {}, g); title.textContent = t.name + (tg.length ? ' (' + tg.join(', ') + ')' : '');
    g.addEventListener('click', e => { e.stopPropagation(); onToken(t, e); });
    g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToken(t, null); } });
    t._g = g;
  }

  function marker(g, x, y, text, cls) {
    svg('circle', { cx: x, cy: y, r: 5.5, class: 'tx-mark ' + cls }, g);
    const m = svg('text', { x, y: y + 3, 'text-anchor': 'middle', class: 'tx-mark-t' }, g);
    m.textContent = text;
  }

  function drawOverlay() {
    const o = ui.overlay; if (!o) return;
    o.innerHTML = '';
    if (ui.markLayer) ui.markLayer.innerHTML = '';
    if (ui.mode === 'aim') { drawAim(o); return; }
    if (ui.mode !== 'move' || !ui.reach) return;
    const walk = ui.reach.walk || {};
    const maxFt = Math.max(5, ...Object.values(walk));
    for (const [sq, ft] of Object.entries(walk)) {
      const p = parseSq(sq); if (!p) continue;
      svg('rect', { x: p[0] * C + 1, y: p[1] * C + 1, width: C - 2, height: C - 2, class: 'tx-reach',
                    style: `opacity:${(0.42 - 0.26 * ft / maxFt).toFixed(2)}` }, o);
    }
    for (const sq of Object.keys(ui.reach.dash || {})) {
      const p = parseSq(sq); if (!p) continue;
      svg('rect', { x: p[0] * C + 3, y: p[1] * C + 3, width: C - 6, height: C - 6, class: 'tx-dash' }, o);
    }
    const pv = ui.hover && ui.preview[ui.hover];
    if (pv && pv.path) {
      const pts = pv.path.map(parseSq).filter(Boolean).map(p => `${p[0] * C + C / 2},${p[1] * C + C / 2}`).join(' ');
      svg('polyline', { points: pts, class: 'tx-path' + (pv.opportunity_attacks && pv.opportunity_attacks.length ? ' tx-risky' : '') }, o);
      const end = parseSq(pv.path[pv.path.length - 1]);
      const f = svg('text', { x: end[0] * C + C / 2, y: end[1] * C - 3, 'text-anchor': 'middle', class: 'tx-feet' }, o);
      f.textContent = pv.feet + ' ft';
    }
  }

  // The template under the pointer: squares, creatures caught, allies in a warning colour.
  function drawAim(o) {
    const at = parseSq(ui.hover);
    if (!at) return;
    const pv = ui.preview[ui.hover];
    if (pv) {
      const allySq = new Set((pv.affected || []).filter(a => a.ally).map(a => a.square));
      for (const sq of pv.squares || []) {
        const p = parseSq(sq); if (!p) continue;
        svg('rect', { x: p[0] * C, y: p[1] * C, width: C, height: C,
                      class: 'tx-tpl' + (pv.legal ? '' : ' tx-off') + (allySq.has(sq) ? ' tx-ally' : '') }, o);
      }
      const area = ui.spell && ui.spell.area;
      if (!(pv.squares || []).length && area && area.size) {
        // The engine drew no squares (a spell the GM narrates): outline the
        // radius so the player still sees roughly where it lands.
        const me = current();
        const c = (ui.spell.range || 0) === 0 && me ? [me.x, me.y] : at;
        const r = area.shape === 'sphere' || area.shape === 'cylinder' ? area.size / 5 * C : area.size / 10 * C;
        svg('circle', { cx: c[0] * C + C / 2, cy: c[1] * C + C / 2, r, class: 'tx-tpl-ring' }, o);
      }
      for (const a of pv.affected || []) {
        const t = tokenById(a.id); if (!t) continue;
        svg('circle', { cx: t.x * C + C / 2, cy: t.y * C + C / 2, r: C / 2 - 1, class: 'tx-caught' + (a.ally ? ' tx-ally' : '') }, ui.markLayer);
        const b = 'fail_percent' in a ? a.fail_percent + '%' : 'hit_percent' in a ? a.hit_percent + '%' : 'darts' in a ? '×' + a.darts : '';
        if (b) badge(ui.markLayer, t, b, a.ally ? 'tx-ally' : '');
      }
    }
    svg('rect', { x: at[0] * C + 1, y: at[1] * C + 1, width: C - 2, height: C - 2,
                  class: 'tx-aim' + (ui.armed === ui.hover ? ' tx-armed' : '') }, o);
  }

  // ── side panel: info, actions, log ───────────────────────────────────────
  function renderSide() {
    const t = current();
    el.actions.innerHTML = '';
    renderInfo();
    if (t && myTurn() && !(snap.turn && snap.turn.pending === 'death_save')) {
      const left = snap.turn ? snap.turn.movement_left : 0;
      const used = !!(snap.turn && snap.turn.action_used);
      const why = (u, text) => ({ disabled: u, title: u ? 'Your action is used this turn' : text });
      button('Move', () => toggleMove(t), { pressed: ui.mode === 'move', disabled: left <= 0 && used });
      button('Attack', () => toggleAttack(t, 'weapon'), { pressed: ui.mode === 'attack', disabled: used });
      button('Cast', () => toggleCast(t), { pressed: SPELL_MODES.includes(ui.mode), title: 'Your spells, with slots left' });
      button('Dash', () => act('dash', [t.id]), { disabled: used });
      button('Disengage', () => act('disengage', [t.id]), { disabled: used });
      button('Dodge', () => act('dodge', [t.id]), { disabled: used });
      button('Help', () => toggleHelp(t), Object.assign({ pressed: ui.mode === 'help' }, why(used, 'Give an ally advantage against an enemy next to you')));
      button('Hide', () => act('hide', [t.id]), why(used, 'Stealth, out of every enemy\'s sight'));
      button('Ready', () => toggleReady(t), Object.assign({ pressed: ui.mode === 'ready' }, why(used, 'Hold an attack or a spell for a trigger')));
      if ((t.conditions || []).includes('grappled')) button('Escape', () => act('escape', [t.id]), why(used, 'Athletics or Acrobatics against the grapple'));
      if ((t.conditions || []).includes('prone')) button('Stand up', () => act('stand', [t.id]));
      button('Undo move', () => act('undo-move', []), { title: 'Take back the last move (before an action)' });
      button('End turn', () => act('end-turn', []), { primary: true });
      if (ui.mode === 'attack' && ui.targets) attackChoices();
      if (ui.mode === 'cast') spellChoices(t);
      if (ui.mode === 'darts') dartChoices();
      if (ui.mode === 'ready' && ui.readyStep === 'what') readyChoices();
      if (['aim', 'spell', 'darts', 'help', 'ready'].includes(ui.mode))
        button('Cancel', () => { clearMode(); render(); }, { title: 'Escape also cancels' });
    } else if (t && myTurn()) {
      button('Roll death save', () => act('death-save', [t.id]), { primary: true });
    }
    el.log.innerHTML = '';
    for (const e of (snap.log || []).slice(-8)) {
      const li = document.createElement('li'); li.textContent = e.text; el.log.appendChild(li);
    }
    el.log.scrollTop = el.log.scrollHeight;
  }

  function renderInfo() {
    const t = current();
    if (!t) { el.info.innerHTML = ''; return; }
    if (!myTurn()) { el.info.innerHTML = `<strong>${esc(t.name)}</strong> is acting. The GM narrates their turn.` + statusLine(t); return; }
    if (snap.turn && snap.turn.pending === 'death_save') { el.info.innerHTML = `<strong>${esc(t.name)}</strong> is dying: roll a death save.`; return; }
    el.info.innerHTML = infoText(t);
  }

  function economy() {
    const tn = snap.turn || {};
    const s = (w, u) => `${w} ${u ? 'used' : 'ready'}`;
    return `${tn.movement_left || 0} ft of movement, ${s('action', tn.action_used)}, ${s('bonus action', tn.bonus_used)}, ` +
           `${s('reaction', tn.reaction === false)}.`;
  }

  function statusLine(t) {
    const bits = [];
    if (t.concentration) bits.push(`Concentrating on ${esc(t.concentration)}.`);
    if ((t.effects || []).length) bits.push(`Effects: ${esc(t.effects.join(', '))}.`);
    if (t.readied) bits.push(`Readied: ${esc(t.readied)}.`);
    if (t.hidden) bits.push('Hidden.');
    return bits.length ? '<br><span class="tx-status">' + bits.join(' ') + '</span>' : '';
  }

  function infoText(t) {
    let s = `<strong>Your turn, ${esc(t.name)}.</strong> ${economy()}` + statusLine(t);
    const sp = ui.spell;
    if (ui.mode === 'move') {
      const pv = ui.hover && ui.preview[ui.hover];
      s += '<br>' + (pv ? esc(pv.text) + (ui.armed === ui.hover ? ' <em>Tap again to move.</em>' : '')
                        : 'Pick a square. Shaded: walking range. Dashed: needs Dash.');
      if (pv && pv.opportunity_attacks && pv.opportunity_attacks.length)
        s += '<br><span class="tx-warn">This move provokes an opportunity attack.</span>';
    } else if (ui.mode === 'attack') {
      s += '<br>Pick a target on the map. Hit chance is shown on each one.';
    } else if (ui.mode === 'cast') {
      s += '<br>Pick a spell.';
    } else if (ui.mode === 'aim') {
      const pv = ui.hover && ui.preview[ui.hover];
      const shape = sp.area ? `${sp.area.size} ft ${sp.area.shape}` : 'area';
      if (!pv) s += `<br><strong>${esc(sp.name)}</strong> (${shape}): point at a square to aim, then click. On a phone, tap twice.`;
      else {
        s += `<br>${esc(pv.text || pv.reason || '')}` + (pv.legal && ui.armed === ui.hover ? ' <em>Tap again to cast.</em>' : '');
        const allies = (pv.affected || []).filter(a => a.ally);
        if (allies.length) s += `<br><span class="tx-warn tx-ff">Friendly fire: this catches ${esc(allies.map(a => a.name).join(', '))}.</span>`;
        if (sp.mode === 'narrate') s += '<br>The GM narrates what this spell does.';
      }
    } else if (ui.mode === 'spell') {
      const waiting = ui.singles && Object.values(ui.singles).some(v => v === null);
      s += `<br><strong>${esc(sp.name)}</strong>: pick a target on the map.` + (waiting ? ' Working out the odds...' : '');
    } else if (ui.mode === 'darts') {
      const names = (ui.darts || []).map(id => (tokenById(id) || {}).name || id);
      s += `<br><strong>${esc(sp.name)}</strong>: pick 1 target for every dart, or ${MAX_DARTS} darts in order (repeats allowed).`;
      if (names.length) s += `<br>Picked: ${esc(names.join(', '))}.` + (names.length === 2 ? ' Pick one more.' : '');
      if (ui.dartsText) s += `<br>${esc(ui.dartsText)}`;
    } else if (ui.mode === 'help') {
      s += ui.helpTarget ? `<br>Now pick the ally who gets advantage against ${esc((tokenById(ui.helpTarget) || {}).name || '')}.`
                         : '<br>Help: pick an enemy next to you.';
    } else if (ui.mode === 'ready') {
      if (ui.readyStep === 'what') s += '<br>Ready: pick what to hold.';
      else s += `<br>Ready ${esc(ui.readyWhat.label)}: pick ${ui.readyWhat.area ? 'a square' : 'a target'} on the map.`;
    }
    return s;
  }

  function button(text, fn, o) {
    o = o || {};
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'tx-btn' + (o.primary ? ' tx-primary' : '') + (o.cls ? ' ' + o.cls : ''); b.textContent = text;
    if (o.pressed !== undefined) b.setAttribute('aria-pressed', String(!!o.pressed));
    if (o.title) b.title = o.title;
    if (o.meta) { const m = document.createElement('span'); m.className = 'tx-meta'; m.textContent = o.meta; b.appendChild(m); }
    b.disabled = !!o.disabled || (ui.busy && !o.always) || !fn;
    if (fn) b.addEventListener('click', fn);
    (o.parent || el.actions).appendChild(b);
    return b;
  }

  function box(cls, labelText) {
    const d = document.createElement('div'); d.className = cls;
    if (labelText) { d.setAttribute('role', 'group'); d.setAttribute('aria-label', labelText); }
    el.actions.appendChild(d);
    return d;
  }

  function note(parent, text, cls) {
    const n = document.createElement(cls === 'tx-group' ? 'h4' : 'span');
    n.className = cls || 'tx-note'; n.textContent = text; parent.appendChild(n); return n;
  }

  function attackChoices() {
    const b = box('tx-attacks');
    const names = [...new Set(ui.targets.filter(r => kindOf(r.attack) === ui.kind).map(r => r.attack))];
    if (!names.length) note(b, 'No weapon attack available.');
    for (const name of names) {
      const legal = ui.targets.filter(r => r.attack === name && r.legal);
      const best = legal.reduce((m, r) => Math.max(m, r.hit_percent), 0);
      button(name + (legal.length ? ` (up to ${best}%)` : ' (no target)'), () => { ui.attack = name; render(); },
        { pressed: ui.attack === name, disabled: !legal.length, parent: b });
    }
  }

  function kindOf(attackName) {
    const t = current(); const a = t && (t.attacks || []).find(x => x.name === attackName);
    if (a) return a.source === 'spell' ? 'spell' : 'weapon';
    return /bolt|ray|blast|missile|sliver|spray|flame|touch/i.test(attackName) ? 'spell' : 'weapon';
  }

  function bestTarget(id) {
    if (!ui.targets || !ui.attack) return null;
    return ui.targets.find(r => r.target === id && r.attack === ui.attack) || null;
  }

  // ── spells: the list ─────────────────────────────────────────────────────
  const isReaction = sp => sp.mode === 'reaction' || sp.casting === 'reaction';

  function describe(sp) {
    const bits = [];
    if (sp.casting === 'bonus') bits.push('bonus action');
    if (sp.area) bits.push(`${sp.area.size} ft ${sp.area.shape}`);
    bits.push({ attack: 'spell attack', save: 'save', darts: 'auto-hit darts', heal: 'healing',
                effect: 'on yourself', narrate: 'GM narrates', unknown: 'not readable' }[sp.mode] || sp.mode);
    if (sp.range) bits.push(sp.range + ' ft');
    if (sp.concentration) bits.push('concentration');
    return bits.join(', ');
  }

  function spellChoices(t) {
    const b = box('tx-spells', 'Spells');
    if (!ui.spells) { note(b, 'Loading spells...'); return; }
    const list = ui.spells.filter(sp => !isReaction(sp));
    if (!list.length) note(b, `${t.name} has no spells to cast.`);
    const levels = [...new Set(list.map(sp => sp.level == null ? 99 : sp.level))].sort((a, z) => a - z);
    for (const lv of levels) {
      let head = lv === 0 ? 'Cantrips' : lv === 99 ? 'Other' : `Level ${lv}`;
      const slot = (t.slots || {})[String(lv)];
      if (lv > 0 && lv < 99) head += slot ? ` · ${Math.max(0, slot.total - slot.used)} of ${slot.total} slots left` : ' · no slots';
      note(b, head, 'tx-group');
      for (const sp of list.filter(s => (s.level == null ? 99 : s.level) === lv)) {
        button(sp.name, () => pickSpell(sp), { parent: b, disabled: !sp.ok, cls: 'tx-spell',
          meta: sp.ok ? describe(sp) : sp.reason, title: sp.ok ? describe(sp) : sp.reason });
      }
    }
    const reactive = ui.spells.filter(sp => sp.mode === 'reaction');   // the ones the engine offers (Shield, Silvery Barbs)
    if (reactive.length) {
      const names = reactive.map(sp => sp.name).join(', ');
      const row = document.createElement('div'); row.className = 'tx-react-row';
      row.setAttribute('role', 'group'); row.setAttribute('aria-label', 'Spell reactions: ask, auto or off');
      note(row, `Reactions (${names}):`);
      for (const mode of ['ask', 'auto', 'off']) {
        button(mode[0].toUpperCase() + mode.slice(1), () => act('reactions', [t.id, mode], { keepMode: true }),
          { parent: row, pressed: (t.reactions || 'ask') === mode, cls: 'tx-small',
            title: { ask: 'Ask me when one could be used', auto: 'Use them whenever they help', off: 'Never use them' }[mode] });
      }
      b.appendChild(row);
    }
  }

  async function toggleCast(t) {
    if (SPELL_MODES.includes(ui.mode)) { clearMode(); render(); return; }
    clearMode(); ui.mode = 'cast'; render();
    await loadSpells(t);
    if (ui.mode !== 'cast') return;
    render();
    reveal(el.actions.querySelector('.tx-spells'));   // on a phone the list sits below the fold
  }

  function reveal(node) {                        // phone layout only: the panel scrolls there
    if (node && node.scrollIntoView && window.matchMedia && matchMedia('(max-width: 860px)').matches) node.scrollIntoView({ block: 'nearest', behavior: reduced() ? 'auto' : 'smooth' });
  }

  async function loadSpells(t) {
    const res = await call('spells', [t.id]);
    if (res.error) { toast(res.error, 'error'); return null; }
    ui.spells = (res.result && res.result.spells) || [];
    return ui.spells;
  }

  function pickSpell(sp) {
    const t = current(); if (!t) return;
    ui.spell = sp; ui.preview = {}; ui.hover = ui.armed = null;
    if (sp.targeting !== 'self' && sp.targeting !== 'none') reveal(el.board);
    const selfArea = sp.area && !sp.range && sp.mode === 'narrate';   // Detect Magic: centred on the caster
    if (sp.targeting === 'area' && !selfArea) { ui.mode = 'aim'; render(); return; }
    if (sp.targeting === 'single') { enterSingle(t, sp); return; }
    if (sp.targeting === 'darts') { ui.mode = 'darts'; ui.darts = []; render(); return; }
    castSpell([]);                                // self, effect and narrated spells need no target
  }

  function castSpell(targets) {
    const t = current(), sp = ui.spell;
    if (!t || !sp) return Promise.resolve(false);
    return act('cast', [t.id, sp.name, ...targets], { note: sp.mode === 'narrate' ? 'The GM narrates the effect.' : '' });
  }

  // ── spells: single targets ───────────────────────────────────────────────
  async function enterSingle(me, sp) {
    ui.mode = 'spell'; ui.singles = {};
    const heal = sp.mode === 'heal';
    const cands = living().filter(t => heal ? !hostile(me, t) : (t.id !== me.id && hostile(me, t)));
    for (const t of cands) ui.singles[t.id] = null;
    render();
    for (const t of cands) {
      const res = await call('preview-area', [me.id, sp.name, t.id]);
      if (ui.spell !== sp) return;
      ui.singles[t.id] = res.result || { legal: false, reason: res.error || 'Not a valid target.' };
      render();
    }
    if (!cands.some(t => ui.singles[t.id] && ui.singles[t.id].legal)) toast(`No target in reach for ${sp.name}.`, 'error');
  }

  // ── spells: darts ────────────────────────────────────────────────────────
  function dartChoices() {
    const b = box('tx-attacks');
    const n = ui.darts.length;
    button(n === 1 ? `Cast: every dart at ${(tokenById(ui.darts[0]) || {}).name}` : `Cast ${ui.spell.name}`,
      () => castSpell(ui.darts.slice()), { parent: b, primary: true, disabled: !(n === 1 || n === MAX_DARTS),
        title: 'One target takes every dart; or pick one target per dart' });
    button('Undo last dart', () => { ui.darts.pop(); ui.dartsText = ''; render(); dartsPreview(); }, { parent: b, disabled: !n });
  }

  async function dartsPreview() {
    const me = current(), sp = ui.spell, picked = (ui.darts || []).slice();
    if (!me || !(picked.length === 1 || picked.length === MAX_DARTS)) return;
    const res = await call('preview-area', [me.id, sp.name, ...picked]);
    if (ui.spell !== sp || String(ui.darts) !== String(picked)) return;
    ui.dartsText = res.result ? (res.result.text || res.result.reason || '') : (res.error || '');
    renderInfo();
  }

  // ── Help and Ready ───────────────────────────────────────────────────────
  function toggleHelp(t) {
    if (ui.mode === 'help') { clearMode(); render(); return; }
    clearMode(); ui.mode = 'help';
    if (!living().some(x => hostile(t, x) && adjacent(t, x))) toast('Help needs an enemy within 5 ft of you.', 'error');
    else if (!living().some(x => x.id !== t.id && !hostile(t, x))) toast('There is no ally to help.', 'error');
    render();
  }

  async function toggleReady(t) {
    if (ui.mode === 'ready') { clearMode(); render(); return; }
    clearMode(); ui.mode = 'ready'; ui.readyStep = 'what'; render();
    const [res] = await Promise.all([call('targets', [t.id]), ui.spells ? null : loadSpells(t)]);
    if (ui.mode !== 'ready') return;
    ui.targets = (res && res.result && res.result.targets) || [];
    render();
  }

  function readyChoices() {
    const b = box('tx-spells', 'What to ready');
    if (!ui.targets) { note(b, 'Loading...'); return; }
    const attacks = [...new Set(ui.targets.map(r => r.attack))].filter(n => kindOf(n) === 'weapon');
    note(b, 'Attack', 'tx-group');
    if (!attacks.length) note(b, 'No weapon attack.');
    for (const name of attacks)
      button(name, () => readyPick({ kind: 'attack', what: name, label: name }), { parent: b, cls: 'tx-spell' });
    const spells = (ui.spells || []).filter(sp => !isReaction(sp) && sp.casting === 'action');
    if (spells.length) note(b, 'Spell (held with concentration)', 'tx-group');
    for (const sp of spells)
      button(sp.name, () => readyPick({ kind: 'cast', what: sp.name, label: sp.name, area: sp.targeting === 'area',
                                         none: ['self', 'none'].includes(sp.targeting) }),
        { parent: b, cls: 'tx-spell', meta: describe(sp) });
  }

  function readyPick(w) {
    ui.readyWhat = w;
    if (w.none) { readyFinish(null); return; }
    ui.readyStep = 'target'; render();
  }

  async function readyFinish(target) {
    const me = current(), w = ui.readyWhat; if (!me || !w) return;
    const who = target ? ' at ' + ((tokenById(target) || {}).name || target) : '';
    const trig = prompt(`Ready ${w.label}${who}.\nWhat triggers it? (for example: a frog comes within reach)`);
    if (!trig || !trig.trim()) { toast('Ready cancelled: no trigger given.'); clearMode(); render(); return; }
    const args = [me.id, w.kind, w.what];
    if (target) args.push('--target', target);
    args.push('--trigger=' + trig.trim().replace(/\s+/g, ' ').slice(0, 140));
    await act('ready', args);
  }

  // ── interaction ──────────────────────────────────────────────────────────
  async function toggleMove(t) {
    if (ui.mode === 'move') { clearMode(); render(); return; }
    clearMode(); ui.mode = 'move';
    const res = await call('reachable', [t.id]);
    if (res.error) { toast(res.error, 'error'); clearMode(); render(); return; }
    ui.reach = res.result || {}; render();
  }

  async function toggleAttack(t, kind) {
    if (ui.mode === 'attack' && ui.kind === kind) { clearMode(); render(); return; }
    clearMode(); ui.mode = 'attack'; ui.kind = kind;
    const res = await call('targets', [t.id]);
    if (res.error) { toast(res.error, 'error'); clearMode(); render(); return; }
    ui.targets = (res.result && res.result.targets) || [];
    const first = ui.targets.find(r => r.legal && kindOf(r.attack) === kind);
    ui.attack = first ? first.attack : null;
    render();
  }

  function cellAt(evt) {
    const pt = ui.svg.createSVGPoint(); pt.x = evt.clientX; pt.y = evt.clientY;
    const q = pt.matrixTransform(ui.svg.getScreenCTM().inverse());
    const x = Math.floor(q.x / C), y = Math.floor(q.y / C);
    if (x < 0 || y < 0 || x >= (ui.W || 0) || y >= (ui.H || 0)) return null;
    return label(x, y);
  }

  const inReach = sq => !!ui.reach && ((sq in (ui.reach.walk || {})) || (sq in (ui.reach.dash || {})));

  function onHover(evt) {
    if (evt.pointerType === 'touch') return;
    const sq = cellAt(evt);
    if (ui.mode === 'aim') {
      if (!sq || sq === ui.hover) return;
      ui.hover = sq; ui.armed = null;
      clearTimeout(ui.hoverTimer);
      drawOverlay();
      if (ui.preview[sq]) renderInfo();
      else ui.hoverTimer = setTimeout(() => previewAim(sq), 90);
      return;
    }
    if (ui.mode !== 'move' || !ui.reach) return;
    if (sq === ui.hover) return;
    ui.hover = sq;
    clearTimeout(ui.hoverTimer);
    if (!sq || !inReach(sq)) { drawOverlay(); return; }
    ui.hoverTimer = setTimeout(() => previewTo(sq), 90);
  }

  async function previewTo(sq) {
    const t = current(); if (!t) return null;
    if (!ui.preview[sq]) {
      const res = await call('preview', [t.id, sq]);
      if (res.error || !res.result) return null;
      ui.preview[sq] = res.result;
    }
    if (ui.hover === sq) { drawOverlay(); renderInfo(); }
    return ui.preview[sq];
  }

  // Cached per square, like the move preview; a stale answer (the spell
  // changed meanwhile) is dropped.
  async function previewAim(sq) {
    const t = current(), sp = ui.spell; if (!t || !sp) return null;
    if (!ui.preview[sq]) {
      const res = await call('preview-area', [t.id, sp.name, sq]);
      if (ui.spell !== sp) return null;
      ui.preview[sq] = res.result || { squares: [], affected: [], legal: false,
                                       reason: res.error || 'No preview.', text: res.error || '' };
    }
    if (ui.hover === sq) { drawOverlay(); renderInfo(); }
    return ui.preview[sq];
  }

  async function onBoardClick(evt) {
    if (ui.busy) return;
    const sq = cellAt(evt);
    if (!sq) return;
    if (ui.mode === 'aim') return clickAim(evt, sq);
    if (ui.mode === 'ready' && ui.readyStep === 'target' && ui.readyWhat && ui.readyWhat.area) return readyFinish(sq);
    if (ui.mode !== 'move' || !ui.reach) return;
    const t = current(); if (!t) return;
    if (!inReach(sq)) return;
    // A mouse has already previewed on hover: one click moves. Touch (or no
    // hover yet): the first tap previews, a second tap on the same square moves.
    const hovered = evt.pointerType === 'mouse' && ui.hover === sq && ui.preview[sq];
    if (!hovered && ui.armed !== sq) {
      ui.hover = sq; ui.armed = sq;
      await previewTo(sq);
      return;
    }
    const pv = ui.preview[sq] || await previewTo(sq);
    if (!pv) return;
    if (pv.opportunity_attacks && pv.opportunity_attacks.length) {
      const who = pv.opportunity_attacks.map(o => `${o.name} (${o.attack}, ${o.hit_percent}% to hit)`).join(', ');
      if (!confirm(`Moving to ${sq} provokes ${who}. Move anyway?`)) return;
    }
    if (sq in (ui.reach.dash || {})) {
      if (!confirm(`${sq} is ${pv.feet} ft away. Use your action to Dash?`)) return;
      if (!await act('dash', [t.id], { keepMode: true })) return;
    }
    ui.lastMove = { id: t.id, path: pv.path };
    await act('move', [t.id, sq]);
  }

  // Aim: a mouse click casts where it has already previewed; a tap (or Enter
  // on a token) previews first and casts on the second one.
  async function clickAim(evt, sq) {
    const hovered = evt && evt.pointerType === 'mouse' && ui.hover === sq && ui.preview[sq];
    if (!hovered && ui.armed !== sq) {
      ui.hover = sq; ui.armed = sq;
      drawOverlay();
      if (ui.preview[sq]) renderInfo(); else await previewAim(sq);
      return;
    }
    const pv = ui.preview[sq] || await previewAim(sq);
    if (!pv) return;
    if (!pv.legal) { toast(pv.reason || 'The spell cannot go there.', 'error'); return; }
    const allies = (pv.affected || []).filter(a => a.ally);
    if (allies.length) {
      const who = allies.map(a => `${a.name}${a.id === snap.current ? ' (you)' : ''}` +
        ('fail_percent' in a ? ` (${a.fail_percent}% to fail)` : '')).join(', ');
      if (!confirm(`${ui.spell.name} at ${sq} also catches ${who}. Cast anyway?`)) return;
    }
    await castSpell([sq]);
  }

  function onToken(t, evt) {
    if (ui.busy || !myTurn()) return;
    const me = current();
    if (ui.mode === 'aim') { if (!t.dead) clickAim(evt, sqOf(t)); return; }
    if (ui.mode === 'spell') {
      if (!ui.singles || !(t.id in ui.singles)) return;
      const pv = ui.singles[t.id];
      if (!pv) { toast('Still working out the odds for ' + t.name + '.'); return; }
      if (!pv.legal) { toast(pv.reason || 'Not a valid target.', 'error'); return; }
      castSpell([t.id]);
      return;
    }
    if (ui.mode === 'darts') {
      if (t.dead || t.id === me.id || !hostile(me, t)) return;
      if (ui.darts.length >= MAX_DARTS) { toast(`All ${MAX_DARTS} darts are placed. Undo one or cast.`); return; }
      ui.darts.push(t.id); ui.dartsText = ''; render(); dartsPreview();
      return;
    }
    if (ui.mode === 'help') {
      if (!ui.helpTarget) {
        if (!hostile(me, t) || t.dead) return;
        if (!adjacent(me, t)) { toast(`${t.name} is not within 5 ft of you.`, 'error'); return; }
        ui.helpTarget = t.id; render();
      } else if (t.id !== me.id && !hostile(me, t) && !t.dead) {
        act('help', [me.id, t.id, ui.helpTarget]);
      }
      return;
    }
    if (ui.mode === 'ready' && ui.readyStep === 'target') {
      if (t.id === me.id || t.dead) return;
      readyFinish(ui.readyWhat.area ? sqOf(t) : t.id);
      return;
    }
    if (t.id === me.id) { if (ui.mode === 'move') { clearMode(); render(); } else toggleMove(t); return; }
    if (ui.mode === 'attack' && ui.attack) {
      const row = bestTarget(t.id);
      if (!row) return;
      if (!row.legal) { toast(row.reason || 'Not a valid target.', 'error'); return; }
      act('attack', [me.id, t.id, ...ui.attack.split(/\s+/)]);
    }
  }

  // ── actions and prompts ──────────────────────────────────────────────────
  async function act(cmd, args, o) {
    o = o || {};
    ui.busy = true; renderSide();
    const extra = { rolls: [] };
    let done = false;
    for (let guard = 0; guard < 8; guard++) {
      const res = await call(cmd, args, extra);
      if (res.pending) {
        const answer = await ask(res.pending);
        if (!answer) break;
        if (answer.forMe) extra.for_me = true;
        else if (answer.react) (extra.react = extra.react || []).push(answer.react);   // one per decision, in order
        else extra.rolls.push(answer.roll);
        continue;
      }
      if (res.error) toast(res.error, 'error');
      else {                                      // GM hints ("Next: options frog-1") are not for players
        const text = res.text.split('\n').filter(l => !/^(Next:|Then:|Waiting for)/.test(l))
          .map(l => l.replace(/\s*The GM runs: .*$/, '')).join(' ');
        toast(o.note && !/GM decides/.test(text) ? text + ' ' + o.note : text);
        done = true;
      }
      break;
    }
    ui.busy = false;
    if (!o.keepMode) clearMode();
    render();
    return done;
  }

  function ask(message) {
    const first = message.split(/\n/)[0].replace(/ Nothing has happened yet\.?/, '');
    const react = /--react yes or --react no/.test(message);
    const oa = /Opportunity attack\?/.test(message);
    const m = /rolls (\d+)d(\d+)([+-]\d+)?( with (advantage|disadvantage))?/.exec(first);
    return new Promise(resolve => {
      const box = el.prompt; box.hidden = false; box.innerHTML = '';
      const p = document.createElement('div'); p.textContent = first; box.appendChild(p);
      const row = document.createElement('div'); row.className = 'tx-row'; box.appendChild(row);
      const finish = v => { box.hidden = true; box.innerHTML = ''; resolve(v); };
      if (react) {
        const yes = button(oa ? 'Yes, attack' : 'Yes', () => finish({ react: 'yes' }), { parent: row, primary: true, always: true });
        button(oa ? 'No, let them go' : 'No', () => finish({ react: 'no' }), { parent: row, always: true });
        setTimeout(() => yes.focus(), 0);
      } else if (m) {
        const n = +m[1], sides = +m[2], adv = m[5];
        const input = document.createElement('input');
        input.type = 'number'; input.min = n; input.max = n * sides; input.inputMode = 'numeric';
        input.placeholder = n + ' to ' + n * sides;
        input.setAttribute('aria-label', `Your ${n}d${sides} result, without modifiers`);
        row.appendChild(input);
        const use = button('Use my roll', () => {
          const v = +input.value; if (v >= n && v <= n * sides) finish({ roll: v }); else input.focus();
        }, { parent: row, primary: true, always: true });
        button('Roll the dice', () => {
          const d = () => 1 + (crypto.getRandomValues(new Uint32Array(1))[0] % sides);
          let total;
          if (adv && n === 1 && sides === 20) {
            const a = d(), b = d(); total = adv === 'advantage' ? Math.max(a, b) : Math.min(a, b);
            toast(`d20 with ${adv}: ${a} and ${b}, keep ${total}`);
          } else { total = 0; for (let i = 0; i < n; i++) total += d(); toast(`${n}d${sides}: ${total}`); }
          finish({ roll: total });
        }, { parent: row, always: true });
        button('Roll for me', () => finish({ forMe: true }), { parent: row, always: true, title: 'The engine rolls this action for you' });
        input.addEventListener('keydown', e => { if (e.key === 'Enter') use.click(); });
        setTimeout(() => input.focus(), 0);
      }
      button('Cancel', () => finish(null), { parent: row, always: true });
    });
  }

  // ── movement animation and damage floaters ───────────────────────────────
  function animate() {
    if (!prev || reduced()) { ui.lastMove = null; return; }
    for (const t of snap.tokens || []) {
      const was = (prev.tokens || []).find(p => p.id === t.id);
      if (!was || !t._g || (was.x === t.x && was.y === t.y)) continue;
      let pts = [[was.x, was.y], [t.x, t.y]];
      if (ui.lastMove && ui.lastMove.id === t.id && ui.lastMove.path) {
        const path = ui.lastMove.path.map(parseSq).filter(Boolean);
        const endAt = path.findIndex(p => p[0] === t.x && p[1] === t.y);
        if (endAt > 0) pts = path.slice(0, endAt + 1);
      }
      const g = t._g, step = 120, start = performance.now();
      const frame = now => {
        const k = Math.min((now - start) / step, pts.length - 1);
        const i = Math.floor(k), f = k - i, a = pts[i], b = pts[Math.min(i + 1, pts.length - 1)];
        const px = a[0] + (b[0] - a[0]) * f, py = a[1] + (b[1] - a[1]) * f;
        g.setAttribute('transform', `translate(${(px - t.x) * C},${(py - t.y) * C})`);
        if (k < pts.length - 1) requestAnimationFrame(frame); else g.removeAttribute('transform');
      };
      requestAnimationFrame(frame);
    }
    ui.lastMove = null;
  }

  function floaters() {
    if (!prev || !ui.floatLayer) return;
    for (const t of snap.tokens || []) {
      const was = (prev.tokens || []).find(p => p.id === t.id);
      if (!was || was.hp === t.hp) continue;
      const d = t.hp - was.hp;
      const f = svg('text', { x: t.x * C + C / 2, y: t.y * C + 4, 'text-anchor': 'middle',
                              class: 'tx-float ' + (d < 0 ? 'tx-dmg' : 'tx-heal') }, ui.floatLayer);
      f.textContent = (d > 0 ? '+' : '') + d;
      setTimeout(() => f.remove(), 1400);
    }
  }

  // ── boot ─────────────────────────────────────────────────────────────────
  async function init() {
    build();
    try {
      const r = await fetch('/combat/state');
      const s = await r.json();
      if (s && s.status === 'active' && !snap) update(s);
    } catch (e) { /* no combat endpoint or no fight: nothing to show */ }
    let resizeTimer = 0;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { if (snap) render(); }, 150);
    });
  }

  window.Tactics = { update, init, state: () => snap };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
