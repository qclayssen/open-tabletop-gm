"""Connection status, error surfacing and replay dedupe (W5, finding N-7).

N-7, as the reader reported it: the pill worked, and the UI stayed live while
disconnected. Stopping the server left the map buttons enabled and stale, and a
click during the outage did nothing at all.

WHAT THESE TESTS ARE AIMING AT
=============================
The interesting one is the outage test, because it is the only way to be honest
about the defect. Two weaker shapes would both pass on the broken code:

  - `context.set_offline(True)` does not disconnect an already-open EventSource
    in Chromium, so a page behind it never leaves `connected` and the test
    proves nothing. Verified on this Chromium before it was written.
  - `DisplayServer.stop()` calls werkzeug's `shutdown()`, which stops the accept
    loop but leaves the thread serving an open SSE response alive. The stream
    keeps delivering and the page never notices. Verified too.

So the outage here is a real subprocess, SIGKILLed. That is what the reader did,
and it is the only version of "the server went away" that the socket notices.

WHAT IS DELIBERATELY NOT HERE
============================
A test that asserts on narration text surviving on screen. It cannot be written
today: narration pushed to the display is deleted from the DOM about 3.6s after
it arrives, on clean `origin/main` as well (see KNOWN PRE-EXISTING BUG below).
The replay dedupe test therefore counts rendered blocks, which the bug does not
touch, and asserts the count does not double across a reconnect.
"""
import contextlib
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request

from tests._browser import BrowserTestCase, BrowserUnavailable, shared_browser

ROOT = pathlib.Path(__file__).resolve().parent.parent
APP = ROOT / "display" / "gm-display-app.py"

# How long to wait for a display to accept a connection. Local it is under a
# second; the budget exists for a loaded runner, not for a healthy start.
START_TIMEOUT = 30


# ── a real display process, so it can actually be killed ────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _connect(host: str, port: int, timeout: float = 0.4):
    """True if something accepts a connection there, else the OSError saying why.

    The refusal reason is kept rather than flattened to False. "Connection
    refused" and "timed out" are different diagnoses: refused means nothing is
    listening on that address, timed out means something is and it is not
    answering.
    """
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family) as s:
        s.settimeout(timeout)
        try:
            s.connect((host, port))
            return True
        except OSError as exc:
            return exc


def _port_is_open(port: int, timeout: float = 0.4) -> bool:
    return _connect("127.0.0.1", port, timeout) is True


@contextlib.contextmanager
def _no_core_dumps():
    """RLIMIT_CORE 0 across the Popen, so a SIGABRT leaves nothing behind.

    POSIX only, hence the guard. The soft limit is what a core dump is charged
    against and the child inherits it, so this costs nothing when no dump is
    wanted and prevents a several-hundred-megabyte file appearing in the repo
    when one is.
    """
    try:
        import resource
    except ImportError:
        yield
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_CORE)
        resource.setrlimit(resource.RLIMIT_CORE, (0, hard))
    except (ValueError, OSError):
        yield
        return
    try:
        yield
    finally:
        with contextlib.suppress(ValueError, OSError):
            resource.setrlimit(resource.RLIMIT_CORE, (soft, hard))


class DisplayProcess:
    """gm-display-app.py as its own process, on a port nothing else is using.

    A subprocess rather than tests/_browser.py's DisplayServer for one reason:
    the defect under test needs the server to stop existing. Werkzeug's
    shutdown() leaves the SSE response thread serving, so the page carries on
    receiving and nothing is being tested. SIGKILL is what a reader does.

    This is the only file in the suite that starts the real app this way, which
    is why it keeps everything the process says. Sent to DEVNULL, a display that
    dies at import, or hangs before it binds, leaves no evidence at all and the
    test can only report that a port stayed shut. That is a symptom, and a
    symptom is what makes a red run impossible to act on and a green local run
    look like the last word.
    """

    def __init__(self, state_dir=None) -> None:
        self.port = _free_port()
        self.proc = None
        self.state_dir = pathlib.Path(state_dir) if state_dir else None
        self._log = None

    def _env(self) -> dict:
        env = dict(os.environ, GM_DISPLAY_PORT=str(self.port), TACTICS_NO_DISPLAY="1")
        if self.state_dir:
            # Keep this display's runtime state out of the repo.
            #
            # Both files are gitignored, and both are loaded at startup and
            # replayed into every display that connects afterwards. A test that
            # writes either one does not just leave litter: it changes what the
            # next display test measures. stats.json in particular gives the
            # sidebar data, and a populated sidebar moves the reading column's
            # left inset, so a stray roster makes an unrelated layout test fail.
            env["GM_TEXT_LOG_FILE"] = str(self.state_dir / "text_log.json")
            env["GM_STATS_FILE"] = str(self.state_dir / "stats.json")
        # For _await_port's SIGABRT. Without it an abort kills the child silently
        # and the one case that most needs explaining, a display that is alive
        # and not listening, is the one case that explains nothing.
        env["PYTHONFAULTHANDLER"] = "1"
        return env

    def _transcript(self, limit: int = 6000) -> str:
        """Everything the display has written so far, oldest first."""
        if self._log is None:
            return "(there was no process to read output from)"
        try:
            self._log.flush()
            self._log.seek(0)
            data = self._log.read()
        except (OSError, ValueError):
            return "(the display's output could not be read back)"
        text = data.decode("utf-8", "replace").strip()
        if not text:
            return "(the display wrote nothing to stdout or stderr)"
        if len(text) > limit:
            text = f"[{len(data) - limit} earlier bytes elided]\n{text[-limit:]}"
        return text

    def _stack_dump(self) -> str:
        """Abort a live-but-silent display and keep the traceback it prints.

        PYTHONFAULTHANDLER turns SIGABRT into Python's own all-threads dump, so
        the report names the call the process is stuck in instead of leaving
        that to be guessed at from the outside. The process is already a failed
        test at this point, so aborting it costs nothing that matters.
        """
        if self.proc is None or self.proc.poll() is not None:
            return ""
        try:
            self.proc.send_signal(signal.SIGABRT)
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            return "\n\n(it did not respond to SIGABRT either)"
        except OSError as exc:
            return f"\n\n(SIGABRT could not be delivered: {exc})"
        return ""

    def _spawn(self) -> None:
        # One unlinked temporary file for both streams, so the banner and any
        # traceback interleave in the order the process wrote them. Unlinked
        # means a child that has to be killed cannot leave a log behind.
        self._log = tempfile.TemporaryFile()
        with _no_core_dumps():
            self.proc = subprocess.Popen(
                [sys.executable, str(APP)], cwd=str(ROOT), env=self._env(),
                stdout=self._log, stderr=subprocess.STDOUT)

    def _await_port(self) -> "DisplayProcess":
        started = time.time()
        while time.time() - started < START_TIMEOUT:
            if _port_is_open(self.port):
                return self
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"the display on port {self.port} exited with code "
                    f"{self.proc.returncode} after {time.time() - started:.1f}s, "
                    f"without ever accepting a connection.\n"
                    f"started: {sys.executable} {APP}\n"
                    f"cwd: {ROOT}\n"
                    f"GM_DISPLAY_PORT={self.port}\n\n"
                    f"--- everything it wrote ---\n{self._transcript()}")
            time.sleep(0.2)
        # Alive, and still not listening. That is a different failure from a
        # crash and it is the one that has to be told apart, so ask the process
        # itself where it is before saying anything about the port. Everything
        # is read before the abort, because the abort ends the process.
        v4 = _connect("127.0.0.1", self.port)
        v6 = _connect("::1", self.port)
        was_alive = self.proc.poll() is None
        stacks = self._stack_dump()
        raise RuntimeError(
            f"the display on port {self.port} was {'still running' if was_alive else 'not running'} "
            f"after {START_TIMEOUT}s and had accepted no connection on 127.0.0.1.\n"
            f"pid {self.proc.pid}\n"
            f"127.0.0.1: {v4 if v4 is not True else 'accepted'}\n"
            f"[::1]:     {v6 if v6 is not True else 'accepted'}\n"
            f"started: {sys.executable} {APP}\n"
            f"cwd: {ROOT}\n\n"
            f"--- everything it wrote, and where it was stuck ---\n"
            f"{self._transcript()}{stacks}")

    def start(self) -> "DisplayProcess":
        self._spawn()
        return self._await_port()

    def url(self, path: str = "/") -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            self.proc.wait(timeout=10)

    def restart(self) -> "DisplayProcess":
        """A new process on the SAME port, which is what a server restart is.

        Reusing the port is the point: a display reconnecting after its server
        restarts reconnects to the same address, and the new process has a new
        epoch and a seq counter back at 1. Starting on a fresh port would not
        exercise the epoch half of the replay dedupe at all.
        """
        self.kill()
        self.proc = None
        self._log = None
        self._spawn()
        return self._await_port()

    def push(self, text: str, **extra) -> None:
        body = json.dumps({"text": text, **extra}).encode("utf-8")
        req = urllib.request.Request(self.url("/chunk"), data=body,
                                     headers={"Content-Type": "application/json"},
                                     method="POST")
        urllib.request.urlopen(req, timeout=10).read()


# A fight with a player turn, so the action bar is the one a player uses.
SNAPSHOT = {
    "status": "active", "round": 1, "current": "kairos", "unseen_turn": False,
    "meta": {"name": "Frog Pond"},
    "grid": {"name": "Frog Pond", "rows": ["......", "......", "......"]},
    "order": ["kairos"],
    "turn": {"movement_left": 30, "action_used": False},
    "tokens": [{"id": "kairos", "name": "Kairos", "side": "pc", "x": 0, "y": 0,
                "hp": 10, "max_hp": 10, "ac": 15, "dead": False, "hidden": False,
                "controller": "player", "conditions": [], "effects": []}],
    "log": [],
}

# The three actions N-7 names, plus one that is always there.
ACTION_BUTTONS = ("Move", "Attack", "Dash", "End turn")


def action_bar(page) -> dict:
    return page.evaluate("""() => {
      const bar = document.getElementById('tx-actions');
      if (!bar) return {present: false};
      const btns = [...bar.querySelectorAll('button')];
      return {present: true, total: btns.length,
              enabled: btns.filter(b => !b.disabled).map(b => b.textContent.trim()),
              titles: Object.fromEntries(btns.map(b => [b.textContent.trim(), b.title]))};
    }""")


def pill(page) -> tuple:
    return (page.get_attribute("#conn-status", "data-state"),
            page.inner_text("#conn-status"))


def narration_blocks(page) -> int:
    return page.evaluate("document.querySelectorAll('#text-content .dm-block').length")


# Narration replayed on connect comes from display/text_log.json, which is
# gitignored runtime state that every display test writes to. Tests here give
# their own display a temp log (GM_TEXT_LOG_FILE) rather than that one, because
# a display test that connects afterwards replays whatever is in the shared file
# and then asserts against `.dm-block`, which is simply the first block on its
# page: a leftover from here silently becomes another file's fixture.


class ConnectionTestCase(unittest.TestCase):
    """Shared setup: one chromium, a display process and a page per test."""

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.browser = shared_browser()
        except BrowserUnavailable as exc:
            raise unittest.SkipTest(str(exc)) from exc

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # This display's runtime state is a temp directory, so nothing written by
        # a test here reaches display/text_log.json or display/stats.json and can
        # become another display test's fixture.
        self.state_dir = pathlib.Path(self.tmp.name)
        self.log_file = self.state_dir / "text_log.json"
        self.server = DisplayProcess(state_dir=self.state_dir).start()
        self.addCleanup(self.server.kill)
        self.context = self.browser.new_context(viewport={"width": 1280, "height": 900})
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        # Every POST the page tries to make to the engine. This is the evidence
        # that a click during an outage did not merely look like it did nothing.
        self.combat_posts = []
        self.page.on("request", lambda r: self.combat_posts.append(r.url)
                     if "/combat/do" in r.url else None)
        self.page.on("pageerror", lambda e: self.fail(f"uncaught page error: {e}"))

    def open_with_fight(self, wait: int = 1200) -> None:
        self.page.goto(self.server.url("/"), wait_until="load")
        self.page.wait_for_timeout(900)
        self.page.evaluate("(s) => { window.Tactics.update(s); }", SNAPSHOT)
        self.page.wait_for_timeout(wait)

    def wait_for_pill(self, state: str, timeout: int = 25) -> float:
        """Seconds until the pill reads `state`. Fails the test if it never does."""
        deadline = time.time() + timeout
        start = time.time()
        while time.time() < deadline:
            if self.page.get_attribute("#conn-status", "data-state") == state:
                return time.time() - start
            self.page.wait_for_timeout(200)
        self.fail(f"the pill never reached {state!r}; it read {pill(self.page)!r}")

    def kill_server(self) -> None:
        self.server.kill()

    def wait_for_typewriter(self, timeout: int = 30) -> None:
        """Wait until no narration is still being typed out.

        Not about looks. The typewriter is still draining when a reconnect's
        replay calls flushNewBlock(), and the characters left in its queue then
        open a fresh block, which is indistinguishable from the replay having
        redrawn the story. Measuring dedupe needs a settled page, or the
        measurement is of the typewriter's timing instead.
        """
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.page.evaluate("() => typeof charQueue !== 'undefined' "
                                  "&& charQueue.length === 0 && !isTyping"):
                return
            self.page.wait_for_timeout(200)
        self.fail("the typewriter never went idle")


# ── N-7: the map must not be live while the display is not ───────────────────

class ActionsDuringOutage(ConnectionTestCase):

    def test_n7_map_actions_are_disabled_while_disconnected(self):
        """N-7: "the map buttons remain enabled and stale during the outage".

        This is the defect. Before the fix every button stayed enabled for the
        whole outage, so the map kept looking playable while nothing behind it
        existed.
        """
        self.open_with_fight()
        self.assertIn("connected", pill(self.page)[0])
        self.assertIn("Move", action_bar(self.page)["enabled"],
                      "the action bar should be live while connected")

        self.kill_server()
        self.wait_for_pill("reconnecting")

        bar = action_bar(self.page)
        self.assertEqual(bar["enabled"], [],
                         "every map action must be disabled while disconnected, "
                         f"but these were still live: {bar['enabled']}")
        self.assertGreater(bar["total"], 0,
                           "the buttons should still be present and disabled, not removed")

    def test_n7_disabled_actions_are_genuinely_disabled_not_just_styled(self):
        """Accessibility, and the difference between a fix and a paint job.

        A button that is only greyed out is still focusable and still fires its
        handler. The test asserts the `disabled` property, that the control is
        out of the tab order, and that the reason is available as text rather
        than only as a colour.
        """
        self.open_with_fight()
        self.kill_server()
        self.wait_for_pill("reconnecting")

        self.assertEqual(self.page.evaluate(
            "document.querySelectorAll('#tx-actions button:not([disabled])').length"), 0,
            "no action button may stay enabled while disconnected")
        self.assertEqual(self.page.evaluate(
            "[...document.querySelectorAll('#tx-actions button')]"
            ".filter(b => b.getAttribute('aria-disabled') === 'false').length"), 0,
            "aria-disabled must not claim the control is available")

        titles = action_bar(self.page)["titles"]
        self.assertTrue(titles, "the action bar should still have its buttons")
        for name, title in titles.items():
            self.assertIn("lost the server", title,
                          f"the {name!r} button must say why it is unavailable, "
                          f"not only look different; title was {title!r}")

        # The state has to reach a reader who cannot see the pill's colour.
        # #tx-banner is role="alert" when it carries a refusal.
        banner = self.page.inner_text("#tx-banner")
        self.assertIn("lost the server", banner)

    def test_n7_click_during_outage_never_reaches_the_server(self):
        """N-7: "a player can click Move or Attack during an outage and get nothing".

        Asserting on the absence of an error message would pass on code that
        fired the request and swallowed the failure. This asserts the request
        never leaves the browser, which is the property that actually matters:
        nothing reaches an engine that may have moved on.
        """
        self.open_with_fight()
        self.kill_server()
        self.wait_for_pill("reconnecting")

        self.combat_posts.clear()
        # Force the click past the disabled attribute the way a stale event or a
        # keyboard activation on a still-focused node would.
        self.page.evaluate("""() => {
          const b = [...document.querySelectorAll('#tx-actions button')]
                      .find(x => x.textContent.trim() === 'Move');
          if (b) b.click();
        }""")
        self.page.wait_for_timeout(2000)
        self.assertEqual(self.combat_posts, [],
                         "a click during an outage must not reach the engine")

    def test_n7_armed_move_is_cancelled_by_an_outage(self):
        """The click that fires later, against state that has moved on.

        Arming Move holds the board in a "pick a square" mode over a reach map
        the engine drew. If the stream drops while it is armed, that map is
        minutes old and the creatures in it have since moved, so finishing the
        move would send a move from a position nobody is at. The mode has to be
        dropped when the connection drops.

        The mode is armed by clicking Move with the one engine call it makes
        stubbed, because this test's fight is injected client-side and there is
        no real encounter for /combat/do to read. What is under test is what the
        disconnect does to an armed panel, not how a mode gets armed.

        `aria-pressed` on the Move button is the signal. It is the panel's own
        public statement of whether the mode is armed, and unlike the internal
        `ui` object it is exactly what a screen reader would be told.
        """
        self.open_with_fight()

        def move_pressed() -> str:
            return self.page.evaluate("""() => {
              const b = [...document.querySelectorAll('#tx-actions button')]
                          .find(x => x.textContent.trim() === 'Move');
              return b ? b.getAttribute('aria-pressed') : 'gone';
            }""")

        self.assertNotEqual(move_pressed(), "true",
                            "no mode should be armed to begin with")

        self.page.evaluate("""() => {
          // toggleMove asks the engine for reach before it arms. Answering that
          // one call here lets the button arm exactly as it does in play.
          const origFetch = window.fetch;
          window.fetch = async (url, opts) => {
            const body = JSON.parse(opts.body);
            if (body.cmd === 'reachable') {
              return { ok: true, json: async () => ({ ok: true, result: { walk: {} } }) };
            }
            return origFetch(url, opts);
          };
          [...document.querySelectorAll('#tx-actions button')]
            .find(b => b.textContent.trim() === 'Move').click();
        }""")
        for _ in range(25):
            self.page.wait_for_timeout(200)
            if move_pressed() == "true":
                break
        self.assertEqual(move_pressed(), "true",
                         "Move should be armed while connected, or this test "
                         "is not exercising the thing it claims to")

        self.kill_server()
        self.wait_for_pill("reconnecting")
        self.assertNotEqual(move_pressed(), "true",
                            "an armed mode must be dropped when the stream drops, "
                            "or its stale reach map is still on the board")

    def test_actions_reenabled_and_live_after_reconnect(self):
        """Re-enabling is half the fix; leaving them dead forever is the other half."""
        self.open_with_fight()
        self.kill_server()
        self.wait_for_pill("reconnecting")
        self.assertEqual(action_bar(self.page)["enabled"], [])

        self.server.restart()                          # same port, new process
        self.wait_for_pill("connected", timeout=30)
        self.page.wait_for_timeout(800)

        bar = action_bar(self.page)
        for name in ACTION_BUTTONS:
            self.assertIn(name, bar["enabled"],
                          f"{name} should be usable again once the display reconnects")


# ── the pill itself ─────────────────────────────────────────────────────────

class PillStates(ConnectionTestCase):

    def test_pill_reports_connected_reconnecting_and_offline(self):
        """Three states, three different sentences.

        `offline` is not a synonym for `reconnecting`: the first means the server
        may well be fine and the network is not, the second means we are retrying
        a server that has gone. A player deciding whether to go find the GM
        needs the difference.
        """
        self.page.goto(self.server.url("/"), wait_until="load")
        self.page.wait_for_timeout(900)

        state, text = pill(self.page)
        self.assertEqual(state, "connected")
        self.assertIn("Connected", text)

        self.page.evaluate("_setConnStatus('reconnecting', 1)")
        self.assertEqual(pill(self.page)[0], "reconnecting")
        self.assertIn("Reconnecting", pill(self.page)[1])

        self.page.evaluate("_setConnStatus('reconnecting', 3)")
        self.assertIn("attempt 3", pill(self.page)[1])

        self.page.evaluate("_setConnStatus('offline', 2)")
        state, text = pill(self.page)
        self.assertEqual(state, "offline")
        self.assertIn("Offline", text)
        self.assertNotIn("Reconnecting", text,
                         "offline is its own sentence, not a louder reconnecting")

    def test_pill_is_a_live_region_and_never_empty(self):
        """An empty live region announces nothing.

        Pinned per state rather than once, because the states are now written by
        three different branches: a new state that fell through to an empty
        string would be a live region that says nothing at exactly the moment
        something has gone wrong.
        """
        self.page.goto(self.server.url("/"), wait_until="load")
        self.page.wait_for_timeout(600)
        self.assertEqual(self.page.get_attribute("#conn-status", "role"), "status")
        self.assertEqual(self.page.get_attribute("#conn-status", "aria-live"), "polite")
        for s in ("connected", "reconnecting", "offline"):
            self.page.evaluate("s => _setConnStatus(s, 1)", s)
            self.assertTrue(self.page.inner_text("#conn-status").strip(),
                            f"the {s} pill must carry text")

    def test_only_one_stream_is_open_at_a_time(self):
        """A second live stream handles every payload twice.

        The backoff timer and the browser's `online` event both want to
        reconnect, on their own schedules. If the network comes back while a
        retry is still pending, both fire and the page ends up with two
        EventSources. Nothing looks wrong: the pill reads connected and the
        story arrives, just doubled, and the server log shows no reason.

        The order is the one that races: the stream fails and arms a retry, the
        OS reports the network down (which is what puts us in `offline`), and
        then the OS reports it back. Without the last step the `online` handler
        has nothing to do, because a stream that failed while the network was up
        is `reconnecting`, not `offline`.
        """
        self.page.goto(self.server.url("/"), wait_until="load")
        self.page.wait_for_timeout(900)

        self.page.evaluate("""() => {
          window.__streams = 0;
          const OrigES = window.EventSource;
          window.EventSource = function(...a) { window.__streams++; return new OrigES(...a); };
          window.EventSource.prototype = OrigES.prototype;
        }""")
        # A failure arms a retry. Then the network drops, which is what makes
        # the state `offline` and so gives the `online` handler something to do.
        self.page.evaluate("""() => { evtSource.close(); evtSource.onerror(); }""")
        self.page.evaluate("window.dispatchEvent(new Event('offline'))")
        self.assertEqual(self.page.get_attribute("#conn-status", "data-state"), "offline")

        # Now the network comes back. A retry may still be queued from the
        # failure above; whichever path connects first, the other must not.
        self.page.evaluate("window.dispatchEvent(new Event('online'))")
        self.page.wait_for_timeout(2500)

        self.assertEqual(self.page.evaluate("window.__streams"), 1,
                         "coming back online must open one stream, not one for "
                         "the event and another for a pending backoff")
        self.assertIsNone(self.page.evaluate("_reconnectTimer"),
                          "the pending retry should have been cancelled, not left "
                          "to open a second stream")

    def test_pill_opacity_is_not_mid_transition(self):
        """N-7: "pill opacity 0 in computed style although visible in the screenshot".

        That reading was the 0.4s `transition: opacity` caught mid-animation:
        computed style was reporting an intermediate value for a pill that was
        plainly on screen. The transition is gone, so the computed value is the
        value. A test that read opacity mid-fade is measuring the animation.
        """
        self.page.goto(self.server.url("/"), wait_until="load")
        self.page.wait_for_timeout(600)
        self.page.evaluate("_setConnStatus('reconnecting', 1)")
        # Read immediately: with a 0.4s opacity transition this would be part of
        # the way to 0, which is exactly the reading N-7 reported.
        self.assertEqual(self.page.evaluate(
            "getComputedStyle(document.getElementById('conn-status')).opacity"), "1")
        self.assertNotIn("opacity", self.page.evaluate(
            "getComputedStyle(document.getElementById('conn-status')).transitionProperty"),
            "the pill must not animate its opacity; a status that is also a "
            "measurement should not be mid-fade when it is read")


# ── errors the display could not render ──────────────────────────────────────

class StreamErrors(ConnectionTestCase):

    def test_a_bad_payload_is_surfaced_not_only_logged(self):
        """One unreadable payload must not be invisible.

        The dispatcher already had a per-branch try/catch so one bad payload
        could not kill the stream, but it only wrote to the console. A branch
        that threw on every payload looked exactly like a display that had gone
        quiet, and the only evidence was in devtools the reader does not have.
        """
        self.page.goto(self.server.url("/"), wait_until="load")
        self.page.wait_for_timeout(800)

        self.assertTrue(self.page.is_hidden("#stream-toast"),
                        "the error toast must start hidden")

        self.page.evaluate("_streamError('A message from the server could not be read.')")
        self.page.wait_for_timeout(300)
        self.assertTrue(self.page.is_visible("#stream-toast"))
        self.assertIn("could not be read", self.page.inner_text("#stream-toast"))
        self.assertEqual(self.page.get_attribute("#stream-toast", "role"), "alert",
                         "an error the reader must not miss interrupts")

    def test_a_throwing_dispatcher_branch_surfaces_an_error(self):
        """One bad branch must not be invisible, and must not kill the stream.

        Driven through the real dispatcher rather than by calling the toast
        directly. `/stats` broadcasts a `stats` payload whose handler is
        `updateStats`; that is replaced with one that throws, exactly as a
        renderer tripping over one malformed name would. Two things are then
        checked, because either alone would pass on the old code:

          - the reader is told, which the old code did not do at all
          - the stream is still alive afterwards, proven by a later payload
            landing, which is what the per-branch try/catch was for
        """
        self.page.goto(self.server.url("/"), wait_until="load")
        self.page.wait_for_timeout(800)

        self.page.evaluate("""() => {
          window.__after = false;
          window.updateStats = () => { throw new Error('boom from a handler'); };
        }""")

        # POST /stats broadcasts a stats payload; the dispatcher hands it to
        # the throwing updateStats above.
        req = urllib.request.Request(
            self.server.url("/stats"),
            data=json.dumps({"players": [{"name": "Kairos", "hp": {"current": 9, "max": 9}}],
                             "turn_order": {}}).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=10).read()
        self.page.wait_for_timeout(1000)

        self.assertTrue(self.page.is_visible("#stream-toast"),
                        "a handler that throws must be reported to the reader")
        self.assertIn("could not be shown", self.page.inner_text("#stream-toast"))

        # The stream survived: a fresh narration still arrives and renders.
        self.page.evaluate("""() => {
          window.updateStats = window.__realUpdateStats || window.updateStats;
        }""")
        self.server.push("The stream is still alive.")
        self.page.wait_for_timeout(1200)
        self.assertTrue(self.page.evaluate(
            "document.getElementById('text-content').textContent.includes('still alive')"),
            "one throwing branch must not take the rest of the stream with it")

    def test_repeated_identical_errors_do_not_flood_the_screen(self):
        """A bad payload can arrive many times a second.

        A toast that re-arms on every one of them never goes away and cannot be
        read, which is the same failure as the silent one.
        """
        self.page.goto(self.server.url("/"), wait_until="load")
        self.page.wait_for_timeout(700)
        self.page.evaluate("""() => {
          window.__errCount = 0;
          const el = document.getElementById('stream-toast');
          new MutationObserver(() => { if (!el.hidden) window.__errCount++; })
            .observe(el, {attributes: true, attributeFilter: ['hidden']});
        }""")
        for _ in range(30):
            self.page.evaluate("_streamError('Part of an update could not be shown.')")
        self.page.wait_for_timeout(400)
        self.assertLessEqual(self.page.evaluate("window.__errCount"), 2,
                             "the same error repeated must not re-announce 30 times")


# ── replay must not redraw the story ─────────────────────────────────────────

class ReplayDedupe(ConnectionTestCase):
    """The W4 seq, used where the narration actually arrives.

    W4 gave every broadcast a seq and the client a per-payload dedupe. The
    replay path was not covered by it: a reconnect sends the recent log back as
    a `replay_batch`, and those entries carried no seq, so the guard could not
    see them and the whole log was redrawn on top of the story already on
    screen.
    """

    def test_narration_is_not_duplicated_after_reconnect(self):
        """The reconnect must not redraw the story already on screen.

        Counted in blocks, not in words, and that is deliberate. Narration text
        is removed from the DOM about 3.6s after it arrives (pre-existing, on
        main as well, and out of scope here), so by the time the reconnect has
        happened there is no text left to count. The block survives that, which
        makes it the one stable thing to measure.

        The log file is cleared for the class, so `before` and `after` are
        counting the same story rather than one that grew from a previous run.
        """
        self.open_with_fight()
        for i in range(3):
            self.server.push(f"A line {i} of this session.")
        self.wait_for_typewriter()
        self.page.wait_for_timeout(800)

        before = narration_blocks(self.page)
        self.assertGreater(before, 0, "the narration should be on screen first")

        self.kill_server()
        self.wait_for_pill("reconnecting")
        self.server.restart()
        self.wait_for_pill("connected", timeout=30)
        self.wait_for_typewriter()
        self.page.wait_for_timeout(800)

        after = narration_blocks(self.page)
        self.assertEqual(after, before,
                         f"the replay redrew the story: {before} blocks before the "
                         f"outage, {after} after reconnecting")

    def test_replayed_entries_carry_a_seq_and_epoch(self):
        """The identity the client dedupes on has to be there.

        Both halves, not just seq: the counter restarts at 1 on every server run,
        so seq 3 of a restarted server is not seq 3 of the one before it. A
        display that stayed up across the restart would drop the new entry as
        already drawn if the epoch were not part of the key.
        """
        self.server.push("Something worth remembering.")
        self.page.wait_for_timeout(800)

        log = self.log_file
        self.assertTrue(log.exists(), f"the pushed narration should be in {log}")
        entries = json.loads(log.read_text(encoding="utf-8"))
        self.assertTrue(entries, "the log should not be empty")
        stamped = [e for e in entries if "seq" in e]
        self.assertTrue(stamped, f"no log entry carried a seq: {entries}")
        self.assertIn("_epoch", stamped[-1],
                      "a log entry needs the server epoch as well as the seq")

    def test_broadcast_returns_the_seq_it_assigned(self):
        """_broadcast handing back its seq is what lets a caller stamp the log."""
        import importlib.util
        spec = importlib.util.spec_from_file_location("w5_bcast_probe", str(APP))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        before = mod._seq
        assigned = mod._broadcast({"text": "seq please"})
        self.assertEqual(assigned, before + 1)
        self.assertEqual(mod._seq, assigned)


class PanelWithoutAStream(BrowserTestCase):
    """The panel runs with no stream at all, and must still be usable.

    display/evidence-panel.html loads tactics.js on its own, with no SSE client
    and no display.js to ask. A panel that treated "no answer about the
    connection" as "disconnected" would draw that harness with every action
    dead, which is what happened when the flag defaulted to false.
    """

    server_kind = "static"
    harness_path = "/evidence-panel.html"
    ready_js = "window.__ready === true"

    def test_actions_are_live_without_a_connection_manager(self):
        page = self.open_page(size=(1440, 900), wait=400)
        self.assertEqual(page.evaluate("typeof window.GMConn"), "undefined",
                         "this harness loads tactics.js with no display.js, so "
                         "there is nothing that could report a connection")

        page.evaluate("(s) => { Tactics.update(s); }", SNAPSHOT)
        page.wait_for_timeout(500)
        bar = action_bar(page)
        self.assertGreater(bar["total"], 0, "the action bar should be built")
        self.assertIn("Move", bar["enabled"],
                      "a panel with no stream cannot be disabled by one")


if __name__ == "__main__":
    unittest.main()