# Milestone 3: grid display

## Goal

The battle map in the display companion: it appears when grid combat starts, updates live, lets the player act by clicking, and hides when combat ends.

## Shipped

- `display/gm-display-app.py`: `POST /combat` (engine snapshot, broadcast as the `combat` SSE event and replayed on connect), `GET /combat/state`, `POST /combat/do` (a click runs the GM's own CLI; LAN token, device approval, rate limit, current player only; results queued for the GM via `check_input.py`). `GM_DISPLAY_PORT` runs a display on another port.
- `display/static/tactics.js` and `tactics.css`: SVG map, reach and dash shading, path preview with opportunity attack warning, action bar, targets with hit chance, roll prompt (type, roll in the browser, or Roll for me), animated moves, damage floaters, turn banner, initiative strip, log, phone layout.
- `display/templates/index.html`: a stylesheet link, a script tag, one line in the SSE handler.
- Tests: `tests/test_display_combat.py`. Browser-verified at 1400 px and 375 px.

## Findings

- **Another display may hold port 5001** (the user's `claude-dnd-skill`). The engine and `push_stats.py` always posted there. Test on another port with `GM_DISPLAY_PORT`, keep `TACTICS_NO_DISPLAY=1` in tests, and never stop the other app.
- **Roll for me** first switched the whole action to engine dice, discarding a d20 the player had already rolled. It now rolls only what is missing.
- **Phone layout.** The page's sidebar and settings (z-index 6 to 8) drew over the panel. Below 860 px the panel sits at z-index 9, under dialogs and device approvals.
- **Browser tool quirks** (not app bugs): screenshots can be taken before the SSE update paints; a synthetic hover can fire `pointerleave` and clear the path preview; a ref click on an SVG `<g>` can miss. Check state with JavaScript before assuming a bug.
- Test runs leave `display/player_input.json`, `stats.json` and other runtime files (gitignored). Remove them, or the GM will drain stale test actions in a real session.

## Decisions

- User: vanilla JS and SVG, no build step, the reference page's look; works at phone width in `--lan` mode.
- Implementer: the grid has its own roll prompt instead of routing to the phone dice drawer.
- Implementer: player clicks run as subprocess calls to the CLI, one at a time, so the browser and the GM use exactly the same code path.

## Open

- Route roll requests to the phone dice drawer when a phone is bound (the existing `dice_request` flow).
- Help, Hide and Ready are greyed out (milestone 4).
- The GM and a browser acting at the same instant could race on `encounter.json` (writes are atomic; the last one wins).
