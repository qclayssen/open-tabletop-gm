"""token_portraits.py: portraits in the combat snapshot.

The design constraint is the one BV1 set for map images and it is the same
constraint here: **a portrait is display-only.** `compile_map` is untouched,
`grid.rows` is byte-identical with or without one, and no rule reads it. If a
test here ever needs the engine to behave differently, the feature has leaked.
"""

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from tactics import maps, sync, token_portraits  # noqa: E402

TOKENS_DIR = ROOT / "display" / "tokens"


def _installed_slugs(tokens_dir):
    """The slugs actually on disk. An absent directory is an empty install, not
    an error: that is a fresh clone, which is a supported state."""
    if not tokens_dir.is_dir():
        return set()
    return {p.stem for p in tokens_dir.glob("*.png")}


def _manifest_art_on_disk(tokens_dir=TOKENS_DIR):
    """Which of the manifest's portraits are really there.

    Deliberately not "does any png exist in the directory". That proxy is
    satisfied by a stray file, and this file used to leave one behind: the route
    fixture below wrote `_portrait_test.png` into display/tokens/ and removed the
    file but not the directory, so by the time the installer test ran
    `TOKENS_DIR.is_dir()` was true and the glob found the fixture. `--check` was
    then asserted clean on a machine holding none of the 98 portraits, and the
    guard that was supposed to prevent exactly that is what allowed it. A file
    the manifest does not name is not the art.
    """
    return set(token_portraits.PORTRAITS.values()) & _installed_slugs(tokens_dir)


def _token(name, side="enemy"):
    """A real Token, not a stub. `snapshot` reads a dozen attributes off it, so
    a hand-rolled fake would keep needing new fields every time the payload
    grows -- and would hide the case where it does."""
    from tactics.state import Token
    return Token(id=name.lower().replace(" ", "-"), name=name, side=side,
                 hp=10, max_hp=10, ac=12, x=0, y=0)


# ─── the manifest ────────────────────────────────────────────────────────────

def test_slugify_matches_the_filenames_on_disk():
    """The manifest and the art have to agree on one rule, or a portrait is
    named in the map and 404s at the table."""
    for name, slug in token_portraits.PORTRAITS.items():
        assert token_portraits.slugify(name) == slug, name


def test_resolve_returns_a_filename_for_a_known_creature():
    assert token_portraits.resolve("Daemogoth") == "daemogoth.png"
    assert token_portraits.resolve("Quandrix Scholar 3") == "quandrix-scholar-3.png"
    assert token_portraits.resolve("archaic") == "archaic.png"


def test_resolve_is_case_and_spacing_insensitive():
    assert token_portraits.resolve("DAEMOGOTH") == "daemogoth.png"
    assert token_portraits.resolve("  daemogoth  ") == "daemogoth.png"


def test_resolve_strips_a_numeric_suffix():
    """A GM placing four of something types "Ghoul 1"; that is still Ghoul."""
    assert token_portraits.resolve("Archaic 2") == "archaic.png"


def test_resolve_returns_none_rather_than_guessing():
    """The important one. A near-match is a wrong face on a creature, which is
    a lie the table has no way to check."""
    assert token_portraits.resolve("Goblin") is None
    assert token_portraits.resolve("Daemogot") is None          # one letter off
    assert token_portraits.resolve("") is None
    assert token_portraits.resolve(None) is None


def test_a_stray_file_is_not_the_art(tmp_path):
    """The condition the skips in this file use, asserted on its own.

    The route test needs a real PNG to serve, and while it used to write that PNG
    into display/tokens/ this is how a test with none of the art came to believe
    it had all of it. If this ever fails, the guards below are asking the wrong
    question again and every one of them is optional.
    """
    (tmp_path / "_portrait_test.png").write_bytes(b"")
    assert _manifest_art_on_disk(tmp_path) == set()
    for slug in token_portraits.PORTRAITS.values():
        (tmp_path / f"{slug}.png").write_bytes(b"")
    assert _manifest_art_on_disk(tmp_path) == set(token_portraits.PORTRAITS.values())


def test_every_manifest_entry_has_a_file_when_the_art_is_installed():
    """Skipped on a clone without the art, which is a supported state.

    Skipped on the absence of any of it rather than of all of it, so a partial
    install still gets checked: that is the state where a portrait 404s at the
    table, and it is the state this test is for.
    """
    if not _manifest_art_on_disk():
        pytest.skip("portrait art not installed")
    for slug in token_portraits.PORTRAITS.values():
        assert (TOKENS_DIR / f"{slug}.png").exists(), slug


def test_no_portrait_file_is_missing_from_the_manifest():
    """The other direction: art on disk that nothing can reach is a portrait
    that silently never appears."""
    if not _manifest_art_on_disk():
        pytest.skip("portrait art not installed")
    known = {f"{v}.png" for v in token_portraits.PORTRAITS.values()}
    for png in TOKENS_DIR.glob("*.png"):
        assert png.name in known, png.name


def test_the_manifest_names_the_artist():
    """Free to download is not free of the artist's claim.

    CREDIT is who made it and SOURCE is where it came from, so the name the GM
    is owed is the one in CREDIT. Checking SOURCE for the artist instead is the
    mistake this test was written against: the two used to be the same string
    with the name doubled, and deduplicating it broke the assertion without
    breaking the attribution.
    """
    assert token_portraits.CREDIT.strip() == "hearden"
    assert token_portraits.SOURCE.strip()


# ─── the opt-in ──────────────────────────────────────────────────────────────

def test_a_map_without_the_flag_offers_no_portraits():
    assert maps.compile_map({"name": "X", "width": 4, "height": 4})["meta"]["portraits"] is False


def test_the_flag_is_carried_into_meta():
    m = maps.compile_map({"name": "X", "width": 4, "height": 4, "portraits": True})
    assert m["meta"]["portraits"] is True


def test_portrait_for_is_a_pure_lookup():
    t = _token("Daemogoth")
    assert sync.portrait_for(t) == "daemogoth.png"
    assert sync.portrait_for(_token("Goblin")) is None


# ─── the design constraint ───────────────────────────────────────────────────

def test_portraits_do_not_change_the_grid():
    """The BV1 guarantee, restated for tokens. `compile_map` is untouched, so
    this is the same rows string with the flag on and off."""
    plain = maps.compile_map({"name": "X", "width": 8, "height": 6,
                              "features": [{"type": "wall", "x": 2, "y": 1, "w": 3, "h": 2}]})
    fancy = maps.compile_map({"name": "X", "width": 8, "height": 6, "portraits": True,
                              "features": [{"type": "wall", "x": 2, "y": 1, "w": 3, "h": 2}]})
    assert plain["grid"]["rows"] == fancy["grid"]["rows"]


def test_every_shipped_map_still_loads_with_the_new_meta_key():
    """`portraits` is a new key in meta; nothing downstream should trip on it."""
    for name in maps.available():
        m = maps.load(name)
        assert "portraits" in m["meta"], name
        assert isinstance(m["meta"]["portraits"], bool), name


def test_the_token_payload_carries_a_portrait_key():
    """The display reads `portrait` off every token, so the key must always be
    present -- null for a token with no art, never missing."""
    snap = sync.snapshot(_enc_with([_token("Daemogoth"), _token("Goblin")]),
                         meta={"portraits": True})
    by_name = {t["name"]: t for t in snap["tokens"]}
    assert by_name["Daemogoth"]["portrait"] == "daemogoth.png"
    assert by_name["Goblin"]["portrait"] is None


def test_portraits_are_off_unless_the_map_asks():
    snap = sync.snapshot(_enc_with([_token("Daemogoth")]), meta={})
    assert snap["tokens"][0]["portrait"] is None


def test_a_missing_portrait_key_from_an_older_snapshot_is_tolerated():
    """A snapshot written before this feature has no `portrait` on its tokens.
    The display must not care, so the key is optional in, present out."""
    assert sync.portrait_for(_token("Daemogoth")) == "daemogoth.png"


# ─── the Flask route ─────────────────────────────────────────────────────────
#
# Same shape as the /maps/images route in test_map_images.py: a real PNG written
# by the test, because the route test needs a file that exists and committing a
# fixture portrait would put a test artefact in the shipped token set.

@pytest.fixture(scope="module")
def app_module():
    sys.path.insert(0, str(ROOT / "display"))
    import importlib.util
    spec = importlib.util.spec_from_file_location("gm_display_app", ROOT / "display" / "gm-display-app.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.app.config["TESTING"] = True
    return mod


@pytest.fixture(scope="module")
def art(app_module, tmp_path_factory):
    """A real PNG in a real directory, but not the shipped one.

    The route resolves the app module's own `_TOKENS_DIR`, so this points that at
    a temp directory for the duration of the module and puts it back afterwards.
    Writing into display/tokens/ instead works exactly once and then lies to
    every later test in the file: the PNG went away but the directory did not, so
    `TOKENS_DIR.is_dir()` stayed true and the skips in this file read a fixture
    file as an installed portrait. A test that changes the machine it runs on is
    a test whose result depends on what ran before it.
    """
    import struct
    import zlib
    tokens = tmp_path_factory.mktemp("tokens")
    path = tokens / "_portrait_test.png"

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    width = height = 4
    raw = b"".join(b"\x00" + bytes([200, 120, 60] * width) for _ in range(height))
    path.write_bytes(b"\x89PNG\r\n\x1a\n"
                     + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
                     + chunk(b"IDAT", zlib.compress(raw))
                     + chunk(b"IEND", b""))
    saved = app_module._TOKENS_DIR
    app_module._TOKENS_DIR = str(tokens)
    yield path
    app_module._TOKENS_DIR = saved


@pytest.fixture(scope="module")
def client(app_module, art):
    return app_module.app.test_client()


def test_the_tokens_route_serves_a_portrait(client, art):
    assert client.get(f"/tokens/{art.name}").status_code == 200


def test_the_tokens_route_cannot_escape_its_directory(client):
    """The same guarantee the icons and maps routes rely on: send_from_directory
    confines it, so a crafted path cannot reach the rest of the disk."""
    assert client.get("/tokens/../../etc/passwd").status_code in (400, 404)


def test_a_missing_portrait_is_a_404_not_an_error(client):
    """The normal state on a clone without the art, and the state the
    JavaScript falls back from. It has to be unremarkable."""
    assert client.get("/tokens/definitely-not-here.png").status_code == 404


def _enc_with(tokens):
    """A real Encounter, not a stub. `snapshot` reads a dozen attributes off it
    (system, turn, order, grid), so a hand-rolled fake would pass here and break
    the moment one of those changes -- which is the opposite of what a test is
    for."""
    from tactics.state import Encounter
    enc = Encounter(campaign="test", grid={"rows": [".....", "....."], "name": "t", "diagonals": "5"})
    enc.tokens = {t.id: t for t in tokens}
    enc.order = list(enc.tokens)
    enc.status = "ended"
    return enc


# ─── the installer ───────────────────────────────────────────────────────────
#
# The art is gitignored, so `install_tokens.py` is the step every machine runs
# once. A bug in it is a silent absence: no portrait, no error, no log line.

def test_the_installer_refuses_a_path_that_is_not_there():
    from install_tokens import main
    with pytest.raises(SystemExit) as e:
        main(["/nonexistent/tokens.zip"])
    assert "No zip at" in str(e.value)


def test_a_refused_install_leaves_no_directory_behind(tmp_path, monkeypatch):
    """A run that refuses must not have touched the machine.

    `main` made display/tokens/ before it looked at the zip, so every failed
    install left an empty directory on a clone that had none. An empty
    `display/tokens/` is a state the rest of the system reads as "the art is
    installed and it is nothing", which is worse than the clean absence it was
    meant to be -- and it is what made the skip guards in this file unreliable
    enough to need rewriting anyway.
    """
    import install_tokens
    tokens = tmp_path / "tokens"
    monkeypatch.setattr(install_tokens, "TOKENS_DIR", tokens)
    with pytest.raises(SystemExit):
        install_tokens.main(["/nonexistent/tokens.zip"])
    with pytest.raises(SystemExit):
        install_tokens.main(["--from-extracted", str(tmp_path / "nope")])
    assert not tokens.exists()


def test_the_installer_reports_a_clean_install():
    """--check is what a person runs to find out whether the art is there, so on
    a machine with the art it must say so and succeed.

    This is the real machine, not a fixture: the only place the shipped art
    exists. The hermetic version of the same claim is the test below, because
    until there was one this line asserted the success path on the one machine
    that happens to own 12MB of third-party art and nowhere else.
    """
    from install_tokens import main
    if not _manifest_art_on_disk():
        pytest.skip("portrait art not installed")
    assert main(["--check"]) == 0


def test_a_full_install_is_a_clean_check(tmp_path, monkeypatch, capsys):
    """--check returns 0 on a machine that has the art, everywhere.

    The success path used to be covered only by a developer checkout with the
    portraits installed, so the one command whose whole job is to answer "are
    the portraits there" was asserted exactly once in the world. A directory
    holding one file per manifest entry is that machine, and it is the same on
    every platform, in every CI clone, without the art.
    """
    import install_tokens
    tokens = tmp_path / "tokens"
    tokens.mkdir()
    for slug in token_portraits.PORTRAITS.values():
        (tokens / f"{slug}.png").write_bytes(b"")
    monkeypatch.setattr(install_tokens, "TOKENS_DIR", tokens)
    assert install_tokens.main(["--check"]) == 0
    out = capsys.readouterr().out
    assert f"{len(token_portraits.PORTRAITS)} portraits installed" in out
    assert "every manifest entry has a file" in out


def test_the_installer_says_so_when_the_art_is_absent(tmp_path, monkeypatch, capsys):
    """The fresh-clone case, which is the one that matters: the art is missing
    on every clone, it is not an error, and nothing else in the system will
    mention it. So the check has to name the state rather than pass quietly."""
    import install_tokens
    monkeypatch.setattr(install_tokens, "TOKENS_DIR", tmp_path / "tokens")
    assert install_tokens.main(["--check"]) == 1
    out = capsys.readouterr().out
    assert "manifest entries have no file" in out
