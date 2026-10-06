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
  - **Resolved 2026-09-29.** The guard is in place at `build_srd.py:690-694`: `advancement` is
    unwrapped from the newer id-keyed dict, and a non-dict entry is skipped rather than
    subscripted. Verified by feeding both real shapes through the function — a 2014
    `ScaleValue` of `type: number` returns `{'wild-shape': {'1': '+1', '2': '+2'}}`, and the
    `dice` default returns `{'spell-slots': {'1': '2d6', '2': '3d6'}}`. A plain string sitting
    alongside a valid entry no longer stops the build. The finding is kept above because it is
    what a reader checks first.

## Decisions
- Reuse upstream data through the existing pinned build script, not submodules of whole apps (Improved Initiative, DiceCloud, MapTool, Kobold Fight Club were surveyed; none is a better base than the in-repo tactics engine). Decided by the user after the survey.
- Rely on CC-BY-4.0, not the OGL (implementer; the OGL would need its full Section 15 text).
- **No Foundry VTT integration, and not as a pending item.** Foundry appears in this project in
  exactly two roles, both settled. It is a *data source* — `foundryvtt/dnd5e` (MIT code,
  CC-BY-4.0 content) supplies class and racial features to `build_srd.py`, with provenance on
  every record and a `NOTICE` carrying the attribution. And it is *inspiration* — a research
  directory, deliberately not an implementation. Its EULA forbids packages that "function in
  the absence of the base software", so vendoring it is the licensing violation, and building
  against it would reintroduce the dependency the survey above already rejected. A Foundry
  display back-end is that same survey question asked again.

## Open
- ~~Fix the Foundry `advancement` crash (blocks a fresh-clone build).~~ **Closed 2026-09-29**;
  see Findings.
- ~~Add a license pointer to `_meta` in the generated `dnd5e_srd.json`.~~ **Closed 2026-10-04**
  by `open-tabletop-gm#249` ("the dataset says what may be done with it"), which added the
  `license`, `license_url` and attribution fields to every source in `_meta`. A consumer reading
  the dataset alone now has the licence pointer, which was the whole point of the line.

  The line previously read *"Still open, and the only genuinely unfinished line in this
  milestone"*, which `main` asserted while simultaneously shipping the fix. It also pointed at
  `build_srd.py:989` for the `_meta` block; that was already stale and is now at
  `build_srd.py:1395`. Both corrections land here rather than in the PR that closed the work,
  because that PR's declared scope was the builder and `NOTICE`, not this milestone doc.

  Still true, and worth keeping: this repo's builder writes no per-record `_license`; the
  sibling `claude-dnd-skill` builder does, so the two datasets still differ in provenance
  granularity and this one remains the coarser of the two.
- Declined, not deferred: Improved Initiative statblock import/export; a Foundry MCP adapter as
  a display back-end; replacing `scripts/dice.py` with `avrae/d20`. The first and last are
  superseded by the in-repo engine; the middle reintroduces the dependency rejected in
  Decisions. Listed so they are not re-proposed, not as a backlog.
