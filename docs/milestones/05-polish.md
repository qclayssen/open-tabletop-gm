# Milestone 5: polish

## Goal

Make the grid easier to read and use, without changing the rules.

## Shipped

- **Visibility** (`scripts/tactics/grid.py`): `Grid.visible_from(square)`, every square with a line of sight, by the same corner rule as `line_of_sight` but stopping at the first clear line (10 to 50 times faster; a test checks it square for square on every shipped map).
- **Sight and fog** (`scripts/tactics/sight.py`): `fog(enc)` is the set of squares at least one living PC sees; `shown()` decides which tokens the players' snapshot carries; `sight(enc, token)` gives the cover from one creature to every square it sees (half or three-quarters; missing squares are no line of sight) plus a one-line text. With `players=True` (the display) only creatures the players can see are counted or listed.
- **CLI**: `sight <token>` (read-only, the GM's text version of the overlay) and `fog hide|dim|off` (stored in `enc.meta["fog"]`, default `hide`). Documented in `scripts/tactics.md`.
- **Snapshot** (`sync.snapshot`): `fog: {mode, visible}` (null when off); in `hide` mode creatures no PC sees are left out, as hidden enemies already were.
- **Display** (`display/static/tactics.js`, `tactics.css`):
  - Fog: squares no PC sees are shadowed. On an unseen creature's turn the banner says "Enemy turn"; squares out of sight are dark and hatched.
  - **Cover** header button (remembered per browser; key C): shades from the selected creature: hatched for no line of sight, dark gold for three-quarters cover, light gold for half, with a legend and the engine's text. With Cover on and no action under way, clicking a creature picks whose view is shaded; it follows the player whose turn it is and refreshes after every move.
  - **Condition badges**: up to two two-letter codes along each token's bottom edge (Pr prone, Gr grappled, Re restrained, Po poisoned, ...), then "+n"; the full list stays in the token's title, the strip and the spoken description.
  - **Keyboard**: the map is one tab stop with a square cursor. Arrow keys move it (moving onto a square previews it, as a mouse hover does), Enter or Space acts on it as a click, Escape cancels, Home goes to the creature whose turn it is.
  - **Accessibility**: every square is described in a polite live region as the cursor lands on it (square, terrain, creature with HP and conditions, out of sight, cover from the selected creature, feet away in move mode). The initiative strip is a list with `aria-current`. Rebuilding the action buttons puts focus back on the same button. Reduced motion also drops damage floaters and smooth scrolling.
- `display/gm-display-app.py`: `sight` is a browser read, only from a creature in the current snapshot, always with `--players` added by the app (a browser flag is refused).
- Tests: `tests/test_tactics_sight.py` (fog, shown, sight, CLI, parity), a new case in `tests/test_display_combat.py`.

## Theatre-of-the-mind parity

Checked with the display off (`TACTICS_NO_DISPLAY=1`): every fact the map shows is in the GM's text. Positions, HP, conditions, concentration and readied actions are in `status`; hit chances in `targets`; area previews in `preview-area`; cover and line of sight in the new `sight`. Fog of war is display only: `status` and `sight` read the same in all three fog modes (`test_the_gm_text_does_not_depend_on_the_display`).

## Findings

- **The full cover map is slow.** 16 corner lines per square cost 0.4 to 0.8 s for a whole map in a fresh process, too slow to run on every snapshot. Fog uses `visible_from` (15 to 150 ms); cover is only computed on request (`sight`, about 0.4 s), for passable squares.
- **Tables give cover to more of the room than expected.** On Firejolt Cafe, from the mephit at I3, the two tables next to it give half cover to most of the room. That is the engine's DMG rule (a feature on any corner line counts), not an overlay bug: the overlay is the same function the attacks use.
- **The auto-mode classifier blocked pointing the live display at another session's scratch campaign.** The browser pass used a static harness page instead: real engine snapshots and `sight` results, with `/combat/do` stubbed. Verified: fog, cover shading from two creatures, badges, keyboard move (arrows preview, Enter moves), focus kept after re-render.

## Decisions

- Advisor council (asked by the user in another session; unanimous): fog **hides creatures no PC can currently see** (as in Baldur's Gate 3), filtered on the server, and dims those squares; `fog dim` stays as a table option (every creature shown), `fog off` shows the whole map. Visible means a living PC has a line of sight past walls; cover never hides; hidden creatures are never drawn. No light or darkvision model.
- Advisor (Interface), implemented: plug every leak. An unseen creature is left out of `order`; on its turn `current` is null and `unseen_turn` true (the banner says "Enemy turn"); log lines name it as "an unseen creature" and lose their dice lines; the sidebar's turn order (`/stats`) leaves it out and shows "Enemy turn". Fogged squares get a hatch as well as the dark fill, so the shading is not colour alone. The GM's own log and text are unchanged.
- Advisor (Game Designer): being out of sight gives no new unseen-attacker advantage (no line of sight is already total cover).
- Implementer: fog counts PCs only (side `pc`), not allied NPCs; with no living PC it is off, rather than blanking the map. The council left allies as "eyes" open for the user.
- Implementer: in the players' view, creature cover counts only creatures the players can see, so the shading never gives away an unseen enemy. An attack still counts every creature, so the shading can in rare cases show less cover than the attack finds.
- Implementer: the SVG is hidden from screen readers; the map speaks through the cursor's live region, so it is one tab stop instead of one per token.

## Open

- **For the user: "last seen" memory.** The council disagrees. Tactician and Interface want a faded, dashed marker (?, name, square, round; no HP; spoken as "Goblin, last seen F7, round 2"; cleared when that square is seen empty). The Game Designer wants none, since a stale marker is a lie once the enemy moves. Not built.
- GM override `reveal <token> on|off` (display only) for an enemy the party hears: a marker without its name (Game Designer). Not built.
- Enemy vision (Tactician, later): enemies should only aim at squares where they last saw a PC, so fog is not one-sided. Frog Pond has no walls, so fog never hides anything there; an ambush there is Hide ("submerged" Stealth at setup).
- Darkness, light and darkvision (fog today is walls only).
- Route roll requests to a bound phone (from milestone 3).
- An upcast picker and readying a move from the browser (from milestone 4).
- Browser pass against a live display once a test campaign can be used on the dev port.
