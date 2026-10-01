"""One creature, one picture: the board and the sheet must agree.

`token_portraits.resolve()` is the only thing in the engine that turns a
creature's name into a file. The board reaches it through the combat snapshot
(`sync.portrait_for` -> `portrait` -> `drawToken`); the character sheet has no
snapshot, so it asks `GET /portrait/<name>`, which reaches the same function.
That is the whole feature, and it is small enough that the only thing worth
testing is whether it stays one resolver.

The failure this file is written against is a second lookup. `url()` is a
wrapper on `resolve()` rather than a shortcut to `PORTRAITS`, and if a future
change makes it a shortcut then the sheet and the board each hold an opinion
about who a creature is, and they will agree right up until the day one is
edited. The first test below is the guard for that, and it is the reason this
file exists rather than a line in test_token_portraits.py.

TWO ABSENCES, AND THEY ARE NOT THE SAME THING
=============================================
`portrait: null` means the manifest has no entry for this creature -- true for
most of the SRD's 334 monsters, ordinary, and worth no message. `url` set with
`installed: false` means the manifest knows the creature and the PNGs were never
unpacked, which is every fresh clone (the art is third-party and gitignored) and
is a real problem the display cannot fix on its own. Only the filesystem can
tell them apart, so the route reports both and the sheet says something only in
the second case.
"""

from __future__ import annotations

import json
import pathlib
import struct
import sys
import zlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tactics import sync, token_portraits  # noqa: E402

HTML = ROOT / "display" / "templates" / "index.html"
SRC = HTML.read_text(encoding="utf-8")

# A manifest entry, so the resolver has something real to return without
# depending on whether this machine happens to hold the 98 third-party PNGs.
FIXTURE_NAME = "Daemogoth"
FIXTURE_SLUG = "daemogoth"


# ── helpers ──────────────────────────────────────────────────────────────────

def _png(path: pathlib.Path, width=4, height=4, rgb=(200, 120, 60)) -> pathlib.Path:
    """A real PNG, written with the stdlib.

    Pillow is optional in this repo (see faculty_sheets.py, which has to refuse
    without it) and CI is the one machine without it, so a test that needs a
    decodable image writes one rather than asking for a dependency. 4x4 is enough
    for the browser and for a byte check; nothing here looks at the pixels.
    """
    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return (struct.pack(">I", len(data)) + body
                + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + bytes(list(rgb) * width) for _ in range(height))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw))
                     + chunk(b"IEND", b""))
    return path


def _token(name, side="enemy"):
    from tactics.state import Token
    return Token(id=name.lower().replace(" ", "-"), name=name, side=side,
                 hp=10, max_hp=10, ac=12, x=0, y=0)


def _enc_with(tokens):
    from tactics.state import Encounter
    enc = Encounter(campaign="test",
                    grid={"rows": [".....", "....."], "name": "t", "diagonals": "5"})
    enc.tokens = {t.id: t for t in tokens}
    enc.order = list(enc.tokens)
    enc.status = "ended"
    return enc


# ── the app, with a tokens directory that is ours ────────────────────────────
#
# A temp directory, not display/tokens/. The route and the /tokens/ route both
# resolve the app module's own `_TOKENS_DIR`, and a test that wrote into the
# shipped one would leave a file behind that `test_token_portraits.py`'s skip
# guards read as an installed portrait -- a test whose result depends on what
# ran before it. The precedent and its reasoning are in that file's `art`
# fixture.

@pytest.fixture(scope="module")
def app_module():
    sys.path.insert(0, str(ROOT / "display"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "gm_display_app", ROOT / "display" / "gm-display-app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True
    return mod


@pytest.fixture
def tokens(app_module, tmp_path, monkeypatch):
    """Point the app's token art at a temp directory holding one real PNG.

    Function-scoped, so each test decides for itself whether the art is there.
    A module-scoped directory would make `installed` a property of test order,
    which is the exact bug the two-absences distinction exists to be honest
    about.
    """
    d = tmp_path / "tokens"
    d.mkdir()
    monkeypatch.setattr(app_module, "_TOKENS_DIR", str(d))
    return d


@pytest.fixture
def client(app_module):
    return app_module.app.test_client()


def _portrait(client, name):
    res = client.get(f"/portrait/{name}")
    assert res.status_code == 200, res.status_code
    return res.get_json()


# ── the claim: one resolver, two surfaces ────────────────────────────────────

def test_the_board_and_the_sheet_resolve_one_name_to_one_picture(client, tokens):
    """The feature, asserted in the form it can break.

    Two independent lookups would agree here and on every other name in the
    manifest, and then diverge the first time one of them was edited. Comparing
    the two answers -- rather than each against a hardcoded string -- is what
    actually holds them together.
    """
    _png(tokens / f"{FIXTURE_SLUG}.png")
    on_board = sync.portrait_for(_token(FIXTURE_NAME))
    on_sheet = _portrait(client, FIXTURE_NAME)
    assert on_board == f"{FIXTURE_SLUG}.png"
    assert on_sheet["portrait"] == on_board
    assert on_sheet["url"] == token_portraits.URL_PREFIX + on_board


def test_url_is_a_wrapper_on_resolve_not_a_second_lookup(client, tokens):
    """A parallel lookup would produce the same answer and the same bug.

    `url()` skips `resolve()` and goes to `PORTRAITS` directly, the trailing
    numeric-suffix fallback disappears with it, and the two surfaces start
    disagreeing on exactly the name the fallback exists for: a GM who typed
    "Archaic 2" for the second of four.
    """
    assert token_portraits.url("Archaic 2") == token_portraits.URL_PREFIX + \
        token_portraits.resolve("Archaic 2")
    on_sheet = _portrait(client, "Archaic 2")
    assert on_sheet["portrait"] == token_portraits.resolve("Archaic 2") == "archaic.png"
    assert sync.portrait_for(_token("Archaic 2")) == on_sheet["portrait"]


def test_the_case_and_spacing_rules_are_shared_too(client, tokens):
    _png(tokens / f"{FIXTURE_SLUG}.png")
    for name in ("DAEMOGOTH", "  Daemogoth  ", "daemogoth"):
        assert _portrait(client, name)["portrait"] == f"{FIXTURE_SLUG}.png", name


# ── importing the image: the small end-to-end test ───────────────────────────

def test_an_image_on_disk_is_served_by_the_url_the_route_hands_out(client, tokens):
    """Drop a PNG in, ask for the creature, fetch what comes back.

    The whole path in three requests, on a machine with none of the shipped art:
    a name resolves, the answer is a URL, and that URL is a decodable PNG. This
    is the "does importing an image actually work on the display" test, and it
    is hermetic on purpose -- the shipped 98 PNGs are gitignored, so a test that
    leaned on them would be skipped on every clone.
    """
    written = _png(tokens / f"{FIXTURE_SLUG}.png")
    info = _portrait(client, FIXTURE_NAME)
    assert info["installed"] is True

    res = client.get(info["url"])
    assert res.status_code == 200
    assert res.data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert res.data == written.read_bytes(), "the bytes served are not the bytes imported"
    assert res.mimetype == "image/png"


def test_the_served_png_is_the_same_file_the_manifest_names(client, tokens):
    """Not merely "a PNG came back" -- the one the resolver chose.

    A route that served any image would pass the test above, and the creature on
    the board would be a different creature from the one in the header. The
    decoy is a different colour rather than a different name so the two files
    are byte-different: two same-coloured 4x4 PNGs are the same file twice, and
    the assertion below would be comparing a value with itself.
    """
    _png(tokens / f"{FIXTURE_SLUG}.png", rgb=(200, 120, 60))
    _png(tokens / "arctic.png", rgb=(20, 40, 180))
    info = _portrait(client, FIXTURE_NAME)
    assert info["url"] == token_portraits.URL_PREFIX + f"{FIXTURE_SLUG}.png"
    assert client.get(info["url"]).data != client.get("/tokens/arctic.png").data


def test_the_credit_travels_with_the_picture(client, tokens):
    """Free to download is not free of the artist's claim.

    The board has nowhere to put this, so the sheet is the only surface that can
    name the artist, and it is also the only one a GM reads on their own time.
    Skipped with the art, because the fields ride on a portrait existing.
    """
    _png(tokens / f"{FIXTURE_SLUG}.png")
    info = _portrait(client, FIXTURE_NAME)
    assert info["credit"] == token_portraits.CREDIT == "hearden"
    assert info["source"] == token_portraits.SOURCE


# ── the two absences ─────────────────────────────────────────────────────────

def test_a_creature_with_no_portrait_is_a_plain_200_with_nulls(client, tokens):
    """The normal state for most of the SRD, so it must not look like a failure.

    A 404 would make every caller treat "this monster has no art" as an error to
    report, which is noise on the common path and hides the real absence below.
    """
    res = client.get("/portrait/Goblin")
    assert res.status_code == 200
    body = res.get_json()
    assert body["portrait"] is None and body["url"] is None
    assert body["installed"] is False
    assert body["credit"] is None


def test_a_known_creature_with_no_png_installed_is_distinguishable(client, tokens):
    """`portrait` set, `installed` false: the art is on the books and not here.

    This is every fresh clone, and it is the case the display cannot paper over
    by falling back -- the GM installed nothing and now believes they have art.
    It is also the case a caller cannot derive for itself, because the only
    difference is a file's existence.
    """
    info = _portrait(client, FIXTURE_NAME)
    assert info["portrait"] == f"{FIXTURE_SLUG}.png"
    assert info["url"] is not None
    assert info["installed"] is False, "manifest entry with no PNG must say so"
    assert info["credit"] == token_portraits.CREDIT, (
        "credit is owed whether or not the file arrived")


def test_the_refusal_to_guess_holds_on_the_sheet_too(client, tokens):
    """The board already refuses a near-match; the sheet must not be looser.

    A wrong face is the one output here that looks finished and is not, and the
    sheet is read in private, away from the table -- so a face the board would
    refuse has nothing checking it.
    """
    for near_miss in ("Daemogot", "daemogothx", "ogoth", "daemogoth-tita",
                      "Daemogot Titan", "the Daemogoth", "archai"):
        assert _portrait(client, near_miss)["portrait"] is None, near_miss
        assert _portrait(client, near_miss)["url"] is None, near_miss


def test_two_similar_names_get_their_own_faces_and_not_each_others(client, tokens):
    """The converse, because a resolver that refused everything would pass the
    test above.

    `daemogoth` and `daemogoth-titan` are two entries in the manifest because
    they are two different creatures, and the base-dragon substitution
    recorded in a collection is exactly the kind of collapse this set of tests
    exists to prevent. Both must resolve, and to themselves.
    """
    _png(tokens / "daemogoth.png", rgb=(200, 120, 60))
    _png(tokens / "daemogoth-titan.png", rgb=(20, 40, 180))
    assert _portrait(client, "Daemogoth")["portrait"] == "daemogoth.png"
    assert _portrait(client, "Daemogoth Titan")["portrait"] == "daemogoth-titan.png"


def test_an_empty_name_is_not_an_error(client, tokens):
    assert client.get("/portrait/").status_code in (200, 308, 404)


# ── neither route can be walked out of ───────────────────────────────────────

def test_the_lookup_route_cannot_be_used_to_reach_a_file(client, tokens):
    """The name is sanitised and then slugified, never joined onto a path.

    `/portrait/..%2f..%2fetc%2fpasswd` has to come back as the honest "no
    portrait for that name", not as a read. There is no traversal to defend
    against in the lookup itself -- it never touches the filesystem by name --
    but it is the shape of a future edit that would.
    """
    for attempt in ("..%2f..%2fetc%2fpasswd", "....//....//etc/passwd",
                    "..%5c..%5cetc%5cpasswd"):
        body = _portrait(client, attempt)
        assert body["portrait"] is None, attempt


def test_the_serving_route_still_cannot_escape_its_directory(client, tokens):
    """Unchanged guarantee, asserted here because the sheet now points at it.

    The sheet hands a browser this URL, so this route has a second consumer that
    the board alone did not justify.
    """
    assert client.get("/tokens/../../etc/passwd").status_code in (400, 404)


# ── the URL prefix cannot drift ──────────────────────────────────────────────

def test_the_prefix_the_index_names_is_the_route_that_exists(app_module):
    """Three places know "/tokens/": the index, the Flask rule, and drawToken.

    One is a constant the other two are not. If the route is ever moved and
    only the route changes, every portrait in a live fight 404s and falls back to
    the coloured shape -- which looks like a working board, so it would be found
    by a player rather than by a test.
    """
    rules = {r.rule for r in app_module.app.url_map.iter_rules()}
    assert token_portraits.URL_PREFIX + "<path:filename>" in rules, (
        f"the route is {sorted(r for r in rules if 'token' in r)}; the index says "
        f"{token_portraits.URL_PREFIX!r}")


def test_draw_token_fetches_the_prefix_the_index_names():
    """The board's own URL, read out of the file that builds it.

    Pinned against tactics.js source rather than a live browser: the string is
    the contract, and a rendered token is not observable from a test.
    """
    js = (ROOT / "display" / "static" / "tactics.js").read_text(encoding="utf-8")
    assert f"href: '{token_portraits.URL_PREFIX}'" in js, (
        "tactics.js builds the portrait URL by hand; keep it in step with "
        "token_portraits.URL_PREFIX")


# ── the sheet pane is actually wired to it ───────────────────────────────────

def test_the_sheet_pane_has_a_portrait_slot():
    assert 'id="cp-portrait"' in SRC
    # Hidden by default, so a creature with no art costs no space and a previous
    # creature's face cannot survive a failed load.
    assert 'id="cp-portrait" hidden' in SRC


def test_the_sheet_pane_asks_the_portrait_route():
    assert "/portrait/${encodeURIComponent(name)}" in SRC, (
        "the sheet must read its portrait from the shared resolver's route, "
        "not from a second lookup of its own")


def test_the_sheet_only_shows_a_face_that_the_route_vouched_for():
    """`url` gates the image and `installed` gates the message.

    The two are separate conditions on purpose and the template has to keep them
    separate: showing art on a null `url` would be a picture of nothing, and
    showing the "not installed" line on a null `url` would tell a GM to install
    art for a creature that has none.
    """
    body = SRC[SRC.index("async function _loadSheetPortrait"):]
    body = body[:body.index("\nfunction ")]
    assert "if (!info.url) return;" in body
    assert "if (!info.installed)" in body
    assert body.index("if (!info.url) return;") < body.index("if (!info.installed)")


def test_a_broken_image_clears_the_slot_rather_than_leaving_a_glyph():
    """The art is gitignored, so a 404 here is a supported state on any clone."""
    assert "img.addEventListener('error', _clearSheetPortrait)" in SRC


def test_loading_a_new_character_clears_the_previous_face():
    """The pane is reused across characters and this is the bug that follows.

    A sheet that fails to load returns early from the `fetch` error path, so
    without a clear at the top the previous creature's portrait sits above the
    error message -- the one place a wrong face is least likely to be noticed.
    """
    load = SRC[SRC.index("async function _loadCharacterSheet"):]
    load = load[:load.index("async function _loadSheetPortrait")]
    assert "_clearSheetPortrait();" in load
    assert load.index("_clearSheetPortrait();") < load.index("if (!ch) {"), (
        "the clear has to happen before every early return, not inside one branch")


def test_the_credit_is_rendered_next_to_the_picture():
    assert "Portrait by ${info.credit}" in SRC


# ── the design constraint, restated for the new surface ──────────────────────

def test_the_sheet_does_not_depend_on_the_maps_opt_in(client, tokens):
    """The board's `portraits` flag is a per-fight choice, and it stops there.

    A map that did not ask for faces is a map the GM wants the old flat discs
    on. That is a statement about the board, not about the character sheet, and
    a sheet that refused to show a face because the current map is a tactics
    grid would be a surprising coupling between two unrelated surfaces.

    The consequence is recorded rather than fixed: on a portraitless map the
    sheet has a face and the board has a disc. They are the same picture in the
    same file, which is the guarantee; they are not guaranteed to both be
    visible at once, and nothing here claims they are.
    """
    _png(tokens / f"{FIXTURE_SLUG}.png")
    off = _portrait(client, FIXTURE_NAME)
    board_off = sync.snapshot(_enc_with([_token(FIXTURE_NAME)]), meta={})
    assert board_off["tokens"][0]["portrait"] is None, "the flag still governs the board"
    assert off["installed"] is True, "and it does not govern the sheet"


def test_sharing_a_portrait_does_not_touch_the_grid():
    """The BV1 guarantee, on the new surface.

    The sheet is a second reader of the same index, and the standing rule is
    that a portrait is display-only: no rule reads it and `grid.rows` is
    byte-identical with or without one.
    """
    from tactics import maps
    spec = {"name": "X", "width": 8, "height": 6,
            "features": [{"type": "wall", "x": 2, "y": 1, "w": 3, "h": 2}]}
    plain = maps.compile_map(spec)
    fancy = maps.compile_map(dict(spec, portraits=True))
    assert plain["grid"]["rows"] == fancy["grid"]["rows"]
    assert plain["grid"] == fancy["grid"]


def test_the_snapshot_still_carries_a_bare_filename(client, tokens):
    """Not a URL.

    The payload key is pinned by test_token_portraits.py and the display builds
    its own URL from it. Promoting it to a full URL here would be a coherent
    change and the wrong one: it would put the serving path into every snapshot
    on disk, and a snapshot is a record of a fight rather than a client of this
    one route.
    """
    _png(tokens / f"{FIXTURE_SLUG}.png")
    snap = sync.snapshot(_enc_with([_token(FIXTURE_NAME)]), meta={"portraits": True})
    portrait = snap["tokens"][0]["portrait"]
    assert "/" not in portrait and portrait.endswith(".png")
    assert _portrait(client, FIXTURE_NAME)["url"] == token_portraits.URL_PREFIX + portrait


def test_the_json_is_the_documented_shape(client, tokens):
    """Pin the field names, so a rename cannot silently break the sheet.

    `installed` in particular: a caller that does not know about it cannot tell
    the two absences apart, and a test that only checked `url` would pass
    happily while that stayed true.
    """
    _png(tokens / f"{FIXTURE_SLUG}.png")
    assert set(_portrait(client, FIXTURE_NAME)) == {
        "name", "portrait", "url", "installed", "credit", "source"}


# ── is_installed reads the directory it is given ─────────────────────────────

def test_is_installed_asks_about_the_directory_it_was_handed(tmp_path):
    """Not the module's own idea of where the art lives.

    The Flask app has its own `_TOKENS_DIR`, which a test points at a temporary
    folder. An existence check against a different directory than the one being
    served answers a question nobody asked, and returns a confident False.
    """
    _png(tmp_path / f"{FIXTURE_SLUG}.png")
    assert token_portraits.is_installed(FIXTURE_NAME, tmp_path) is True
    assert token_portraits.is_installed(FIXTURE_NAME, tmp_path / "elsewhere") is False


def test_is_installed_is_false_for_a_name_with_no_portrait(tmp_path):
    assert token_portraits.is_installed("Goblin", tmp_path) is False
    assert token_portraits.is_installed("", tmp_path) is False


def test_a_missing_tokens_directory_is_an_empty_install_not_an_error(tmp_path):
    """The fresh clone. `is_installed` must answer False, not raise."""
    assert token_portraits.is_installed(FIXTURE_NAME, tmp_path / "nope") is False


def test_the_route_needs_the_lan_token_when_one_is_set(app_module, tokens, monkeypatch):
    """A portrait is not public information the way the sheet body is.

    The art is third-party, downloadable by anyone, and the map and icon routes
    are unauthenticated. This one is gated because it is keyed on character
    NAMES, so an unauthenticated version would turn the route into an oracle for
    which creatures this campaign has.
    """
    monkeypatch.setattr(app_module, "_lan_token", "s3cret")
    client = app_module.app.test_client()
    try:
        assert client.get(f"/portrait/{FIXTURE_NAME}").status_code == 403
        ok = client.get(f"/portrait/{FIXTURE_NAME}",
                        headers={"X-DND-Token": "s3cret"})
        assert ok.status_code == 200
    finally:
        monkeypatch.setattr(app_module, "_lan_token", None)


def test_json_body_is_a_dict_not_a_list(client, tokens):
    """A shape guard, because `assert body["portrait"]` on a list would raise
    rather than fail with a readable message on someone else's machine."""
    assert isinstance(_portrait(client, FIXTURE_NAME), dict)
    assert json.dumps(_portrait(client, FIXTURE_NAME))  # serialisable
