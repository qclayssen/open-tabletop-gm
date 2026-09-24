/* tactics.js: the grid combat panel for the display companion.
 *
 * The engine owns the rules; this page only draws its snapshots and sends the
 * player's clicks. Every click goes to POST /combat/do, which runs the same
 * command the GM would run and queues the result for the GM to narrate.
 *
 * Snapshots arrive as the `combat` SSE event (see sync.snapshot in
 * scripts/tactics/sync.py). The panel appears when combat starts and hides
 * when it ends; with the display off nothing here runs.
 */
(function () {
  'use strict';

  const C = 32;                                   // SVG units per 5 ft square
  const LEGEND = { '.': 'floor', '#': 'wall', ',': 'difficult', '~': 'water',
                   '^': 'hazard', 'o': 'feature', '_': 'void' };
  const BUILTIN = ['floor', 'wall', 'difficult', 'water', 'hazard', 'feature', 'void'];
  const SVGNS = 'http://www.w3.org/2000/svg';

  let snap = null, prev = null;
  const ui = { mode: null, kind: null, reach: null, targets: null, attack: null, preview: {},
               hover: null, armed: null, busy: false, lastMove: null, toastTimer: 0, hoverTimer: 0 };
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
    ui.preview = {};
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
      clearMode();
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

  function renderStrip() {
    el.strip.innerHTML = '';
    for (const id of snap.order || []) {
      const t = tokenById(id); if (!t) continue;
      const pct = Math.max(0, Math.round(100 * t.hp / Math.max(1, t.max_hp)));
      const c = document.createElement('div');
      c.className = 'tx-chip' + (id === snap.current ? ' tx-now' : '') + (t.dead ? ' tx-dead' : '');
      c.innerHTML = `<span>${esc(t.name)}</span><span class="tx-hpbar" role="img" aria-label="${t.hp} of ${t.max_hp} HP"><i class="${pct <= 25 ? 'tx-low' : ''}" style="width:${pct}%"></i></span>` +
        `<span>${t.dead ? 'dead' : t.hp + '/' + t.max_hp + ' HP'}${t.conditions && t.conditions.length ? ' · ' + esc(t.conditions.join(', ')) : ''}</span>`;
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
    const avail = Math.max(240, el.board.clientWidth - 4);
    const cell = Math.max(28, Math.min(40, Math.floor(avail / Math.max(1, W))));
    const s = svg('svg', { viewBox: `0 0 ${W * C} ${H * C}`, width: W * cell, height: H * cell,
                           role: 'group', 'aria-label': `Battle map, ${W} by ${H} squares` });
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
    ui.floatLayer = svg('g', {}, s);
    ui.svg = s;
    drawOverlay();
    for (const t of snap.tokens || []) drawToken(t);
    s.addEventListener('pointermove', onHover);
    s.addEventListener('click', onBoardClick);
    s.addEventListener('pointerleave', () => { if (ui.mode === 'move') { ui.hover = null; drawOverlay(); } });
    el.board.innerHTML = ''; el.board.appendChild(s);
    floaters();
  }

  function drawToken(t) {
    const cx = t.x * C + C / 2, cy = t.y * C + C / 2;
    const cls = ['tx-tok'];
    if (t.id === snap.current) cls.push('tx-now');
    if (t.dead) cls.push('tx-dead');
    const target = ui.mode === 'attack' && bestTarget(t.id);
    if (target) cls.push('tx-target');
    const g = svg('g', { class: cls.join(' '), 'data-id': t.id, tabindex: t.dead ? -1 : 0, role: 'button',
      'aria-label': `${t.name}, ${t.dead ? 'dead' : t.hp + ' of ' + t.max_hp + ' HP'}, ${label(t.x, t.y)}` +
        (t.conditions && t.conditions.length ? ', ' + t.conditions.join(', ') : '') +
        (target && target.legal ? `, ${target.hit_percent}% to hit` : '') }, ui.tokenLayer);
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
    }
    if (target && target.legal) {
      svg('rect', { x: t.x * C + C - 22, y: t.y * C - 6, width: 28, height: 14, rx: 3, class: 'tx-pct-bg' }, g);
      const p = svg('text', { x: t.x * C + C - 8, y: t.y * C + 5, 'text-anchor': 'middle', class: 'tx-pct' }, g);
      p.textContent = target.hit_percent + '%';
    }
    const title = svg('title', {}, g); title.textContent = t.name;
    g.addEventListener('click', e => { e.stopPropagation(); onToken(t); });
    g.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onToken(t); } });
    t._g = g;
  }

  function drawOverlay() {
    const o = ui.overlay; if (!o) return;
    o.innerHTML = '';
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

  // ── side panel: info, actions, log ───────────────────────────────────────
  function renderSide() {
    const t = current();
    el.info.innerHTML = '';
    el.actions.innerHTML = '';
    if (t) {
      const left = snap.turn ? snap.turn.movement_left : 0;
      const used = !!(snap.turn && snap.turn.action_used);
      if (!myTurn()) {
        el.info.innerHTML = `<strong>${esc(t.name)}</strong> is acting. The GM narrates their turn.`;
      } else if (snap.turn && snap.turn.pending === 'death_save') {
        el.info.innerHTML = `<strong>${esc(t.name)}</strong> is dying: roll a death save.`;
        button('Roll death save', () => act('death-save', [t.id]), { primary: true });
      } else {
        el.info.innerHTML = infoText(t, left, used);
        button('Move', () => toggleMove(t), { pressed: ui.mode === 'move', disabled: left <= 0 && used });
        button('Attack', () => toggleAttack(t, 'weapon'), { pressed: ui.mode === 'attack' && ui.kind === 'weapon', disabled: used });
        button('Cast', () => toggleAttack(t, 'spell'), { pressed: ui.mode === 'attack' && ui.kind === 'spell', disabled: used });
        button('Dash', () => act('dash', [t.id]), { disabled: used });
        button('Disengage', () => act('disengage', [t.id]), { disabled: used });
        button('Dodge', () => act('dodge', [t.id]), { disabled: used });
        button('Help', null, { disabled: true, title: 'Help arrives in a later milestone' });
        button('Hide', null, { disabled: true, title: 'Hide arrives in a later milestone' });
        if ((t.conditions || []).includes('prone')) button('Stand up', () => act('stand', [t.id]));
        button('Undo move', () => act('undo-move', []), { title: 'Take back the last move (before an action)' });
        button('End turn', () => act('end-turn', []), { primary: true });
        if (ui.mode === 'attack' && ui.targets) attackChoices();
      }
    }
    el.log.innerHTML = '';
    for (const e of (snap.log || []).slice(-8)) {
      const li = document.createElement('li'); li.textContent = e.text; el.log.appendChild(li);
    }
    el.log.scrollTop = el.log.scrollHeight;
  }

  function infoText(t, left, used) {
    let s = `<strong>Your turn, ${esc(t.name)}.</strong> ${left} ft of movement, action ${used ? 'used' : 'ready'}.`;
    if (ui.mode === 'move') {
      const pv = ui.hover && ui.preview[ui.hover];
      s += '<br>' + (pv ? esc(pv.text) + (ui.armed === ui.hover ? ' <em>Tap again to move.</em>' : '')
                        : 'Pick a square. Shaded: walking range. Dashed: needs Dash.');
      if (pv && pv.opportunity_attacks && pv.opportunity_attacks.length)
        s += '<br><span class="tx-warn">This move provokes an opportunity attack.</span>';
    } else if (ui.mode === 'attack') {
      s += '<br>Pick a target on the map. Hit chance is shown on each one.';
    }
    return s;
  }

  function button(text, fn, o) {
    o = o || {};
    const b = document.createElement('button');
    b.type = 'button'; b.className = 'tx-btn' + (o.primary ? ' tx-primary' : ''); b.textContent = text;
    if (o.pressed !== undefined) b.setAttribute('aria-pressed', String(!!o.pressed));
    if (o.title) b.title = o.title;
    b.disabled = !!o.disabled || (ui.busy && !o.always) || !fn;
    if (fn) b.addEventListener('click', fn);
    (o.parent || el.actions).appendChild(b);
    return b;
  }

  function attackChoices() {
    const box = document.createElement('div'); box.className = 'tx-attacks';
    const names = [...new Set(ui.targets.filter(r => kindOf(r.attack) === ui.kind).map(r => r.attack))];
    if (!names.length) {
      const n = document.createElement('span'); n.className = 'tx-info';
      n.textContent = 'No ' + (ui.kind === 'spell' ? 'attack spell' : 'weapon attack') + ' available.';
      box.appendChild(n);
    }
    for (const name of names) {
      const legal = ui.targets.filter(r => r.attack === name && r.legal);
      const best = legal.reduce((m, r) => Math.max(m, r.hit_percent), 0);
      button(name + (legal.length ? ` (up to ${best}%)` : ' (no target)'), () => { ui.attack = name; render(); },
        { pressed: ui.attack === name, disabled: !legal.length, parent: box });
    }
    el.actions.appendChild(box);
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
    return label(Math.floor(q.x / C), Math.floor(q.y / C));
  }

  const inReach = sq => !!ui.reach && ((sq in (ui.reach.walk || {})) || (sq in (ui.reach.dash || {})));

  function onHover(evt) {
    if (ui.mode !== 'move' || !ui.reach || evt.pointerType === 'touch') return;
    const sq = cellAt(evt);
    if (sq === ui.hover) return;
    ui.hover = sq;
    clearTimeout(ui.hoverTimer);
    if (!inReach(sq)) { drawOverlay(); return; }
    ui.hoverTimer = setTimeout(() => previewTo(sq), 90);
  }

  async function previewTo(sq) {
    const t = current(); if (!t) return null;
    if (!ui.preview[sq]) {
      const res = await call('preview', [t.id, sq]);
      if (res.error || !res.result) return null;
      ui.preview[sq] = res.result;
    }
    if (ui.hover === sq) { drawOverlay(); renderSide(); }
    return ui.preview[sq];
  }

  async function onBoardClick(evt) {
    if (ui.busy || ui.mode !== 'move' || !ui.reach) return;
    const t = current(); if (!t) return;
    const sq = cellAt(evt);
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

  function onToken(t) {
    if (ui.busy || !myTurn()) return;
    const me = current();
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
        else if (answer.react) extra.react = answer.react;
        else extra.rolls.push(answer.roll);
        continue;
      }
      if (res.error) toast(res.error, 'error');
      else {                                      // GM hints ("Next: options frog-1") are not for players
        toast(res.text.split('\n').filter(l => !/^(Next:|Then:|Waiting for)/.test(l)).join(' '));
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
    const react = /Opportunity attack\?/.test(message);
    const m = /rolls (\d+)d(\d+)([+-]\d+)?( with (advantage|disadvantage))?/.exec(first);
    return new Promise(resolve => {
      const box = el.prompt; box.hidden = false; box.innerHTML = '';
      const p = document.createElement('div'); p.textContent = first; box.appendChild(p);
      const row = document.createElement('div'); row.className = 'tx-row'; box.appendChild(row);
      const finish = v => { box.hidden = true; box.innerHTML = ''; resolve(v); };
      if (react) {
        button('Yes, attack', () => finish({ react: 'yes' }), { parent: row, primary: true, always: true });
        button('No, let them go', () => finish({ react: 'no' }), { parent: row, always: true });
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
