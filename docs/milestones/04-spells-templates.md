# Milestone 4: spells and templates

## Goal

Spells on the grid: area templates with a preview, saving throws, concentration, and Kairos's cantrips and level 1 spells first.

## Shipped

- **Areas** (`scripts/tactics/grid.py`, `area()`): sphere, cylinder, cone, line (with width), cube. A square is caught if its centre is inside the shape, boundary included. Shapes are true geometry (a 20 ft sphere is round, 49 squares), never movement distance. Spheres centre on the middle of the target square; cones and lines start where the line from the caster's centre toward the aim leaves the caster's square (a 15 ft cone east is the 1-1-3 shape); a cube from self is the block in front of the caster in the nearest of 8 directions. Walls block the area (no line of effect from the origin to the square's centre).
- **Spell data** (`systems/dnd5e/build_srd.py`, `_spell_mechanics`): casting time, range and origin, attack or save, damage by slot or character level, healing, area (a line's width from the text), concentration, and flags for what the engine cannot run. `systems/dnd5e/tactics_spells.py` merges it with hand-written numbers: Magic Missile's darts, conditions on a failed save (Hold Person, Entangle, Web, ...), and the non-SRD spells Kairos needs (Mind Sliver, Silvery Barbs), mechanics only.
- **Casting** (`scripts/tactics/spells.py`): `cast` with the bonus-action spell rule (PHB p202), slot levels and upcasting, range and line of sight, all checked before anything is spent. Spell attacks go through the normal attack path; save spells roll damage once for everyone in the area, then a save per creature, cover adding to DEX saves; half or none on a success; conditions and Mind Sliver's penalty on a failure. Magic Missile: one roll for all darts (Sage Advice), each dart a separate damage instance. Healing substitutes the spellcasting modifier. Mage Armor sets AC 13 + DEX. A spell the engine cannot run is still cast: slot and action spent, "GM decides the effect".
- **Previews**: `preview` / `preview-area` returns the squares, every creature caught with its chance to fail and expected damage, allies flagged; hit chance for spell attacks. `castable` / `spells` lists known spells and why one cannot be cast now.
- **Effects and concentration** (`scripts/tactics/effects.py`): effects live on the affected token (`Token.effects`) with a source and an end (start or end of the source's next turn, or the source's concentration). Concentration: a CON save per damage instance, dropping to 0 ends it, a new concentration spell ends the old one, effects end with it. Repeat saves at the end of the target's turn.
- **Reactions**: Shield (+5 AC until the start of the caster's turn; only offered when it turns a hit into a miss, never on a natural 20; blocks Magic Missile) and Silvery Barbs (on an enemy's hit or successful save; the lower d20 is kept; advantage to the attacked ally or the caster). Per token `reactions ask|auto|off`; GM creatures take useful reactions. Barbs is offered before Shield.
- **Riders**: build_srd structures grapples (escape DC, restrained while grappled), save or be knocked prone / take a condition (with a repeat save), and save for poison damage (half or none). The engine applies them; the rest of the text is printed as "GM decides: ...". A grapple ends when the grappler is incapacitated, when either moves out of reach, or on a successful `escape`.
- **Actions** (`scripts/tactics/actions.py`): Help (attack version), Hide (needs no hostile line of sight; Stealth vs each hostile's passive Perception, ties to the hider), Escape, Ready (attack, spell or move; a readied spell spends its slot and is held with concentration) and `trigger`. Hidden creatures get advantage, give disadvantage, are not enemy targets, and are revealed when they attack, cast, or a hostile sees them with no cover.
- **Enemy options** (`scripts/tactics/ai.py`): area save actions (breath weapons) are aimed from here or a short move away and scored by expected damage over each target's save chance, minus damage to allies; recharge ("recharge 6") and per-day use tracked and rolled at the start of the creature's turn; `(Frost Breath recharging)`; a grappler keeps biting its target and stays put; concentrating casters are tagged.
- **Monsters**: save proficiencies, skills and passive Perception from the SRD (closes milestone 1's open item on monster saves).
- **CLI**: `cast`, `preview-area`, `spells`, `use`, `help`, `hide`, `escape`, `ready`, `trigger`, `reactions`, `condition <id> remove concentration`; `--react` repeatable. A paused command keeps its seed in `combat/pending.json`, so the re-run replays the same enemy dice.
- **Sheet**: known spells, spell save DC and attack bonus, level, skills and passive Perception from the Kairos sheet.
- **Demo**: `demo.py --scenario mephit` (Kairos against an ice mephit in the Firejolt Cafe).
- **Display**: see the section below.
- Tests: `tests/test_srd_mechanics.py`, `tests/test_tactics_spells.py`, new cases in `test_tactics_grid.py`, `test_tactics_cli.py`, `test_display_combat.py`.

## Display

(Filled in by the display work: cast list, template preview on hover, affected tokens highlighted, Help, Hide, Escape and Ready buttons.)

## Findings

- **Upstream spell data is mostly usable as is.** 319 SRD spells: 194 have no attack, save, damage or healing ("effect": illusions, utility), 10 have saves whose outcome is "other", 5 lines are walls placed at range. A line's width is only in the text ("5 feet wide" or "5-foot-wide").
- **Mind Sliver and Silvery Barbs are not in the SRD.** Their numbers are written by hand in `tactics_spells.py` (no rules text).
- **Rider shapes are few.** 151 SRD attack riders; grapple, save or prone, save for poison and save or condition cover 59 of them. Qualifiers ("a creature other than an elf or undead", "a Medium or smaller creature") are never structured: the engine does not know creature types or sizes yet.
- **A frog's bite now stops a move.** With the grapple applied, an opportunity attack that grapples leaves the mover restrained (speed 0) where it was hit. The frog demo is even deadlier: the second frog bites a restrained Kairos with advantage.
- **Paused commands need the same dice.** A monster's attack roll happens before Kairos decides on Shield; without a kept seed, the re-run would reroll it. `combat/pending.json` holds the seed and the decisions asked so far, keyed by the command without its answer flags.
- **Upstream's mephit breath recharges on a 6 only** (`min_value: 6`): labels come from the data, not from "5-6".

## Decisions

- User (standing): the engine owns the rules; nothing is guessed from prose; small local models get one call and a few lines.
- Advisor (Game Designer) and implementer, pending the user's confirmation of the three questions from the plan:
  - Template rule: **the centre of the square** (boundary included), true geometry. It picks the same squares as DMG half coverage for the straight cone. Sphere origin on the middle of a square rather than a grid corner: about 2.5 ft more reach, much easier to name.
  - Riders: **structured and applied** when the text parses exactly, otherwise text for the GM. Dragging a grappled creature (PHB p195) is not supported: a grappler that moves out of reach lets go (known deviation).
  - Reactions: **offered only when they would change the outcome**, with a per-token `ask | auto | off` (as in Baldur's Gate 3).
- Advisor (Tactical Combat Designer): area options scored by expected damage over save chances minus 1.5 x damage to allies; a grappler keeps its target; Help, Hide and Ready stay player-only in the enemy menu for now; the ice mephit demo.
- Implementer: Hide needs no hostile line of sight at all (total cover), per PHB p177; being seen in the open with no cover reveals. Ties go to the hider (passive Perception is a DC).
- Implementer: Mind Sliver's penalty is spent by the target's next save of any kind (death saves and concentration saves included) and ends at the end of the caster's next turn.
- Implementer: casting always reveals a hidden caster (spell components are not tracked).

## Open

- Creature size and type: Large and bigger footprints, and riders or spells limited by size or type (Hold Person targets humanoids; the engine does not check it).
- Dragging a grappled creature at half speed; shove (knock prone or push 5 ft).
- Persistent zones (Web, Entangle, Spirit Guardians, Moonbeam as areas that stay on the map); today their conditions apply once.
- Multiattack in the enemy menu; monster spellcasting.
- Death Burst and other on-death traits.
- Silvery Barbs on ability checks; Help for ability checks.
- A readied spell lost to broken concentration is only reported at the next `trigger` or turn start.
- Ideas from tactical games and other tabletop RPGs, ranked for milestones 5 and 6, are in the dnd-gm repo (`docs/research/tactical-games.md`, `tabletop-games.md`).
