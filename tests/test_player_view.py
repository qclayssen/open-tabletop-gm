"""#118 — the `?view=player` table screen, and the policy that governs it.

The policy is the PLAYER_SURFACES / OPERATOR_SURFACES sets in
`display/static/player-view.js`. #118 asks that permitted surfaces come from
"one explicit policy so new operator controls stay hidden by default".

That default cannot be delivered by CSS alone. Marking operator controls with
an attribute and hiding those fails OPEN: a new control that forgets the
attribute is visible to players. So the enforcement lives here. These tests
enumerate every surface the display actually builds and FAIL on any that
neither list classifies — which is what makes "hidden by default" true rather
than aspirational.

Read these as part of the policy, not as a companion to it.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

DISPLAY = pathlib.Path(__file__).resolve().parents[1] / "display"
POLICY_JS = DISPLAY / "static" / "player-view.js"
TEMPLATE = DISPLAY / "templates" / "index.html"
TACTICS_JS = DISPLAY / "static" / "tactics.js"
CSS = DISPLAY / "static" / "display.css"

# #118 names these specifically. Asserted by name so that deleting one from the
# policy is a test failure rather than a silent scope reduction.
AC_NAMED_OPERATOR = (
    "sent-log",       # operator log
    "tx-log",         # combat log
    "tx-actions",     # tactical command controls
    "tx-cover",       # cover toggle
    "device-approvals",  # approval controls
    "cp-status",      # settings / control-panel status
)

# #118: the death-save prompt lives here. Hiding it is worse than any control
# left on screen, so it is pinned as player-facing in both directions.
MUST_STAY_PLAYER_FACING = (
    "tx-info",
    "tx-board",
    "text-scroll",
    "input-panel",
)


def _policy_sets() -> tuple[set[str], set[str]]:
    """Read the two sets out of the policy module without a JS runtime.

    Parsed rather than imported: the repo has no node in CI, and the point is
    to prove the *source of truth* is the JS, not a Python copy that could drift.
    """
    src = POLICY_JS.read_text(encoding="utf-8")
    player = _set_from(src, "PLAYER_SURFACES")
    operator = _set_from(src, "OPERATOR_SURFACES")
    return player, operator


def _strip_js_comments(src: str) -> str:
    """Remove `//` line comments before any quote pairing.

    Load-bearing, not tidiness. Pairing quoted tokens with `'([^']+)'` over raw
    source silently MISREADS this policy: prose comments contain apostrophes
    ("which are the player's to make", "input-only hides it"), and a stray
    apostrophe pairs a real id with a word from a comment. The result is a
    short list that looks plausible and reports live surfaces as unclassified —
    a test that fails for the wrong reason and invites someone to "fix" it by
    deleting entries from the policy.
    """
    return re.sub(r"//[^\n]*", "", src)


def _set_from(src: str, name: str) -> set[str]:
    src = _strip_js_comments(src)
    m = re.search(rf"const {name} = new Set\(\[(.*?)\]\)", src, re.S)
    assert m, f"{name} not found in {POLICY_JS.name}; the test must follow the policy"
    return set(re.findall(r"'([^']+)'", m.group(1)))


def _template_ids() -> set[str]:
    return set(re.findall(r'id="([A-Za-z0-9_-]+)"', TEMPLATE.read_text(encoding="utf-8")))


def _tactics_ids() -> set[str]:
    """Ids tactics.js builds at runtime.

    `#tx-panel` and its ~15 controls are created by `build()` in JS, so they
    exist in no template. Reading only index.html would classify a whole combat
    UI as "unclassified" and prove nothing.
    """
    return set(re.findall(r'id="(tx-[a-z0-9-]+)"', TACTICS_JS.read_text(encoding="utf-8")))


@pytest.fixture(scope="module")
def policy():
    return _policy_sets()


# ── THE TEETH: nothing may be unclassified ────────────────────────────────

def test_no_surface_is_unclassified(policy):
    """The whole mechanism. Every surface the display builds is either
    player-facing or operator furniture. Anything else fails here.

    A new operator control cannot be added without someone deciding which side
    of the line it is on — and until they do, this is red, not silently visible
    to players.
    """
    player, operator = policy
    surfaces = _template_ids() | _tactics_ids()
    unclassified = sorted(s for s in surfaces if s not in player and s not in operator)
    assert not unclassified, (
        "surfaces in neither PLAYER_SURFACES nor OPERATOR_SURFACES — classify "
        f"each in {POLICY_JS.name}: {unclassified}"
    )


def test_the_two_lists_do_not_overlap(policy):
    """An id in both lists is ambiguous, and `applyTo` resolves it in favour of
    player-facing — so the deny entry silently does nothing. That is a bug
    waiting for someone to trust a suppression that is not happening."""
    player, operator = policy
    both = sorted(player & operator)
    assert not both, f"classified as both player-facing and operator: {both}"


def test_every_ac_named_operator_surface_is_suppressed(policy):
    """#118's own words, one by one. If a future refactor drops the operator log
    from the deny list, this fails before the scope quietly narrows."""
    _, operator = policy
    missing = [s for s in AC_NAMED_OPERATOR if s not in operator]
    assert not missing, f"#118 requires these suppressed, not classified: {missing}"


@pytest.mark.parametrize("surface", MUST_STAY_PLAYER_FACING)
def test_required_player_surface_is_not_suppressed(policy, surface):
    """`tx-info` is in MUST_STAY_PLAYER_FACING because it carries the
    death-save prompt. Hiding it means a player cannot see that their
    character is dying — a worse screen than the one #118 set out to fix."""
    player, operator = policy
    assert surface in player, f"{surface} must stay player-facing"
    assert surface not in operator, f"{surface} must not be operator furniture"


def test_tx_info_is_not_hidden_by_css():
    """Guards the CSS half independently of the JS half. #tx-info is called out
    by name in the CSS comment explaining why it is absent; this makes the
    absence an assertion instead of a hope."""
    css = CSS.read_text(encoding="utf-8")
    player_block = css.split("body.player-view")[-1].split("input-only view")[0]
    assert "#tx-info" not in player_block, (
        "#tx-info carries the death-save prompt and must not be hidden")


def test_the_tactical_panel_is_player_facing_but_its_controls_are_not(policy):
    """The distinction #118 actually turns on: the map is public information
    at the table, the toolbar around it is not. A policy that hid the map would
    satisfy 'no GM tools' and be useless."""
    player, operator = policy
    assert "tx-panel" in player and "tx-board" in player
    for control in ("tx-actions", "tx-cover", "tx-log"):
        assert control in operator, f"{control} is a GM control and must be suppressed"
    # tx-rulers is a CLASS, not an id, so it cannot be denied by id at all --
    # it has to be in OPERATOR_ATTRS or the ruler buttons stay live.
    clean = _strip_js_comments(POLICY_JS.read_text(encoding="utf-8"))
    attrs = re.search(r"const OPERATOR_ATTRS = \[(.*?)\];", clean, re.S)
    assert attrs, "OPERATOR_ATTRS is gone; class-addressed GM controls would show"
    assert ".tx-rulers" in attrs.group(1), (
        "tx-rulers is class-addressed and must be denied via OPERATOR_ATTRS")


def test_class_based_operator_surface_is_handled():
    """`.tx-rulers` is a class, not an id, so it cannot be denied by id alone.
    It carries OPERATOR_ATTRS in the policy; if that entry is dropped, the ruler
    buttons stay live on a player screen."""
    src = _strip_js_comments(POLICY_JS.read_text(encoding="utf-8"))
    assert "OPERATOR_ATTRS" in src and ".tx-rulers" in src, (
        "the ruler/Cone/Circle group is not id-addressable and must be handled "
        "via OPERATOR_ATTRS")


def test_the_policy_is_loaded_and_deferred():
    """Ordering matters. tactics.js builds #tx-panel in JS; if player-view.js
    loaded after it without the observer, a frame could render GM controls on a
    player screen. Deferring both preserves document order, so the observer is
    armed first."""
    html = TEMPLATE.read_text(encoding="utf-8")
    assert "player-view.js" in html, "the policy is never loaded"
    assert re.search(r'<script src="/static/player-view\.js" defer>', html)
    # Match the SCRIPT TAGS, not the bare names: "tactics.js" also appears in
    # display.css link comments and in this file's own prose, so a bare index()
    # finds the wrong occurrence and asserts something meaningless.
    pv = html.index(re.search(r'<script src="/static/player-view\.js" defer>', html).group(0))
    tx = html.index(re.search(r'<script src="/static/tactics\.js" defer>', html).group(0))
    assert pv < tx, "player-view.js must be declared before tactics.js"


def test_a_mutation_observer_guards_late_built_dom():
    """tactics.js is deferred, so the tactical DOM does not exist when the
    policy boots. Without this the observer, a one-shot sweep at load leaves
    the entire battle-map toolbar visible on the player screen — which is the
    failure #118 is about, arriving through a different door."""
    src = POLICY_JS.read_text(encoding="utf-8")
    assert "MutationObserver" in src
    assert "subtree: true" in src and "childList: true" in src, (
        "the observer must cover the whole subtree, or nodes built inside "
        "tactics.js are missed")


def test_the_module_states_that_it_is_not_an_authorization_boundary():
    """#118: 'This is a presentation feature, not an authorization boundary.' The
    module is the first thing anyone opening this file reads, and the honest
    framing has to be in it rather than only in the issue."""
    head = POLICY_JS.read_text(encoding="utf-8")[:2000]
    assert "not an authorization boundary" in head


def test_player_view_does_not_mutate_the_normal_operator_display():
    """'ordinary operator behavior is retained' (#118). The mode is inert unless
    ?view=player is present, and deactivating restores what was hidden rather
    than clearing a blanket style attribute."""
    src = POLICY_JS.read_text(encoding="utf-8")
    assert "if (!isPlayerView()) return;" in src, (
        "the policy must be inert without the query parameter")
    assert "removeProperty('display')" in src, (
        "deactivate must undo its own hiding, not blank every display value")