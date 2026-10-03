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
 *
 * Overlays: fog of war (squares no PC sees, from the snapshot) and, with the
 * Cover button on, cover and line of sight from one selected creature (the
 * engine's `sight` command). Keyboard: the map is one tab stop with a square
 * cursor; arrow keys move it, Enter or Space acts on the square (as a click),
 * Escape cancels, Home jumps to the creature whose turn it is, C toggles cover.
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
               helpTarget: null, readyWhat: null, readyStep: null,
               cursor: null, ruler: { tool: null, a: null, b: null, fixed: false }, camMap: null, camTimer: 0,
               sight: false, sightFrom: null, sightData: null, sightKey: '', layoutAt: null,
                svg: null, boardKey: null,
                // Whether the display is receiving live updates.
                //
                // Starts true, and that is not a claim that the stream is up.
                // It is the absence of a claim that it is down. Only
                // display/static/display.js knows how the stream is doing, so
                // this starts out of its hands and is corrected the moment it
                // is told; on a real page that happens during init, before any
                // snapshot is drawn. The panel also runs on its own, with no
                // stream at all (display/evidence-panel.html, the layout
                // harness), and a panel with no connection cannot have its
                // actions disabled by one.
                online: true };
  const CONDITION_CODES = { blinded: 'Bl', charmed: 'Ch', deafened: 'De', exhaustion: 'Ex', frightened: 'Fr',
    grappled: 'Gr', incapacitated: 'In', invisible: 'Iv', paralyzed: 'Pa', petrified: 'Pe', poisoned: 'Po',
    prone: 'Pr', restrained: 'Re', stunned: 'St', unconscious: 'Un' };
  const el = {};

  // Why the map cannot be acted on, in the words the engine uses everywhere
  // else it refuses something. This is the title on every disabled action, and
  // the banner text below the map, so the reason is never only a colour.
  const OFFLINE_TITLE = 'The display has lost the server. This comes back when it reconnects.';
  const OFFLINE_BANNER = 'The display lost the server. The map below is out of date and cannot be acted on until it reconnects.';

  // ── helpers ──────────────────────────────────────────────────────────────
  const colLabel = x => { let s = ''; x += 1; while (x) { const r = (x - 1) % 26; s = String.fromCharCode(65 + r) + s; x = Math.floor((x - 1) / 26); } return s; };
  const label = (x, y) => colLabel(x) + (y + 1);
  const parseSq = s => { const m = /^([A-Z]+)(\d+)$/.exec(s || ''); if (!m) return null;
    let x = 0; for (const ch of m[1]) x = x * 26 + (ch.charCodeAt(0) - 64); return [x - 1, +m[2] - 1]; };
  // The one escape helper for the display is esc(), at the top of
  // display/static/display.js. That file is a classic script, so esc() is a
  // global, and index.html loads display.js before this one. This file is also
  // loaded on its own by display/evidence-panel.html (the layout harness, which
  // does not load display.js), hence the fallback: the same five characters, the
  // same null handling, so the two definitions cannot drift in behaviour.
  // tests/test_display_xss.py pins that they are equivalent.
  const esc = window.esc || (s => String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;'));
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

  /* Pure helpers: the side table, the frame geometry, the cell rule, the roll
     wording and the board cache key. They touch no DOM and no snapshot, so
     tests/test_display_tactics_ui.py runs them straight out of this file and
     pins the numbers below. */
  const SIDES = {
    enemy: { cls: 'tx-side-enemy', word: 'enemy',   glyph: '⚔', colour: 'var(--tx-danger)' },
    pc:    { cls: 'tx-side-pc',    word: 'ally',    glyph: '♥', colour: 'var(--tx-quan)' },
    other: { cls: 'tx-side-other', word: 'neutral', glyph: '✦', colour: 'var(--tx-lore)' },
  };
  const sideOf = t => SIDES[t.side === 'enemy' ? 'enemy' : t.side === 'pc' ? 'pc' : 'other'];

  // A regular octagon over the box of radius r: eight real corners, so a player
  // who cannot tell one colour from another still knows a notched frame from a
  // round one. It spans the same 2r box as the circle it replaces, so an enemy
  // token is not the bigger target. The colour repeats the shape; it never
  // carries the side alone.
  function octagon(cx, cy, r) {
    const k = r * 0.4142136;                       // tan(22.5°): half the length of a flat side
    const pt = (dx, dy) => `${+(cx + dx).toFixed(2)},${+(cy + dy).toFixed(2)}`;
    return `M${pt(-r, -k)}L${pt(-k, -r)}L${pt(k, -r)}L${pt(r, -k)}` +
           `L${pt(r, k)}L${pt(k, r)}L${pt(-k, r)}L${pt(-r, k)}Z`;
  }

  // The width the panel's phone layout turns at, so the board and the CSS agree
  // on which screen this is.
  const PHONE_MAX_W = 860;
  const TABLE_MIN = 40, TABLE_MAX = 72;            // a table display: read across the room
  const PHONE_MIN = 10;                            // a phone: a square is still a fingertip

  // How many pixels a square is drawn at, given the space it is drawn into.
  // Two rules, because there are two kinds of screen. A phone fits the whole
  // map to its width -- the old fixed 28..40px squares put the right-hand end
  // of a 20-wide map off the screen -- and lets the height follow from the
  // map's own shape. Sizing a phone from the height instead would be circular:
  // the box is as tall as the map we have just drawn into it. A shared table
  // display goes the other way: the 40px floor wins and a big map scrolls
  // rather than shrinking into a squint for whoever is sitting furthest away.
  function boardCell(W, H, availW, availH, phone) {
    const across = Math.max(1, Math.floor(availW / Math.max(1, W)));
    if (phone) return Math.max(PHONE_MIN, Math.min(TABLE_MAX, across));
    // Both: a map that is too tall for the box scrolls as little as possible.
    const both = Math.min(across, Math.max(1, Math.floor(availH / Math.max(1, H))));
    return Math.max(TABLE_MIN, Math.min(TABLE_MAX, both));
  }

  // The chance a roll was made under, as the system worded it.
  //
  // The system owns these words. dnd5e fills Roll.odds in with hit_chance's
  // and save_chance's own answer, and it is deliberately not re-derived here:
  // one number, quoted twice, cannot drift. A system that wants to label its
  // odds "to parry" says so in the label and this formats it, untouched.
  //
  // The percent is the system's own -- hit_chance's chance to hit, save_chance's
  // chance to FAIL -- which is why the label carries the direction and must be
  // read, not assumed. "75% to hit" and "65% to fail the save" are both the
  // number going the player's way, and swapping them is the failure this
  // avoids.
  //
  // Empty is the ordinary answer for a roll with no question attached to it (a
  // damage roll, a plain check), so it returns '' and every caller treats that
  // as "say nothing" rather than as a missing value.
  function oddsText(odds) {
    if (!odds || odds.percent === undefined || odds.percent === null) return '';
    const p = Math.round(Number(odds.percent));
    if (!isFinite(p)) return '';
    const label = (odds.label || '').trim();
    // A system that gave a number but no label still gets its number shown: a
    // bare "65%" is honest, where a trailing space after "65% " is a typo in a
    // sentence. Never "0% ", which would read as a lie about a 65% roll.
    return label ? p + '% ' + label : p + '%';
  }

  // The odds of every roll in one log entry, as a single line.
  //
  // Every roll, never just the first. An entry can carry several (directional
  // cover resolving four saves at once is the densest moment in 5e), and a line
  // that showed one chance out of four would read as the other three being
  // withheld, which is a worse suspicion than the one the number answers.
  // Repeats are collapsed, because one AoE that rolls four saves at 65% is one
  // thing to be told, not four.
  function entryOdds(entry) {
    const out = [];
    for (const r of ((entry && entry.rolls) || [])) {
      const t = oddsText(r && r.odds);
      if (t && out.indexOf(t) === -1) out.push(t);
    }
    return out.join(' · ');
  }
  // The banner for a roll or reaction the engine is waiting on. turn.pending is
  // "roll:<notation>[|<advantage>]", "react:<id>:<what>" or "death_save".
  // Returns '' when there is nothing to wait on. `byId` resolves a token id.
  //
  // Two things this used to get wrong, both of them about naming.
  //
  // The reaction named whoever was acting rather than whoever has to decide.
  // Those are different creatures: while Kobold 2 acts, the engine can be
  // waiting on Kairos to spend a reaction, and "Waiting on Kobold 2: silvery
  // barbs?" asks a kobold to answer for a spell reaction it is the victim of.
  // The key carries the reacting creature's id, so that is who gets named.
  //
  // The roll named the dice and nothing else. "roll 1d20+4" reads as a straight
  // roll, and the player watches for one number, when the engine is holding two
  // and keeping the lower. The advantage rides along in the marker
  // (cli._push_pending) precisely so the wait can say so: it is part of what is
  // being waited on, not a footnote to the result. Without it the only place
  // the disadvantage appeared was the log line after the dice had landed, which
  // is too late to plan around.
  function pendingBanner(p, who, byId) {
    if (!p || p === 'death_save') return '';
    const waiting = who || 'the party';
    if (p.startsWith('roll:')) {
      const bar = p.slice(5);
      const cut = bar.indexOf('|');
      const dice = cut === -1 ? bar : bar.slice(0, cut);
      const adv = cut === -1 ? '' : bar.slice(cut + 1).trim();
      return `Waiting on ${waiting}: roll ${dice}` + (adv ? ` with ${adv}` : '');
    }
    if (p.startsWith('react:')) {
      const parts = p.slice(6).split(':');
      const what = parts[parts.length - 1];
      const decider = parts.length > 1 && byId ? byId(parts[0]) : null;
      return `Waiting on ${decider ? decider.name : waiting}: ${what}? yes/no`;
    }
    return `Waiting on ${waiting}: ${p}`;
  }

  // The two geometry seams for anything drawn or measured on the board. The ruler
  // and new board drawing call only these, so a later hex grid swaps the bodies
  // and leaves the callers alone.
  // Centre of a square in SVG units.
  const cellCentre = (x, y) => ({ px: x * C + C / 2, py: y * C + C / 2 });
  // Feet between two squares (a, b are [x, y]), ignoring terrain. `sq` is the
  // square size in ft (snap.square_ft).
  const distFeet = (a, b, diagonals, sq) => gridDistance(diagonals, sq || 5, a, b);

  // Feet between two squares, ignoring terrain: a port of Grid.distance in
  // scripts/tactics/grid.py, which is what reach, range and opportunity attacks
  // use. `diagonals` is the map's own rule ("5" or "5-10-5", snap.grid.diagonals)
  // and `sq` the square size in feet (snap.square_ft). tests/test_display_ruler.py
  // pins this against the Python answer on sample pairs, so the two cannot drift.
  function gridDistance(diagonals, sq, a, b) {
    const dx = Math.abs(a[0] - b[0]), dy = Math.abs(a[1] - b[1]);
    const diag = Math.min(dx, dy), straight = Math.abs(dx - dy);
    if (diagonals !== '5-10-5') return (diag + straight) * sq;
    return (straight + diag + Math.floor(diag / 2)) * sq;
  }

  // The squares a sphere, cone or line covers, ignoring walls: the shape half of
  // grid.area (same containment rule: a square counts when its centre is inside,
  // boundary included). The engine also drops squares behind walls; a ruler is a
  // measuring aid and says so. Pinned against the Python on an open map.
  // shape: 'circle' (centred on target), 'cone' or 'line' (from caster toward target).
  function templateSquares(shape, sizeFt, sq, caster, target, W, H, widthFt) {
    const EPS = 1e-9, cells = sizeFt / sq, out = [];
    const inb = (x, y) => x >= 0 && y >= 0 && x < W && y < H;
    if (shape === 'circle') {
      const ox = target[0] + 0.5, oy = target[1] + 0.5;
      for (let x = Math.floor(ox - cells); x <= Math.ceil(ox + cells); x++)
        for (let y = Math.floor(oy - cells); y <= Math.ceil(oy + cells); y++)
          if (inb(x, y) && Math.hypot(x + 0.5 - ox, y + 0.5 - oy) <= cells + EPS) out.push([x, y]);
    } else if (shape === 'cone' || shape === 'line') {
      if (caster[0] === target[0] && caster[1] === target[1]) return out;
      const cx = caster[0] + 0.5, cy = caster[1] + 0.5;
      const dx = target[0] + 0.5 - cx, dy = target[1] + 0.5 - cy;
      const scale = 0.5 / Math.max(Math.abs(dx), Math.abs(dy));
      const ox = cx + dx * scale, oy = cy + dy * scale;
      const len = Math.hypot(dx, dy), ux = dx / len, uy = dy / len;
      const halfW = (widthFt || sq) / sq / 2, reach = Math.ceil(cells) + 2;
      for (let x = caster[0] - reach; x <= caster[0] + reach; x++)
        for (let y = caster[1] - reach; y <= caster[1] + reach; y++) {
          if (!inb(x, y)) continue;
          const px = x + 0.5 - ox, py = y + 0.5 - oy;
          const along = px * ux + py * uy, across = Math.abs(px * uy - py * ux);
          if (along <= EPS || along > cells + EPS) continue;
          if (across <= (shape === 'cone' ? along / 2 : halfW) + EPS) out.push([x, y]);
        }
    }
    return out.sort((p, q) => p[1] - q[1] || p[0] - q[0]);
  }

  // Camera memory: one localStorage entry, {mapname: {scrollL, scrollT[, zoom]}}.
  // `zoom` is optional and passed through untouched so a later pan/zoom camera
  // can extend the record without a migration. Newest map last; oldest dropped
  // past CAMERA_MAX so the entry cannot grow without bound.
  const CAMERA_MAX = 40;
  function cameraParse(raw) {
    let o; try { o = JSON.parse(raw); } catch (e) { return {}; }
    if (!o || typeof o !== 'object' || Array.isArray(o)) return {};
    const out = {};
    for (const k of Object.keys(o)) {
      const v = o[k];
      if (!v || !isFinite(v.scrollL) || !isFinite(v.scrollT)) continue;
      out[k] = { scrollL: Math.max(0, +v.scrollL), scrollT: Math.max(0, +v.scrollT) };
      if (isFinite(v.zoom) && +v.zoom > 0) out[k].zoom = +v.zoom;
    }
    return out;
  }
  function cameraPut(store, name, cam) {
    if (!name) return store;
    const next = Object.assign({}, store);
    delete next[name];
    next[name] = cam;
    const keys = Object.keys(next);
    for (const k of keys.slice(0, Math.max(0, keys.length - CAMERA_MAX))) delete next[k];
    return next;
  }

  // The action economy of the creature whose turn it is, as pips. Every value is
  // the engine's own (snap.turn); nothing is worked out here. Spent is told by
  // the glyph and the word as well as the grey, so it survives greyscale.
  function economyPips(turn) {
    const tn = turn || {};
    if (!('action_used' in tn) && !('movement_left' in tn)) return [];
    const ft = Math.max(0, Math.round(Number(tn.movement_left) || 0));
    return [
      { key: 'action', word: 'Action', short: 'A', spent: !!tn.action_used },
      { key: 'bonus', word: 'Bonus action', short: 'B', spent: !!tn.bonus_used },
      { key: 'reaction', word: 'Reaction', short: 'R', spent: tn.reaction === false },
      { key: 'move', word: ft + ' ft of movement', short: ft + ' ft', spent: ft <= 0 },
    ];
  }

  // Why a number is what it is, from the fields the engine returned with it
  // (hit_chance and save_chance, through attack_options and the spell preview).
  // A field the engine did not send produces no chip: nothing is inferred.
  function coverWord(n) {
    return n >= 5 ? 'three-quarters cover +5 AC' : n > 0 ? 'half cover +' + n + ' AC' : '';
  }
  function whyChips(row) {
    const out = [], r = row || {};
    const num = v => typeof v === 'number' && isFinite(v);
    const sg = n => (n < 0 ? '-' : '+') + Math.abs(n);
    if ('hit_percent' in r) {
      if (num(r.attack_bonus)) out.push({ kind: 'num', text: sg(r.attack_bonus) + ' to hit' });
      if (num(r.target_ac)) out.push({ kind: 'num', text: 'AC ' + r.target_ac });
      if (num(r.cover) && r.cover > 0) out.push({ kind: 'cover', text: coverWord(r.cover) });
      if (num(r.need)) out.push({ kind: 'num', text: 'needs ' + r.need + '+ on the d20' });
    } else if ('fail_percent' in r) {
      if (num(r.dc)) out.push({ kind: 'num', text: 'DC ' + r.dc });
      if (num(r.save_bonus)) out.push({ kind: 'num', text: sg(r.save_bonus) + ' to the save' });
      if (num(r.cover) && r.cover > 0) out.push({ kind: 'cover', text: 'cover +' + r.cover + ' (in that bonus)' });
    }
    const adv = r.advantage;
    if (adv && adv !== 'normal') {
      const kind = adv === 'advantage' ? 'adv' : 'dis';
      const glyph = kind === 'adv' ? '\u25B2 ' : '\u25BC ';
      const why = (r.reasons || []).filter(x => typeof x === 'string' && x);
      if (!why.length) out.push({ kind, glyph, text: adv });
      for (const w of why) out.push({ kind, glyph, text: adv + ': ' + w });
    }
    return out;
  }

  // One target's forecast: the chance in the engine's own direction, the expected
  // damage, the chips behind them and whether walking away provokes it.
  function forecast(row) {
    const r = row || {};
    let pct = null, phrase = '';
    if (typeof r.hit_percent === 'number') { pct = r.hit_percent; phrase = pct + '% to hit'; }
    else if (typeof r.fail_percent === 'number') { pct = r.fail_percent; phrase = pct + '% to fail the save'; }
    if (pct === null) return null;
    const exp = typeof r.expected_damage === 'number' ? r.expected_damage
              : typeof r.expected === 'number' ? r.expected : null;
    return { percent: pct, phrase, expected: exp, chips: whyChips(r), provokes: r.provokes === true };
  }

  // ── the board cache key ──────────────────────────────────────────────────
  //
  // The panel used to throw the whole board away and rebuild it on every
  // snapshot: W*H terrain rects, the grid lines, the fog, then
  // `board.innerHTML = ''` and a fresh append. None of that is per-turn, so it
  // is built once and kept, and these two say when it may be kept. Both are
  // pure functions of the snapshot, so the rule can be pinned without a
  // browser.

  // The fog, as one string.
  //
  // Part of the terrain key, not an overlay: it decides which squares are lit,
  // so a cached terrain layer carries its fog with it and a fog change has to
  // rebuild rather than repaint over it.
  //
  // "off" and "on:" are deliberately different. A snapshot with no `fog` at all
  // draws no fog; one with `fog: {runs: []}` draws fog over every square, and a
  // key that collapsed the two would leave the first map's clear board on screen
  // for the second, which is the one answer that may not go stale.
  //
  // The runs are sorted so that the same set of seen squares sent in a
  // different order is the same key. Sorting can only ever cost a rebuild (two
  // shapes of the same set), never buy a wrong reuse.
  function fogKey(sp) {
    const f = sp && sp.fog;
    if (!f) return 'off';
    return 'on:' + (f.runs || []).map(r => r.join(':')).sort().join(',');
  }

  // What the static layers are, exactly. A match means the terrain on screen is
  // the terrain this snapshot asks for; anything else means tear it all down.
  //
  // - the map name and its shape, so a different battle never inherits a grid
  // - the rows themselves: the terrain is drawn from them, and a map edited in
  //   place keeps its name and its W and H
  // - image, and the image_px and grid_align that decide where it is *drawn*:
  //   an alignment the GM has just saved has to rebuild this layer, or the
  //   cached picture stays under the new grid
  // - zones, labels and colors: the rest of what the map contributes to
  //   the static drawing, all from the same map file as the rows
  // - the fog, above
  //
  // Deliberately not hashed to a short digest. A hash is a fixed length whatever
  // it covers and two different maps can collide, and a collision here is one
  // map's terrain drawn over another's: the one failure this cache must not
  // have. The exact string is a few KB on the largest map the display ships and
  // comparing it costs far less than the rects it saves.
  function boardKey(sp, W, H) {
    const meta = (sp && sp.meta) || {}, g = (sp && sp.grid) || {};
    return JSON.stringify([meta.name || '', W, H, g.rows || null, meta.image || '',
                           meta.image_px || null, meta.grid_align || null,
                           meta.zones || null, meta.labels || null, meta.colors || null,
                           fogKey(sp)]);
  }

  /* Pin geometry. Pins arrive from the server already filtered: a pin that is
     not `revealed` is not in the payload at all, because the display has one
     audience and no way to tell a GM from a player. So nothing here is a
     security check -- the allow-list, the visibility rule and the containment
     are all enforced server-side in scripts/pins.py, and a browser cannot reach
     a file it was not sent.

     What is here is layout, and it is here because it is arithmetic on C and
     cell centres, which is exactly what the pure-helpers block exists to pin.
     SPEC-grid-and-map.md 7 asks for the ruler and the pin layer to go through
     one geometry helper, so P3's hex geometry object replaces pinCentre rather
     than leaving a second one behind. */

  // A pin sits in the top-left of its square, not the centre: the centre is
  // where a token is, and a pin drawn under a token is a pin nobody can click.
  //
  // `cell` is a parameter, not the module-level C, so this block stays runnable
  // on its own: the pure-helpers block is extracted by
  // tests/test_display_tactics_ui.py and executed with nothing else in scope,
  // and a helper that reached for C would raise ReferenceError there rather than
  // in a browser. octagon() and boardCell() take their numbers the same way.
  const PIN_INSET = 5, PIN_R = 7;
  const pinCentre = (p, cell) => [p.x * cell + PIN_INSET + PIN_R, p.y * cell + PIN_INSET + PIN_R];

  // Shape carries the kind, not colour. A triangle is a note, a diamond is a
  // map, so a GM does not click one expecting prose and get a fight -- and the
  // distinction survives greyscale and colour blindness, which a hue pair does
  // not. Both are the same 14-unit box, so the board learns one visual language
  // rather than two.
  const pinGlyph = kind => (kind === 'map' ? 'diamond' : 'triangle');

  const pinPath = (p, cell) => {
    const [cx, cy] = pinCentre(p, cell), r = PIN_R;
    if (pinGlyph(p.kind) === 'diamond') {
      return `M${cx},${cy - r} L${cx + r},${cy} L${cx},${cy + r} L${cx - r},${cy} Z`;
    }
    // The triangle points UP rather than down, which is what makes it the same
    // height as the diamond: an upward triangle's apex is at cy-r and its base
    // at cy+r/2, so both shapes occupy r above centre and the pin never grows
    // toward the HP bar and condition badges that live in the square's middle
    // and lower edge. A downward triangle reaches 1.4r down and collides.
    return `M${cx},${cy - r} L${cx + r},${cy + r * 0.5} L${cx - r},${cy + r * 0.5} Z`;
  };

  // Which side a pin's label tucks to, so it runs off the board as little as
  // possible: a pin on the last column draws its label out of the viewBox and
  // half of it is clipped away.
  const pinLabelAnchor = p => {
    const W = ui.W || 0;
    return (p.x + 1) * C > (W * C) / 2 ? 'end' : 'start';
  };

  // How much of a label the board has room for. The board is read across a
  // table, so the cap is deliberately short: a longer one runs over the next
  // square, which may hold a token. Never wrapped -- a wrapped label becomes a
  // block of text that reads as terrain.
  const pinLabel = (p, cell) => {
    const max = Math.max(4, Math.min(18, Math.floor(cell / 2.4)));
    const text = String(p.label == null ? '' : p.label);
    return text.length > max ? text.slice(0, max - 1) + '…' : text;
  };

  // A pin on a square that is not on the board. Dropped rather than clamped:
  // a clamp puts a pin from off-map onto a real square, which reads as a pin
  // somewhere it is not.
  const pinOnBoard = (p, W, H) =>
    Number.isFinite(p.x) && Number.isFinite(p.y) &&
    p.x >= 0 && p.y >= 0 && p.x < W && p.y < H;

  /* Where the map's artwork goes, as the attributes of its <image>.

     The map file records two facts about its picture: how big it is
     (`meta.image_px`) and how many of its pixels one 5 ft square spans, plus
     where the first square starts (`meta.grid_align`, all three validated in
     scripts/tactics/maps.py). With them the art scales by `k = cell / cell_px`
     -- the same `cell` the grid lines are drawn at -- and sits at `-offset * k`,
     so the grid on screen IS the grid the GM lined up against the file. Uniform
     on both axes, and that is the whole point: a picture stretched to fill the
     board instead has its 100px squares land at a different width than height,
     and every distance read off it is subtly wrong.

     With neither, this returns null and the caller falls back to the legacy
     stretch. That branch is not "wrong" -- a map with no recorded size draws
     exactly as it always has, which is what every map written before the size
     was recorded depends on -- so the check is for *usable* numbers rather than
     for their presence. One bad number anywhere takes the whole map back to the
     legacy draw rather than to a NaN attribute.

     `cell` is a parameter rather than the module-level C for the reason
     `pinCentre` takes one: this block is extracted and run under node by
     tests/test_display_tactics_ui.py, where there is no C. */
  /* Which terrain cells have a differently-terrained neighbour, and on which
     sides. An edge, not a stroke-per-rect: outlining every cell draws a box
     grid over uniform ground, which is worse over artwork than no outline at
     all, and a stylesheet cannot ask a cell what its neighbours are.

     Out of bounds counts as different, so a region's outer boundary is drawn
     against the artwork beyond the board as well as against its interior
     divisions. The comparison is by terrain NAME and not by fill: two map-
     specific terrain types can share a colour and are still two terrains, and
     `legend` (terrainOf's lookup) is what decides which a cell is.

     Pure, and in this block, so the geometry is pinned under node like the rest
     of the board's arithmetic. `rows` is the row-string grid and `names` the
     per-cell terrain name grid of the same shape. */
  const terrainEdges = (rows, names) => {
    const H = rows.length, W = H ? rows[0].length : 0;
    const out = [];
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const me = names[y][x];
      const l = (x === 0 || names[y][x - 1] !== me) ? 1 : 0;
      const r = (x === W - 1 || names[y][x + 1] !== me) ? 1 : 0;
      const t = (y === 0 || names[y - 1][x] !== me) ? 1 : 0;
      const b = (y === H - 1 || names[y + 1][x] !== me) ? 1 : 0;
      if (l || r || t || b) out.push({ x, y, l, r, t, b });
    }
    return out;
  };

  /* A difficult-terrain marker sized in FRACTIONS of a cell, so it stays the
     same share of a square at every cell size. The old chevron was fixed at 12
     user units in a 32-unit cell: at the phone minimum of 10px a square that is
     3.75px of marker, indistinguishable from the grid line beside it, and its
     stroke-opacity was .3.

     `unit` is the module's C, passed in for the reason artAttrs takes its
     `cell`: this block is run under node with no C in scope. */
  const difficultMark = (x, y, unit) => {
    const s = unit * 0.5, cx = x * unit + unit / 2, cy = y * unit + unit / 2;
    // An up-chevron about (cx, cy), with the two vertical extremes the same
    // distance either side of cy so the box is centred on the square rather
    // than merely inside it. The old path was a fixed offset inside the cell
    // (`M x*32+9, y*32+23 l6,-10 l6,10`), whose box centred on (15, 18) in a
    // cell whose centre is (16, 16): a marker hanging low and left.
    return `M${(cx - s / 2).toFixed(2)},${(cy + s * 0.45).toFixed(2)} ` +
           `l${(s * 0.5).toFixed(2)},${(-s * 0.9).toFixed(2)} ` +
           `l${(s * 0.5).toFixed(2)},${(s * 0.9).toFixed(2)}`;
  };

  const artAttrs = (art, imagePx, align, W, H, cell) => {
    const legacy = () => ({ x: 0, y: 0, width: W * cell, height: H * cell,
                            preserveAspectRatio: 'none' });
    if (!art || !imagePx || !align || !align.cell_px) return legacy();
    const cellPx = align.cell_px, w = imagePx[0], h = imagePx[1];
    const ox = align.offset_x == null ? 0 : align.offset_x;
    const oy = align.offset_y == null ? 0 : align.offset_y;
    // `typeof` rather than Number(), deliberately: Number("100") is 100, so a
    // pitch that arrived as a string would quietly be accepted and drawn, while
    // maps.py refuses exactly that value. This guard mirrors maps.grid_alignment
    // so the two cannot disagree about what a usable number is -- a browser that
    // is more forgiving than the compiler is how a rejected alignment comes back
    // as a subtly wrong picture instead of a load error.
    const num = v => typeof v === 'number' && isFinite(v);
    if (!(cellPx > 0) || !num(cellPx) || !num(w) || !num(h) ||
        !(w > 0) || !(h > 0) || !num(ox) || !num(oy)) return legacy();
    const k = cell / cellPx;
    return { x: -ox * k, y: -oy * k, width: w * k, height: h * k, cropped: true };
  };
  /* end pure helpers */

  // Nudge a text element back inside the board, horizontally and vertically.
  //
  // The board is an SVG with a viewBox, so anything past its edge is not drawn
  // at all rather than clipped or wrapped. A float wider than a cell therefore
  // loses its ends silently: "40% to fail the save" over a token in the first
  // column renders as nothing, which is a number going missing for no visible
  // reason. Measured after the text is set, because the width is only known
  // then, and moved on the axis it is actually off on.
  //
  // The x/y attributes, NOT a transform: the rise animation is a CSS
  // `transform`, and a CSS transform wins over the SVG presentation attribute
  // of the same name, so setting one silently moves nothing. That is exactly
  // how this first went in broken and looked correct in the diff.
  //
  // The damage float does not need any of this: it is one or two glyphs and
  // always fits over a cell. This is here for the odds, which are a phrase.
  function keepOnBoard(node, w, h) {
    let b;
    try { b = node.getBBox(); } catch (e) { return; }   // not laid out yet, or detached
    if (!b || !b.width) return;
    let dx = 0, dy = 0;
    if (b.x < 0) dx = -b.x + 2; else if (b.x + b.width > w) dx = w - b.x - b.width - 2;
    if (b.y < 0) dy = -b.y + 2; else if (b.y + b.height > h) dy = h - b.y - b.height - 2;
    if (!dx && !dy) return;
    node.setAttribute('x', +node.getAttribute('x') + dx);
    node.setAttribute('y', +node.getAttribute('y') + dy);
  }

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
      const body = await r.json().catch(() => null);
      if (r.ok) return body || { ok: true };
      return { error: refusal(r.status, body), status: r.status };
    } catch (e) {
      return { error: 'The display could not reach the engine. Is the server still running?' };
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
      '<button class="tx-btn" id="tx-cover" type="button" aria-pressed="false" title="Shade cover and line of sight from a creature (C)">Cover</button>' +
      '<span class="tx-rulers" role="group" aria-label="Measure">' +
      '<button class="tx-btn tx-small" data-ruler="line" type="button" aria-pressed="false" title="Measure a distance: tap two squares">Ruler</button>' +
      '<button class="tx-btn tx-small" data-ruler="cone" type="button" aria-pressed="false" title="Cone template from a square toward another">Cone</button>' +
      '<button class="tx-btn tx-small" data-ruler="circle" type="button" aria-pressed="false" title="Circle template: centre, then edge">Circle</button>' +
      '<span id="tx-ruler-out" class="tx-ruler-out" role="status" aria-live="polite"></span></span>' +
      '<button class="tx-btn" id="tx-min" type="button" aria-expanded="true">Hide map</button></header>' +
      '<div id="tx-strip" class="tx-strip" role="list" aria-label="Initiative order"></div>' +
      '<div class="tx-body"><div id="tx-board" class="tx-board" tabindex="0" role="application" aria-roledescription="battle map"' +
      ' aria-label="Battle map" aria-describedby="tx-keys"></div>' +
      '<p id="tx-keys" class="tx-sr">Arrow keys move the square cursor. Enter or Space acts on the square, as a click. ' +
      'Escape cancels. Home goes to the creature whose turn it is. C shades cover.</p>' +
      '<div id="tx-say" class="tx-sr" aria-live="polite"></div>' +
      '<div class="tx-side"><div id="tx-info" class="tx-info" aria-live="polite"></div>' +
      '<div id="tx-forecast" class="tx-forecast-slot"></div>' +
      '<div id="tx-actions" class="tx-actions" role="toolbar" aria-label="Actions">' +
      '<div id="tx-leads" class="tx-leads"></div></div>' +
      '<div id="tx-prompt" class="tx-prompt" hidden></div>' +
      '<ol id="tx-log" class="tx-log" aria-label="Combat log"></ol></div></div>' +
      '<div id="tx-toast" class="tx-toast" role="status" hidden></div>';
    document.body.appendChild(p);
    for (const id of ['map', 'round', 'banner', 'cover', 'min', 'strip', 'board', 'info', 'actions',
                      'leads', 'prompt', 'log', 'toast', 'say', 'forecast'])
      el[id] = document.getElementById('tx-' + id);
    el.panel = p;
    el.min.addEventListener('click', () => {
      const min = document.body.classList.toggle('tx-min');
      el.min.textContent = min ? 'Show map' : 'Hide map';
      el.min.setAttribute('aria-expanded', String(!min));
      publishPanelExtent();          // folding changes the panel's height at once
    });
    document.addEventListener('keydown', e => {
      if (e.key !== 'Escape') return;
      // On the document, not on the board and not on the action bar, so Escape
      // is one key from anywhere: the map has the keyboard after a Tab and the
      // side list after another, and a player who armed a mode with the mouse is
      // not holding the keyboard at all.
      //
      // The engine's own question comes first. ask() puts the keyboard inside
      // itself and is the topmost thing on screen, so it is the thing Escape has
      // to answer; without this the prompt is the one thing in the panel a
      // keyboard can only get out of by finding its own Cancel button.
      if (el.prompt && !el.prompt.hidden && el.prompt.onEscape) { el.prompt.onEscape(); return; }
      // An open note next, and before ui.mode for the reason the chain above
      // already gives twice: Escape answers the topmost thing on screen. If the
      // note were below the mode check, closing a note the GM was reading would
      // also silently cancel a player's in-progress Move, with nothing on
      // screen to say why.
      if (ui.note) { closeNotePanel(); return; }
      if (ui.ruler.tool) { setRuler(null); return; }
      // render() redraws the bar and puts the keyboard back (restoreFocus), so
      // cancelling a mode does not also cost the player their place in it.
      if (ui.mode) { clearMode(); render(); }
    });
    el.cover.addEventListener('click', toggleSight);
    watchPanel();
    for (const b of p.querySelectorAll('[data-ruler]')) b.addEventListener('click', () => setRuler(b.dataset.ruler));
    el.rulerOut = document.getElementById('tx-ruler-out');
    el.board.addEventListener('scroll', saveCameraSoon, { passive: true });
    el.board.addEventListener('keydown', onBoardKey);
    el.board.addEventListener('focus', () => { if (!ui.cursor) placeCursor(homeSquare()); else say(describeSquare(ui.cursor)); drawCursor(); });
    el.board.addEventListener('blur', drawCursor);
    try { ui.sight = localStorage.getItem('tx-cover') === '1'; } catch (e) { /* storage blocked */ }
    el.cover.setAttribute('aria-pressed', String(ui.sight));
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
    // Always transient, refusals included. A toast is a duplicate of the banner
    // for a refusal and of the log for a roll, and a duplicate that outlives its
    // twin is the thing that made a stale error look current.
    ui.toastTimer = setTimeout(() => { el.toast.hidden = true; },
                               kind === 'error' ? 6000 : 4500);
  }

  // A refusal, in the one place that is always visible.
  //
  // The toast sits at the bottom of the panel, which is where the combat log
  // and the action bar are: it covered the log line that says the same thing
  // more fully, and on a phone it covered the action bar outright. The banner
  // is above the map with nothing under it, so that is where a refusal goes,
  // and it is announced: it arrives without a page load and it is the answer to
  // a click the player just made.
  function refuse(message) {
    el.banner.textContent = message;
    el.banner.classList.add('tx-refusal');
    el.banner.setAttribute('role', 'alert');
    flash();
  }

  // Clear the refusal when the next snapshot is genuinely a new state. A push
  // that is only a turn's own progress keeps it up until the player acts, since
  // nothing they did was the cause and the banner is still the answer.
  function clearRefusal() {
    if (!el.banner.classList.contains('tx-refusal')) return;
    el.banner.classList.remove('tx-refusal');
    el.banner.setAttribute('role', 'status');
  }

  function clearMode() {
    ui.mode = ui.kind = ui.reach = ui.targets = ui.attack = ui.hover = ui.armed = null;
    ui.spell = ui.singles = ui.darts = ui.helpTarget = ui.readyWhat = ui.readyStep = null;
    ui.dartsText = '';
    ui.preview = {};
    clearTimeout(ui.hoverTimer);
  }

  // Why a refusal happened, in the reader's terms, before the engine's.
  //
  // "It is not a player's turn." arrives while the banner above the map is
  // saying "Your turn, Kairos", which reads as the display being wrong rather
  // than the click being refused, and the two were both true: the map's own
  // snapshot had a player's turn open and the engine had moved on. A 409 while
  // the display believes it is the player's turn is exactly that race.
  //
  // The status code is the honest signal here, not the message: the server
  // already distinguishes "no campaign", "device not approved" and "not your
  // turn", and each of those needs a different thing said about it. A message
  // the display cannot act on is shown, not paraphrased into a guess.
  function refusal(status, body) {
    const why = (body && body.error) || '';
    if (status === 404 || /no (active )?campaign/i.test(why)) {
      return 'No fight is open for this display. Start one in the terminal ' +
             '(combat start), then reload. Nothing was sent.';
    }
    if (status === 403 && /not approved|device/i.test(why)) {
      return 'This device is not approved to act. The GM has to approve it in ' +
             'the terminal (devices approve) before the map buttons do anything.';
    }
    if (status === 409) {
      // The server already names the creature acting when there is one, and that
      // is the sentence a player needs: "it is Kobold 2's turn" is the reason,
      // and the old generic wording was read as the display disagreeing with
      // itself. Kept verbatim, with only "Nothing was sent" appended when the
      // server did not say it, so nothing the engine said is ever lost.
      const turns = /\b([A-Z][\w' -]*?)'s turn\b/.exec(why);
      if (turns) {
        return /nothing was sent/i.test(why) ? why
          : why.replace(/\s*$/, '') + ' Nothing was sent.';
      }
      if (/only .* can act now/i.test(why)) {
        const m = /only (.*?) can act now/i.exec(why);
        return `${m[1]}'s turn now, not yours. Nothing was sent; wait for your turn.`;
      }
      return 'It is not your turn: the engine has moved on since this map was ' +
             'drawn. Nothing was sent; wait for the banner to say your turn.';
    }
    if (status === 429) return why;
    return why || ('The engine refused that (HTTP ' + status + '). Nothing was sent.');
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
      // A new turn is new state, so any standing refusal is stale: the engine
      // has moved, which is often the very thing the refusal was about.
      clearRefusal();
      el.banner.textContent = t ? (t.controller === 'player' ? 'Your turn, ' + t.name : t.name + "'s turn") : snap.unseen_turn ? 'Enemy turn' : '';
      flash();
      if (t && t.controller === 'player') ui.sightFrom = t.id;
    }
    if (ui.sight) loadSight();
    render();
    animate();
    announceRolls();
    publishPanelExtent();
  }

  function hide() {
    el.panel.hidden = true;
    document.body.classList.remove('tx-on', 'tx-min');
    el.toast.hidden = true;
    el.banner.classList.remove('tx-refusal');
    clearMode();
    publishPanelExtent();
  }
  function flash() { el.banner.classList.remove('tx-flash'); void el.banner.offsetWidth; el.banner.classList.add('tx-flash'); }

  function announceRolls() {
    const last = (snap.log || []).slice(-1)[0];
    const before = prev && (prev.log || []).slice(-1)[0];
    if (!last || !(last.rolls || []).length) return;
    if (before && before.text === last.text && before.round === last.round) return;
    toast(last.rolls.map(r => {
      // Show the natural on d20 rolls (crits/fumbles were invisible in the toast).
      const isD20 = /d20/.test(r.notation || '');
      let shown = `${r.label}: ${r.notation}`;
      if (r.dice.length > 1 && r.advantage !== 'normal') shown += ' [' + r.dice.join(', ') + ']';
      else if (isD20 && r.natural !== undefined && r.natural !== null) shown += ` [${r.natural}]`;
      shown += ` = ${r.total}`;
      // The chance this was, in the system's own words. A player doubts a roll
      // AFTER seeing the 3; the pre-action preview badge quoted the same number
      // and has scrolled out of the toast by then. Roll.odds is the preview's
      // own answer carried onto the roll, so the two cannot disagree.
      const odds = oddsText(r.odds);
      if (odds) shown += ` (${odds})`;
      if (isD20 && r.natural === 20) shown += ' — CRIT';
      else if (isD20 && r.natural === 1) shown += ' — fumble';
      if (r.source !== 'engine') shown += ` (${r.source})`;
      return shown;
    }).join(' · '));
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
      const tg = tags(t), side = sideOf(t);
      const ac = (t.ac === undefined || t.ac === null) ? '?' : t.ac;
      const pips = slotPips(t);
      const c = document.createElement('div');
      // The chip carries its side in a stripe and a glyph as well as in colour:
      // the strip is the only place the whole table is on screen at once.
      c.className = 'tx-chip ' + side.cls + (id === snap.current ? ' tx-now' : '') + (t.dead ? ' tx-dead' : '');
      c.setAttribute('role', 'listitem');
      if (id === snap.current) c.setAttribute('aria-current', 'true');
      if (tg.length) c.title = tg.join(', ');
      // side.glyph and side.word are the constants in the SIDES table, so they
      // need nothing. ac, hp and max_hp come from the snapshot, and /combat
      // stores the posted dict as-is without validating it, so they are escaped
      // here: hp and max_hp sit inside an aria-label attribute, where a quote
      // would close it and start a tag.
      c.innerHTML = `<span class="tx-chip-top"><i class="tx-side-glyph" aria-hidden="true">${side.glyph}</i>` +
        `<span class="tx-sr">${side.word}.</span><span class="tx-chip-name">${esc(t.name)}</span>` +
        `<span class="tx-ac" title="Armor Class">AC ${esc(ac)}</span></span>` +
        (id === snap.current ? econPips() : '') +
        `<span class="tx-hpbar" role="img" aria-label="${esc(t.hp)} of ${esc(t.max_hp)} HP"><i class="${pct <= 25 ? 'tx-low' : ''}" style="width:${pct}%"></i></span>` +
        `<span>${t.dead ? 'dead' : esc(t.hp + '/' + t.max_hp) + ' HP'}${tg.length ? ' · ' + esc(tg.join(', ')) : ''}</span>${pips}`;
      el.strip.appendChild(c);
    }
  }

  // Action, bonus action, reaction and movement left on the chip of whoever's
  // turn it is. Filled and "ready" until spent, then hollow, struck through and
  // "used": the state is in the glyph and the word, and the grey only repeats it.
  function econPips() {
    const pips = economyPips(snap.turn);
    if (!pips.length) return '';
    const bits = pips.map(p => `<span class="tx-pip${p.spent ? ' tx-spent' : ''}" title="${esc(p.word)}: ${p.spent ? 'used' : 'ready'}">` +
      `<i aria-hidden="true">${p.spent ? '\u25CB' : '\u25CF'}</i>${esc(p.short)}` +
      `<span class="tx-sr"> ${esc(p.word)} ${p.spent ? 'used' : 'ready'}.</span></span>`);
    return `<div class="tx-econ" role="group" aria-label="Action economy">${bits.join('')}</div>`;
  }

  function slotPips(t) {
    // Spell slots left per level, e.g. "1st ●●": filled = left, hollow = spent.
    const sl = t.slots || {};
    const bits = [];
    for (const lv of Object.keys(sl).map(Number).sort((a, b) => a - b)) {
      const s = sl[String(lv)]; if (!s || !s.total) continue;
      const left = Math.max(0, s.total - (s.used || 0));
      const ord = lv === 1 ? 'st' : lv === 2 ? 'nd' : lv === 3 ? 'rd' : 'th';
      bits.push(`${lv}${ord} ${'●'.repeat(left)}${'○'.repeat(s.total - left)}`);
    }
    return bits.length ? `<span class="tx-slots" title="Spell slots left">${bits.join(' · ')}</span>` : '';
  }

  function terrainOf(ch) {
    const g = snap.grid || {};
    return (g.legend && g.legend[ch]) || LEGEND[ch] || 'floor';
  }
  function fillFor(name) {
    const base = BUILTIN.includes(name) ? name : (((snap.meta || {}).colors || {})[name] || 'floor');
    return 'var(--tx-' + base + ')';
  }

  // The width the side column costs the board, the gap between them, and the
  // panel's own horizontal padding. Named here and mirrored by the rules in
  // tactics.css; a container query would read these straight off the box, but
  // the split is decided before the map is measured, so the arithmetic is
  // spelled out instead.
  const SIDE_COL = 250, BODY_GAP = 10, PANEL_PAD_X = 12;

  // Whether the side column sits beside the board or under it.
  //
  // It used to be a viewport breakpoint: two columns from 1101px, one below.
  // That is the wrong question. The split is worth having only when the board
  // can still hold the whole map at a square a player can read from across the
  // room; below that it takes 260px the board needed and the map is clipped
  // instead, which is how a 12x9 grid lost its right-hand two columns at
  // 1200px while fitting whole at 768px -- a wider screen with less of the
  // fight on it. So it is measured: the board gets the whole panel when the
  // two-column split would clip the map.
  //
  // Never the reverse: a map too wide for the panel still scrolls. That is the
  // table-display rule (boardCell's floor), and stacking would not make it fit.
  function chooseLayout(W) {
    // Measured from the panel, not the board: the board's own width is only
    // meaningful once the split is decided, so reading it here would be circular
    // and the two layouts would each keep re-deciding for the other.
    const inner = el.panel.clientWidth - PANEL_PAD_X * 2;
    // Held for as long as the panel is this wide. A decision taken from the
    // current class name flips back on the next render and oscillates: stacked
    // measures as fitting, which un-stacks it, which then measures as not
    // fitting. Re-deciding only when the width itself changes is what makes it
    // settle.
    if (ui.layoutAt === inner) return el.panel.classList.contains('tx-stacked') ? 'stack' : 'side';
    // The floor, not a guess: a square narrower than TABLE_MIN is the squint the
    // table-display rule exists to prevent, so if the split cannot buy it the
    // split has to go.
    const sideBySide = inner - SIDE_COL - BODY_GAP >= W * TABLE_MIN;
    el.panel.classList.toggle('tx-stacked', !sideBySide);
    ui.layoutAt = inner;
    return sideBySide ? 'side' : 'stack';
  }

  // ── the board cache ───────────────────────────────────────────────────────
  //
  // The board used to be thrown away and rebuilt on every push: W*H terrain
  // rects, the grid lines, the fog, then `board.innerHTML = ''` and a fresh
  // append. A snapshot arrives on every action and on every hover-driven
  // preview, so on a 40x30 map that was 1200 rects and 70 lines created and
  // discarded to show one token that had moved.
  //
  // None of that is per-turn. The ground under a fight does not change while
  // the fight does, so the svg, the terrain, the artwork, the grid, the labels
  // and the fog are built once and kept, and only the layers the player is
  // actually changing are emptied and redrawn. boardKey and fogKey, which say
  // when, are pure and live in the pure block above; this is where the answer
  // is used.

  function renderBoard() {
    const rows = (snap.grid && snap.grid.rows) || [];
    const H = rows.length, W = H ? rows[0].length : 0;
    ui.W = W; ui.H = H;
    ui.layout = chooseLayout(W);
    const box = boardBox();
    const cell = boardCell(W, H, box.w, box.h, box.phone);
    // Reuse is only safe while the cached svg is still the one being shown.
    // `ui.svg.parentNode === el.board` is the check that matters: it survives a
    // panel rebuilt underneath us, a board emptied by anything else, and a map
    // that has changed, none of which can leave the key equal and the node
    // somewhere nobody is looking.
    const key = boardKey(snap, W, H);
    const reuse = key === ui.boardKey && ui.svg && ui.svg.parentNode === el.board;
    let keepL = el.board.scrollLeft, keepT = el.board.scrollTop;
    // A map that has just mounted (page load, or a new battle) opens where the
    // table last left it; a re-render of the same map keeps the live scroll.
    const camName = cameraName(), fresh = camName !== ui.camMap;
    const saved = fresh ? cameraLoad()[camName] : null;
    if (saved) { keepL = saved.scrollL; keepT = saved.scrollT; }
    ui.camMap = camName;
    const s = reuse ? ui.svg : buildBoard(W, H, key);
    // The svg is sized to the box it is drawn into, not to the map, so a window
    // resize or a phone reflow reuses the terrain and still refits the board.
    s.setAttribute('width', W * cell);
    s.setAttribute('height', H * cell);
    s.classList.toggle('tx-aiming', ui.mode === 'aim');
    clearLayers();
    drawSight();
    for (const t of snap.tokens || []) drawToken(t);
    drawOverlay();
    drawPins();
    drawCursor();
    // Only a fresh svg has just had the scroll knocked out of it by the
    // innerHTML wipe; a reused one was never detached, so its scroll still is
    // where the player put it.
    if (!reuse) { el.board.scrollLeft = keepL; el.board.scrollTop = keepT; }
    drawRuler();
    // First look at a map, with no camera to return to: show the whole fight,
    // not the square the hero happens to be standing on. Anything else and a
    // wide map opens on one corner with every enemy off-screen, which is the
    // first thing a player sees and the one they have to notice is incomplete.
    // A re-render of the map already on screen only nudges the view when the
    // acting token has genuinely gone out of it, so a player who scrolled
    // somewhere on purpose keeps that view.
    if (fresh && !saved) frameEncounter(cell);
    else ensureActorVisible(cell);
    saveCameraSoon();
    floaters();
  }

  // The layers emptied on every redraw: the three whose draw calls only add.
  //
  // drawToken appends a token and its portrait clip and never removes either,
  // and a floating number leaves on its own 1400ms timer, long after the push
  // that put it there. Everything else clears the layer it owns before drawing
  // (drawSight, drawOverlay with the mark layer, drawCursor, drawRuler), so
  // that rule is not applied to it twice.
  function clearLayers() {
    for (const layer of [ui.defsLayer, ui.tokenLayer, ui.floatLayer]) {
      if (layer) layer.innerHTML = '';
    }
  }

  // The svg and the part of it a map does not change: the hatch patterns, the
  // artwork, the terrain, the fog, the grid, the zones and the labels. Built
  // once per key and kept until the key stops matching.
  //
  // The z-order of everything below is load-bearing and unchanged: the grid and
  // the labels sit over the fog, and the armed-mode overlay and the tokens sit
  // over the grid.
  function buildBoard(W, H, key) {
    const s = svg('svg', { viewBox: `0 0 ${W * C} ${H * C}`, width: W * C, height: H * C,
                           role: 'group', 'aria-label': `Battle map, ${W} by ${H} squares` });
    const hatch = svg('pattern', { id: 'tx-hatch', width: 6, height: 6, patternUnits: 'userSpaceOnUse',
                                   patternTransform: 'rotate(45)' }, svg('defs', {}, s));
    svg('line', { x1: 0, y1: 0, x2: 0, y2: 6, class: 'tx-hatch-line' }, hatch);
    const fogHatch = svg('pattern', { id: 'tx-fog-hatch', width: 8, height: 8, patternUnits: 'userSpaceOnUse',
                                      patternTransform: 'rotate(-45)' }, s.querySelector('defs'));
    svg('line', { x1: 0, y1: 0, x2: 0, y2: 8, class: 'tx-fog-line' }, fogHatch);
    // Enemy reach: red, leaning the other way from the cover hatch so the two read apart.
    const threatHatch = svg('pattern', { id: 'tx-threat-hatch', width: 7, height: 7, patternUnits: 'userSpaceOnUse',
                                         patternTransform: 'rotate(-45)' }, s.querySelector('defs'));
    svg('line', { x1: 0, y1: 0, x2: 0, y2: 7, class: 'tx-threat-line' }, threatHatch);
    // Map artwork, when the map has it (scripts/art_import.py and
    // scripts/atlas_to_map.py write one). Two ways to draw it, and which one is in
    // force is the map's decision rather than the browser's: artAttrs scales the
    // picture by C / cell_px and offsets it by the recorded origin when the map
    // recorded both its size and its pitch, so the grid lines drawn below sit on
    // the squares the GM lined up against the file; without them the picture is
    // stretched to the board, which is what every map written before the size was
    // recorded has always done.
    const art = (snap.meta && snap.meta.image) || '';
    if (art) {
      const aligned = artAttrs(art, snap.meta.image_px, snap.meta.grid_align, W, H, C);
      const img = svg('image', Object.assign({ href: '/maps/' + art, class: 'tx-art' }, aligned),
                      s);
      if (aligned.cropped) {
        // Aligned art is cropped to the board, not fitted to it. A picture whose
        // pixels do not divide by its own cell size has leftovers, and stretching
        // them into the last square is how the grid ends up drifting at the far
        // edge of a long map. The clip lives in this board's own defs, which are
        // rebuilt with the board, so the id cannot outlive what references it.
        const clip = svg('clipPath', { id: 'tx-art-clip' }, s.querySelector('defs'));
        svg('rect', { x: 0, y: 0, width: W * C, height: H * C }, clip);
        img.setAttribute('clip-path', 'url(#tx-art-clip)');
      }
    }
    // tx-terrain names the group in its own right, not just as the stylesheet's
    // hook for the over-art case: it is the layer the board cache keeps, so a
    // test can hold a reference to it and ask whether it is still the same one.
    //
    // With artwork underneath, terrain is a translucent wash rather than an opaque
    // fill, so the terrain still has to read but the picture is the point. A map
    // with no image is unchanged, which is what every existing map expects.
    const terrain = svg('g', { class: 'tx-terrain' + (art ? ' tx-terrain-over-art' : '') }, s);
    const rows = (snap.grid && snap.grid.rows) || [];
    // The per-cell terrain names, built once: the edge pass compares neighbours
    // by name and terrainOf is a two-step lookup, and this is W*H of them.
    const names = [];
    for (let y = 0; y < H; y++) {
      const line = [];
      for (let x = 0; x < W; x++) line.push(terrainOf(rows[y][x]));
      names.push(line);
    }
    // Where two DIFFERENT terrains meet. Over artwork this is what has to read,
    // and it is drawn only there: a stroke per cell would outline uniform ground
    // as a box grid. The fill alone cannot do it at any opacity (see
    // .tx-terrain-over-art in tactics.css for the measurement), so the boundary
    // is a separate channel from the colour.
    const edges = new Map();
    if (art) for (const e of terrainEdges(rows, names)) edges.set(e.y * W + e.x, e);
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++) {
      const name = names[y][x];
      // data-t is what the stylesheet keys on to keep walls and voids solid over
      // artwork; a selector on the inline style string would be brittle.
      const attrs = { x: x * C, y: y * C, width: C, height: C,
                      style: 'fill:' + fillFor(name), 'data-t': name };
      const e = edges.get(y * W + x);
      if (e) {
        // The sides that face a different terrain or the board edge, so a
        // region's outline follows its actual shape rather than every cell in
        // it. `all` when that is all four.
        const sides = (e.l ? 'l' : '') + (e.r ? 'r' : '') +
                      (e.t ? 't' : '') + (e.b ? 'b' : '');
        attrs['data-edge'] = sides.length === 4 ? 'all' : sides;
      }
      svg('rect', attrs, terrain);
      // The difficult-terrain marker, drawn whether or not there is artwork: it
      // was before this change and the artless case reads on its own opaque
      // fill. Only its weight differs between the two, and that is CSS.
      if (name === 'difficult')
        svg('path', { d: difficultMark(x, y, C), class: 'tx-difficult-mark' }, terrain);
    }
    drawFog(svg('g', { 'aria-hidden': 'true' }, s), W, H);
    ui.sightLayer = svg('g', { 'aria-hidden': 'true' }, s);
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
    // Its own layer, above the overlay and below the tokens, and that is load
    // bearing twice over.
    //
    // Not ui.overlay: drawOverlay() does `o.innerHTML = ''` and re-runs from
    // hoverSquare() on every pointermove, so pins drawn there would be destroyed
    // about sixty times a second while a mouse moved over the map.
    //
    // Below ui.tokenLayer: a token standing on a pinned square wins. That is the
    // panel's existing doctrine (a token's silhouette must never be occluded,
    // and test_the_frame_is_drawn_after_whatever_is_inside_it pins it), so a pin
    // is a marker for an empty square, not a thing that competes for attention.
    // It is pinned in the top-left corner of its square, not the centre, which is
    // where a token is.
    //
    // aria-hidden because the whole board svg is aria-hidden (buildBoard sets it,
    // "the board itself speaks: see describe()"). ARIA on a pin would be
    // decorative; the keyboard route is the board cursor, which is why
    // describeSquare() has to learn about pins separately.
    ui.pinLayer = svg('g', { 'aria-hidden': 'true', class: 'tx-pin-layer' }, s);
    // Portrait clips live in defs, not in the token layer: they are referenced
    // by url(#id) and never drawn themselves, and keeping them out of the layer
    // means nothing can mistake one for content. Rebuilt with the tokens on every
    // redraw (clearLayers), so a clip cannot outlive the token that named it.
    ui.defsLayer = svg('defs', {}, s);
    ui.tokenLayer = svg('g', {}, s);
    ui.markLayer = svg('g', { 'aria-hidden': 'true' }, s);
    ui.rulerLayer = svg('g', { 'aria-hidden': 'true', class: 'tx-ruler-layer' }, s);
    ui.floatLayer = svg('g', {}, s);
    ui.cursorLayer = svg('g', { 'aria-hidden': 'true' }, s);
    ui.svg = s;
    ui.boardKey = key;
    // Listeners go on once, with the svg. Re-adding them on every render would
    // stack a second pointermove and a second click handler on the same node,
    // and one click on a token would send two actions.
    s.addEventListener('pointermove', onHover);
    s.addEventListener('click', onBoardClick);
    s.setAttribute('aria-hidden', 'true');          // the board itself speaks: see describe()
    s.addEventListener('pointerleave', () => {
      if ((ui.mode === 'move' || ui.mode === 'aim') && ui.armed !== ui.hover) { ui.hover = null; drawOverlay(); renderInfo(); }
      else if (ui.mode === 'attack' || ui.mode === 'spell') { ui.hover = null; renderInfo(); }
    });
    el.board.innerHTML = ''; el.board.appendChild(s);
    return s;
  }

  const isPhone = () => !!(window.matchMedia && matchMedia(`(max-width: ${PHONE_MAX_W}px)`).matches);

  // ── the panel's own geometry, published to the stylesheet ────────────────
  //
  // The panel is an overlay, and the reading column has to start below it. How
  // much room it takes is decided by what is in it (the board, the spell list,
  // the log, a header that wrapped), so the inset cannot be a constant in the
  // stylesheet: a fixed guess was 25px short of the panel at 1200x784 and 116px
  // short at 1024x768, which is how the opening lines of every scene ended up
  // under the board. Measured here and read back as --tx-bottom.
  //
  // A ResizeObserver rather than a call after every render, because the panel
  // changes height without tactics.js being involved: the header re-wraps when
  // the banner text changes, "Hide map" folds it, the window resizes.
  function publishPanelExtent() {
    if (!el.panel || el.panel.hidden) { document.body.style.removeProperty('--tx-bottom'); return; }
    document.body.style.setProperty('--tx-bottom', Math.round(el.panel.getBoundingClientRect().bottom) + 'px');
  }
  function watchPanel() {
    if (!el.panel || !window.ResizeObserver) { publishPanelExtent(); return; }
    new ResizeObserver(publishPanelExtent).observe(el.panel);
  }

  // The space the board is drawn into. On a table display the height comes
  // from the board's CSS box, not from the map inside it, so a short map does
  // not shrink its own squares on every re-render. On a phone the width is
  // what matters and the height follows the map.
  function boardBox() {
    const st = getComputedStyle(el.board);
    return {
      w: Math.max(240, el.board.clientWidth - 4),
      h: Math.max(160, parseFloat(st.maxHeight) || el.board.clientHeight) - 4,
      phone: isPhone(),
    };
  }

  // A map larger than the box scrolls. The scroll position is a choice the
  // player made, so it is only ever moved to make something visible that is
  // not: here, the acting token, and only when it has actually left the box.
  // Framing around the acting token on every render is what threw the player's
  // scroll away after each push, and a token already inside the box is exactly
  // the case that used to trigger it.
  function ensureActorVisible(cell) {
    const t = (snap.tokens || []).find(k => k.id === snap.current);
    const b = el.board;
    if (!t || !b.clientWidth || !b.clientHeight) return;
    const x0 = t.x * cell, y0 = t.y * cell, left = b.scrollLeft, top = b.scrollTop;
    // Already wholly inside the box: leave it alone. This is the whole point of
    // the function, and the old version got it wrong in a way that lost the
    // player's scroll on every push: it compared the token against a padded
    // edge, so a hero standing in the first two columns always looked "out of
    // view" however far right the player had scrolled, and the board was sent
    // back to the left edge to fix it.
    if (x0 >= left && x0 + cell <= left + b.clientWidth &&
        y0 >= top && y0 + cell <= top + b.clientHeight) return;
    const pad = cell;
    if (x0 < left) b.scrollLeft = Math.max(0, x0 - pad);
    else if (x0 + cell > left + b.clientWidth) b.scrollLeft = x0 + cell + pad - b.clientWidth;
    if (y0 < top) b.scrollTop = Math.max(0, y0 - pad);
    else if (y0 + cell > top + b.clientHeight) b.scrollTop = y0 + cell + pad - b.clientHeight;
  }

  // Every living token in the box, with a square of slack around them.
  //
  // The first view of a map has to answer "where is everyone" without a scroll.
  // Centring the acting token, which is what this used to do, put the party in
  // the middle of an empty floor at 1200px wide and left both kobolds past the
  // right edge of the board. When the fight is wider than the box there is no
  // framing that shows all of it, so the hero stays put: a fight you cannot see
  // all of should open on the piece you control, and the board scrolls the rest.
  function frameEncounter(cell) {
    const alive = living();
    const b = el.board;
    if (!alive.length || !b.clientWidth || !b.clientHeight) return;
    const xs = alive.map(t => t.x), ys = alive.map(t => t.y);
    const left = Math.min(...xs) * cell, top = Math.min(...ys) * cell;
    const right = (Math.max(...xs) + 1) * cell, bottom = (Math.max(...ys) + 1) * cell;
    const pad = cell;
    if (right - left + pad * 2 <= b.clientWidth && bottom - top + pad * 2 <= b.clientHeight) {
      b.scrollLeft = Math.max(0, left - pad - (b.clientWidth - (right - left + pad * 2)) / 2);
      b.scrollTop = Math.max(0, top - pad - (b.clientHeight - (bottom - top + pad * 2)) / 2);
      return;
    }
    ensureActorVisible(cell);
  }

  // What the current mode says about a token: a ring class, a badge and words for screen readers.
  function markFor(t) {
    const me = current();
    if (!me || t.dead) return null;
    if (ui.mode === 'attack') {
      const r = bestTarget(t.id);
      const why = r && r.legal ? whyChips(r).map(c => c.text).join(', ') : '';
      return r ? { cls: 'tx-target', badge: r.legal ? r.hit_percent + '%' : null,
                   say: r.legal ? `${r.hit_percent}% to hit` + (why ? ` (${why})` : '') +
                                  (r.provokes ? ', moving away provokes' : '') : '' } : null;
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
    const ac = (t.ac === undefined || t.ac === null) ? '?' : t.ac;
    const g = svg('g', { class: cls.join(' '), 'data-id': t.id, role: 'button',
      'aria-label': `${t.name}, ${sideOf(t).word}, ${t.dead ? 'dead' : t.hp + ' of ' + t.max_hp + ' HP'}, AC ${ac}, ${label(t.x, t.y)}` +
        (tg.length ? ', ' + tg.join(', ') : '') + (mark && mark.say ? ', ' + mark.say : '') }, ui.tokenLayer);
    const side = sideOf(t);
    svg('circle', { cx, cy, r: C / 2 - 1, class: 'tx-ring' }, g);
    // The frame is what says whose turn-relevant creature this is: a notched
    // octagon for an enemy, a round frame for everyone else. Side stays
    // readable in greyscale, at a glance, and to a colour-blind player.
    //
    // A portrait REPLACES the flat fill but not the frame. The frame is drawn
    // after the art, as a stroke over it, because the shape -- not the colour,
    // and not the face -- is what identifies an enemy in a crowded grid. Drop
    // the stroke and a screen full of portraits becomes a screen where you hunt
    // for the red one; that is the thing this shape exists to prevent.
    //
    // Everything the portrait does is inside the clip, so the silhouette the
    // player reads is the same silhouette as before.
    const art = t.portrait || '';
    const isEnemy = t.side === 'enemy';
    // The silhouette, as tag + geometry. One description, drawn twice: once as
    // the fill underneath and once as the frame over the top, so the two can
    // never drift apart.
    const shape = isEnemy
      ? { tag: 'path', geo: { d: octagon(cx, cy, C / 2 - 4) } }
      : { tag: 'circle', geo: { cx, cy, r: C / 2 - 4 } };
    // svg() takes (tag, attrs, parent) -- three. The style belongs in attrs, or
    // it lands in the parent slot and the call throws.
    const shapeNode = (style, parent) => svg(shape.tag, Object.assign({}, shape.geo, { style }), parent);
    // The three initials, used when there is no portrait and again if a
    // portrait fails to load. Outlined so they stay readable on light art.
    function initialsInto(g, cx, cy) {
      const initials = t.name.split(/\s+/).map(w => /^\d+$/.test(w) ? w : w[0]).join('').slice(0, 3);
      const tx = svg('text', { x: cx, y: cy + 4, 'text-anchor': 'middle',
        style: 'fill:#fff;font:700 11px Figtree,sans-serif;paint-order:stroke;stroke:rgba(0,0,0,.6);stroke-width:2.5' }, g);
      tx.textContent = initials;
    }
    if (art) {
      // A portrait that fails to load falls back to the coloured shape. The art
      // is gitignored, so this is the normal state on a clone without it and it
      // must not be a broken image or an empty hole.
      //
      // The fallback REPLACES the <image> in place rather than appending: the
      // frame is stroked after everything else, and an appended fill would land
      // on top of it and hide the one thing that says which side this is.
      let fellBack = false, img = null;
      const paint = () => {
        if (fellBack || !g.isConnected || !img) return;
        fellBack = true;
        const fill = shapeNode(`fill:${side.colour}`, g);
        g.insertBefore(fill, img);
        g.removeChild(img);
        img = null;
        initialsInto(g, cx, cy);
      };
      const clipId = 'txclip-' + t.id.replace(/[^A-Za-z0-9_-]/g, '');
      const clip = svg('clipPath', { id: clipId }, ui.defsLayer);
      svg(shape.tag, shape.geo, clip);
      img = svg('image', {
        class: 'tx-art', x: cx - C / 2 + 2, y: cy - C / 2 + 2, width: C - 4, height: C - 4,
        preserveAspectRatio: 'xMidYMid slice', 'clip-path': `url(#${clipId})`,
        href: '/tokens/' + art.split('/').pop()
      }, g);
      img.addEventListener('error', paint);
    } else {
      shapeNode(`fill:${side.colour}`, g);
      initialsInto(g, cx, cy);
    }
    // The frame, last, over whatever is inside it.
    shapeNode('fill:none;stroke:var(--tx-panel);stroke-width:2', g);
    if (!t.dead) {
      const pct = Math.max(0, t.hp / Math.max(1, t.max_hp));
      svg('rect', { x: t.x * C + 4, y: t.y * C + C - 5, width: C - 8, height: 3, style: 'fill:var(--tx-line)' }, g);
      svg('rect', { x: t.x * C + 4, y: t.y * C + C - 5, width: (C - 8) * pct, height: 3,
                    style: 'fill:' + (pct <= .25 ? 'var(--tx-danger)' : 'var(--tx-heal)') }, g);
      // Small corner markers: C = concentrating (top left), R = a readied action (top right).
      if (t.concentration) marker(g, t.x * C + 5, t.y * C + 5, 'C', 'tx-conc');
      if (t.readied) marker(g, t.x * C + C - 5, t.y * C + 5, 'R', 'tx-ready');
      conditionBadges(g, t);
    }
    if (mark && mark.badge) badge(g, t, mark.badge, mark.ally ? 'tx-ally' : '');
    const title = svg('title', {}, g); title.textContent = t.name + ` (AC ${ac})` + (tg.length ? ' (' + tg.join(', ') + ')' : '');
    g.addEventListener('click', e => { e.stopPropagation(); onToken(t, e); });
    t._g = g;
  }

  // Up to two condition badges along the bottom edge, "+n" for the rest (the
  // full list is in the token's title, its label and the initiative strip).
  // The badge is a tab on the token's lower edge rather than a dot beside it:
  // it is 8 user units tall, which is 10px on a table display at 40px squares.
  function conditionBadges(g, t) {
    const list = (t.conditions || []).filter(c => c !== 'hidden');
    if (!list.length) return;
    const shown = list.length > 2 ? list.slice(0, 1) : list;
    const codes = shown.map(c => CONDITION_CODES[c] || c.slice(0, 2).replace(/^./, m => m.toUpperCase()));
    if (list.length > shown.length) codes.push('+' + (list.length - shown.length));
    codes.forEach((code, i) => {
      const x = t.x * C + 1 + i * 16, y = t.y * C + C - 20;
      svg('rect', { x, y, width: 15, height: 12, rx: 3, class: 'tx-cond' + (code[0] === '+' ? ' tx-cond-more' : '') }, g);
      const tx = svg('text', { x: x + 7.5, y: y + 9, 'text-anchor': 'middle', class: 'tx-cond-t' }, g);
      tx.textContent = code;
    });
  }

  function marker(g, x, y, text, cls) {
    svg('circle', { cx: x, cy: y, r: 5.5, class: 'tx-mark ' + cls }, g);
    const m = svg('text', { x, y: y + 3, 'text-anchor': 'middle', class: 'tx-mark-t' }, g);
    m.textContent = text;
  }

  // Draw the pins on this map. Empty on every map that has none, which is
  // nearly all of them, so the layer is usually one `innerHTML = ''`.
  //
  // Everything about *whether* a pin appears was decided on the server: the GM
  // authored it in a shell (`scripts/pin.py`), it was validated against the
  // allow-list, and the unrevealed ones were dropped before this payload was
  // built. Nothing here can reveal a pin, and nothing here reads a file.
  function drawPins() {
    const layer = ui.pinLayer; if (!layer) return;
    layer.innerHTML = '';
    const pins = (snap && snap.pins) || [];
    for (const p of pins) {
      if (!pinOnBoard(p, ui.W, ui.H)) continue;
      // data-kind is what the stylesheet keys on. The shape is already the
      // distinction; this is so the two are visibly different at rest rather
      // than only under a magnifier.
      // The class names are spelled out in full rather than composed from a
      // prefix, because that is what makes them findable.
      // `ScriptAndStylesheetAgree` (test_display_tactics_ui.py) works by reading
      // the class names out of this file and finding each one in tactics.css, and
      // a name assembled at runtime ('tx-pin-' + kind) is in neither: the
      // stylesheet half passes because the string is not there to look for, and
      // the pin silently loses its colour. Spelling them out keeps the inventory
      // static, which is the only reason the check works.
      const mark = svg('path', { d: pinPath(p, C),
                                class: p.kind === 'map' ? 'tx-pin tx-pin-map' : 'tx-pin tx-pin-note',
                                'data-kind': p.kind === 'map' ? 'map' : 'note' }, layer);
      const [cx, cy] = pinCentre(p, C);
      const label = svg('text', { x: cx + (pinLabelAnchor(p) === 'end' ? -11 : 11),
                                 y: cy + 4,
                                 'text-anchor': pinLabelAnchor(p),
                                 class: 'tx-pin-label' }, layer);
      // The label is a text node, and it is the ONLY text this path writes. A
      // pin label has already been through scripts/pins.py's label cleaner, and
      // textContent means it is never parsed as markup. There is no innerHTML on
      // the pin path at all, which is the whole of the XSS position for a pin.
      label.textContent = pinLabel(p, C);
      mark.setAttribute('data-pin-id', p.id || '');
    }
  }

  function drawOverlay() {
    const o = ui.overlay; if (!o) return;
    o.innerHTML = '';
    if (ui.markLayer) ui.markLayer.innerHTML = '';
    if (ui.mode === 'aim') { drawAim(o); return; }
    if (ui.mode !== 'move' || !ui.reach) return;
    const walk = ui.reach.walk || {}, dash = ui.reach.dash || {};
    // A fixed fill in its own colour (the old teal fade vanished on water), and a
    // line round the edge of each range: the line, not the colour, carries the meaning.
    for (const sq of Object.keys(walk)) {
      const p = parseSq(sq); if (!p) continue;
      svg('rect', { x: p[0] * C + 1, y: p[1] * C + 1, width: C - 2, height: C - 2, class: 'tx-reach' }, o);
    }
    for (const sq of Object.keys(dash)) {
      const p = parseSq(sq); if (!p) continue;
      svg('rect', { x: p[0] * C + 3, y: p[1] * C + 3, width: C - 6, height: C - 6, class: 'tx-dash' }, o);
    }
    rangeEdge(o, closeHoles(walk), 'tx-reach-edge');
    rangeEdge(o, closeHoles(Object.assign({}, walk, dash)), 'tx-dash-edge');
    for (const sq of threatened().keys()) {
      const p = parseSq(sq); if (!p) continue;
      svg('rect', { x: p[0] * C, y: p[1] * C, width: C, height: C, class: 'tx-threat' }, o);
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

  // A range with the mover's own square and any creature ringed by the range put
  // back in, so the edge outlines the area rather than boxing each token.
  function closeHoles(set) {
    const out = Object.assign({}, set), me = current();
    if (me) out[sqOf(me)] = 0;
    for (const t of living()) {
      const sq = sqOf(t);
      if (sq in out) continue;
      const n = [[0, -1], [0, 1], [-1, 0], [1, 0]].map(([dx, dy]) => [t.x + dx, t.y + dy])
        .filter(([x, y]) => x >= 0 && y >= 0 && x < ui.W && y < ui.H);
      if (n.every(([x, y]) => label(x, y) in out)) out[sq] = 0;
    }
    return out;
  }

  // The outline of a set of squares: a segment on every side that borders a square outside it.
  function rangeEdge(layer, set, cls) {
    let d = '';
    for (const sq of Object.keys(set)) {
      const p = parseSq(sq); if (!p) continue;
      const [x, y] = [p[0] * C, p[1] * C];
      if (!(label(p[0], p[1] - 1) in set)) d += `M${x},${y}h${C}`;
      if (!(label(p[0], p[1] + 1) in set)) d += `M${x},${y + C}h${C}`;
      if (!(label(p[0] - 1, p[1]) in set)) d += `M${x},${y}v${C}`;
      if (!(label(p[0] + 1, p[1]) in set)) d += `M${x + C},${y}v${C}`;
    }
    if (d) svg('path', { d, class: cls }, layer);
  }

  // Squares inside the reach of a hostile creature that could make an opportunity
  // attack now (snapshot `threat`, in feet; every square is 5 ft, diagonals too).
  // Map: square -> names of the creatures threatening it.
  function threatened() {
    const me = current(), out = new Map();
    if (!me) return out;
    for (const t of living()) {
      if (!t.threat || !hostile(me, t)) continue;
      const r = Math.floor(t.threat / 5);
      for (let dy = -r; dy <= r; dy++) for (let dx = -r; dx <= r; dx++) {
        const x = t.x + dx, y = t.y + dy;
        if ((!dx && !dy) || x < 0 || y < 0 || x >= ui.W || y >= ui.H) continue;
        const sq = label(x, y);
        out.set(sq, (out.get(sq) || []).concat(t.name));
      }
    }
    return out;
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

  // ── fog of war, cover shading, keyboard cursor ───────────────────────────
  // fog.runs is [row, firstCol, lastCol] per stretch of visible squares: the
  // snapshot sends runs, not one entry per square, so expand them here.
  function fogSet() {
    if (!snap || !snap.fog) return null;
    const out = new Set();
    for (const r of snap.fog.runs || []) {
      const y = r[0];
      for (let x = r[1]; x <= r[2]; x++) out.add(label(x, y));
    }
    return out;
  }

  function drawFog(layer, W, H) {
    const seen = fogSet(); if (!seen) return;
    for (let y = 0; y < H; y++) for (let x = 0; x < W; x++)
      if (!seen.has(label(x, y))) {
        svg('rect', { x: x * C, y: y * C, width: C, height: C, class: 'tx-fog' }, layer);
        svg('rect', { x: x * C, y: y * C, width: C, height: C, class: 'tx-fog-hatch' }, layer);   // not colour alone
      }
  }

  // From the selected creature: no line of sight is hatched dark, three-quarters
  // cover darker than half. Squares in plain view are left as they are.
  function drawSight() {
    const layer = ui.sightLayer; if (!layer) return;
    layer.innerHTML = '';
    const d = ui.sight && ui.sightData; if (!d) return;
    const from = tokenById(d.from); if (!from || sqOf(from) !== d.square) return;
    const vis = new Set(d.visible || []);
    const rows = (snap.grid && snap.grid.rows) || [];
    for (let y = 0; y < (ui.H || 0); y++) for (let x = 0; x < (ui.W || 0); x++) {
      const sq = label(x, y);
      if (terrainOf(rows[y][x]) === 'wall') continue;
      const cls = !vis.has(sq) ? 'tx-nolos' : d.cover[sq] === 'half' ? 'tx-cov2' : d.cover[sq] === 'three-quarters' ? 'tx-cov5' : '';
      if (cls) svg('rect', { x: x * C, y: y * C, width: C, height: C, class: cls }, layer);
    }
    svg('circle', { cx: from.x * C + C / 2, cy: from.y * C + C / 2, r: C / 2 + 2, class: 'tx-eye' }, layer);
  }

  function toggleSight() {
    ui.sight = !ui.sight;
    try { localStorage.setItem('tx-cover', ui.sight ? '1' : '0'); } catch (e) { /* storage blocked */ }
    el.cover.setAttribute('aria-pressed', String(ui.sight));
    if (ui.sight) loadSight(); else { drawSight(); renderInfo(); }
  }

  function sightSource() {
    let t = ui.sightFrom && tokenById(ui.sightFrom);
    if (!t || t.dead) {
      const c = current();
      t = c && c.controller === 'player' ? c : living().find(x => x.controller === 'player') || living().find(x => x.side === 'pc');
    }
    return t || null;
  }

  // Asked again whenever a creature moves (cover depends on who stands where).
  async function loadSight() {
    const t = sightSource(); if (!t) return;
    ui.sightFrom = t.id;
    const key = t.id + '|' + living().map(x => x.id + sqOf(x)).join(',');
    if (key === ui.sightKey && ui.sightData) { drawSight(); return; }
    ui.sightKey = key;
    const res = await call('sight', [t.id]);
    if (ui.sightKey !== key) return;
    if (res.error) { toast(res.error, 'error'); return; }
    ui.sightData = res.result || null;
    drawSight(); renderInfo();
    if (ui.sightData && ui.sightData.text) say(ui.sightData.text);
  }

  function selectSight(t) {
    ui.sightFrom = t.id; ui.sightData = null; ui.sightKey = '';
    loadSight();
  }

  function sightLine() {
    const d = ui.sight && ui.sightData;
    let s = d && d.text ? '<br><span class="tx-status">' + esc(d.text) + '</span>' +
      '<br><span class="tx-legend">Hatched: no line of sight. Dark gold: three-quarters cover. Light gold: half cover.</span>' : '';
    if (snap && snap.fog) s += '<br><span class="tx-legend">Shadowed squares: no one in the party can see there.</span>';
    return s;
  }

  function say(text) { if (el.say) { el.say.textContent = ''; el.say.textContent = text; } }

  function homeSquare() {
    const t = current() || sightSource();
    return t ? [t.x, t.y] : [0, 0];
  }

  function placeCursor(p) {
    ui.cursor = [Math.max(0, Math.min((ui.W || 1) - 1, p[0])), Math.max(0, Math.min((ui.H || 1) - 1, p[1]))];
    drawCursor();
    const sq = label(ui.cursor[0], ui.cursor[1]);
    hoverSquare(sq);
    say(describeSquare(ui.cursor));
  }

  function drawCursor() {
    const layer = ui.cursorLayer; if (!layer) return;
    layer.innerHTML = '';
    if (!ui.cursor || document.activeElement !== el.board) return;
    svg('rect', { x: ui.cursor[0] * C + 1.5, y: ui.cursor[1] * C + 1.5, width: C - 3, height: C - 3, class: 'tx-cursor' }, layer);
  }

  // Words for one square: where it is, what is there, and what the current mode says about it.
  function describeSquare(p) {
    const sq = label(p[0], p[1]);
    const rows = (snap && snap.grid && snap.grid.rows) || [];
    const bits = [sq, terrainOf((rows[p[1]] || '')[p[0]] || '.')];
    const t = (snap.tokens || []).find(x => x.x === p[0] && x.y === p[1] && !x.dead) ||
              (snap.tokens || []).find(x => x.x === p[0] && x.y === p[1]);
    // A pin is named here rather than given an aria-label, because the board svg
    // is aria-hidden: anything inside it is decorative to a screen reader, and
    // the square cursor reading through #tx-say is the board's only voice. A
    // pin with no line here is a pin a keyboard player cannot find at all.
    const pin = ((snap && snap.pins) || []).find(x => x.x === p[0] && x.y === p[1]);
    if (pin) bits.push(pin.kind === 'map'
      ? `map pin to ${pin.target}, ${pin.label}` : `note pin, ${pin.label}`);
    if (t) {
      const tg = tags(t), mark = markFor(t);
      bits.push(`${t.name} (${sideOf(t).word}), ${t.dead ? 'dead' : t.hp + ' of ' + t.max_hp + ' HP'}` +
                (tg.length ? ', ' + tg.join(', ') : '') + (mark && mark.say ? ', ' + mark.say : ''));
    }
    const seen = fogSet();
    if (seen && !seen.has(sq)) bits.push('out of sight');
    const d = ui.sight && ui.sightData;
    if (d && d.square !== sq) {
      const from = (tokenById(d.from) || {}).name || 'the selected creature';
      bits.push(!(d.visible || []).includes(sq) ? `no line of sight from ${from}` :
                d.cover[sq] ? `${d.cover[sq]} cover from ${from}` : `in clear view of ${from}`);
    }
    if (ui.mode === 'move' && ui.reach) {
      const walk = (ui.reach.walk || {})[sq], dash = (ui.reach.dash || {})[sq];
      bits.push(walk !== undefined ? `${walk} ft away` : dash !== undefined ? `${dash} ft away, needs Dash` : 'out of reach');
      const by = threatened().get(sq);
      if (by) bits.push(`inside ${by.join(' and ')}'s reach`);
    }
    return bits.map(b => b[0].toUpperCase() + b.slice(1)).join('. ') + '.';
  }

  function onBoardKey(e) {
    if (!snap) return;
    const step = { ArrowUp: [0, -1], ArrowDown: [0, 1], ArrowLeft: [-1, 0], ArrowRight: [1, 0] }[e.key];
    const at = ui.cursor || homeSquare();
    if (step) { e.preventDefault(); placeCursor([at[0] + step[0], at[1] + step[1]]); return; }
    if (e.key === 'Home') { e.preventDefault(); placeCursor(homeSquare()); return; }
    if ((e.key === 'c' || e.key === 'C') && !e.ctrlKey && !e.metaKey && !e.altKey) { e.preventDefault(); toggleSight(); return; }
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      if (!ui.cursor) { placeCursor(at); return; }
      const t = (snap.tokens || []).find(x => x.x === ui.cursor[0] && x.y === ui.cursor[1] && !x.dead);
      // A keyboard user has already previewed the square by moving onto it, as a mouse hovers.
      if (t) onToken(t, { pointerType: 'mouse' });
      else clickSquare(label(ui.cursor[0], ui.cursor[1]), 'mouse');
    }
  }

  // ── ruler and templates (BV6) ────────────────────────────────────────────
  // Ephemeral: lives in ui.ruler and one SVG layer, never sent to the engine and
  // never written to encounter.json. Distances come from gridDistance, the port
  // of Grid.distance, fed the map's own diagonal rule and square size.
  const RULER_WORD = { line: 'Ruler', cone: 'Cone', circle: 'Circle' };

  function setRuler(tool) {
    const r = ui.ruler;
    r.tool = tool && tool !== r.tool ? tool : null;
    r.a = r.b = null; r.fixed = false;
    for (const b of el.panel.querySelectorAll('[data-ruler]'))
      b.setAttribute('aria-pressed', String(b.dataset.ruler === r.tool));
    el.panel.classList.toggle('tx-ruling', !!r.tool);
    if (el.rulerOut) el.rulerOut.textContent = r.tool ? RULER_WORD[r.tool] + ': pick the first square.' : '';
    drawRuler();
  }

  function rulerClick(sq) {
    const p = parseSq(sq), r = ui.ruler; if (!p) return;
    if (!r.a || r.fixed) { r.a = p; r.b = p; r.fixed = false; }
    else { r.b = p; r.fixed = true; }
    drawRuler();
  }
  function rulerHover(sq) {
    const p = parseSq(sq), r = ui.ruler;
    if (!p || !r.a || r.fixed || (r.b && r.b[0] === p[0] && r.b[1] === p[1])) return;
    r.b = p; drawRuler();
  }

  function drawRuler() {
    const layer = ui.rulerLayer, r = ui.ruler; if (!layer) return;
    layer.innerHTML = '';
    if (!r.tool || !r.a) return;
    const g = snap.grid || {}, sq = snap.square_ft || 5, a = r.a, b = r.b || r.a;
    const ft = distFeet(a, b, g.diagonals, sq);
    const ca = cellCentre(a[0], a[1]), cb = cellCentre(b[0], b[1]);
    const ax = ca.px, ay = ca.py, bx = cb.px, by = cb.py;
    let text = '';
    if (r.tool !== 'line' && ft > 0 || r.tool === 'circle') {
      const cells = templateSquares(r.tool === 'circle' ? 'circle' : r.tool, ft, sq,
                                    a, r.tool === 'circle' ? a : b, ui.W || 0, ui.H || 0, sq);
      for (const c of cells) svg('rect', { x: c[0] * C, y: c[1] * C, width: C, height: C, class: 'tx-ruler-cell' }, layer);
      text = `${RULER_WORD[r.tool]} ${ft} ft: ${cells.length} squares, walls not counted`;
    } else if (r.tool === 'line') {
      text = `${ft} ft (${ft / sq} squares)`;
    } else text = 'Pick the second square.';
    svg('line', { x1: ax, y1: ay, x2: bx, y2: by, class: 'tx-ruler-line' }, layer);
    svg('circle', { cx: ax, cy: ay, r: 4, class: 'tx-ruler-dot' }, layer);
    if (b !== a) svg('circle', { cx: bx, cy: by, r: 4, class: 'tx-ruler-dot' }, layer);
    if (ft > 0 || r.tool === 'circle') {
      const t = svg('text', { x: Math.min((ui.W || 1) * C - 4, bx + 8), y: Math.max(14, by - 8), class: 'tx-cell-lbl tx-ruler-lbl',
                              style: 'stroke:var(--tx-paper)' }, layer);
      t.textContent = ft + ' ft';
      t.setAttribute('text-anchor', bx > (ui.W || 1) * C - 60 ? 'end' : 'start');
    }
    if (el.rulerOut) el.rulerOut.textContent = text;
    if (r.fixed) say(text);
  }

  // ── camera memory (PV3) ──────────────────────────────────────────────────
  // {mapname: {scrollL, scrollT[, zoom]}} in localStorage, restored when the map
  // mounts. Same class as the tx-cover toggle: a convenience, so blocked storage
  // is ignored.
  const CAMERA_KEY = 'tx-camera';
  function cameraName() { return ((snap && snap.meta && snap.meta.name) || (snap && snap.grid && snap.grid.name) || '').trim(); }
  function cameraLoad() {
    try { return cameraParse(localStorage.getItem(CAMERA_KEY)); } catch (e) { return {}; }
  }
  function saveCamera() {
    ui.camTimer = 0;
    const name = ui.camMap, b = el.board;
    if (!name || !b || !b.clientWidth) return;              // hidden panel: scroll reads 0, not where they were
    try {
      localStorage.setItem(CAMERA_KEY, JSON.stringify(cameraPut(cameraLoad(), name,
        { scrollL: Math.round(b.scrollLeft), scrollT: Math.round(b.scrollTop) })));
    } catch (e) { /* storage blocked */ }
  }
  function saveCameraSoon() {
    if (!ui.camTimer) ui.camTimer = setTimeout(saveCamera, 300);   // throttle: at most one write per 300 ms
  }

  // Which control has the keyboard, as an identity that survives a rebuild.
  //
  // The action bar is emptied and refilled on every snapshot, which lands focus
  // on the body, and a body focus sends the next Tab back to the top of the
  // document: a keyboard player part-way along the bar is thrown back to the
  // first control in the page on every action, and actions arrive constantly.
  //
  // By data-key, not by text content. Text is not an identity here: the attack
  // buttons read "Longsword (up to 65%)" and the number moves with the roll, so
  // the one control that most needs to be found again is the one whose text
  // changes. A key is a name, so it does not move.
  function focusedKey() {
    const a = document.activeElement;
    if (!a || a === document.body || !el.panel || !el.panel.contains(a)) return null;
    return (a.dataset && a.dataset.key) || null;
  }

  // Put the keyboard back where it was.
  //
  // The key names the control, and so do its prefixes: "attack:Longsword" is a
  // choice inside the attack mode, so when Escape takes that choice away with
  // the mode, the keyboard falls back to the Attack button that opened the list.
  // It walks down the prefixes until one names a control that is still there and
  // can still be pressed, and lands on the board if none does: the board is
  // always there, is where the next thing is done, and has the square cursor on
  // it, so a lost focus stays inside the panel instead of dropping the player at
  // the top of the page.
  //
  // Not while the engine is asking a question (ask()): that prompt owns the
  // keyboard for as long as it is up, and this would pull focus out of it.
  function restoreFocus(key) {
    if (!key || (el.prompt && !el.prompt.hidden)) return;
    const all = el.panel.querySelectorAll('[data-key]');
    for (let k = key; k; k = k.includes(':') ? k.slice(0, k.lastIndexOf(':')) : '') {
      for (const node of all) if (node.dataset.key === k && !node.disabled) { node.focus(); return; }
    }
    if (el.board) el.board.focus();
  }

  // ── side panel: info, actions, log ───────────────────────────────────────
  function renderSide() {
    const t = current();
    const had = focusedKey();
    // The lead row is a real element that survives the rebuild, so the phone's
    // pinned row stays first and the rest of the bar is emptied under it.
    el.actions.innerHTML = '';
    el.actions.appendChild(el.leads);
    el.leads.innerHTML = '';
    renderInfo();
    if (t && myTurn() && !(snap.turn && snap.turn.pending === 'death_save')) {
      const left = snap.turn ? snap.turn.movement_left : 0;
      const used = !!(snap.turn && snap.turn.action_used);
      const why = (u, text) => ({ disabled: u, title: u ? 'Your action is used this turn' : text });
      // These three are what a player reaches for on every turn, so on a phone
      // they are pinned to the top edge of the action bar and a long spell
      // list scrolls under them.
      button('Move', () => toggleMove(t), { pressed: ui.mode === 'move', disabled: left <= 0 && used, lead: true });
      button('Attack', () => toggleAttack(t, 'weapon'), { pressed: ui.mode === 'attack', disabled: used, lead: true });
      button('Cast', () => toggleCast(t), { pressed: SPELL_MODES.includes(ui.mode), lead: true,
        title: 'Your spells, with slots left' });
      button('Dash', () => act('dash', [t.id]), { disabled: used });
      button('Disengage', () => act('disengage', [t.id]), { disabled: used });
      button('Dodge', () => act('dodge', [t.id]), { disabled: used });
      button('Help', () => toggleHelp(t), Object.assign({ pressed: ui.mode === 'help' }, why(used, 'Give an ally advantage against an enemy next to you')));
      button('Hide', () => act('hide', [t.id]), why(used, 'Stealth, out of every enemy\'s sight'));
      button('Ready', () => toggleReady(t), Object.assign({ pressed: ui.mode === 'ready' }, why(used, 'Hold an attack or a spell for a trigger')));
      if ((t.conditions || []).includes('grappled')) button('Escape', () => act('escape', [t.id]), why(used, 'Athletics or Acrobatics against the grapple'));
      if ((t.conditions || []).includes('prone')) button('Stand up', () => act('stand', [t.id]));
      button('Undo move', () => act('undo-move', []), { title: 'Take back the last move (before an action)' });
      // End turn stays where it has always been in the flow (last of the turn's
      // own actions, ahead of any spell list) and the phone pins it to the
      // bottom edge of the bar, so it cannot be scrolled away either.
      button('End turn', () => act('end-turn', []), { primary: true, end: true });
      if (ui.mode === 'attack' && ui.targets) attackChoices();
      if (ui.mode === 'cast') spellChoices(t);
      if (ui.mode === 'darts') dartChoices();
      if (ui.mode === 'ready' && ui.readyStep === 'what') readyChoices();
      if (['aim', 'spell', 'darts', 'help', 'ready'].includes(ui.mode))
        button('Cancel', () => { clearMode(); render(); }, { title: 'Escape also cancels' });
    } else if (t && myTurn()) {
      button('Roll death save', () => act('death-save', [t.id]), { primary: true });
    }
    restoreFocus(had);
    el.log.innerHTML = '';
    for (const e of (snap.log || []).slice(-8)) {
      const li = document.createElement('li');
      li.textContent = e.text;
      // The odds for every roll in the entry, in the system's own words, so the
      // number a player wants is not only in a toast that has already faded.
      // All of them, not the first: directional cover is the highest roll
      // density in 5e, and a log that shows one of four chances looks like the
      // other three were withheld. A roll with no odds (a damage die) simply
      // contributes nothing.
      const odds = entryOdds(e);
      if (odds) {
        const s = document.createElement('span');
        s.className = 'tx-odds';
        s.textContent = odds;
        li.appendChild(s);
      }
      el.log.appendChild(li);
    }
    el.log.scrollTop = el.log.scrollHeight;
  }

  function renderInfo() {
    const t = current();
    // #tx-info and #tx-forecast are rebuilt with innerHTML on every render, and
    // a render arrives on every hover and every snapshot. Nothing focusable is in
    // them today, but the same rule as renderSide applies if that changes, and a
    // hover must not be able to move the keyboard.
    const had = focusedKey();
    el.forecast.innerHTML = '';
    if (!t) { el.info.innerHTML = snap.unseen_turn ? 'A creature you cannot see is acting. The GM narrates.' : ''; restoreFocus(had); return; }
    if (!myTurn()) { el.info.innerHTML = `<strong>${esc(t.name)}</strong> is acting. The GM narrates their turn.` + pendingLine() + statusLine(t) + sightLine(); restoreFocus(had); return; }
    if (snap.turn && snap.turn.pending === 'death_save') { el.info.innerHTML = `<strong>${esc(t.name)}</strong> is dying: roll a death save.`; restoreFocus(had); return; }
    el.info.innerHTML = infoText(t) + pendingLine() + (ui.mode ? '' : sightLine());
    el.forecast.innerHTML = forecastHtml();
    restoreFocus(had);
  }

  // The hover forecast for one target: hit or fail chance, expected damage, the
  // reasons behind them and the provokes flag. Only engine-provided fields are
  // rendered. Chips carry a glyph for advantage and disadvantage, not colour alone.
  function forecastCard(row, name) {
    const f = forecast(row);
    if (!f) return '';
    const chips = f.chips.map(c => `<li class="tx-why tx-why-${c.kind}">${c.glyph ? `<i aria-hidden="true">${c.glyph}</i>` : ''}${esc(c.text)}</li>`);
    if (f.provokes) chips.push('<li class="tx-why tx-why-warn"><i aria-hidden="true">\u26A0 </i>moving away provokes an opportunity attack</li>');
    return `<div class="tx-forecast" role="group" aria-label="Forecast against ${esc(name)}">` +
      `<div class="tx-fc-head"><strong>${esc(name)}</strong> <span class="tx-fc-pct">${esc(f.phrase)}</span>` +
      (f.expected !== null ? ` <span class="tx-fc-exp">about ${f.expected} damage</span>` : '') + '</div>' +
      (chips.length ? `<ul class="tx-whys">${chips.join('')}</ul>` : '') + '</div>';
  }

  // The token under the pointer or the keyboard cursor, in a mode that targets one.
  function forecastHtml() {
    if (!ui.hover || !myTurn()) return '';
    if (ui.mode === 'aim') {                       // an area: one card per creature it would catch
      const pv = ui.preview[ui.hover];
      return pv && pv.legal ? (pv.affected || []).map(a => forecastCard(a, a.name)).join('') : '';
    }
    const t = living().find(k => sqOf(k) === ui.hover);
    if (!t || t.id === current().id) return '';
    if (ui.mode === 'attack' && ui.attack) {
      const row = bestTarget(t.id);
      if (!row) return '';
      return row.legal ? forecastCard(row, t.name)
        : `<div class="tx-forecast"><strong>${esc(t.name)}</strong> <span class="tx-why tx-why-warn">not a valid target: ${esc(row.reason || '')}</span></div>`;
    }
    if (ui.mode === 'spell' && ui.singles && t.id in ui.singles) {
      const pv = ui.singles[t.id];
      const row = pv && pv.legal && (pv.affected || []).find(a => a.id === t.id);
      return row ? forecastCard(row, t.name) : '';
    }
    return '';
  }

  function pendingLine() {
    // A roll or reaction the engine is waiting on (cli.py mirrors it into
    // turn.pending): shown on every turn, so spectators and the party do not
    // see a bare "Kairos's turn" while the engine is paused.
    const t = current();
    const text = pendingBanner(snap.turn && snap.turn.pending, t && t.name, id => tokenById(id));
    return text ? `<br><span class="tx-warn">${esc(text)}</span>` : '';
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
                        : 'Pick a square. Outlined: walking range. Dashed: needs Dash.' +
                          (threatened().size ? ' <span class="tx-legend">Red hatching: inside an enemy\'s reach; leaving it can provoke.</span>' : ''));
      if (pv && pv.opportunity_attacks && pv.opportunity_attacks.length)
        s += '<br><span class="tx-warn">This move provokes an opportunity attack.</span>';
    } else if (ui.mode === 'attack') {
      s += '<br>Pick a target on the map. Hit chance is shown on each one.';
    } else if (ui.mode === 'cast') {
      s += '<br>Pick a spell.';
    } else if (ui.mode === 'aim') {
      const pv = ui.hover && ui.preview[ui.hover];
      const shape = sp.area ? `${sp.area.size} ft ${sp.area.shape}` : 'area';
      if (!pv) s += `<br><strong>${esc(sp.name)}</strong> (${esc(shape)}): point at a square to aim, then click. On a phone, tap twice.`;
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
    // A stable name, so the keyboard can be put back here after the bar is
    // rebuilt (restoreFocus). The text is the name of the action, so it is the
    // key, except where the caller knows better: the attack, spell and dart
    // lists put a number in their text that moves with the roll, and a key
    // built from that would not survive the roll.
    b.dataset.key = o.key || text;
    if (o.pressed !== undefined) b.setAttribute('aria-pressed', String(!!o.pressed));
    if (o.lead) b.dataset.tx = 'lead';        // the phone's pinned row
    if (o.end) b.dataset.tx = 'end';          // pinned to the bar's bottom edge
    if (o.title) b.title = o.title;
    if (o.meta) { const m = document.createElement('span'); m.className = 'tx-meta'; m.textContent = o.meta; b.appendChild(m); }
    // Genuinely disabled, not styled as such. `disabled` takes the control out
    // of the tab order and stops the click reaching the handler at all, which is
    // the point: during an outage the map is a picture of a fight, and every one
    // of these buttons would send an action into a server that is not there.
    //
    // `always` is deliberately not consulted here. It exists so an engine prompt
    // (ask()) keeps its buttons usable while the panel is busy, and a prompt
    // answered during an outage would be answering about a fight that may have
    // moved on, so offline overrides it.
    b.disabled = !ui.online || !!o.disabled || (ui.busy && !o.always) || !fn;
    if (!ui.online && fn) b.title = OFFLINE_TITLE;
    if (fn) b.addEventListener('click', e => {
      fn(e);
      // Enter or Space on a button that arms a mode hands the keyboard to the
      // board. A mode is answered with a square, the square cursor is what the
      // arrow keys move and Enter acts on, and the board is one Tab away at the
      // far end of a bar that can be twenty buttons long with a spell list in
      // it. Without this, arming a mode from the keyboard leaves the keyboard on
      // the button that armed it and the arrow keys do nothing.
      //
      // detail 0 is a keyboard-generated click (a real pointer click is 1), so a
      // mouse player keeps the keyboard where it is: they are looking at the
      // board, not at the bar.
      if (e.detail === 0 && ui.mode) el.board.focus();
    });
    // A lead action goes in the pinned row; everything else lands under it.
    (o.parent || (o.lead ? el.leads : el.actions)).appendChild(b);
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
        { key: 'attack:' + name, pressed: ui.attack === name, disabled: !legal.length, parent: b });
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
        button(sp.name, () => pickSpell(sp), { parent: b, disabled: !sp.ok, cls: 'tx-spell', key: 'cast:' + sp.name,
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
          { parent: row, pressed: (t.reactions || 'ask') === mode, cls: 'tx-small', key: 'react:' + mode,
            title: { ask: 'Ask me when one could be used', auto: 'Use them whenever they help', off: 'Never use them' }[mode] });
      }
      b.appendChild(row);
    }
  }

  async function toggleCast(t) {
    if (SPELL_MODES.includes(ui.mode)) { clearMode(); render(); return; }
    clearMode(); ui.mode = 'cast'; ui.spells = null; render();
    await loadSpells(t);
    if (ui.mode !== 'cast') return;
    render();
    reveal(el.actions.querySelector('.tx-spells'));   // on a phone the list sits below the fold
  }

  function reveal(node) {                        // phone layout only: the panel scrolls there
    if (node && node.scrollIntoView && isPhone()) node.scrollIntoView({ block: 'nearest', behavior: reduced() ? 'auto' : 'smooth' });
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
        key: 'darts:cast',
        title: 'One target takes every dart; or pick one target per dart' });
    button('Undo last dart', () => { ui.darts.pop(); ui.dartsText = ''; render(); dartsPreview(); },
      { parent: b, disabled: !n, key: 'darts:undo' });
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
    const [res] = await Promise.all([call('targets', [t.id]), loadSpells(t)]);
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
      button(name, () => readyPick({ kind: 'attack', what: name, label: name }),
        { parent: b, cls: 'tx-spell', key: 'ready:attack:' + name });
    const spells = (ui.spells || []).filter(sp => !isReaction(sp) && sp.casting === 'action');
    if (spells.length) note(b, 'Spell (held with concentration)', 'tx-group');
    for (const sp of spells)
      button(sp.name, () => readyPick({ kind: 'cast', what: sp.name, label: sp.name, area: sp.targeting === 'area',
                                         none: ['self', 'none'].includes(sp.targeting) }),
        { parent: b, cls: 'tx-spell', disabled: !sp.ok, meta: sp.ok ? describe(sp) : sp.reason,
          key: 'ready:cast:' + sp.name,
          title: sp.ok ? describe(sp) : sp.reason });
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
    hoverSquare(cellAt(evt));
  }

  function hoverSquare(sq) {
    if (ui.ruler.tool) { rulerHover(sq); return; }
    if (ui.mode === 'aim') {
      if (!sq || sq === ui.hover) return;
      ui.hover = sq; ui.armed = null;
      clearTimeout(ui.hoverTimer);
      drawOverlay();
      if (ui.preview[sq]) renderInfo();
      else ui.hoverTimer = setTimeout(() => previewAim(sq), 90);
      return;
    }
    if ((ui.mode === 'attack' && ui.attack) || (ui.mode === 'spell' && ui.singles)) {
      if (sq === ui.hover) return;
      ui.hover = sq; renderInfo();
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

  function onBoardClick(evt) {
    // A pin is checked before the square, because a pin sits in a square and
    // the square is what everything else here is keyed on. Clicking a pin during
    // a move must open the note and NOT move a token, so this returns rather
    // than falling through -- which is also why a pin needs no "am I in move
    // mode" test of its own.
    const pin = pinAt(evt);
    if (pin) { openPin(pin); return; }
    const sq = cellAt(evt);
    if (sq) clickSquare(sq, evt.pointerType);
  }

  // The pin under a pointer, or null. Uses the element the browser hit-tested
  // rather than recomputing the cell: a pin is 14px across in the corner of a
  // 32px square, so a cell-derived answer would make most of the pin's own area
  // a miss and the click would move whoever was standing there instead.
  function pinAt(evt) {
    const target = evt.target;
    if (!target || typeof target.getAttribute !== 'function') return null;
    const id = target.getAttribute('data-pin-id');
    if (!id) return null;
    return ((snap && snap.pins) || []).find(p => p.id === id) || null;
  }

  // Open a pin. A note pin fetches its text and shows it in the panel; a map pin
  // navigates, because there is no thumbnail to show (an inline SVG preview
  // would be a second renderer, wrong for hex the moment it lands).
  async function openPin(p) {
    if (p.kind === 'map') return navigateToMap(p);
    try {
      const r = await fetch('/pins/note?id=' + encodeURIComponent(p.id) +
                            '&map=' + encodeURIComponent(currentMapSlug()));
      if (!r.ok) { refuse('That note is not available.'); return; }
      showNotePanel(p.label, await r.text());
    } catch (e) {
      refuse('That note is not available.');
    }
  }

  // The map this board is showing, which is what the pins on it belong to. The
  // snapshot carries the map's name rather than its slug, so this is the one
  // place the two have to be reconciled; an empty slug makes the fetch 404 and
  // the pin say so, which is the honest failure if a map ever lacks a slug.
  function currentMapSlug() {
    const meta = (snap && snap.meta) || {};
    return meta.slug || '';
  }

  // A map pin navigates to the target map's page. `/maps/<slug>` is the ARTWORK
  // route (send_from_directory, gm-display-app.py:1476) -- going there opens a
  // JPEG, not a map -- so the destination is the editor page, which is the only
  // page that renders one named map.
  //
  // Deliberately not a preview. An inline SVG built from `rows` would be a
  // second renderer, wrong for hex the moment SPEC-grid-and-map.md 4.2 lands, and
  // it needs a route that compiles an arbitrary map and serves it to every phone
  // on the LAN. `architect` cut it and that stands; see the brief's follow-ups.
  function navigateToMap(p) {
    // The same confirm() idiom clickSquare uses for a move that costs an action:
    // leaving the encounter's map mid-turn is the same class of "are you sure",
    // and this file already has the pattern rather than needing a new mechanism.
    const target = p.label || p.target;
    if (!confirm(`Open the map "${target}"? This leaves the fight on this map.`)) return;
    window.location.assign('/maps/' + encodeURIComponent(p.target) + '/edit');
  }

  // The note panel. Its body goes in through textContent and nowhere else:
  // `_renderMarkdown` escapes first and is the display's one markdown path, but
  // this panel is reached by a click on a campaign file, and the cheapest way to
  // be sure is for this function to have no HTML sink at all.
  // tests/test_pins_ui.py asserts that structurally.
  function showNotePanel(title, body) {
    if (!el.notePanel) return;
    el.notePanel.hidden = false;
    if (el.noteTitle) el.noteTitle.textContent = title || '';
    if (el.noteBody) el.noteBody.textContent = body || '';
    if (el.noteClose) el.noteClose.focus();
  }

  function closeNotePanel() {
    if (!el.notePanel) return;
    el.notePanel.hidden = true;
    if (el.noteBody) el.noteBody.textContent = '';
  }

  async function clickSquare(sq, pointerType) {
    if (ui.ruler.tool) { rulerClick(sq); return; }   // measuring is local; works offline
    if (ui.busy) return;
    // The board is a click target too, so disabling the action bar does not
    // cover it. Everything past this point would send an action.
    if (!ui.online) return;
    if (ui.mode === 'aim') return clickAim({ pointerType }, sq);
    if (ui.mode === 'ready' && ui.readyStep === 'target' && ui.readyWhat && ui.readyWhat.area) return readyFinish(sq);
    if (ui.mode !== 'move' || !ui.reach) return;
    const t = current(); if (!t) return;
    if (!inReach(sq)) return;
    // A mouse has already previewed on hover: one click moves. Touch (or no
    // hover yet): the first tap previews, a second tap on the same square moves.
    const hovered = pointerType === 'mouse' && ui.hover === sq && ui.preview[sq];
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
    if (ui.ruler.tool) { rulerClick(sqOf(t)); return; }
    // With cover shading on and no action under way, a click picks whose view to shade.
    if (ui.sight && !ui.mode && !t.dead && t.id !== ui.sightFrom) { selectSight(t); return; }
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
    // Refuse before the fetch, not only in the button's disabled state.
    //
    // The disabled attribute is the front door, but a click can still arrive
    // without one: Enter on a control that was focused the moment the stream
    // dropped, a pointer that went down before the outage and came up after, a
    // queued touch event. Each of those would send an action built from a
    // snapshot the server has since moved past, and the engine would either
    // apply it to the wrong state or answer 409. This is the door behind it.
    if (!ui.online) {
      refuse(OFFLINE_BANNER);
      return false;
    }
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
      if (res.error) { refuse(res.error); toast(res.error, 'error'); }
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
      const finish = v => { box.hidden = true; box.innerHTML = ''; box.onEscape = null; resolve(v); };
      // How the document-level Escape answers this prompt. Set for all three
      // kinds of question and cleared by finish, so a prompt that has gone is
      // never one Escape can find.
      box.onEscape = () => finish(null);
      if (react) {
        const yes = button(oa ? 'Yes, attack' : 'Yes', () => finish({ react: 'yes' }),
          { parent: row, primary: true, always: true, key: 'ask:yes' });
        button(oa ? 'No, let them go' : 'No', () => finish({ react: 'no' }),
          { parent: row, always: true, key: 'ask:no' });
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
        }, { parent: row, primary: true, always: true, key: 'ask:use' });
        button('Roll the dice', () => {
          const d = () => 1 + (crypto.getRandomValues(new Uint32Array(1))[0] % sides);
          let total;
          if (adv && n === 1 && sides === 20) {
            const a = d(), b = d(); total = adv === 'advantage' ? Math.max(a, b) : Math.min(a, b);
            toast(`d20 with ${adv}: ${a} and ${b}, keep ${total}`);
          } else { total = 0; for (let i = 0; i < n; i++) total += d(); toast(`${n}d${sides}: ${total}`); }
          finish({ roll: total });
        }, { parent: row, always: true, key: 'ask:dice' });
        button('Roll for me', () => finish({ forMe: true }),
          { parent: row, always: true, key: 'ask:forme', title: 'The engine rolls this action for you' });
        input.addEventListener('keydown', e => { if (e.key === 'Enter') use.click(); });
        setTimeout(() => input.focus(), 0);
      }
      button('Cancel', () => finish(null), { parent: row, always: true, key: 'ask:cancel' });
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
        const k = Math.max(0, Math.min((now - start) / step, pts.length - 1));   // the frame time can predate `start`
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
    oddsFloaters();
  }

  // The chance, drawn over the creature it was about, so the number lands where
  // the player is already looking when the roll resolves.
  //
  // This is the ONE of the three odds surfaces allowed to go missing, and it is
  // missing for a reason the other two do not share: a float has to name a
  // token that is actually drawn, and `about` may be a creature the players
  // cannot see. The lookup is against the tokens in this snapshot, which
  // sight.shown has already filtered, so an absent id is an unseen creature and
  // gets no float.
  //
  // Note what that is NOT. When an entry names an unseen creature,
  // sight.redact_log empties the whole `rolls` list for it, so the toast and
  // the log line are empty of it too -- and that is the correct answer, since
  // the chance is a function of that creature's AC and resistances. The float
  // is the only surface that goes missing while the number is still available
  // to show, which is the case worth handling: a roll against a creature that
  // is on the board but whose entry was not redacted. Nothing here re-derives
  // anything from the encounter, so a number redaction removed cannot be
  // recovered here even by accident.
  function oddsFloaters() {
    // The newest entry only, which is the scope announceRolls has always used
    // and the one the damage float needs (it is a delta, so it has a "before").
    // Re-floating every entry the snapshot still carries would put a number on
    // the board for a roll from a minute ago, on every update, forever. The log
    // line keeps all eight and is where the older ones live.
    const entry = (snap.log || []).slice(-1)[0];
    if (!entry) return;
    // One float per token, the first chance said about it: an AoE that rolls
    // four saves against the same creature should not stack four numbers on
    // top of each other.
    const seenIds = {};
    for (const r of (entry.rolls || [])) {
      const odds = r && r.odds, about = odds && odds.about;
      const text = oddsText(odds);
      if (!text || !about || seenIds[about]) continue;
      // Found among the drawn tokens or not at all. The snapshot is all this
      // file has, and sync.snapshot has already filtered the tokens the players
      // may see, so an absent id is an unseen creature and gets no float.
      const t = (snap.tokens || []).find(p => p.id === about);
      if (!t) continue;
      seenIds[about] = true;
      const f = svg('text', { x: t.x * C + C / 2, y: t.y * C - 6, 'text-anchor': 'middle',
                              class: 'tx-float tx-odds-float' }, ui.floatLayer);
      f.textContent = text;
      // Keep the whole phrase on the board. "40% to fail the save" is about
      // three cells wide, so a token in the first or last column has half its
      // text outside the viewBox and simply not drawn -- the chance silently
      // missing for exactly the creatures at the edge of the map, which is the
      // same class of bug as the float being absent and just as quiet.
      keepOnBoard(f, ui.W * C, ui.H * C);
      setTimeout(() => f.remove(), 1400);
    }
  }

  // ── connection ───────────────────────────────────────────────────────────
  //
  // What the display is doing about the stream being down.
  //
  // Before this, the pill said "Reconnecting" and the map carried on looking
  // perfectly usable. Every action button stayed enabled, so a player could
  // click Move into an outage and watch nothing happen, or click into a
  // recovery and have the action land against a fight that had moved on. The
  // snapshot on screen is only as current as the last payload that arrived, so
  // while the stream is down it is a picture, not a state.
  function setOnline(ok) {
    if (ui.online === ok) return;
    ui.online = ok;
    if (!ok) {
      // Disarm anything that was armed against the snapshot we are about to
      // stop trusting. A Move mode held open across the outage would be
      // offering a reach map drawn minutes ago, and the click that finishes it
      // would be a move from a position the creatures have since left.
      clearMode();
      // A prompt the engine asked before the outage is asking about a decision
      // that may have been resolved while we were not listening. Close it
      // rather than leave a live answer on screen; act() treats a closed prompt
      // as "no answer", which sends nothing.
      if (el.prompt && !el.prompt.hidden && el.prompt.onEscape) el.prompt.onEscape();
      if (snap) {
        el.banner.textContent = OFFLINE_BANNER;
        el.banner.classList.add('tx-refusal');
        el.banner.setAttribute('role', 'alert');
      }
    } else {
      // Coming back. The server replays the current combat state on every
      // connect, so update() is what makes the bar live again; clearing the
      // refusal here lets the next snapshot speak for itself.
      if (snap) clearRefusal();
    }
    // Rebuild the bar so every action button picks up the new disabled state.
    // Focus is restored by renderSide's own restoreFocus, which skips a control
    // that is disabled and falls back to the board, so a keyboard user is never
    // stranded on a control that can no longer be pressed.
    if (snap) renderSide();
  }

  // ── boot ─────────────────────────────────────────────────────────────────
  async function init() {
    build();
    // Subscribe before the first snapshot arrives, so a fight that arrives while
    // the stream is still down is drawn with its actions already dead.
    // GMConn.on fires straight away with the state as it is, so by the time any
    // snapshot has been rendered this reflects the real stream rather than the
    // optimistic default.
    if (window.GMConn && typeof window.GMConn.on === 'function') window.GMConn.on(setOnline);
    try {
      const r = await fetch('/combat/state');
      const s = await r.json();
      if (s && s.status === 'active' && !snap) update(s);
    } catch (e) { /* no combat endpoint or no fight: nothing to show */ }
    let resizeTimer = 0;
    window.addEventListener('resize', () => {
      clearTimeout(resizeTimer);
      resizeTimer = setTimeout(() => { if (snap) render(); publishPanelExtent(); }, 150);
    });
  }

  // refusal() is exported so the wording is testable as a function rather than
  // only through a click: it is a pure map from (status, body) to a sentence,
  // and the cases that matter are the four the server can return.
  window.Tactics = { update, init, state: () => snap, refusal, pendingBanner, setOnline };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
