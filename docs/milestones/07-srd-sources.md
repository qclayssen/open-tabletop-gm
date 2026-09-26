# Milestone 7: SRD sources and attribution

## Goal
Stop depending on a dead upstream, and ship the attribution the bundled SRD data requires.

## Shipped
- `build_srd.py` and `sync_srd.py` read `5e-bits/5e-srd-api` (`packages/5e-database`). Data checked identical to the old source: 319 spells, 237 equipment, 362 magic items, 15 conditions, 334 monsters.
- `systems/dnd5e/NOTICE`: CC-BY-4.0 notices for SRD 5.1 and 5.2, a modification note, and which license the project relies on. README links to it.

## Findings
- `5e-bits/5e-database` is archived. The staleness check watched it, so `sync_srd.py` could never report updates. The new check filters commits to `packages/5e-database`, so unrelated monorepo commits do not trigger a rebuild.
- The first `sync_srd.py` run after this change reports "NEW COMMITS" once (the stored SHA came from the old repo) and rebuilds; that rewrites the recorded source repo.
- **The full build is broken on unmodified main**: `_parse_scale_tables` crashes because a Foundry advancement entry is now a plain string. `--no-fvtt` builds fine but leaves `features` empty. Tracked as a follow-up task.

## Decisions
- Reuse upstream data through the existing pinned build script, not submodules of whole apps (Improved Initiative, DiceCloud, MapTool, Kobold Fight Club were surveyed; none is a better base than the in-repo tactics engine). Decided by the user after the survey.
- Rely on CC-BY-4.0, not the OGL (implementer; the OGL would need its full Section 15 text).

## Open
- Fix the Foundry `advancement` crash (blocks a fresh-clone build).
- Optional: Improved Initiative statblock import/export; Foundry MCP adapter as a display back-end; replace `scripts/dice.py` with `avrae/d20` (low value).
- Optional: add a license pointer to `_meta` in the generated `dnd5e_srd.json`.
