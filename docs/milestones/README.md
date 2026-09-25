# Tactical combat milestones

One file per milestone. `CLAUDE.md` holds only what every session needs (rules, architecture, commands, workflow); the detail lives here. A new session reads `CLAUDE.md`, then the file for the milestone it is working on.

| # | Milestone | Status | PR |
|---|-----------|--------|----|
| 1 | [Engine core](01-engine-core.md) | done | [#1](https://github.com/qclayssen/open-tabletop-gm/pull/1) |
| 2 | [CLI and GM loop](02-cli-gm-loop.md) | done | [#1](https://github.com/qclayssen/open-tabletop-gm/pull/1) |
| 3 | [Grid display](03-grid-display.md) | done | [#2](https://github.com/qclayssen/open-tabletop-gm/pull/2) |
| 4 | [Spells and templates](04-spells-templates.md) | done | [#5](https://github.com/qclayssen/open-tabletop-gm/pull/5) |
| 5 | [Polish](05-polish.md) | done | [#9](https://github.com/qclayssen/open-tabletop-gm/pull/9) |

## Template

Each file has the same sections, updated at the end of the milestone:

- **Goal**: what the milestone is for.
- **Shipped**: what exists now, and where.
- **Findings**: what was learned the hard way (bugs, data quirks, traps), so no later session rediscovers it.
- **Decisions**: choices made, by whom (user or implementer), and why.
- **Open**: what is left, deferred or known to be imperfect.

A planned milestone has **Plan** and **Questions for the user** instead of Shipped and Findings.
