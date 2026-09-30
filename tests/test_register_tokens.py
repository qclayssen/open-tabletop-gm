"""register_tokens.py: a file on disk is not an Atlas token.

The whole script exists because of one fact: Atlas lists a token from an entry
in assets-metadata.json, and copying a portrait into the vault does not create
an entry. A vault full of correct art therefore still shows "Missing image" for
every token, which is indistinguishable from the Bestiary Folder bug and just
as invisible.
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import register_tokens as rt
import utf8io

try:
    from PIL import Image
    HAVE_PIL = True
except ImportError:
    HAVE_PIL = False


def vault(tmp_path, *, collections=("Strixhaven",), extra_assets=None):
    """A minimal Atlas vault, close enough to the real one to be worth testing."""
    v = tmp_path / "vault"
    meta = v / "atlas-vtt/.atlas-data"
    meta.mkdir(parents=True)
    (meta / "assets-metadata.json").write_text(json.dumps({
        "version": 2, "vaultId": "v", "defaultCollectionId": collections[0],
        "collections": {c: {"id": c, "uid": f"uid-{c}", "version": 1, "name": c,
                            "tags": {}, "settings": {"conditions": []},
                            "createdAt": 1, "modifiedAt": 1} for c in collections},
        "assets": extra_assets or {},
    }, indent=2), encoding="utf-8")
    return v


def art(tmp_path, names=("Aboleth", "Cultist"), suffix=".png"):
    d = tmp_path / "art"
    d.mkdir(exist_ok=True)
    for n in names:
        p = d / f"{n}{suffix}"
        if suffix == ".png" and HAVE_PIL:
            Image.new("RGBA", (256, 256), (10, 20, 30, 255)).save(p)
        else:
            p.write_bytes(b"\x89PNG\r\n\x1a\n" + n.encode())
    return d


def index(v):
    return json.loads(utf8io.read_text(v / "atlas-vtt/.atlas-data/assets-metadata.json"))


# ── the hash is not our invention; it has to agree with Atlas's own ──────────

def test_the_thumbnail_hash_matches_the_five_a_working_vault_already_had():
    """These five are the thumbnail suffixes of a real Strixhaven vault. If the
    hash drifts, every token this script writes gets a blank swatch, and nothing
    errors -- so this is pinned against observed output, not against the spec."""
    observed = {
        "atlas-vtt/collections/Strixhaven/tokens/brass.png": "3ff844a4",
        "atlas-vtt/collections/Strixhaven/tokens/lore.png": "9af0a5dd",
        "atlas-vtt/collections/Strixhaven/tokens/pris.png": "824b335f",
        "atlas-vtt/collections/Strixhaven/tokens/silv.png": "8989e753",
        "atlas-vtt/collections/Strixhaven/tokens/with.png": "cae21461",
    }
    for path, expected in observed.items():
        assert rt.fnv1a_32(path) == expected, path


def test_the_hash_is_32_bit_fnv1a_and_not_a_python_bignum():
    """JS Math.imul is a 32-bit multiply. Without the mask, Python's unbounded
    ints produce a different hash for every path."""
    h = rt.fnv1a_32("atlas-vtt/collections/Strixhaven/tokens/zzz.png")
    assert len(h) == 8 and all(c in "0123456789abcdef" for c in h)


# ── registering actually makes it importable ────────────────────────────────

def test_a_registered_token_points_at_a_file_that_exists(tmp_path):
    v = vault(tmp_path)
    res = rt.register(v, art(tmp_path), "Strixhaven", ["open-tabletop-gm"])
    assert len(res["added"]) == 2
    for entry in index(v)["assets"].values():
        assert (v / entry["imagePath"]).exists(), entry["imagePath"]


def test_the_entry_shape_is_the_one_atlas_reads(tmp_path):
    v = vault(tmp_path)
    rt.register(v, art(tmp_path), "Strixhaven", ["bestiary"])
    entry = next(iter(index(v)["assets"].values()))
    for key in ("id", "type", "name", "imagePath", "tags", "collection",
                "createdAt", "modifiedAt", "thumbnailPath"):
        assert key in entry, f"Atlas needs {key!r} and this entry lacks it"
    assert entry["type"] == "token"
    assert entry["tags"] == ["bestiary"]


def test_the_thumbnail_is_written_at_the_path_the_entry_names(tmp_path):
    v = vault(tmp_path)
    res = rt.register(v, art(tmp_path), "Strixhaven", [])
    entry = next(iter(index(v)["assets"].values()))
    if res["thumbnails"]:
        assert (v / entry["thumbnailPath"]).exists()
        assert pathlib.Path(entry["thumbnailPath"]).name == (
            f"{pathlib.Path(entry['imagePath']).stem}-"
            f"{rt.fnv1a_32(entry['imagePath'])}.webp")


def test_names_are_slugged_so_the_image_path_survives_a_space(tmp_path):
    v = vault(tmp_path)
    rt.register(v, art(tmp_path, names=("Adult Blue Dragon",)), "Strixhaven", [])
    entry = next(iter(index(v)["assets"].values()))
    assert " " not in pathlib.Path(entry["imagePath"]).name
    assert entry["name"] == "Adult Blue Dragon"   # display name keeps the space


# ── idempotence, because this is safe to run in a loop ──────────────────────

def test_running_twice_registers_nothing_new(tmp_path):
    v = vault(tmp_path)
    a = art(tmp_path)
    rt.register(v, a, "Strixhaven", [])
    before = index(v)
    res = rt.register(v, a, "Strixhaven", [])
    assert len(res["added"]) == 0 and len(res["skipped"]) == 2
    assert index(v) == before, "a second run must not churn the index"


def test_running_twice_without_pillow_still_skips(tmp_path, monkeypatch):
    """The idempotence the section above promises must not depend on Pillow.

    resize_for_thumbnail returns False when Pillow is absent, so no thumbnail
    file is ever written and the "entry is complete" check can never be
    satisfied. Without this, every run re-registered the whole folder and
    reported it as `updated`, which is false and unbounded. It surfaced only in
    CI, because CI is the one machine without Pillow, and it turned main red.
    """
    monkeypatch.setattr(rt, "HAVE_PIL", False)
    v = vault(tmp_path)
    a = art(tmp_path)
    first = rt.register(v, a, "Strixhaven", [])
    second = rt.register(v, a, "Strixhaven", [])
    assert len(first["added"]) == 2 and not first["skipped"]
    assert second["added"] == [] and second["updated"] == [], (
        "a second run with no Pillow must not re-register unchanged portraits")
    assert len(second["skipped"]) == 2


def test_a_changed_portrait_is_still_registered_without_pillow(tmp_path, monkeypatch):
    """The companion risk: skipping must not mean ignoring changed art.

    The skip above is gated on the copied file matching the source byte for
    byte, so a portrait that genuinely changed still reaches the write path.
    """
    monkeypatch.setattr(rt, "HAVE_PIL", False)
    v = vault(tmp_path)
    a = art(tmp_path)
    # Overwrite with stub bytes either way. art() writes a REAL png when Pillow
    # is importable in this module, and if the first run produced a real
    # thumbnail on disk then the second run skips for the ordinary, correct
    # reason and this test would prove nothing. Stub bytes also keep
    # resize_for_thumbnail from trying to open them.
    (a / "Aboleth.png").write_bytes(b"\x89PNG\r\n\x1a\nBEFORE")
    rt.register(v, a, "Strixhaven", [])
    (a / "Aboleth.png").write_bytes(b"\x89PNG\r\n\x1a\nAFTER")
    res = rt.register(v, a, "Strixhaven", [])
    assert "Aboleth" not in res["skipped"], "changed art must not be skipped"
    assert "Aboleth" in res["updated"]
    dest = v / "atlas-vtt/collections/Strixhaven/tokens/aboleth.png"
    assert dest.read_bytes() == b"\x89PNG\r\n\x1a\nAFTER", "new bytes must be copied"


def test_force_re_registers_a_complete_entry(tmp_path):
    v = vault(tmp_path)
    a = art(tmp_path)
    rt.register(v, a, "Strixhaven", [])
    res = rt.register(v, a, "Strixhaven", [], force=True)
    assert len(res["updated"]) == 2 and len(index(v)["assets"]) == 2


# ── refusals: a shared document is worth more than a convenient run ─────────

def test_an_unknown_collection_is_refused(tmp_path):
    v = vault(tmp_path)
    try:
        rt.register(v, art(tmp_path), "Nope", [])
    except rt.Refused as e:
        assert "Nope" in str(e)
    else:
        raise AssertionError("must refuse a collection Atlas does not have")


def test_a_corrupt_index_is_refused_and_left_exactly_as_it_was(tmp_path):
    v = vault(tmp_path)
    f = v / "atlas-vtt/.atlas-data/assets-metadata.json"
    f.write_text("{ broken", encoding="utf-8")
    try:
        rt.register(v, art(tmp_path), "Strixhaven", [])
    except rt.Refused:
        pass
    else:
        raise AssertionError("must refuse rather than overwrite the library")
    assert f.read_text(encoding="utf-8") == "{ broken"


def test_a_vault_with_no_index_is_refused_not_created(tmp_path):
    v = tmp_path / "not-a-vault"
    v.mkdir()
    try:
        rt.register(v, art(tmp_path), "Strixhaven", [])
    except rt.Refused as e:
        assert "atlas-vtt" in str(e)
    else:
        raise AssertionError("must refuse a directory that is not an Atlas vault")


def test_an_empty_art_directory_is_refused(tmp_path):
    v = vault(tmp_path)
    d = tmp_path / "empty"
    d.mkdir()
    try:
        rt.register(v, d, "Strixhaven", [])
    except rt.Refused:
        pass
    else:
        raise AssertionError("must refuse rather than write an empty index change")


def test_dry_run_changes_nothing_on_disk(tmp_path):
    v = vault(tmp_path)
    before = index(v)
    res = rt.register(v, art(tmp_path), "Strixhaven", [], dry_run=True)
    assert len(res["added"]) == 2
    assert index(v) == before
    assert not (v / "atlas-vtt/collections").exists(), "dry run wrote files"


# ── --list is how the user finds out a token is broken ──────────────────────

def test_list_reports_a_missing_image_and_exits_nonzero(tmp_path):
    v = vault(tmp_path)
    rt.register(v, art(tmp_path), "Strixhaven", [])
    # delete one picture, leaving the entry behind: the real-world failure
    entry = next(iter(index(v)["assets"].values()))
    (v / entry["imagePath"]).unlink()
    assert rt.list_tokens(v) == 1


# ── the atomic write, which is what makes the whole thing safe ──────────────

def test_write_text_leaves_no_temp_file_behind(tmp_path):
    p = tmp_path / "x.json"
    utf8io.write_text(p, '{"a":1}\n')
    assert [q.name for q in tmp_path.iterdir()] == ["x.json"]
    assert utf8io.read_text(p) == '{"a":1}\n'


def test_a_failed_write_does_not_clobber_the_original(tmp_path):
    p = tmp_path / "x.json"
    utf8io.write_text(p, 'ORIGINAL')
    try:
        utf8io.write_text(p, object())      # not a str
    except TypeError:
        pass
    assert utf8io.read_text(p) == "ORIGINAL"
    assert [q.name for q in tmp_path.iterdir()] == ["x.json"]


def test_write_text_replaces_in_place_not_appends(tmp_path):
    p = tmp_path / "x.json"
    utf8io.write_text(p, 'FIRST')
    utf8io.write_text(p, 'SECOND')
    assert utf8io.read_text(p) == 'SECOND'
