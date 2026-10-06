"""`--pins`: the campaign's note pins, exported into a pushed Atlas scene.

P11, and the last piece of BV4. The leg is **one-way**: pins live at
`<campaign>/pins/<map-slug>.json` and are written into `objects.pins`. Nothing is
read back, which is the shared document `OBSIDIAN-INTEGRATION-DECISION.md`
rejects and the reason these tests include one that intercepts every `.atlasmap`
the exporter opens.

Every failure mode on this leg is silent, which is why the whole file is refusals
rather than happy paths. A `notePath` naming a file that is not there produces a
scene that looks complete and a pin that does nothing when clicked. Atlas's
`sceneLinkTarget` (`sceneLinks.ts:30-36`) rewrites a foreign-collection link to
the same filename inside the linking collection, and `linkedFileFile` returning
null is not an error, it is a click that opens the file as plain text.

THE FIXTURE, because getting it wrong makes this file prove nothing
---------------------------------------------------------------

`paths.find_campaign` returns a path that does not exist when it misses, and
`map_to_atlas.py` only checks `is_dir()`. So a campaign built as a bare directory,
or one without a `state.md`, resolves to nothing, every refusal test goes green
by reading nothing, and the suite passes having proved nothing. SPEC §6 lines
511-513 says so explicitly.

Every fixture here therefore goes through `paths.require_campaign`, which
**raises** rather than returning a miss, and the notes the positive tests read
really exist. `test_the_fixture_is_a_real_campaign` makes that explicit, and the
positive cases fail loudly if the fixture ever goes dead.

`before fix:` for the 14 behavioural cases is `KeyError`, because `build_scene`
wrote `"pins": {}` (`map_to_atlas.py:436`) and there was nothing to assert on.
The other seven pass on the pre-fix code by construction; `verifier` required each
to name the mutant it kills, and they are listed in the change brief.
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import pins as _pins  # noqa: E402
from paths import require_campaign  # noqa: E402


def _load():
    """`map_to_atlas.py` loaded by path, as `test_map_to_atlas.py` does it.

    By path rather than by import because the module is a script, and the other
    two files that test it do the same.
    """
    spec = importlib.util.spec_from_file_location(
        "map_to_atlas_pins", ROOT / "scripts" / "map_to_atlas.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mta = _load()

COLL = "Strixhaven"


def _map(name="Test Cave", slug="test-cave", **over):
    """A map spec, defaulting to 4x3 cells of 100px and one token at (1,2)."""
    spec = {
        "name": name,
        "width": 4,
        "height": 3,
        "diagonals": "5",
        "image": f"images/{slug}.png",
        "grid": {"cell_px": 100, "offset_x": 0, "offset_y": 0},
        "base": "floor",
        "features": [],
        "spawns": [{"id": "K", "name": "Kairos", "color": "quan", "x": 1, "y": 2}],
    }
    spec.update(over)
    return spec


def _png():
    import struct as s
    import zlib as z

    def chunk(kind, payload):
        return (s.pack(">I", len(payload)) + kind + payload
                + s.pack(">I", z.crc32(kind + payload) & 0xFFFFFFFF))
    raw = b"".join(b"\x00" + b"\xc8\x28\x28\xff" for _ in range(1))
    return (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", s.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", z.compress(raw)) + chunk(b"IEND", b""))


@pytest.fixture
def maps(tmp_path):
    """A maps dir. One map with artwork, one without (a legal map pin target)."""
    root = tmp_path / "maps"
    (root / "images").mkdir(parents=True)
    (root / "images" / "test-cave.png").write_bytes(_png())
    (root / "test-cave.json").write_text(json.dumps(_map()), encoding="utf-8")
    (root / "cellar.json").write_text(
        json.dumps(_map("The Cellar", "cellar", image="images/cellar.png")), encoding="utf-8")
    (root / "images" / "cellar.png").write_bytes(_png())
    return root


@pytest.fixture
def vault(tmp_path):
    """An Atlas vault. The campaign lives INSIDE it -- under `vault/campaigns/` --
    because `notePath` is resolved through `app.vault`, and a campaign beside the
    vault rather than inside it is exactly refusal 3."""
    v = tmp_path / "atlas-vault"
    v.mkdir()
    return v


#: The one filename in `camp` below that is deliberately not ASCII. Hard rule 4
#: is about a note target that has to survive a non-ASCII name, so it is not
#: decoration -- every test here needs this file to exist.
NON_ASCII_NOTE = "caf\u00e9-notes.md"


def _fs_can_hold(name: str) -> bool:
    """Can this interpreter create a file with this name at all?

    Python encodes paths with the filesystem encoding, which under a genuine C
    locale on Linux is ASCII. The `\u00e9` in the fixture's filename then cannot be
    created -- not "may misbehave", cannot exist -- and every test here errors in
    fixture setup rather than in the code under test.

    This is an OS-level constraint, not a defect in this tree, and it is
    invisible on a developer machine because macOS pins the filesystem encoding to
    UTF-8 regardless of locale. Same shape and same reasoning as
    `_argv_can_carry()` in `test_encoding_utf8.py`.
    """
    try:
        name.encode(sys.getfilesystemencoding())
        return True
    except (UnicodeEncodeError, LookupError):
        return False


pytestmark = pytest.mark.skipif(
    not _fs_can_hold(NON_ASCII_NOTE),
    reason=(
        "filesystem encoding is "
        f"{sys.getfilesystemencoding()!r}, which cannot represent the non-ASCII "
        f"note name {NON_ASCII_NOTE!r} the `camp` fixture creates. Every test "
        "here would fail in fixture setup instead of testing hard rule 4. The "
        "guards in test_encoding_utf8.py still run under this locale and cover "
        "the encoding class this file is incidentally sensitive to."
    ),
)


@pytest.fixture
def camp(vault, monkeypatch):
    """A campaign that genuinely resolves: `state.md`, three allowed folders, and
    the sealed material that must never become a `notePath`.

    Inside the vault, and reachable only by pointing `GM_CAMPAIGN_ROOT` at the
    vault. `require_campaign` raises rather than returning a miss, so a fixture
    that went dead would fail every test here instead of passing them.
    """
    root = vault / "campaigns" / "demo"
    for folder in ("notes", "locations", "handouts"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "state.md").write_text("**System:** D&D 5e\n", encoding="utf-8")
    (root / "notes" / "harbour.md").write_text("# The Harbour\n\nFog.\n", encoding="utf-8")
    (root / "notes" / NON_ASCII_NOTE).write_text(
        "# Le Café\n\nA non-ASCII note, so hard rule 4 is exercised on this path.\n",
        encoding="utf-8")
    (root / "locations" / "rotunda.md").write_text("# The Rotunda\n", encoding="utf-8")
    (root / "handouts" / "wanted.md").write_text("# Wanted\n", encoding="utf-8")
    # The sealed material. Not one of these may ever appear in a scene.
    (root / "answer-key.md").write_text("Kairos is the traitor.\n", encoding="utf-8")
    (root / "secrets").mkdir()
    (root / "secrets" / "plan.md").write_text("Burn the archive.\n", encoding="utf-8")
    (root / "DM_SEALED").mkdir()
    (root / "DM_SEALED" / "twist.md").write_text("The villain is the Duke.\n",
                                                encoding="utf-8")
    # The root find_campaign resolves through, so `require_campaign` finds it.
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(vault))
    return require_campaign("demo")


@pytest.fixture
def env(maps, vault, camp):
    """Everything an export needs, and the campaign as a resolved Path."""
    return maps, vault, camp


def _store(camp, map_id, *records) -> list:
    """Write a pin store through the store's own writer, so the fixture cannot
    drift from the format and every record is one `validate` accepted."""
    return _pins.save(camp, map_id, [dict(r) for r in records])


def _raw_store(camp, map_id, *records) -> list:
    """Write a pin store **around** `pins.save`, as a hand-edited or older file.

    `pins.save` refuses `answer-key.md` at authoring time, which is the correct
    behaviour and makes it useless for testing the refusal: the export has to
    refuse it too, because a pin file can be hand edited, copied between
    campaigns, or written before the allow-list was this strict. That is exactly
    why `pins.note_body` re-runs every gate on the **read** path, and the same
    reason the export cannot trust what it reads.

    So the refusal tests below write the file directly. That is not a weaker
    fixture, it is the realistic one: it is what the read path is defending
    against, and `pins.py:322-327` says so in as many words.
    """
    path = _pins.pins_path(camp, map_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([dict(r) for r in records], indent=2,
                               ensure_ascii=False) + "\n", encoding="utf-8")
    return [dict(r) for r in records]


def _run(env, spec=None, map_id="test-cave", **kwargs) -> dict:
    """An export with pins on. The default for this file."""
    maps, vault, camp = env
    kwargs.setdefault("with_pins", True)
    kwargs.setdefault("camp_dir", camp)
    return mta.export(spec or _map(), map_id, vault, COLL, maps, **kwargs)


def _coll_dir(env, collection=COLL) -> pathlib.Path:
    """Where the exporter writes one collection's scenes, sidecars and artwork."""
    return env[1] / "atlas-vtt" / "collections" / collection


def _scene_path(env, collection=COLL, spec=None,
                 map_id="test-cave") -> pathlib.Path:
    """The scene for one map, by name.

    Not `next(glob(...))`: the map-pin tests write a second scene into the same
    collection, and a glob would hand back whichever the filesystem listed
    first. A test that reads the wrong scene passes or fails for the wrong
    reason, which is the failure mode the whole fixture discipline here exists to
    avoid.
    """
    name = mta.scene_file_name(spec or _map(), map_id)
    return _coll_dir(env, collection) / name


def _objects(env, collection=COLL, spec=None, map_id="test-cave") -> dict:
    path = _scene_path(env, collection, spec, map_id)
    return json.loads(path.read_text(encoding="utf-8"))["state"]["objects"]


def pin(**over):
    """A note pin record. `id` is generated unless given, which is what a real
    store holds, so the tests that care about ids pass one explicitly."""
    base = {"x": 2, "y": 1, "label": "Harbour", "kind": "note",
            "target": "notes/harbour.md"}
    base.update(over)
    return base


# ── the fixture, proved live ────────────────────────────────────────────────

def test_the_fixture_is_a_real_campaign(camp):
    """`find_campaign` returns a path that does not exist on a miss, and the
    exporter only checks `is_dir()`. A dead fixture makes every refusal test in
    this file pass by reading nothing, which is worse than no test at all.

    `require_campaign` raises instead of returning a miss, so reaching this
    assertion at all is the proof. SPEC §6 lines 511-513.
    """
    assert camp.is_dir()
    assert (camp / "state.md").is_file(), "a campaign needs state.md to resolve"
    assert (camp / "notes" / "harbour.md").is_file()


# ── off by default ──────────────────────────────────────────────────────────

def test_off_by_default_writes_no_pins(env):
    """The flag exists because the failure modes are silent, and a flag nobody
    sets cannot fail silently.

    Asserted in **both** directions, which is what gives this test teeth: a
    `--pins` that defaults on would export pins into a vault nobody asked to have
    them, and `verifier`'s mutant is exactly that.

    before fix: PASSES (the flag does not exist); kills the flag-defaulting mutant.
    """
    maps, vault, camp = env
    _store(camp, "test-cave", pin(id="p1"))

    # Off: nothing, and the report says so.
    off = mta.export(_map(), "test-cave", vault, COLL, maps)
    assert off["pins"] == 0
    assert _objects(env)["pins"] == {}

    # On: the same store lands.
    on = _run(env)
    assert on["pins"] == 1
    assert len(_objects(env)["pins"]) == 1


def test_a_pin_carries_a_literal_pin_kind(env):
    """Atlas's `kind: 'pin'` (types.ts:9) is not our "note"/"map" vocabulary.
    Writing ours produces a pin Atlas does not draw.

    before fix: KeyError -- `objects["pins"]` is `{}`.
    """
    _store(env[2], "test-cave", pin(id="p1"))
    _run(env)
    pins = _objects(env)["pins"]
    assert pins
    for entry in pins.values():
        assert entry["kind"] == "pin", f"Atlas wants a literal 'pin', not {entry['kind']!r}"
        # Nothing else of ours leaks in. `label` is dropped and `target`/`revealed`
        # are renamed, so a pin carrying our vocabulary is a pin that did not
        # convert.
        assert "label" not in entry
        assert "target" not in entry
        assert "revealed" not in entry


def test_the_label_does_not_cross(env):
    """Atlas's `label` is an auto-assigned sequence, reassigned on import for
    label-style pins (`stores/mapObjectsSlice.ts:55`), so ours would be
    overwritten. Paired with `test_the_non_ascii_label_survives_the_round`, which
    is where the non-ASCII actually gets exercised.

    before fix: KeyError.
    """
    _store(env[2], "test-cave", pin(id="p1", label="Kairos is the traitor"))
    _run(env)
    written = _objects(env)["pins"]["p1"]
    assert "Kairos is the traitor" not in json.dumps(written, ensure_ascii=False)


def test_a_pin_lands_on_its_cell_centre(env):
    """`offsetX + (x + 0.5) * size`, the expression tokens already use, and now
    through the one `cell_centre` both callers share.

    The second assertion is the one that matters: a pin and a token on the same
    square must land on the same pixel, and they can only do that if there is one
    expression rather than two copies kept in step by luck.

    before fix: KeyError.
    """
    spec = _map(grid={"cell_px": 100, "offset_x": 7, "offset_y": 11})
    # The token sits at (1,2); the pin goes on the SAME square, which is the only
    # way the comparison below can distinguish "one shared expression" from
    # "two copies that happen to agree".
    _store(env[2], "test-cave", pin(id="p1", x=1, y=2))
    _run(env, spec=spec)
    objects = _objects(env)
    entry = objects["pins"]["p1"]
    assert entry["x"] == round(7 + (1 + 0.5) * 100, 2) == 157.0
    assert entry["y"] == round(11 + (2 + 0.5) * 100, 2) == 261.0

    # Same cell, same pixel, on the same scene.
    token = objects["tokens"]["K"]
    assert (entry["x"], entry["y"]) == (token["x"], token["y"]) == (157.0, 261.0)


def test_revealed_crosses_as_gmonly(env):
    """The field is renamed and the value is inert (declared at `types.ts:15`,
    read nowhere in 0.4.2), but the rename is what makes the export re-checkable
    when Atlas changes. Also asserts the pin store's fail-closed default, since a
    pin written without the flag must not arrive as revealed.

    before fix: KeyError.
    """
    _store(env[2], "test-cave",
           pin(id="shown", revealed=True),
           pin(id="hidden", x=3, y=2, revealed=False))
    _run(env)
    written = _objects(env)["pins"]
    assert written["shown"]["gmOnly"] is True
    assert written["hidden"]["gmOnly"] is False


# ── the note target: the security property ──────────────────────────────────

def test_a_note_pin_lands_with_a_notepath(env):
    """`notePath` is vault-relative, because Atlas resolves it through
    `app.vault`. The positive case, so a dead fixture cannot make the refusals
    below pass vacuously.

    before fix: KeyError.
    """
    _store(env[2], "test-cave", pin(id="p1"))
    report = _run(env)
    entry = _objects(env)["pins"]["p1"]
    assert entry["notePath"] == "campaigns/demo/notes/harbour.md"
    assert "\\" not in entry["notePath"], "Atlas is not a Windows program"
    assert report["pins"] == 1


@pytest.mark.parametrize("target", [
    "answer-key.md",        # not in the allow-list, and it is the hard-rule-1 file
    "state.md",             # a .md file, but not under an allowed folder
    "DM_SEALED/twist.md",   # a sealed folder
    "secrets/plan.md",      # a folder nobody classified
    "../outside.md",        # containment, not the allow-list
    "notes/../../outside.md",
    "notes\\harbour.md",    # legal on POSIX, a traversal on Windows
])
def test_a_note_target_campaign_path_refuses_is_refused(env, target):
    """SPEC refusal 2, and the reason the leg is not "copy the string".

    `campaign_path` closes the traversal corpus and `check_note_target`'
    allow-list refuses the sealed files *by their absence from ALLOWED_DIRS*, not
    by name. Without both, the export converts a path the display refuses into
    one Obsidian opens in a single click.

    before fix: KeyError -- the pin dict is empty, so nothing is refused and
    nothing is written.
    """
    # Written raw: `pins.save` refuses these at authoring time, which is correct
    # and is precisely why the read path has to refuse them again.
    _raw_store(env[2], "test-cave", pin(id="p1", target=target))
    report = _run(env)
    assert _objects(env)["pins"] == {}, f"{target!r} must not become a notePath"
    assert report["pins"] == 0
    assert report["pin_refused"], f"{target!r} must be refused by name"
    written = json.dumps(_objects(env), ensure_ascii=False)
    assert "answer-key" not in written and "DM_SEALED" not in written


def test_a_note_target_outside_the_vault_is_refused(env, tmp_path):
    """SPEC refusal 3. `--vault` defaults to `$ATLAS_VAULT`/`~/atlas-vault`, which
    need not be the pin root, and `notePath` goes through `app.vault`. So a note
    outside the vault is a path Obsidian cannot reach, and the link is dead on
    arrival.

    The campaign here really exists and every gate before this one really passes.
    It is the vault that does not contain it, which is the only way to reach this
    refusal -- a refusal-only fixture could not tell this apart from a broken
    campaign.

    before fix: KeyError.
    """
    maps, vault, _ = env
    outside = tmp_path / "elsewhere"
    camp2 = outside / "campaigns" / "demo"
    (camp2 / "notes").mkdir(parents=True)
    (camp2 / "state.md").write_text("**System:** D&D 5e\n", encoding="utf-8")
    (camp2 / "notes" / "harbour.md").write_text("# The Harbour\n", encoding="utf-8")
    monkey = pytest.MonkeyPatch()
    monkey.setenv("GM_CAMPAIGN_ROOT", str(outside))
    try:
        real = require_campaign("demo")
        assert real.is_dir(), "the out-of-vault campaign must be real"
        _store(real, "test-cave", pin(id="p1"))
        report = mta.export(_map(), "test-cave", vault, COLL, maps,
                            with_pins=True, camp_dir=real)
    finally:
        monkey.undo()

    assert _objects(env)["pins"] == {}
    assert report["pins"] == 0
    assert "outside the vault" in report["pin_refused"][0]
    # The message names both paths, because the fix is to move one or the other.
    assert str(vault) in report["pin_refused"][0]


def test_a_note_target_outside_the_campaign_is_refused(env):
    """The symlink hole, on the export side.

    `notes/harbour.md -> ../../answer-key.md` passes the allow-list *as spelled*
    (segment `notes`, suffix `.md`) and passes containment, because the symlink
    points inside the campaign and `is_relative_to` answers "is this inside?",
    not "is this allowed?". The guard that closes it is re-applying the allow-list
    to the **resolved** path.

    The refusal-only corpus cannot tell a correct guard from "refuse every
    symlink", so `test_a_symlink_between_two_allowed_folders_still_works` is the
    other direction.

    before fix: KeyError.
    """
    maps, vault, camp = env
    # Points at `camp/answer-key.md`, so it stays INSIDE the campaign and passes
    # containment -- which is the point: the allow-list is what has to catch it.
    (camp / "notes" / "sneaky.md").symlink_to(camp / "answer-key.md")
    _store(camp, "test-cave", pin(id="p1", target="notes/sneaky.md"))
    report = _run(env)
    assert _objects(env)["pins"] == {}
    assert report["pins"] == 0
    assert "sneaky.md" in report["pin_refused"][0]


def test_a_symlink_between_two_allowed_folders_still_works(env):
    """The direction a refusal-only corpus cannot check. If every symlink were
    refused, the test above would pass for the wrong reason.

    before fix: KeyError (a symlinked note cannot land, because nothing lands).
    """
    maps, vault, camp = env
    (camp / "locations" / "aliased.md").symlink_to(camp / "notes" / "harbour.md")
    _store(camp, "test-cave", pin(id="p1", target="locations/aliased.md"))
    report = _run(env)
    assert report["pins"] == 1, report.get("pin_refused")
    # The RESOLVED path, because `campaign_path` resolves and the allow-list is
    # applied to what it resolved to. Both are allowed here, so it passes.
    assert _objects(env)["pins"]["p1"]["notePath"].endswith("notes/harbour.md")


def test_a_note_target_that_is_not_a_file_is_refused(env):
    """Neither reused gate requires the file to **exist** (`paths.py:210-216` says
    so outright), so without this the export would write a `notePath` naming a
    note that has been deleted. Same silent dead link the map side refuses, on the
    note side. `security`'s finding, and the one refusal here the spec does not
    list.

    before fix: KeyError.
    """
    _store(env[2], "test-cave", pin(id="p1", target="notes/never-written.md"))
    report = _run(env)
    assert _objects(env)["pins"] == {}
    assert report["pins"] == 0
    assert "not a file" in report["pin_refused"][0]


# ── the map target ──────────────────────────────────────────────────────────

def test_a_map_pin_lands_on_the_target_scene(env):
    """`notePath` ends `.atlasmap` and names the file `scene_file_name` builds
    from the **target's own** `name`, which is not its slug.

    before fix: KeyError.
    """
    maps, vault, camp = env
    cellar = _map("The Cellar", "cellar")
    mta.export(cellar, "cellar", vault, COLL, maps)
    _store(camp, "test-cave", pin(id="p1", kind="map", target="cellar"))
    report = _run(env)
    written = _objects(env)["pins"]["p1"]
    assert written["notePath"] == f"atlas-vtt/collections/{COLL}/The Cellar.atlasmap"
    assert report["pins"] == 1
    # The file it names is really there. This is the whole point of resolving
    # through `scene_file_name`: the click must not fall through to openLinkText.
    assert (vault / written["notePath"]).is_file()


def test_every_shipped_map_filename_differs_from_its_slug():
    """The reason the map resolution cannot be an interpolation.

    A test about `origin/main`'s data rather than about this code, kept
    deliberately: the "simplification" it forbids is one line long
    (`notePath = f".../{target}.atlasmap"`), and it writes a dead link for every
    map that ships. If a future change ever made the slug and the filename agree,
    this goes red and the resolution needs re-deciding rather than silently
    inheriting a stale assumption.

    before fix: PASSES (a data assertion); kills the slug-interpolation mutant.
    """
    maps_dir = ROOT / "display" / "maps"
    differing = 0
    for path in sorted(maps_dir.glob("*.json")):
        spec = json.loads(path.read_text(encoding="utf-8"))
        if mta.scene_file_name(spec, path.stem) != f"{path.stem}.atlasmap":
            differing += 1
    assert differing, "expected shipped maps whose scene filename differs from the slug"


def test_a_map_pin_whose_scene_is_absent_is_refused(env):
    """SPEC refusal 1, and the reason the P11 kill criterion did not fire.

    The target map exists in `display/maps/` and would export cleanly, but its
    `.atlasmap` is not in this collection yet. Writing the pin anyway produces a
    link that `sceneLinkTarget` cannot resolve, and the click falls through to
    `openLinkText` on an `.atlasmap` path, which Obsidian opens as plain text.

    before fix: KeyError.
    """
    _store(env[2], "test-cave", pin(id="p1", kind="map", target="cellar"))
    report = _run(env)
    assert _objects(env)["pins"] == {}, "a dead scene link must not be written"
    assert report["pins"] == 0
    assert "The Cellar.atlasmap" in report["pin_refused"][0]


def test_a_map_pin_the_same_run_writes_is_kept(env):
    """`map_to_atlas.py a b --pins` is the ordinary way to push two linked maps,
    and a pin on `a` naming `b` resolves within the same second. Refusing it for
    being a moment early would make the flag useless for the case it exists for.

    before fix: KeyError.
    """
    maps, vault, camp = env
    _store(camp, "test-cave", pin(id="p1", kind="map", target="cellar"))
    # `pending` is what main() passes: the scene names this run will write.
    pending = {mta.scene_file_name(_map("The Cellar", "cellar"), "cellar")}
    mta.export(_map("The Cellar", "cellar"), "cellar", vault, COLL, maps)
    report = mta.export(_map(), "test-cave", vault, COLL, maps,
                        with_pins=True, camp_dir=camp, pending=pending)
    assert report["pins"] == 1, report["pin_refused"]
    assert _objects(env)["pins"]["p1"]["notePath"].endswith("The Cellar.atlasmap")


def test_a_map_pin_naming_a_map_that_is_gone_is_refused(env):
    """A slug that no longer exists in `display/maps/`. `load_map` raises
    `FileNotFoundError`, which the pin loop catches -- a pin file can be hand
    edited and copied between campaigns, so the store is not trusted.

    before fix: KeyError.
    """
    _store(env[2], "test-cave", pin(id="p1", kind="map", target="map-that-was-deleted"))
    report = _run(env)
    assert _objects(env)["pins"] == {}
    assert report["pins"] == 0
    assert "map-that-was-deleted" in report["pin_refused"][0]


def test_a_map_pin_target_that_is_a_path_is_refused(env):
    """A map target is a slug, never a path. `pins.validate` refuses one on the
    way in; this asserts the export does not resolve one if a store predates that
    or is hand-edited.

    before fix: KeyError.
    """
    _raw_store(env[2], "test-cave", pin(id="p1", kind="map", target="../../etc/passwd"))
    report = _run(env)
    assert _objects(env)["pins"] == {}
    assert report["pins"] == 0


# ── refusal 4: the duplicate id ─────────────────────────────────────────────

def test_a_duplicate_id_is_refused(env):
    """Ours is a list and Atlas's is a keyed `Record`, so the second pin with an
    id disappears with no error at all.

    `pins.save` deduplicates by **square**, not by id, so two pins on different
    squares with the same id both reach the store. First wins and the loser is
    named; which one survived must not depend on the store's order without saying
    so.

    before fix: KeyError.
    """
    _store(env[2], "test-cave",
           pin(id="dup", x=0, y=0, label="first"),
           pin(id="dup", x=3, y=2, label="second"))
    report = _run(env)
    written = _objects(env)["pins"]
    assert list(written) == ["dup"], "one id, one pin"
    assert report["pins"] == 1
    assert len(report["pin_refused"]) == 1
    assert "'dup'" in report["pin_refused"][0]
    assert "second" in report["pin_refused"][0], "the refused one is named"


# ── the tie: the count must be of what was written ──────────────────────────

def test_report_counts_what_was_written_not_what_was_read(env):
    """The assertion SPEC §6 line 505 asks for, tied to the refusals so it has
    teeth.

    Five pins in the store, four of which are refused for four different reasons,
    and `report["pins"] == len(objects["pins"]) == 1`. An implementation counting
    what it *read* reports 5 and fails. An implementation that read nothing and
    wrote nothing reports 0 and also fails, because the store is asserted to have
    held five and the fourth refusal is asserted to have happened.

    before fix: KeyError.
    """
    camp = env[2]
    # Raw, because four of the five are ones `pins.save` correctly refuses at
    # authoring time -- and a store a GM can only get by hand-editing is exactly
    # what the read path has to survive.
    stored = _raw_store(
        camp, "test-cave",
        pin(id="good", target="notes/harbour.md"),
        pin(id="sealed", target="answer-key.md"),
        pin(id="escapes", target="../../outside.md"),
        pin(id="dead-scene", kind="map", target="cellar"),
        pin(id="vanished", target="notes/never-written.md"),
    )
    assert len(stored) == 5, "the fixture must hold five pins or this proves nothing"

    report = _run(env)
    written = _objects(env)["pins"]

    assert report["pins"] == len(written) == 1
    assert report["pins"] != len(stored), "a count of what was read cannot pass"
    assert list(written) == ["good"]
    assert len(report["pin_refused"]) == 4
    # Each refusal names its own pin, so a GM can find it. The two `..` targets
    # are refused by `validate`'s shape rules before any path is built, so the
    # message quotes the target rather than a resolved path.
    for wanted in ("answer-key.md", "outside.md", "The Cellar.atlasmap",
                   "never-written.md"):
        assert any(wanted in r for r in report["pin_refused"]), \
            f"{wanted} must be refused by name, not dropped silently"
    # The store held five distinct ids and one survived, which is what makes the
    # count a count of writes: 5 read, 1 written, 4 refused.
    assert len({p["id"] for p in stored}) == 5


# ── refusal 5: no campaign is not an empty store ────────────────────────────

def test_no_campaign_is_not_an_empty_store(env, capsys):
    """`--campaign` is optional, so a run with no campaign would silently write
    zero pins -- indistinguishable from a GM who has placed none. The report and
    the summary both have to say which zero it is.

    The campaign and its pins are real here. The refusal is about there being no
    campaign **to look in**, and a test that reached it through a dead fixture
    would be proving nothing.

    before fix: PASSES (the flag does not exist); kills the "both cases report
    pins: 0" mutant.
    """
    maps, vault, camp = env
    _store(camp, "test-cave", pin(id="p1"))
    assert _pins.pins_path(camp, "test-cave").is_file(), "the store must really exist"
    report = mta.export(_map(), "test-cave", vault, COLL, maps, with_pins=True)
    assert report.get("pin_missing_campaign") is True
    assert report["pins"] == 0
    assert _objects(env)["pins"] == {}

    # And the summary says why, not just how many.
    rc = mta.main(["test-cave", "--pins", "--vault", str(vault),
                   "--maps-dir", str(maps), "--dry-run"])
    out = capsys.readouterr()
    assert rc == 1
    assert "--pins needs --campaign" in out.err


def test_an_empty_store_is_reported_differently(env, capsys):
    """The other zero: a real campaign with no pins for this map. Same count,
    different sentence -- which is the entire requirement.

    before fix: PASSES (the flag does not exist); kills the same mutant from the
    other direction.
    """
    maps, vault, camp = env
    assert _run(env)["pins"] == 0
    assert _objects(env)["pins"] == {}

    rc = mta.main(["test-cave", "--pins", "--campaign", "demo",
                   "--vault", str(vault), "--maps-dir", str(maps)])
    out = capsys.readouterr()
    assert rc == 0
    assert "campaign" in out.out and "no pins for this map" in out.out
    assert "--pins needs --campaign" not in out.out + out.err


def test_pins_on_with_a_campaign_reports_both_kinds(env, capsys):
    """One pin and one refusal in the same run, so the summary proves the two
    lines are not alternatives.

    before fix: KeyError.
    """
    _raw_store(env[2], "test-cave",
               pin(id="good", target="notes/harbour.md"),
               pin(id="sealed", target="answer-key.md"))
    maps, vault, _ = env
    rc = mta.main(["test-cave", "--pins", "--campaign", "demo",
                   "--vault", str(vault), "--maps-dir", str(maps)])
    out = capsys.readouterr()
    assert rc == 0
    assert "1 pushed" in out.out
    assert "refused" in out.err and "answer-key.md" in out.err


# ── refusal 6: never read pins back ─────────────────────────────────────────

def test_the_exporter_never_opens_an_atlasmap_for_reading(env, monkeypatch):
    """The testable form of "do not read pins back".

    SPEC refusal 6, and it is the reason `carryForward` is not ported: that
    function works by opening the existing scene, which is exactly this. Every
    `.atlasmap` the exporter opens must be a **write**.

    before fix: PASSES (nothing reads a scene today); kills the mutant where
    somebody ports `carryForward` in.

    `pathlib.Path.open` is the single door: `read_text`, `read_bytes`,
    `write_text`, `write_bytes` and `Path.open` itself all go through it, so a
    wrapper here sees every filesystem access to a scene.
    """
    maps, vault, camp = env
    _store(camp, "test-cave", pin(id="p1"))
    real_open = pathlib.Path.open
    opened: list[tuple[str, str]] = []

    def spy(self, *a, **kw):
        if self.suffix == ".atlasmap":
            mode = kw.get("mode", a[0] if a else "r")
            opened.append((str(self), mode))
        return real_open(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "open", spy)
    # **Twice**, and that is the load-bearing detail: a `carryForward` port is
    # guarded by `if scene_path.is_file()`, so it reads nothing on a first export
    # into an empty collection and only reads on a re-export. A single run tests
    # the one case where the mutant is invisible, which is how a read-back
    # implementation could have passed this test.
    _run(env)
    _run(env)
    monkeypatch.undo()

    assert opened, "the exporter should have written the scene"
    for path, mode in opened:
        # "r" covers 'r', 'r+' and '+', all of which can read. 'w' and 'a' cannot.
        assert "r" not in mode, \
            f"{path} was opened for reading (mode {mode!r}); pins must not be read back"


def test_a_pin_placed_in_atlas_is_not_carried_forward(env):
    """The documented overwrite, asserted rather than assumed.

    Atlas is a second **author** (it has add, update and delete for pins), so a
    pin placed in Atlas is deleted by the next run: the scene is written with
    `write_text` unconditionally. `chartdown_to_atlas.mjs` solves this for its own
    leg by carrying forward every `objects` key it does not own -- by reading the
    existing scene, which refusal 6 forbids here.

    SPEC §4.5 offers the choice: port the idea, or report the overwrite. Both
    cannot be done, and this asserts which one was taken so it is a decision in
    the code rather than an accident.

    before fix: PASSES (the Atlas pin survives because nothing writes pins);
    kills the same mutant as above, observed rather than intercepted.
    """
    maps, vault, camp = env
    _run(env)
    scene = _scene_path(env)
    document = json.loads(scene.read_text(encoding="utf-8"))
    document["state"]["objects"]["pins"]["placed-in-atlas"] = {
        "id": "placed-in-atlas", "kind": "pin", "x": 1.0, "y": 1.0,
        "notePath": "notes/somewhere.md"}
    scene.write_text(json.dumps(document, indent=1) + "\n", encoding="utf-8")
    assert "placed-in-atlas" in _objects(env)["pins"]

    _run(env)
    assert "placed-in-atlas" not in _objects(env)["pins"], \
        "an Atlas pin surviving means somebody started reading scenes back"


def test_the_pin_file_is_unchanged_by_the_run(env):
    """The pin store is read-only on this path. The whole leg is one-way, and a
    re-serialised store would quietly normalise a GM's hand-edited file (key
    order, `ensure_ascii`, indentation) on every export.

    before fix: PASSES (nothing writes the store); kills the in-place-rewrite
    mutant, which is the one SPEC §6 line 508 names explicitly.
    """
    maps, vault, camp = env
    _store(camp, "test-cave",
           pin(id="p1", label="Ünicode label", target="notes/café-notes.md"),
           pin(id="p2", kind="map", x=3, y=2, target="cellar"))
    path = _pins.pins_path(camp, "test-cave")
    before = path.read_bytes()

    _run(env)
    assert path.read_bytes() == before, "the export must not write the pin store"
    assert path.read_text(encoding="utf-8").count("\n") == before.count(b"\n")


def test_the_non_ascii_label_survives_the_round(env):
    """Hard rule 4 on the new path, and it is exercised twice: a non-ASCII
    **label** in the store (which must not break the read) and a non-ASCII note
    **filename** (which does cross, inside `notePath`).

    before fix: KeyError.
    """
    maps, vault, camp = env
    _store(camp, "test-cave",
           pin(id="p1", label="Ünicode Ünïcode", target="notes/café-notes.md"))
    report = _run(env)
    assert report["pins"] == 1, report.get("pin_refused")
    written = _objects(env)["pins"]["p1"]
    assert written["notePath"] == "campaigns/demo/notes/café-notes.md"

    # Hard rule 4, on the bytes. `export` writes the scene with
    # `encoding="utf-8"` (`:665`), so it decodes as UTF-8 whatever it holds, and
    # the accent survives the round trip rather than arriving as mojibake. The
    # JSON escapes it as `é` by default, which is valid and is what a
    # reader gets either way.
    raw = _scene_path(env).read_bytes()
    # The scene is written with `encoding="utf-8"`, so the bytes decode as UTF-8
    # and the accent round-trips. Asserted through `json.loads` rather than by
    # substring, because `json.dumps` escapes non-ASCII by default and the file
    # carries `café`, not the raw byte. Both facts are the point:
    # a reader gets the right path either way, and it decodes cleanly.
    document = json.loads(raw.decode("utf-8"))
    assert document["state"]["objects"]["pins"]["p1"]["notePath"] == \
        "campaigns/demo/notes/café-notes.md", "the accent must survive the write"
    raw.decode("utf-8")            # raises if the file is not valid UTF-8
    # And the store's own non-ASCII label did not break the read that found it.
    stored = _pins.pins_path(camp, "test-cave").read_text(encoding="utf-8")
    assert "Ünicode Ünïcode" in stored, "the label was not rewritten by the export"


# ── what does not change ────────────────────────────────────────────────────

def test_a_run_without_the_flag_never_reads_the_store(env, monkeypatch):
    """Off means off, not "off unless it is convenient". A pin store that cannot
    be read at all on the default path is what makes the flag safe to leave off.

    before fix: PASSES; kills the mutant where `build_pins` is called
    unconditionally.
    """
    maps, vault, camp = env
    _store(camp, "test-cave", pin(id="p1"))
    real_load = _pins.load
    called = []
    monkeypatch.setattr(_pins, "load",
                        lambda *a, **kw: (called.append(a), real_load(*a, **kw))[1])
    mta.export(_map(), "test-cave", vault, COLL, maps)
    assert not called, "the default path must not touch the pin store"


def test_build_scene_still_defaults_to_no_pins(env):
    """The 38 existing `build_scene` tests describe a four-argument call, so the
    new parameter is keyword-only with a `None` default and `{}` is what an
    unpassed pin set means. This asserts that directly rather than trusting the
    other file.

    before fix: PASSES; kills the mutant where the parameter becomes required.
    """
    scene = mta.build_scene(_map(), "test-cave", None,
                            {"quan": {"vault_path": "quan.png"}})
    assert scene["objects"]["pins"] == {}