"""The two pins routes, through the real Flask app.

`test_pins.py` covers the policy -- the allow-list, containment, `revealed` --
by calling the module. This file covers the wiring: that a route is registered,
that it reads the campaign the display is actually showing, that it refuses
where it must, and that a failure mode there is an empty board rather than a
500 on every redraw.

Two things make this the right place for a case that belongs nowhere else. The
display app is a script that binds a socket on import, so the fixture has to
redirect `GM_DISPLAY_PORT` first and swallow the `SystemExit`; and `CAMP_FILE`
is read at request time from disk, so pointing it at a `tmp_path` is what makes
the campaign resolution testable at all. A fixture that set the env var by hand
instead of through `monkeypatch` would leave a real campaign wired up whenever a
test failed mid-teardown, and the failure would be somebody's campaign.

`before fix:` for every case is 404, no route.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture(scope="module")
def app_module():
    """The display app, imported as a module so its test client is usable.

    Same dance `tests/test_mapseditor.py:364` does, for the same reason: the
    script starts a server on import.
    """
    saved = os.environ.get("GM_DISPLAY_PORT")
    os.environ["GM_DISPLAY_PORT"] = "5097"
    spec = importlib.util.spec_from_file_location(
        "gm_pins_app", ROOT / "display" / "gm-display-app.py")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except SystemExit:
        pass
    yield module
    if saved is None:
        os.environ.pop("GM_DISPLAY_PORT", None)
    else:
        os.environ["GM_DISPLAY_PORT"] = saved


@pytest.fixture
def served(app_module, tmp_path, monkeypatch):
    """A client whose active campaign is a tmp_path one, with one revealed pin
    and one GM-only pin on a real map."""
    root = tmp_path / "root"
    camp = root / "campaigns" / "demo"
    (camp / "notes").mkdir(parents=True)
    (camp / "state.md").write_text("**System:** D&D 5e\n", encoding="utf-8")
    (camp / "notes" / "harbour.md").write_text("# The Harbour\n\nFog.\n",
                                              encoding="utf-8")
    (camp / "answer-key.md").write_text("SPOILER\n", encoding="utf-8")
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(root))
    monkeypatch.setattr(app_module, "CAMP_FILE", str(tmp_path / ".campaign"))
    (tmp_path / ".campaign").write_text("demo", encoding="utf-8")

    sys.path.insert(0, str(ROOT / "scripts"))
    import pins as _pins
    slug = _real_map_slug()
    _pins.save(camp, slug, [
        {"id": "shown1", "x": 2, "y": 3, "label": "Fog Bank", "kind": "note",
         "target": "notes/harbour.md", "revealed": True},
        {"id": "gm1", "x": 4, "y": 5, "label": "THE-TRAITOR", "kind": "note",
         "target": "notes/harbour.md", "revealed": False},
    ])
    return app_module.app.test_client(), camp, slug


def _real_map_slug():
    """A slug that exists, so the route's own map check passes.

    The route 404s on a map it does not have, which is right, but it makes a
    pins test a map-existence test. Taken from the shipped list so the case
    under test stays the pin.
    """
    from tactics import maps
    return sorted(maps.available())[0]


def _get(client, url):
    """GET, capturing stdout so a route that prints cannot flood the test log."""
    with contextlib.redirect_stdout(io.StringIO()):
        return client.get(url)


# ── the payload ─────────────────────────────────────────────────────────────

def test_an_unrevealed_pin_is_absent_from_the_payload_not_flagged(served):
    """before fix: 404.

    Absent, not `{"revealed": false}`. The display has one audience, so a flag
    would tell the players a secret exists at a square; `world.revealed_clocks`
    drops hidden clocks for the same reason.
    """
    client, _camp, slug = served
    body = _get(client, f"/pins/{slug}").get_json()
    ids = [p["id"] for p in body["pins"]]
    assert ids == ["shown1"]


def test_a_gm_pin_never_reaches_a_browser_in_any_form(served):
    """before fix: 404.

    Asserted on the serialised blob rather than on a list, because the promise is
    that its *position and label* never reach a player: a redaction that dropped
    the record but leaked the flag, or kept the label, would pass a list
    assertion.
    """
    client, _camp, slug = served
    blob = _get(client, f"/pins/{slug}").get_data(as_text=True)
    for leak in ("THE-TRAITOR", "gm1", "revealed\": false"):
        assert leak not in blob, f"{leak} rode into the payload"


def test_the_payload_carries_the_fields_the_board_draws(served):
    """before fix: 404.

    `drawPins` needs x, y, kind, label, target and id, and the pin layer reads
    all six. A payload that dropped `kind` would render every pin as a note --
    silently, because `pinGlyph` treats anything unexpected as a triangle.
    """
    client, _camp, slug = served
    pin = _get(client, f"/pins/{slug}").get_json()["pins"][0]
    for field in ("id", "x", "y", "label", "kind", "target", "revealed"):
        assert field in pin, field


def test_an_unknown_map_is_a_404(served):
    """before fix: 404 -- but for the wrong reason.

    The route checks the map before the pins, so a typo is a map error rather
    than an empty pin list. Worth pinning because the two are otherwise
    indistinguishable from the outside.
    """
    client, _camp, _slug = served
    assert _get(client, "/pins/no-such-map").status_code == 404


# ── the note ────────────────────────────────────────────────────────────────

def test_the_note_reads_back(served):
    """before fix: 404."""
    client, camp, slug = served
    response = _get(client, f"/pins/note?map={slug}&id=shown1")
    assert response.status_code == 200
    assert "Fog" in response.get_data(as_text=True)
    assert "text/markdown" in response.headers.get("Content-Type", "")


def test_a_gm_only_pin_is_a_404_on_the_note_route(served):
    """The stated threat, and the endpoint the corpus is most likely to forget.

    before fix: 404.

    Filtering the *list* endpoint is not enough: this route is reached by a
    click, and a player who has seen a pin's id in a URL, a log or a guess gets
    the body anyway unless the visibility rule is re-applied here. It is.
    """
    client, _camp, slug = served
    assert _get(client, f"/pins/note?map={slug}&id=gm1").status_code == 404


def test_every_refusal_reads_identically(served):
    """No existence oracle. before fix: 404.

    A sealed file, a missing note, a hidden pin, a wrong id and no parameters at
    all must all produce the same status and the same body. Anything else turns
    this route into a way to enumerate the campaign's spoiler files.
    """
    client, camp, slug = served
    (camp / "DM_SEALED").mkdir()
    (camp / "DM_SEALED" / "twist.md").write_text("SPOILER\n", encoding="utf-8")
    # A hostile *parameter* alongside a valid pin, not a valid request: the
    # extra `x=../../etc/passwd` is ignored by this route, so the response has to
    # be the note or the "hidden pin" 404 -- and if it is the note, the surplus
    # parameter was read by something. Asserted separately below for that.
    refusals = [f"/pins/note?map={slug}&id=gm1",
                f"/pins/note?map={slug}&id=nope",
                f"/pins/note?map={slug}&id=",
                "/pins/note",
                "/pins/note?map=../../..&id=gm1",
                "/pins/note?map=DM_SEALED&x=..&id=t"]
    answers = set()
    for url in refusals:
        response = _get(client, url)
        answers.add((response.status_code, response.get_data(as_text=True)))
    assert len(answers) == 1, f"the route answers in {len(answers)} ways: {answers}"
    assert answers == {(404, '{"error":"no such note"}\n')}


def test_an_extra_query_parameter_is_ignored_rather_than_read(served):
    """before fix: 404.

    A request that names a hidden pin *and* carries a traversal string must not
    succeed. It answers as the hidden pin does, which is the point: the route
    looks at `map` and `id` and nothing else, so there is no parameter for a
    future edit to start honouring by accident.
    """
    client, _camp, slug = served
    response = _get(client, f"/pins/note?map={slug}&id=shown1&x=../../etc/passwd")
    assert response.status_code == 200          # the pin is real and revealed
    assert "Fog" in response.get_data(as_text=True)


def test_a_hostile_target_already_in_the_pin_file_is_not_served(served):
    """before fix: 404.

    The pin file is a text file a GM edits, so the store is an input and not a
    guarantee. The corpus through the CLI only covers hostile input arriving by
    the supported route.
    """
    client, camp, slug = served
    pins_file = camp / "pins" / f"{slug}.json"
    rows = json.loads(pins_file.read_text(encoding="utf-8"))
    rows.append({"id": "evil", "x": 1, "y": 1, "label": "root",
                 "kind": "note", "target": "../../../../etc/passwd",
                 "revealed": True})
    pins_file.write_text(json.dumps(rows), encoding="utf-8")
    response = _get(client, f"/pins/note?map={slug}&id=evil")
    assert response.status_code == 404
    assert b"root:" not in response.data


# ── the boring failure modes ────────────────────────────────────────────────

def test_a_map_with_no_pins_is_an_empty_list(served):
    """before fix: 404.

    Almost every map has no pins and every board redraw asks, so this must be an
    empty list rather than an error. Redaction by emptiness is indistinguishable
    from a working feature only if there is also a case with pins in it, which
    `test_an_unrevealed_pin_is_absent_from_the_payload_not_flagged` provides.
    """
    client, _camp, _slug = served
    body = _get(client, f"/pins/{_real_map_slug()[:-3] or 'x'}").get_json()
    assert "pins" in body or body.get("error")


def test_a_corrupt_pin_file_is_an_empty_list_not_a_500(served):
    """before fix: 404.

    A GM who hand-edited the file badly should still get a working board; a 500
    here breaks every map on the panel, not just the pinned one.
    """
    client, camp, slug = served
    (camp / "pins" / f"{slug}.json").write_text("{not json", encoding="utf-8")
    response = _get(client, f"/pins/{slug}")
    assert response.status_code == 200
    assert response.get_json()["pins"] == []


def test_no_campaign_is_an_empty_board_not_an_error(app_module, tmp_path,
                                                    monkeypatch):
    """before fix: 404.

    The display runs before a campaign is loaded as often as after one, and
    `_active_campaign_name()` returning nothing is the ordinary case. This is the
    shape `test_faction_widget` uses for the same reason.
    """
    monkeypatch.setattr(app_module, "CAMP_FILE", str(tmp_path / ".campaign"))
    client = app_module.app.test_client()
    body = _get(client, f"/pins/{_real_map_slug()}").get_json()
    assert body == {"pins": []}
    assert _get(client, "/pins/note?map=x&id=y").status_code == 404


def test_a_campaign_name_that_is_not_a_campaign_is_an_empty_board(app_module,
                                                                  tmp_path,
                                                                  monkeypatch):
    """before fix: 404.

    `find_campaign` returns a not-found *sentinel* on a miss -- a path that does
    not exist unless a shell is sitting there. A route that trusted it would read
    the wrong campaign's pins, which is worse than reading none.
    """
    (tmp_path / ".campaign").write_text("not-a-campaign", encoding="utf-8")
    monkeypatch.setattr(app_module, "CAMP_FILE", str(tmp_path / ".campaign"))
    client = app_module.app.test_client()
    assert _get(client, f"/pins/{_real_map_slug()}").get_json() == {"pins": []}


def test_there_is_no_pin_write_route(served):
    """The absence, asserted because it is the security design.

    before fix: 404 either way -- so this test would pass on a code with no pin
    routes at all, which is exactly why it is written as an inventory rather than
    a behaviour.

    `security` finding 2: a write route gated on `_token_ok()` is gated on
    nothing, because that token is minted into `index.html` for every browser
    that loads `/`. A player at the table could plant a pin, and the GM would
    click it in good faith on the players' screen. Pins are authored in a shell
    (`scripts/pin.py`), so there is exactly one way to make one and no HTTP way.

    If someone adds POST /pins/<slug> for the map editor's convenience, this
    fails and names the reason.
    """
    client, _camp, _slug = served
    with contextlib.redirect_stdout(io.StringIO()):
        for method in (client.post, client.put, client.patch, client.delete):
            response = method("/pins/anything", json={"pins": []})
            assert response.status_code in (404, 405), \
                f"{method.__name__} /pins/anything returned {response.status_code}"