# Milestone 4: spells and templates

## Goal

Spells on the grid: area templates with a preview, saving throws, concentration, and Kairos's cantrips and level 1 spells first.

## Plan

- **Templates** in `grid.py`: cone, sphere (radius), line (with width), cube, using the DMG rule that a square is affected if the template covers at least half of it (or its centre; pick one and document it).
- **Rules interface**: `cast(caster, spell, target_or_point, ctx, roller, player)`; spell data from the SRD (`lookup.lookup_record(..., "spell")`), falling back to a flag when a spell's effect cannot be read exactly.
- **Saves**: the target rolls (players roll their own under `roll_mode: players`), half damage on success where the spell says so.
- **Concentration**: `Token.concentration`; the CON save DC already comes back from `damage()` as `concentration_dc`; one spell at a time; tracker sync.
- **Kairos first**: Fire Bolt (attack, done), Mind Sliver (INT save, subtract 1d4 from the next save), Magic Missile (auto-hit darts, upcast), Shield (reaction, +5 AC until his next turn, triggered by a hit), Mage Armor (AC 13 + DEX, 8 hours), Silvery Barbs (reaction reroll, inspiration to an ally).
- **Spell slots**: spend on cast; written back at `end` (already supported).
- **Actions**: Help, Hide (Stealth vs passive Perception) and Ready (a trigger and an action).
- **CLI**: `cast <token> <spell> <target|square> [--level N]`, `preview-area <token> <spell> <square>`; display: template preview on hover, affected tokens highlighted.
- **GM loop**: enemy options include save spells and breath weapons (the `save` actions with a parsed `area`).

## Questions for the user

- Structure common riders (grapple with escape DC, prone on a failed save) so the engine applies them, or keep them GM-decided?
- Reactions (Shield, Silvery Barbs): ask every time a trigger happens, or only when the player has turned them on for the fight?
- Template rule: DMG "half a square" or "the centre of the square"?
