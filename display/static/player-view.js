/* ── ?view=player — the shared read-only table screen (#118) ────────────────
 *
 * WHAT THIS IS, PRECISELY
 * =======================
 * A presentation mode, not an authorization boundary. #118 says so outright:
 * "This is a presentation feature, not an authorization boundary or a reported
 * data-exposure vulnerability." Do not use it to keep secrets from anyone who
 * can reach this port. It hides GM *furniture* so a projector or second table
 * screen shows the table, not the console.
 *
 * WHY A SEPARATE FILE
 * ===================
 * #118's load-bearing criterion is "derive permitted surfaces from ONE explicit
 * policy so new operator controls stay hidden by default". A 4,400-line
 * display.js that also holds this policy is not one policy, it is one policy
 * with a large unreviewable prefix. This module is the whole policy, and it is
 * the only thing that decides what a player may see. `display.js` is
 * deliberately untouched.
 *
 * HOW "HIDDEN BY DEFAULT" IS ACTUALLY ACHIEVED
 * ============================================
 * Honestly, this needs saying out loud, because a CSS class does not deliver
 * it on its own. Marking operator controls with an attribute and hiding those
 * fails OPEN: a new control that forgets the attribute is visible to players.
 * Two things close that:
 *
 *   1. Containment. Operator UI lives inside containers that are hidden whole
 *      (#sidebar, #tx-actions, #tx-log). A new control added inside one of them
 *      is hidden for free, because its parent is. Most new operator controls
 *      land there by construction.
 *   2. The teeth are in the test, not the CSS.
 *      `tests/test_player_view.py` enumerates every surface id in index.html
 *      and every one tactics.js builds, and FAILS if any is in neither list.
 *      So a new operator control cannot be added without someone classifying
 *      it, which is the only mechanism that actually makes the default hold.
 *      Read that test as part of this policy.
 *
 * MutationObserver is load-bearing, not decoration: tactics.js is `defer`red,
 * so it builds #tx-panel and every tactical control AFTER this file runs. A
 * one-shot pass at DOMContentLoaded would leave the entire battle-map toolbar,
 * its cover toggle, its action buttons and its combat log visible on the
 * player screen — the exact thing #118 asks to suppress.
 */
(function () {
  'use strict';

  /* ── THE POLICY ──────────────────────────────────────────────────────────
   *
   * Player-facing: what a person at the table may see and use. Positive
   * entries only — anything not here is treated as operator furniture.
   *
   * `#tx-info` IS HERE DELIBERATELY AND MUST NOT BE REMOVED. It is not a
   * control, it is the combat read-out, and it carries the death-save prompt.
   * Hide it and a player at the table cannot see that their character is
   * dying. That regression is worse than any control left on screen.
   */
  const PLAYER_SURFACES = new Set([
    // Narration — the reason the screen exists.
    'text-scroll', 'text-content', 'narrate-row', 'narrate-label',
    'new-content-pill', 'skip-story',

    // Battle map, read-only. The map and its initiative order are public
    // information at the table; the controls around them are not.
    'tx-panel', 'tx-board', 'tx-strip', 'tx-round', 'tx-map', 'tx-banner',
    'tx-info', 'tx-say', 'tx-keys',      // tx-keys is the a11y description of tx-board

    // Player input — a player screen with no way to answer is useless.
    'input-panel', 'input-body', 'input-answer', 'input-footer', 'input-error',
    'input-panel-label', 'send-btn', 'player-input-text',
    'dp-tabs', 'dp-reel-wrap', 'dp-reel', 'dp-die-row', 'dp-roll',
    'dp-name', 'dp-mod-val', 'dp-result-line', 'dp-hint', 'dp-toggle-sum',

    // Dice. Players roll these at the table; dp-offer-row / dp-options carry the
    // advantage and disadvantage choices, which are the player's to make.
    'dice-pad', 'dice-pending-badge', 'dp-bound', 'dp-label',
    'dp-offer-row', 'dp-options', 'dp-toggle',

    // Damage forecast, same class as tx-info: a read-out of what will land on
    // whom, not a control over it. Players see incoming damage at the table.
    'tx-forecast',

    // The SRD reference. Legitimate table knowledge, contains nothing secret,
    // and input-only hides it only because a phone has no room for it.
    'srd-modal', 'srd-panel', 'srd-body', 'srd-close', 'srd-category-badge',

    // Ambient scene and accessibility. The skip link is an a11y affordance and
    // must survive every view that hides chrome.
    'corner-logo', 'flash-overlay', 'sky', 'skip-link', 'input-badge',

    // Ambient chrome that is legitimately part of the scene.
    'bg-a', 'bg-b', 'particles', 'vignette', 'frame', 'bottom-fade',
    'main-content'
  ]);

  /* ── Operator surfaces: the deny list ───────────────────────────────────
   *
   * Everything #118 names, plus what is actually GM furniture. Listed for
   * documentation and for the early-exit it buys; containment already hides
   * most of these, and the policy catches any that escape their container.
   */
  const OPERATOR_SURFACES = new Set([
    // #118: "operator log"
    'sent-log', 'tx-log',
    // #118: "tactical command controls"
    'tx-actions', 'tx-leads', 'tx-prompt', 'tx-toast',
    // #118: "cover toggle"
    'tx-cover',
    // #118: "approval/settings controls"
    'device-approvals', 'cp-status', 'cp-body',
    'controls-toggle', 'controls-toggle-row',
    'dm-help-btn',
    'sidebar', 'sidebar-toggle', 'char-tabs', 'character-pane',
    'sb-turn-section', 'sb-turn-list', 'sb-quests', 'sb-factions', 'sb-clocks',
    'sheet-panel', 'sheet-content', 'sheet-modal', 'sheet-close',
    // Measurement rulers: operator instrumentation, not table information.
    'tx-ruler-out',
    // Audio / presentation controls the GM drives.
    // Injected at RUNTIME by display.js `_initModeSwitcher`, so they exist in
    // no template and no source scan can find them. Found instead by
    // test_player_view_browser.py, which walks the live DOM: the "Phone Mode"
    // picker lists the character roster from the SSE stats payload, which is
    // not "intended player-facing state" on a screen everyone at the table can
    // see. Same hazard as #217 — a control whose markup is generated rather
    // than declared is invisible to a source-level inventory.
    'phone-mode-btn', 'phone-mode-menu',

    // Layout and navigation the GM drives. input-only hides these too; the two
    // modes agree here because a table screen has no business offering them.
    'input-panel-header', 'input-toggle-arrow', 'overview-link',
    'ruleset-badge', 'scene-indicator',
    'tx-min',                      // "Hide map" — layout control, not table information

    // Sidebar internals, hidden by containment with #sidebar but classified
    // anyway so the deny list stays complete if one is ever moved out.
    'sb-clocks-list', 'sb-round',

    // The world clock. Hidden in input-only for space; here because it is
    // campaign state the GM curates, and the table reads time from the fiction.
    'world-clock', 'wc-date', 'wc-icon', 'wc-sub',

    'audio-controls', 'sfx-row', 'sfx-track',
    // Scroll and pacing controls that let the GM scrub the narration away
    // mid-sentence in front of the table.
    'speed-row', 'speed-label', 'vb-slider', 'vb-val', 'verbosity-row',
    'theme-row', 'theme-label', 'textsize-row', 'ts-dec', 'ts-inc', 'ts-val',
    'skip-turn-btn', 'autorun-indicator', 'waiting-indicator',
    'stream-toast', 'conn-status'
  ]);

  /* Measurement templates and the cover toggle are toggled by class/attr on
   * shared nodes rather than being standalone elements, so they need explicit
   * handling: turning them off by id would leave the ruler drawn on the board. */
  const OPERATOR_ATTRS = [
    ['.tx-rulers', 'hidden'],   // the Ruler / Cone / Circle button group
    ['.tx-actions', 'hidden']   // belt-and-braces; already hidden by id above
  ];

  const PV_ATTR = 'data-pv';
  let active = false;

  function isPlayerView() {
    return new URLSearchParams(location.search).get('view') === 'player';
  }

  /** Hide one surface. Uses the same !important the input-only mode uses. */
  function hide(el) {
    if (!el || el.dataset.pv === 'hidden') return;
    el.dataset.pv = 'hidden';
    el.style.setProperty('display', 'none', 'important');
  }

  /** Classify a single node against the policy. Idempotent. */
  function applyTo(node) {
    if (!active || !node || node.nodeType !== 1) return;
    if (node.dataset && node.dataset.pv) return;      // already decided
    if (node.id && PLAYER_SURFACES.has(node.id)) return;  // permitted; leave visible

    if (node.id && OPERATOR_SURFACES.has(node.id)) { hide(node); return; }

    // An unclassified node with no id: fall back to containment. Anything
    // inside an already-hidden operator container is hidden with it, so there
    // is nothing to do here; anything else is left alone and is the test's
    // problem, not silently guessed at.
  }

  function sweep(root) {
    applyTo(root);
    const scope = root.querySelectorAll ? root.querySelectorAll('[id]') : [];
    scope.forEach(applyTo);
    OPERATOR_ATTRS.forEach(([sel, attr]) => {
      (root.querySelectorAll ? root.querySelectorAll(sel) : []).forEach(el => {
        if (el.id && PLAYER_SURFACES.has(el.id)) return;
        hide(el);
      });
    });
  }

  function activate() {
    active = true;
    document.body.classList.add('player-view');
    sweep(document.body);

    // tactics.js is deferred: #tx-panel, #tx-actions, #tx-cover and #tx-log do
    // not exist yet. Without this the whole tactical toolbar is visible on the
    // player screen.
    const mo = new MutationObserver(muts => {
      for (const m of muts) {
        for (const n of m.addedNodes) {
          if (n.nodeType === 1) sweep(n);
        }
      }
    });
    mo.observe(document.body, { childList: true, subtree: true });
    document.dispatchEvent(new CustomEvent('gm:player-view', { detail: { on: true } }));
  }

  function deactivate() {
    active = false;
    document.body.classList.remove('player-view');
    document.querySelectorAll('[data-pv="hidden"]').forEach(el => {
      delete el.dataset.pv;
      el.style.removeProperty('display');
    });
    document.dispatchEvent(new CustomEvent('gm:player-view', { detail: { on: false } }));
  }

  // Exported so tests and the mode switcher can read the policy rather than
  // restate it. Read-only: nothing outside here should edit these.
  window.GMPlayerView = {
    PLAYER_SURFACES,
    OPERATOR_SURFACES,
    activate,
    deactivate,
    isPlayerView
  };

  function boot() {
    if (!isPlayerView()) return;
    // Body may still be parsing if this ever gets loaded non-deferred.
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', activate, { once: true });
    } else {
      activate();
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();