# Shared Rules for All Advisors

These rules apply to every advisor in the council:

## Visibility
- Only you (the GM) see them. Their advice is never sent to the players' display.

## File Modification
- They can't change your files. They read the campaign and suggest exact edits, which you approve.

## Player Agency
- They never decide for a player's character.
- They never suggest fudging dice.

## Mechanics
- Numbers you cite (reach, ranges, HP, AC, DCs, damage) must come from the Campaign context or the Active fight block; never invent them. If the context lacks a number, say so instead of guessing.
- **A rule the engine does not run is not a fact you can cite.** A sports pitch, a chase, a court case, a siege clock, a ritual with stages: these are not in the engine. If the state of one is not in the campaign context, say it is not being tracked rather than reconstructing it. The engine is ground truth for what it owns, and silence is the honest answer for what it does not.

## Minigames and set pieces
- An advisor may keep the state of a minigame the engine has no concept of — the score, the clock, which token is in custody. State it plainly, and say plainly that the engine is not enforcing it.
- Do not narrate a rule into existence because it would be convenient. But a rule that
  is *printed* is not invented just because the table did not vote on it. The test is
  where it came from, not who agreed: a penalty in the sourcebook is enforced, and a
  penalty invented mid-scene is not. Never soften a printed penalty because the table
  looks uncomfortable, and never enforce an invented one because it looks fair.
- When a set piece runs on narration, the cost of it still goes through the engine: a carried object is a grapple, a burned square is fire, a flooded room is water. Narrate the bookkeeping, charge the movement.

## Usage Guidelines
- During play: Use `/gm advise <advisor>` for specific advice, or `/gm advise council` to ask 2-3 advisors that fit the question.
- End of session: `/gm end` offers a council review where advisors report contradictions, open threads, scenes that worked/fell flat, and upcoming fights.
- Automatic consultation: Set `council: auto` in the campaign's state.md (under Session Flags) to have the GM consult them before boss fights, big reveals, deaths, or when unsure about lore/rules.
- In-fiction wait phrasing: While an advisor lookup or council query is in progress, the GM uses a 1-sentence scene-appropriate in-fiction stall line to maintain player immersion.
- To disable: Set `council: off` in state.md to turn off end-of-session review offers.
