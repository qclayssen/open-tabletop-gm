/* displays.js — the four-display mode.
 *
 * Three jobs, and none of them belongs in display.js:
 *
 *   1. The rail menu on the display that opens the other three.
 *   2. /displays, which opens the set at once and tiles the windows.
 *   3. ?view=dice, the window that shows what the DM has asked for.
 *
 * It is a separate file rather than more of display.js because only one of the
 * four windows needs any of it: a player on a phone asking for a Stealth check
 * should not load a launcher, and display.js is already the largest thing the
 * display serves.
 *
 * The SSE payload arrives through one hook, `window.GMViews.onPayload`, which
 * display.js calls with the raw parsed payload. It is a hook and not a wrap of
 * display.js's own `window._onDiceRequest` for a reason: that handler belongs
 * to the phone dice pad, which locks its buttons and stores a request id, and
 * chaining onto it would mean the dice window inherited a pad it does not have
 * and rendered every request twice — once as a pad and once as a card.
 */
'use strict';

(function () {
  // ── the dice window's state ────────────────────────────────────────────────
  //
  // Declared above the init calls, not below them with their own section:
  // `const` is in the temporal dead zone until its line runs, so a `const` map
  // declared further down this closure is not yet initialised when initDice()
  // creates the first card — which is a throw on the first roll of the evening,
  // on every page that loads this file, from an error that reads as though it
  // came from somewhere else entirely.
  const requests = new Map();   // request_id → card
  const order = [];             // request_ids, oldest first

  // Nothing here reads the view off the body: each part asks for the element it
  // draws into and does nothing when it is absent, so a page that loads this
  // file for the menu (the full display) and a page that loads it for the
  // dice board (the dice window) take the same path.
  initMenu();
  initDice();
  if (document.querySelector('.launcher-page')) initLauncher();

  // ── 1. the rail ───────────────────────────────────────────────────────────

  function initMenu() {
    const btn  = document.getElementById('displays-btn');
    const menu = document.getElementById('displays-menu');
    if (!btn || !menu) return;

    function close() {
      menu.hidden = true;
      btn.setAttribute('aria-expanded', 'false');
    }
    function open() {
      menu.hidden = false;
      btn.setAttribute('aria-expanded', 'true');
    }
    btn.addEventListener('click', e => {
      e.stopPropagation();
      if (menu.hidden) open(); else close();
    });
    // Any click elsewhere closes it, and Escape always does — a menu that can
    // only be dismissed by clicking the button again is a menu a keyboard
    // cannot leave.
    document.addEventListener('click', () => close(), { passive: true });
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape') close();
    });
    menu.addEventListener('click', e => e.stopPropagation());
  }

  // ── 2. the launcher ───────────────────────────────────────────────────────

  /* Four windows, tiled two by two over the screen.
   *
   * `screen.avail*` and not `inner*`: the launcher is a page on the desktop,
   * and tiling against the launcher's own viewport would put four windows
   * inside the bounds of one of them. availWidth/availHeight are the desktop
   * minus the menu bar and the dock, which is the area four windows can share
   * without covering the thing the GM is using to click the button.
   *
   * Sized from the count rather than from the view list, so a three-display
   * set tiles the same way: the first row is the first ceil(n/2) views. */
  function tileFeatures(n, index) {
    const cols = n > 2 ? 2 : n;
    const rows = Math.ceil(n / cols);
    const w = Math.floor(screen.availWidth / cols);
    const h = Math.floor(screen.availHeight / rows);
    const left = (index % cols) * w;
    const top = Math.floor(index / cols) * h;
    return `popup=yes,width=${w},height=${h},left=${left},top=${top}`;
  }

  function initLauncher() {
    const all = document.getElementById('open-all');
    const note = document.getElementById('launcher-note');
    if (!all) return;

    all.addEventListener('click', () => {
      const tiles = Array.from(document.querySelectorAll('.launcher-tile[data-view]'));
      let blocked = 0;
      tiles.forEach((tile, i) => {
        const link = tile.querySelector('a');
        if (!link) return;
        const win = window.open(link.href, 'gm-display-' + tile.dataset.view,
                                tileFeatures(tiles.length, i));
        if (!win) blocked++;
        else win.focus();
      });
      // A blocked popup is silent: window.open returns null and the browser
      // shows a permission strip in the address bar of a page the GM may not
      // be looking at. Say so here, in the one place they just clicked.
      if (blocked && note) {
        note.textContent = blocked + ' of ' + tiles.length + ' windows were blocked. ' +
                           'Allow pop-ups for this page, or open the tiles one at a time.';
      }
    });
  }

  // ── 3. the dice window ────────────────────────────────────────────────────

  /* What the DM asked for, on a screen the table can read.
   *
   * State is kept here rather than read back per event, because three payloads
   * describe one request and none of them describes it alone: `dice_request`
   * arrives when it is issued and says nothing about who has rolled since,
   * `dice_pending` is the only one that says who is still holding a die, and
   * `dice_results` arrives once, at the end, with the rolls that came back.
   * Reassembling them client-side is what lets the window survive a reload —
   * the server replays all three for a newly-connected browser, in that order,
   * for exactly this reason.
   */
  function initDice() {
    if (!document.getElementById('dice-display')) return;
    const list = document.getElementById('dice-requests');
    const idle = document.getElementById('dice-idle');
    if (!list || !idle) return;

    function render() {
      // Newest first: the roll in question is almost always the last one, and
      // the table reads the top of a screen before the bottom of it.
      const cards = order.slice().reverse();
      list.replaceChildren(...cards.map(r => paint(requests.get(r))));
      idle.hidden = cards.length > 0;
    }

    window.GMViews = {
      onPayload(payload) {
        if (!payload) return;
        let changed = false;
        if (payload.dice_request)          changed = note(payload.dice_request) || changed;
        if (payload.dice_results)          changed = landed(payload.dice_results) || changed;
        // After the results, deliberately. A roll lands as `text` then
        // `dice_pending`; reading the snapshot first would mark the request
        // finished on a results list that has not been filled in yet.
        if (payload.dice_pending)          changed = pending(payload.dice_pending) || changed;
        if (payload.dice_request_cancelled) changed = cancelled(payload.dice_request_cancelled) || changed;
        if (changed) render();
      },
    };

    function card(id) {
      let c = requests.get(id);
      if (!c) {
        c = { id, chars: [], spec: '', modifier: 0, advantage: 'normal',
              label: '', dc: null, waiting: [], results: [], cancelled: false,
              done: false };
        requests.set(id, c);
        order.push(id);
        // Ten cards is more than a session asks for and a bounded list is a
        // bounded window: a display left on from Friday has no scrollback to
        // scroll back through.
        while (order.length > 10) requests.delete(order.shift());
      }
      return c;
    }

    /* The request as issued. `characters` is who it was asked of, so a card can
     * show a name as done before the pending snapshot has ever been sent — the
     * snapshot only carries names still waiting, and a roll can land between the
     * two payloads. */
    function note(req) {
      if (!req || !req.request_id) return false;
      const c = card(req.request_id);
      absorb(c, req);
      if (!c.chars.length) {
        c.chars = [String(req.character || 'any')];
      }
      return true;
    }

    /* The snapshot is the whole truth about who is still holding a die, so it
     * replaces the waiting set rather than adding to it.
     *
     * "A card the snapshot says nothing about is finished" is NOT concluded
     * here. The two payloads arrive in the order the rolls happen — `text`,
     * then `dice_pending` — and the text is the one carrying the result. A card
     * marked done on the strength of an empty `pending` therefore says
     * "Everyone has rolled" for the one frame before the result line that
     * proves it, and on the last roll of a request it goes further: the card is
     * finished before its own answer has been attached, so the answer draws
     * after the claim it contradicts.
     *
     * So a card is only finished once it is finished *and* has something to
     * show for it. A name that has left `pending` without a matching result is
     * held as "rolled, result not yet read" and stays a chip rather than
     * becoming a tick — which is also what makes the two payloads compose
     * instead of racing. */
    function pending(snap) {
      const rows = Array.isArray(snap) ? snap : [];
      const seen = new Set();
      rows.forEach(row => {
        if (!row || !row.request_id) return;
        const c = card(row.request_id);
        seen.add(row.request_id);
        c.waiting = Array.isArray(row.pending) ? row.pending.slice() : [];
        absorb(c, row);
        if (Array.isArray(row.results) && row.results.length > c.results.length) {
          c.results = row.results.slice();
        }
        settle(c);
      });
      let changed = false;
      requests.forEach(c => {
        if (!seen.has(c.id) && !c.done) { c.waiting = []; settle(c); changed = true; }
      });
      return true;
    }

    /* A card is finished when nobody is holding a die AND there is a result to
     * show. See `pending` for why the second half is not optional. */
    function settle(c) {
      c.done = c.waiting.length === 0 && c.results.length > 0;
    }

    /* The last roll of a request. The server drops a finished request from the
     * pending set, so without this the card would vanish the instant the answer
     * arrived — taking the answer off the screen a beat after the table asked
     * for it. */
    function landed(res) {
      if (!res || !res.request_id) return false;
      const c = card(res.request_id);
      c.results = Array.isArray(res.results) ? res.results.slice() : [];
      c.waiting = [];
      // The request's own description, for a window that never saw
      // `dice_request`: a reload, or a second screen opened once the roll was
      // already in. Without it the card shows the right rolls under the wrong
      // heading — "1d20 +0" over a 2d6+3 Strength check, which is worse than no
      // card at all because it looks authoritative.
      absorb(c, res.meta);
      settle(c);
      return true;
    }

    /* Copy the request's description onto a card. Shared by all three payloads
     * because they all carry some subset of it and none carries all of it:
     * `dice_request` has the chars and the DC, the snapshot has the waiting set,
     * `dice_results` has whatever the client missed. Each field is only taken
     * when it is actually present, so a payload cannot blank a value an earlier
     * one supplied. */
    function absorb(c, meta) {
      if (!meta || typeof meta !== 'object') return;
      if (Array.isArray(meta.characters) && meta.characters.length) {
        c.chars = meta.characters.map(String);
      }
      if (meta.spec)       c.spec = String(meta.spec);
      if (meta.modifier != null) c.modifier = Number(meta.modifier) || 0;
      if (meta.advantage)  c.advantage = String(meta.advantage);
      if (meta.label)      c.label = String(meta.label);
      if (meta.dc != null) c.dc = Number(meta.dc);
    }

    function cancelled(id) {
      const c = requests.get(id);
      if (!c) return false;
      c.cancelled = true;
      c.waiting = [];
      c.done = true;
      return true;
    }

    // ── painting ────────────────────────────────────────────────────────────

    function el(tag, cls, text) {
      const n = document.createElement(tag);
      if (cls) n.className = cls;
      if (text != null) n.textContent = text;   // never innerHTML: labels are input
      return n;
    }

    /* Who the request is addressed to.
     *
     * "any" is the server's word for a request aimed at whichever phone answers
     * first, so it names nobody and the card says "The table" rather than
     * printing the literal string a player would read as a character. Takes the
     * card as an argument rather than closing over `paint`'s: three of these
     * helpers are siblings of `paint`, and a closure that reaches for its
     * parameter is a ReferenceError on the first roll of the evening. */
    function who(c) {
      const names = c.chars.filter(n => n && n.toLowerCase() !== 'any');
      return names.length ? names.join(' · ') : 'The table';
    }

    /* "1d20 +5", with a real minus sign rather than a hyphen, because this is set
     * large on a screen somebody is reading from the far side of a table.
     *
     * A card that never learned its spec says nothing rather than saying
     * "1d20 +0". Those are different claims: the first is "I don't know", the
     * second is "a straight d20 with no modifier", and a table reading the
     * second one off a screen will argue about the wrong number. The rolls
     * below it are the real answer either way, so the card is still useful
     * while its heading is honest about being incomplete. */
    function rollText(c) {
      if (!c.spec) return '';
      const mod = c.modifier || 0;
      const sign = mod >= 0 ? '+' : '−';
      return c.spec + ' ' + sign + Math.abs(mod);
    }

    function paint(c) {
      if (!c) return document.createComment('gone');
      const card_ = el('li', 'dreq');
      card_.classList.add(c.cancelled ? 'dreq-done' : (c.done ? 'dreq-done' : 'dreq-waiting'));

      const head = el('div', 'dreq-head');
      head.append(el('span', 'dreq-who', who(c)));
      const roll = rollText(c);
      if (roll) {
        const chip = el('span', 'dreq-roll', roll);
        if (c.advantage === 'advantage')         { chip.classList.add('adv'); chip.title = 'Advantage'; }
        else if (c.advantage === 'disadvantage') { chip.classList.add('dis'); chip.title = 'Disadvantage'; }
        head.append(chip);
      }
      if (c.label) head.append(el('span', 'dreq-label', c.label));
      if (c.dc != null) head.append(el('span', 'dreq-dc', 'DC ' + c.dc));
      card_.append(head);

      const state = el('div', 'dreq-state');
      const rolledNames = new Set(c.results.map(r => rollName(r)).filter(Boolean));
      c.chars.filter(n => n && n.toLowerCase() !== 'any').forEach(name => {
        const rolled = rolledNames.size ? rolledNames.has(name.toLowerCase()) : false;
        const waiting = c.waiting.some(w => String(w).toLowerCase() === name.toLowerCase());
        // The tick IS the state, in the label. It used to be a ✓ glyph in the
        // text plus an unstyled `.dchip-mark` span saying "rolled" beside it,
        // and that span had no rule in displays.css at all — so every rolled
        // name read "✓ Aldric rolled" on screen and to a screen reader. One
        // signal, in the text, where the stylesheet can reach it.
        const chip = el('span',
                        'dchip' + (rolled ? ' rolled' : (waiting ? ' dchip-wait' : '')),
                        (rolled ? '✓ ' : '') + name);
        state.append(chip);
      });
      if (c.cancelled) state.append(el('span', 'dreq-cancelled', 'Cancelled by the DM'));
      else if (c.done && !state.childElementCount) state.append(el('span', null, 'Everyone has rolled.'));
      else if (!c.done && !state.childElementCount) state.append(el('span', null, 'Waiting on the table.'));
      card_.append(state);

      if (c.results.length) {
        const out = el('ul', 'dreq-results');
        c.results.forEach(r => out.append(el('li', 'dreq-result', String(r))));
        card_.append(out);
      }
      return card_;
    }

    /* The roller, taken off the front of the roll text.
     *
     * /player-input/dice composes it as "<character> rolls <spec>: …", so the
     * name is the token before " rolls ". Matching on that word rather than on
     * the first colon matters: a character called "Mira" would otherwise be read
     * as "Mira rolls 1d20+5", which is in nobody's `chars` list and so would
     * tick nobody. A roll that does not carry a name — the fallback path when a
     * roll is correlated against a request the server did not issue — simply
     * contributes none, and the pending snapshot decides that name instead. */
    function rollName(text) {
      const m = /^\s*(\S+)\s+rolls\b/.exec(String(text || ''));
      return m ? m[1].toLowerCase() : null;
    }

    render();
  }
})();
