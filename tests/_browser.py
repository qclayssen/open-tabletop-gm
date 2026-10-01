"""Shared browser setup for the display tests (not a test module itself).

WHY THIS EXISTS
===============
Playwright used to be installed only by hand, and each browser file guarded its
own `HAVE_PLAYWRIGHT` and skipped wholesale when the import failed. CI now
installs Chromium (#164), so those files run, and seven of them turned out to
carry the same thirty lines: load gm-display-app.py under a private module name,
bind it to an ephemeral port, start a daemon thread, start a Playwright driver,
launch chromium, and tear all four down again. Every class paid the launch cost
and every file had its own idea of a fixed port, which under `pytest -n 4` is
the bind failure that skips a whole class and reads as a passing suite.

The shape here is the one the rest of the test helpers use (`display_sources`,
`tactics_fixtures`, `localdm_fakes`): a plain module of functions and small
classes, imported by the tests, with no test cases of its own.

Three things are shared and three are deliberately not:

- the chromium process, for the whole pytest worker. Launching it is the single
  most expensive thing a display test does, and nothing in the display tests
  mutates it. One per worker, not one per class. This is what "session scope"
  means here: there is no pytest fixture, so scope is the process, and xdist
  gives each worker its own process and therefore its own browser.
- the server implementation, not the server instance. Each test class still gets
  its own gm-display-app.py module and its own port. That module carries
  mutable state (`_current_stats`, `_current_combat`), and the XSS tests post
  into it, so a shared instance would couple classes that pass today.
- the wait. Every call site passes the wait its own test used. A shared default
  would quietly change the timing of whichever file did not override it, and
  these files are exactly the ones whose timing bugs hide.

`BrowserTestCase` is the base class most of those files want. Subclasses get
`self.browser`, `self.port` and `self.url(path)`, and set `server_kind` to
`"flask"` for the real app or `"static"` for display/evidence-panel.html.
"""
from __future__ import annotations

import atexit
import http.server
import importlib.util
import pathlib
import socketserver
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
DISPLAY_DIR = ROOT / "display"
DISPLAY_APP = DISPLAY_DIR / "gm-display-app.py"
HARNESS = DISPLAY_DIR / "evidence-panel.html"

try:                                                            # pragma: no cover
    from playwright.sync_api import sync_playwright
    HAVE_PLAYWRIGHT = True
except ImportError:                        # the reason the CI step exists
    HAVE_PLAYWRIGHT = False

SKIP_REASON = "playwright is not installed"


class BrowserUnavailable(Exception):
    """Chromium could not be launched. The caller turns this into a SkipTest.

    A distinct type rather than a bare exception so a missing browser reads as
    "the measurement could not run" and not as "the measurement failed". Every
    one of these classes measures something that needs a real browser, so a
    launch failure takes the whole class with it either way; saying which is
    the difference between an honest skip and a mystery.
    """


# ── the shared chromium ───────────────────────────────────────────────────────

class _SharedChromium:
    """One chromium per process, relaunched if it dies."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pw = None
        self._browser = None

    def acquire(self):
        with self._lock:
            if self._browser is not None and self._browser.is_connected():
                return self._browser
            # is_connected() False means a previous class crashed or closed it.
            # Relaunch rather than hand out a dead handle, and rather than leak
            # the old driver: stop() on a crashed driver is cheap but not free.
            self._stop_locked()
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch()
            return self._browser

    def close(self) -> None:
        with self._lock:
            self._stop_locked()

    def _stop_locked(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None


_CHROMIUM = _SharedChromium()
atexit.register(_CHROMIUM.close)


def shared_browser():
    """The chromium for this process. Raises BrowserUnavailable if there is none."""
    if not HAVE_PLAYWRIGHT:
        raise BrowserUnavailable(SKIP_REASON)
    try:
        return _CHROMIUM.acquire()
    except Exception as exc:
        raise BrowserUnavailable(f"chromium is not available: {exc}") from exc


# ── the servers ───────────────────────────────────────────────────────────────

def load_display_app(name: str):
    """Load gm-display-app.py under a private module name.

    Private per call because the module is stateful: `_current_stats` and
    `_current_combat` live on it, and test_display_xss.py posts into the very
    instance its page is subscribed to. Two classes sharing one instance would
    leak payloads between them.
    """
    spec = importlib.util.spec_from_file_location(name, str(DISPLAY_APP))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DisplayServer:
    """The Flask display on a port the OS picks, for tests that load index.html.

    `threaded` defaults to True and should stay that way. The display holds an
    SSE connection open at `/stream` for as long as the page is up, and
    werkzeug serves one request at a time when it is not threaded, so a
    non-threaded server answers exactly one asset after the stream opens and
    then stalls. Two files were written that way and passed only because
    `wait_until="load"` fires before the client subscribes.
    """

    def __init__(self, name: str, threaded: bool = True) -> None:
        from werkzeug.serving import make_server
        self.module = load_display_app(name)
        self.httpd = make_server("127.0.0.1", 0, self.module.app, threaded=threaded)
        self.port = self.httpd.server_port
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def app(self):
        return self.module.app

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self) -> None:
        self.httpd.shutdown()
        # shutdown() stops the serve loop; it does NOT close the listening
        # socket. Without server_close() the port stays bound and a second
        # bind in the same run fails with Errno 48.
        self.httpd.server_close()


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class StaticDisplayServer:
    """display/ as plain files, for tests that drive evidence-panel.html.

    Port 0 rather than a hardcoded one: `pytest -n 4` can put two of these
    classes on two workers at the same moment, and a fixed port is then already
    bound, the bind raises, and setUpClass skips the whole class. That is the
    silent green the playwright CI step was added to stop.
    """

    def __init__(self) -> None:
        handler = lambda *a, **k: _Quiet(*a, directory=str(DISPLAY_DIR), **k)
        socketserver.TCPServer.allow_reuse_address = True
        self.httpd = socketserver.TCPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# ── the base class ────────────────────────────────────────────────────────────

class BrowserTestCase(unittest.TestCase):
    """A test that has to measure the real page in a real browser.

    `server_kind` is "flask" (the real app, loaded by `module_name`) or
    "static" (display/ on disk). `ready_js`, when set, is the predicate
    evidence-panel.html publishes; open_page waits for it before returning,
    which is the whole difference between a measurement and a race.
    """

    server_kind = "flask"
    module_name = "gm_display_app_shared"
    ready_js = None
    ready_timeout = 15000
    harness_path = "/evidence-panel.html"

    @classmethod
    def setUpClass(cls) -> None:
        if not HAVE_PLAYWRIGHT:
            raise unittest.SkipTest(SKIP_REASON)
        try:
            cls.browser = shared_browser()
        except BrowserUnavailable as exc:
            raise unittest.SkipTest(str(exc)) from exc
        try:
            if cls.server_kind == "static":
                if not HARNESS.exists():
                    raise unittest.SkipTest("display/evidence-panel.html is missing")
                cls.server = StaticDisplayServer()
            else:
                cls.server = DisplayServer(cls.module_name)
        except unittest.SkipTest:
            raise
        except OSError as exc:
            # A port we cannot bind is an environment problem, not a layout
            # failure. Raising here would take the whole class with it and read
            # as a broken panel.
            raise unittest.SkipTest(f"cannot serve {DISPLAY_DIR}: {exc}") from exc
        cls.port = cls.server.port

    @classmethod
    def tearDownClass(cls) -> None:
        server = getattr(cls, "server", None)
        if server is not None:
            server.stop()
            cls.server = None

    # ── the address under test ─────────────────────────────────────────────

    def url(self, path: str = None) -> str:
        if path is None:
            path = self.harness_path if self.server_kind == "static" else "/"
        return self.server.url(path)

    def clear_display_state(self) -> None:
        """Wipe the server's stats before measuring a default layout.

        gm-display-app.py loads display/stats.json at import, and that file is
        gitignored runtime state: whichever display test posted a roster last
        leaves one behind. The character sidebar only takes its 210px column
        when it has a card in it, so a layout measurement silently changes with
        the order the files ran in. That is the "green when you run the test,
        red when you run the suite" shape, and it is the same class of bug
        test_display_retest_findings.py documents at length, so the matrix calls
        this rather than trusting the file it inherited.
        """
        if self.server_kind == "static":
            return
        self.server.app.test_client().post("/clear")

    # ── the page ───────────────────────────────────────────────────────────

    def open_page(self, size=None, path=None, wait=500, wait_until="load", **ctx):
        """A page in a context of its own, closed when the test ends.

        `size` is (width, height) or None for the context default. `wait` is the
        settle time this test has always used before it reads anything, passed
        through rather than defaulted: these files are the ones where "not
        changing yet" and "finished" are the same observation.
        """
        context = self.browser.new_context(**(dict(viewport=vp(size)) if size else {}), **ctx)
        self.addCleanup(context.close)
        page = context.new_page()
        page.goto(self.url(path), wait_until=wait_until)
        if self.ready_js:
            page.wait_for_function(self.ready_js, timeout=self.ready_timeout)
        page.wait_for_timeout(wait)
        return page


def vp(size):
    """(width, height) -> the dict playwright wants."""
    return {"width": size[0], "height": size[1]}


# ── the viewport matrix ───────────────────────────────────────────────────────

# The three widths the audit names for W15 (2026-09-30, section 8). They are not
# arbitrary picks: 768 is where the story column used to collapse to an 84px
# ribbon, 1100 is the width the display.css breakpoint sits on, and 375 is the
# phone the party-input panel was built for.
VIEWPORT_MATRIX = (
    ("phone", 375, 667),
    ("tablet", 768, 1024),
    ("small-desktop", 1100, 700),
)

#: A reading column narrower than this is a ribbon. The audit's floor, and the
#: width a 12-character line of body text stops being prose at.
MIN_READING_COLUMN = 320

#: One measurement of the whole layout, as one round trip.
#:
#: Overlap is computed only between PAINTED boxes. An element at opacity 0 still
#: has a rectangle and still answers getBoundingClientRect, so a naive overlap
#: count reports the empty character sidebar covering the party input panel at
#: every width. `painted` is the difference between "these two boxes intersect"
#: and "the reader can see two things on top of each other", and the second is
#: the one worth failing a test over.
#:
#: The pill is forced visible before it is measured. It only appears once the
#: reader has scrolled up and new narration arrives, so off-screen it is at
#: opacity 0 and translated down 8px; the box you would measure then is not the
#: box a reader ever sees.
LAYOUT_PROBE = """() => {
  const R = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return {l: Math.round(b.left), r: Math.round(b.right), t: Math.round(b.top),
            b: Math.round(b.bottom), w: Math.round(b.width), h: Math.round(b.height)}; };
  const painted = el => { if (!el) return false; const cs = getComputedStyle(el);
    const b = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden'
        && parseFloat(cs.opacity) > 0.01 && b.width > 0 && b.height > 0; };
  const area = (a, c) => { if (!a || !c) return 0;
    const w = Math.max(0, Math.min(a.r, c.r) - Math.max(a.l, c.l));
    const h = Math.max(0, Math.min(a.b, c.b) - Math.max(a.t, c.t));
    return w * h; };
  const pill = document.getElementById('new-content-pill');
  if (pill) pill.classList.add('visible');
  const by = id => document.getElementById(id);
  const rails = [['sidebar', by('sidebar')], ['audio-controls', by('audio-controls')]]
    .filter(([, el]) => painted(el)).map(([name]) => name);
  const rail = Object.fromEntries(rails.map(n => [n, R(by(n))]));
  const input = by('input-panel'), pillEl = by('new-content-pill');
  const overlap = {};
  for (const n of rails) {
    overlap[n] = {input: area(rail[n], painted(input) ? R(input) : null),
                   pill: area(rail[n], painted(pillEl) ? R(pillEl) : null)};
  }
  return {vw: innerWidth, vh: innerHeight,
          scrollW: document.documentElement.scrollWidth,
          overflowX: document.documentElement.scrollWidth - innerWidth,
          column: R(by('text-content')), rails, rail, overlap,
          input: R(input), inputPainted: painted(input),
          pill: R(pillEl), pillPainted: painted(pillEl)};
}"""


def assert_viewport_matrix(test, measured, where=""):
    """The three must-haves, for one viewport.

    1. no horizontal overflow. A window that scrolls sideways on a display
       meant to sit on a table is broken at any width.
    2. a reading column of at least MIN_READING_COLUMN. This is the assertion
       that would have caught the 84px ribbon at 768.
    3. no rail painted over the party input panel or the new-content pill. Both
       are fixed to a corner the reader cannot scroll away from, so a rail that
       crosses either one is over something permanently.

    `test` is the TestCase, so this reads as three assertions in the file that
    calls it rather than as a helper that raises.
    """
    tag = f" ({where})" if where else ""
    test.assertLessEqual(measured["overflowX"], 0,
                         f"the window scrolls sideways{tag}: {measured}")
    test.assertGreaterEqual(measured["column"]["w"], MIN_READING_COLUMN,
                            f"the reading column is {measured['column']['w']}px of "
                            f"{measured['vw']}px{tag}: {measured}")
    for name, boxes in measured["overlap"].items():
        test.assertEqual(boxes["input"], 0,
                         f"the {name} rail paints over the party input panel{tag}: {measured}")
        test.assertEqual(boxes["pill"], 0,
                         f"the {name} rail paints over the new-content pill{tag}: {measured}")