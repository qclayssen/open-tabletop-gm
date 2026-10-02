"""The campaign overview map (BV4 S1): the spec, the /atlas routes, the page.

Three layers, one file, because each is small:

- the spec: `scripts/overview_map.py` validate/revealed/load, called directly;
- the routes: /atlas, /atlas/<slug> and /atlas/<slug>/image through the real
  Flask app, with the campaign pointed at a tmp_path one (the same fixture
  shape tests/test_pins_routes.py uses, for the same reasons);
- the page: the entry link in index.html, and /atlas/<slug> drawn in a real
  browser (skipped when playwright or Chromium is absent).

`before fix:` for every case is that none of it exists: no overview_map module,
/atlas is a 404, and the display has no way to reach an overview map.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import sys
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import overview_map  # noqa: E402

from tests._browser import BrowserTestCase  # noqa: E402

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="140" height="190"/>'


def _spec(**over):
    spec = {"kind": "overview", "slug": "campus", "name": "Campus",
            "image": "maps/campus.svg", "extent": [1400, 1900],
            "pins": [{"id": "library", "label": "Library", "x": 0.5, "y": 0.25,
                      "revealed": True},
                     {"id": "vault", "label": "Sealed Vault", "x": 0.1, "y": 0.9}]}
    spec.update(over)
    return spec


def _write_campaign(campaign: pathlib.Path, spec=None) -> pathlib.Path:
    """One overview map: maps/campus.svg plus maps/overview/campus.json."""
    (campaign / "maps" / "overview").mkdir(parents=True, exist_ok=True)
    (campaign / "maps" / "campus.svg").write_text(SVG, encoding="utf-8")
    (campaign / "maps" / "overview" / "campus.json").write_text(
        json.dumps(spec or _spec()), encoding="utf-8")
    return campaign


# ── the spec ──────────────────────────────────────────────────────────────────

def test_valid_spec_keeps_fractional_pins_and_strict_revealed():
    clean = overview_map.validate(_spec())
    assert clean["pins"][0] == {"id": "library", "label": "Library",
                                "x": 0.5, "y": 0.25, "revealed": True}
    # No `revealed` key, and a truthy non-True one, both fail closed.
    assert clean["pins"][1]["revealed"] is False
    truthy = _spec(pins=[{"id": "a", "label": "A", "x": 0, "y": 1, "revealed": "yes"}])
    assert overview_map.validate(truthy)["pins"][0]["revealed"] is False


def test_name_falls_back_to_slug():
    spec = _spec()
    del spec["name"]
    assert overview_map.validate(spec)["name"] == "campus"


@pytest.mark.parametrize("over", [
    {"kind": "battle"},
    {"slug": "../campus"},
    {"image": "campus.svg"},                      # not under maps/
    {"image": "notes/campus.svg"},                # not under maps/
    {"image": "maps/../state.md"},                # traversal
    {"image": "/etc/passwd"},                     # absolute
    {"image": "maps\\campus.svg"},                # backslash
    {"image": "maps/campus.md"},                  # not an image
    {"image": "maps/campus.gm.svg"},              # the GM render
    {"extent": [100]},
    {"extent": [0, 100]},
    {"extent": [True, 100]},
    {"pins": [{"id": "a", "label": "A", "x": 1.5, "y": 0}]},
    {"pins": [{"id": "a", "label": "A", "x": 0, "y": -0.1}]},
    {"pins": [{"id": "a", "label": "A", "x": float("nan"), "y": 0}]},
    {"pins": [{"id": "a", "label": "", "x": 0, "y": 0}]},
    {"pins": [{"id": "a b", "label": "A", "x": 0, "y": 0}]},
    {"pins": [{"id": "a", "label": "A", "x": 0, "y": 0},
              {"id": "a", "label": "B", "x": 1, "y": 1}]},
    {"pins": [{"id": f"p{i}", "label": "P", "x": 0, "y": 0}
              for i in range(overview_map.MAX_PINS + 1)]},
])
def test_invalid_spec_is_refused(over):
    with pytest.raises(overview_map.OverviewMapError):
        overview_map.validate(_spec(**over))


def test_revealed_drops_unrevealed_pins_entirely():
    shown = overview_map.revealed(overview_map.validate(_spec()))
    assert [p["id"] for p in shown["pins"]] == ["library"]
    assert "revealed" not in shown["pins"][0]
    assert "Sealed Vault" not in json.dumps(shown)


def test_load_refuses_a_symlink_out_of_maps(tmp_path):
    camp = _write_campaign(tmp_path / "demo")
    (camp / "notes").mkdir()
    secret = camp / "notes" / "secret.svg"
    secret.write_text(SVG, encoding="utf-8")
    try:
        (camp / "maps" / "link.svg").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available here")
    (camp / "maps" / "overview" / "campus.json").write_text(
        json.dumps(_spec(image="maps/link.svg")), encoding="utf-8")
    with pytest.raises(overview_map.OverviewMapError):
        overview_map.load(camp, "campus")


def test_load_refuses_a_slug_mismatch_and_a_missing_image(tmp_path):
    camp = _write_campaign(tmp_path / "demo")
    (camp / "maps" / "overview" / "other.json").write_text(
        json.dumps(_spec()), encoding="utf-8")  # says slug "campus"
    with pytest.raises(overview_map.OverviewMapError):
        overview_map.load(camp, "other")
    (camp / "maps" / "campus.svg").unlink()
    with pytest.raises(overview_map.OverviewMapError):
        overview_map.load(camp, "campus")


# ── the routes ────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def app_module():
    """The display app as a module. It binds a port on import, hence the dance."""
    saved = os.environ.get("GM_DISPLAY_PORT")
    os.environ["GM_DISPLAY_PORT"] = "5095"
    spec = importlib.util.spec_from_file_location(
        "gm_overview_app", ROOT / "display" / "gm-display-app.py")
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
def client(app_module, tmp_path, monkeypatch):
    """A test client whose active campaign is tmp_path/campaigns/demo (empty)."""
    (tmp_path / "campaigns" / "demo").mkdir(parents=True)
    monkeypatch.setenv("GM_CAMPAIGN_ROOT", str(tmp_path))
    campaign_file = tmp_path / ".campaign"
    campaign_file.write_text("demo", encoding="utf-8")
    monkeypatch.setattr(app_module, "CAMP_FILE", str(campaign_file))
    return app_module.app.test_client()


def _camp(tmp_path):
    return tmp_path / "campaigns" / "demo"


def test_atlas_redirects_to_the_first_overview_map(client, tmp_path):
    _write_campaign(_camp(tmp_path))
    entry = client.get("/atlas", follow_redirects=False)
    assert entry.status_code == 302
    assert entry.headers["Location"] == "/atlas/campus"


def test_atlas_without_an_overview_map_says_so(client):
    entry = client.get("/atlas")
    assert entry.status_code == 404
    assert b"No overview map" in entry.data


def test_overview_page_carries_only_revealed_pins(client, tmp_path):
    _write_campaign(_camp(tmp_path))
    page = client.get("/atlas/campus")
    assert page.status_code == 200
    assert b"Campus" in page.data and b"/static/atlas.js" in page.data
    assert b"Library" in page.data
    # The unrevealed pin is not hidden client-side; it never leaves the server.
    assert b"Sealed Vault" not in page.data and b"vault" not in page.data


def test_overview_page_escapes_a_label_out_of_the_data_block(client, tmp_path):
    hostile = _spec(name="<b>Campus</b>", pins=[
        {"id": "x", "label": "</script><script>alert(1)</script>",
         "x": 0.5, "y": 0.5, "revealed": True}])
    _write_campaign(_camp(tmp_path), hostile)
    page = client.get("/atlas/campus")
    assert page.status_code == 200
    assert b"</script><script>alert(1)" not in page.data
    assert b"<b>Campus</b>" not in page.data


def test_overview_image_is_served_sandboxed(client, tmp_path):
    _write_campaign(_camp(tmp_path))
    image = client.get("/atlas/campus/image")
    assert image.status_code == 200
    assert b"<svg" in image.data
    assert "sandbox" in image.headers["Content-Security-Policy"]


@pytest.mark.parametrize("path", [
    "/atlas/nope", "/atlas/nope/image", "/atlas/..%2Fstate", "/atlas/campus%20x"])
def test_unknown_overview_map_is_a_404(client, tmp_path, path):
    _write_campaign(_camp(tmp_path))
    assert client.get(path).status_code == 404


def test_invalid_spec_is_a_404_not_a_500(client, tmp_path):
    _write_campaign(_camp(tmp_path), _spec(image="maps/campus.gm.svg"))
    assert client.get("/atlas/campus").status_code == 404
    assert client.get("/atlas/campus/image").status_code == 404
    assert client.get("/atlas").status_code == 404


# ── the page ──────────────────────────────────────────────────────────────────

def test_display_has_an_overview_entry_link():
    template = (ROOT / "display" / "templates" / "index.html").read_text(encoding="utf-8")
    assert 'id="overview-link" href="/atlas"' in template
    js = (ROOT / "display" / "static" / "atlas.js").read_text(encoding="utf-8")
    # Labels reach the page through textContent only.
    assert "innerHTML" not in js and "insertAdjacentHTML" not in js


class OverviewPageInBrowser(BrowserTestCase):
    """/atlas/campus drawn by atlas.js: one marker and one legend row per revealed pin."""

    module_name = "gm_display_app_overview"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmp = tempfile.mkdtemp()
        tmp = pathlib.Path(cls._tmp)
        _write_campaign(tmp / "campaigns" / "demo")
        (tmp / ".campaign").write_text("demo", encoding="utf-8")
        cls._saved_root = os.environ.get("GM_CAMPAIGN_ROOT")
        os.environ["GM_CAMPAIGN_ROOT"] = str(tmp)
        cls._saved_camp_file = cls.server.module.CAMP_FILE
        cls.server.module.CAMP_FILE = str(tmp / ".campaign")

    @classmethod
    def tearDownClass(cls):
        server = getattr(cls, "server", None)
        if server is not None and hasattr(cls, "_saved_camp_file"):
            server.module.CAMP_FILE = cls._saved_camp_file
        if hasattr(cls, "_saved_root"):
            if cls._saved_root is None:
                os.environ.pop("GM_CAMPAIGN_ROOT", None)
            else:
                os.environ["GM_CAMPAIGN_ROOT"] = cls._saved_root
        if hasattr(cls, "_tmp"):
            shutil.rmtree(cls._tmp, ignore_errors=True)
        super().tearDownClass()

    def test_markers_and_legend_match_the_revealed_pins(self):
        page = self.open_page(size=(1280, 720), path="/atlas/campus", wait=300)
        got = page.evaluate("""() => {
          const stage = document.getElementById('atlas-stage').getBoundingClientRect();
          const pin = document.querySelector('.atlas-pin');
          const r = pin ? pin.getBoundingClientRect() : null;
          return {
            pins: [...document.querySelectorAll('.atlas-pin')].map(e => e.textContent),
            labels: [...document.querySelectorAll('.atlas-place-label')].map(e => e.textContent),
            empty: document.getElementById('atlas-empty').hidden,
            fx: r ? (r.left + r.width / 2 - stage.left) / stage.width : null,
            fy: r ? (r.top + r.height / 2 - stage.top) / stage.height : null,
            ratio: stage.width / stage.height,
          };
        }""")
        self.assertEqual(got["pins"], ["1"])
        self.assertEqual(got["labels"], ["Library"])
        self.assertTrue(got["empty"])
        # The marker sits at its fraction of the stage, and the stage keeps the
        # extent's aspect, so that is its fraction of the image.
        self.assertAlmostEqual(got["fx"], 0.5, delta=0.01)
        self.assertAlmostEqual(got["fy"], 0.25, delta=0.01)
        self.assertAlmostEqual(got["ratio"], 1400 / 1900, delta=0.01)
