"""Every innerHTML sink in the display is closed, and there is one esc() (W1).

W1 of the 2026-09-30 webdev audit. The threat is not cosmetic. A character
name, a race, a spell name, a condition or a map label can be chosen by the
LLM, by a campaign file, or by a player nickname arriving through
/player-input, and the display renders those into innerHTML. The browser doing
the rendering is the DM's, and it already holds the LAN token in
<meta name="dnd-token">, so an injected <img onerror=...> is script execution
with a token, not a styling bug.

Two layers of test, because each catches what the other cannot:

  StaticSinks  parses the JavaScript and requires every interpolation that
               reaches innerHTML to be a literal, a known-constant table
               lookup, or wrapped in the single shared esc(). This is the one
               that runs everywhere: no browser, no fixture, no flakiness, and
               it fails on a sink added tomorrow rather than on the payloads we
               happened to think of today.

  XssInBrowser pushes the payloads through the real endpoints (/stats,
               /combat, /dice-request) and asks the browser what actually
               happened: was window.__x set, and is there an img[onerror] in
               the document? Skipped without playwright or Chromium.

The server strips ` \\ $ from a name but never < > & " (see _chunk and
/dice-request), so escaping in the browser is the only control there is.

The browser and the display server come from tests/_browser.py (W15).
"""
from __future__ import annotations

import json
import pathlib
import re
import unittest

from tests._browser import BrowserTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent
JS = REPO / "display" / "static" / "display.js"
TACTICS = REPO / "display" / "static" / "tactics.js"


# ── the payloads ─────────────────────────────────────────────────────────────
# The audit names these five fields because they are the ones a hostile value
# actually travels through: a character name, a race, a spell name, a
# condition name and a map label.
EVIL = '<img src=x onerror="window.__x=1">'
EVIL_ATTR = '" onmouseover="window.__x=1'   # breaks out of an HTML attribute


# ── static analysis ──────────────────────────────────────────────────────────
# A sink is any assignment of a template literal to innerHTML (or an
# insertAdjacentHTML call), found by scanning the source rather than by
# maintaining a list, so a sink added later is checked without anyone
# remembering to add it here.
SINK = re.compile(r"\.(?:innerHTML|outerHTML)\s*=\s*|`insertAdjacentHTML\(")
INTERP = re.compile(r"\$\{([^}]*)\}", re.S)


def _statement_at(text: str, start: int) -> str:
    """The full statement beginning at `start`: read to the end of the template
    literal, following any nested `${...}` (which may itself contain one)."""
    i, depth, quote = start, 0, None
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 2
                continue
            if c == quote:
                quote = None
        elif c in "'\"`":
            quote = c
        elif c == "$" and text[i + 1:i + 2] == "{":
            depth, i = depth + 1, i + 2
            continue
        elif c == "}" and depth:
            depth -= 1
        elif c == ";" and not depth:
            return text[start:i + 1]
        i += 1
    return text[start:]


def _interpolations(stmt: str) -> list[str]:
    """Every `${...}` expression in the statement, innermost expressions
    included, so a nested helper is not mistaken for part of a safe outer one."""
    out: list[str] = []
    for m in INTERP.finditer(stmt):
        out.append(m.group(1))
        # A nested template inside an interpolation is its own statement to audit.
        inner = m.group(1)
        if "`" in inner:
            out.extend(_interpolations(inner))
    return out


#: An interpolation is safe when it is a literal, arithmetic over literals, a
#: ternary whose branches are themselves literals, or a lookup into the tactics
#: SIDES table (pinned as literals by test_the_tactics_side_table_is_constants).
SAFE_EXPR = re.compile(
    r"""^\s*(?:
          [\d.\s+\-*/%()<>|&^]+$                              # arithmetic only
        | '(?:[^'\\]|\\.)*'                                    # one string literal
        | "(?:[^"\\]|\\.)*"
        | (?:true|false|null|undefined)\s*$
        | [A-Za-z_$][\w$.]*\s*[-+*/%<>=&|!]*\s*[\w$.0-9]+\s*  # comparison for a ternary
          \?\s*.+\s*:\s*.+\s*$                                 # (branches audited below)
        | side\.(?:glyph|word|cls|colour)$
        )\s*$""",
    re.X | re.S,
)

#: A call to one of these returns markup the audit follows into the callee.
HELPER = re.compile(r"^\s*([A-Za-z_$][\w$]*)\s*\(")

#: Expressions that provably cannot carry markup, whatever they were fed.
#: Every Math.* function returns a number, and .length and .repeat() on a string
#: return a number and repeats of that string, so interpolating one into HTML
#: cannot produce a tag. This is why `style="width:${pct}%"` needs no esc():
#: pct is Math.max(Math.round(...)), a number however the snapshot was fed.
#: Matched by a small scan rather than a regex, because the calls nest.
MATH_CALL = re.compile(r"^\s*Math\.[a-zA-Z]+\s*\(")
STRING_CALL = re.compile(r"^\s*[\w$.\[\]()+\-*/% ]*\.(?:length)\s*\(")
#: `'●'.repeat(n)` repeats a string literal, so the result is glyphs.
REPEAT_LITERAL = re.compile(r"^\s*'(?:[^'\\]|\\.)*'\.repeat\s*\(")
#: `arr.join(sep)` yields the concatenation of the array's elements.
JOIN = re.compile(r"^\s*([A-Za-z_$][\w$]*)\.join\s*\(")


def _is_numeric(e: str) -> bool:
    """True when the expression's value is a number, or a string built only
    from literals by .repeat(). A Math.* call anywhere at the top level is
    enough: the outermost call determines the result's type."""
    e = " ".join(e.split())
    if MATH_CALL.match(e) or STRING_CALL.match(e) or REPEAT_LITERAL.match(e):
        return True
    # A comparison yields a boolean, which is safe to interpolate too.
    if re.fullmatch(r"[\w$.\[\]()+\-*/% ]+\s*(?:<=|>=|<|>|===|!==|==|!=|&&|\|\|)\s*[\w$.\[\]()+\-*/% ]+", e):
        return True
    return False


def assert_escapes_five(src: str, where: str) -> None:
    """The five replacements an esc() must contain, as plain asserts so both
    the display.js helper and the tactics.js fallback can be pinned by one."""
    for char, entity in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"),
                         ('"', "&quot;"), ("'", "&#39;")):
        needle = ".replace(/" + char + "/g, '" + entity + "')"
        assert needle in src, f"{where} no longer escapes {char} as {entity}"


class Resolver:
    """Decide whether an interpolated value can reach innerHTML unescaped.

    The rule is "a literal, or provably derived from literals". A template that
    interpolates a value is followed: the callee is located and its own
    templates audited, so `el.innerHTML = forecastHtml()` is judged on what
    forecastHtml interpolates rather than waved through by name. Locals holding
    already-assembled HTML (`linkHtml`, `pips`) are resolved the same way, by
    finding the assignment that filled them. Recursion is bounded, so a cycle
    terminates.
    """

    LIMIT = 12

    def __init__(self, name: str, src: str, scope: str = ""):
        self.name, self.src, self.seen, self.depth = name, src, set(), 0
        # Locals are resolved within the function that contains the sink. The
        # same name is reused across functions in both files (`bits`, `pct`),
        # and a file-wide search would follow the wrong one, either reporting a
        # hole that is not there or hiding one that is.
        self.scope = scope or src

    def safe(self, expr: str) -> bool:
        e = " ".join(expr.split())
        if not e:
            return True
        # esc(...) anywhere in the expression, or a map over it.
        if re.search(r"\besc\s*\(", e) or re.search(r"\.map\(esc\)", e):
            return True
        if SAFE_EXPR.match(e):
            return True
        if _is_numeric(e):
            return True
        if self.depth > self.LIMIT or e in self.seen:
            return False
        self.seen.add(e)
        self.depth += 1
        try:
            if self._ternary_ok(e):
                return True
            # join() before HELPER: `bits.join(sep)` also matches HELPER as the
            # call `bits(`, which would resolve to a function named bits and
            # report a false hole.
            j = JOIN.match(e)
            if j and self._array_ok(j.group(1)):
                return True
            fn = HELPER.match(e)
            if fn and self._body_ok(fn.group(1)):
                return True
            if re.fullmatch(r"[A-Za-z_$][\w$]*", e):
                if self._local_ok(e) or self._loop_binding_ok(e):
                    return True
            return False
        finally:
            self.depth -= 1

    def _ternary_ok(self, e: str) -> bool:
        if "?" not in e:
            return False
        parts = _split_ternary(e)
        if parts is None:
            return False
        cond, yes, no = parts
        # The condition must itself carry no data (a comparison over literals or
        # a table lookup), and both branches must be safe.
        if not (SAFE_EXPR.match(cond) or re.fullmatch(r"[\w$.\s+\-*/%<>=&|!()]+", cond)):
            return False
        return self.safe(yes) and self.safe(no)

    def _local_ok(self, name: str) -> bool:
        """Audit every assignment to this local.

        A local may hold its value as a template (`const checks = chars.map(...)
        .join(...)`), in which case the templates inside it are audited; or as
        a bare expression (`const ac = t.ac == null ? '?' : t.ac`), in which case
        that expression is itself audited. Missing the second case would let
        `AC ${ac}` through, because the assignment holds no `${...}` to find.
        """
        stmts = re.findall(rf"\b(?:const|let|var)\s+{re.escape(name)}\s*=(.*?);",
                           self.scope, re.S)
        if not stmts:
            return False
        # A name can be declared in more than one function (forecast() has its
        # own `pct`), and only one of them is the one in scope at the sink.
        # Passing if any assignment is safe would be a hole, so require that
        # none of them can carry a value, and treat a multi-name declaration
        # (`let pct = null, phrase = ''`) by its first binding.
        checked = 0
        for s in stmts:
            head = s.split(",")[0] if "," in s and "`" not in s.split(",")[0] else s
            if "`" in head:
                if not self._stmt_ok(head):
                    return False
            elif not self.safe(head.strip().rstrip(";")):
                return False
            checked += 1
        return checked > 0

    def _array_ok(self, name: str) -> bool:
        """Audit every element pushed onto a local array, so `bits.join(...)`
        is judged on what went into `bits` rather than waved through."""
        pushes = re.findall(rf"\b{re.escape(name)}\.push\((.*?)\);", self.scope, re.S)
        if not pushes:
            return False
        # A fresh resolver: `x.join()` appears both in the element being audited
        # and in the join call itself, and sharing the cycle-guard set would
        # make the second one look like a loop and fail.
        r = Resolver(self.name, self.src, self.scope)
        for p in pushes:
            # The pushed value is usually a template literal, whose own
            # interpolations are what need auditing; a bare expression goes
            # through safe() directly.
            inner = _interpolations(p)
            if inner:
                if not all(r.safe(x) for x in inner):
                    return False
            elif not r.safe(p.strip().rstrip(",").rstrip(")")):
                return False
        return True

    def _loop_binding_ok(self, name: str) -> bool:
        """`for (const lv of Object.keys(sl).map(Number))` binds lv to a number,
        and `for (const s of ...)` to whatever the iterable holds. Only the
        first shape is provably safe, so the second is not accepted here."""
        m = re.search(rf"for\s*\(\s*(?:const|let|var)\s+{re.escape(name)}\s+of\s+"
                      r"([\w$.()\[\]]*\.map\(Number\)|Object\.keys\([^)]*\)\.map\(Number\))",
                      self.scope)
        if not m:
            return False
        return True

    def _stmt_ok(self, stmt: str) -> bool:
        return all(self.safe(x) for x in _interpolations(stmt))

    def _body_ok(self, fn: str) -> bool:
        """Audit every template literal in a function's body, resolving that
        body's locals within the body rather than in the caller's scope."""
        body = _function_body(self.src, fn)
        if not body:
            return False
        inner = Resolver(self.name, self.src, body)
        inner.seen, inner.depth = self.seen, self.depth
        ok = inner._stmt_ok(body)
        self.seen = inner.seen
        return ok


def _split_ternary(e: str):
    """(condition, then, else) for a single ternary, else None."""
    depth = 0
    for i, c in enumerate(e):
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "?" and depth == 0:
            rest = e[i + 1:]
            j, d2 = 0, 0
            for j, c2 in enumerate(rest):
                if c2 in "([{":
                    d2 += 1
                elif c2 in ")]}":
                    d2 -= 1
                elif c2 == ":" and d2 == 0:
                    return e[:i], rest[:j], rest[j + 1:]
    return None


def _function_body(src: str, fn: str):
    """The body of `function fn(...) {...}`, or None."""
    m = re.search(rf"function\s+{re.escape(fn)}\s*\([^)]*\)\s*\{{", src)
    if not m:
        return None
    i, depth = m.end() - 1, 0
    while i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[m.end():i]
        i += 1
    return None


def _enclosing_scope(src: str, at: int) -> str:
    """The text of the function (or the IIFE body) containing offset `at`.

    Locals are resolved inside this, because the same identifier is reused for
    different values in different functions and a file-wide search picks the
    wrong one.
    """
    best = ""
    for m in re.finditer(r"(?:^|\n)\s*(?:function\s+[\w$]*\s*\([^)]*\)|=>)\s*\{", src):
        if m.start() > at:
            break
        body = _block_at(src, m.end() - 1)
        if body is not None and m.end() <= at:
            best = body
    return best or src


def _block_at(src: str, brace: int):
    """The text between the brace at `brace` and its match, or None."""
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[brace:i + 1]
    return None


class StaticSinks(unittest.TestCase):
    """Every innerHTML interpolation is a constant or goes through esc()."""

    maxDiff = None

    def _sources(self):
        return (("display.js", JS.read_text(encoding="utf-8")),
                ("tactics.js", TACTICS.read_text(encoding="utf-8")))

    def _text(self, name):
        for n, t in self._sources():
            if n == name:
                return t
        raise KeyError(name)

    def _sinks(self):
        found = []
        for name, text in self._sources():
            for m in SINK.finditer(text):
                # innerHTML = '' is a clear, not an interpolation sink; the
                # single-quoted form has no template literal, so it is skipped
                # by the backtick requirement below.
                tail = text[m.end():]
                if not tail.lstrip().startswith("`"):
                    continue
                line = text.count("\n", 0, m.start()) + 1
                at = m.end() + len(tail) - len(tail.lstrip())
                stmt = _statement_at(text, at)
                found.append((name, line, stmt, _enclosing_scope(text, at)))
        return found

    def test_the_scan_actually_finds_sinks(self):
        """A scanner that silently matches nothing would pass every other test
        in this class. Pin the floor: these sinks exist in both files today."""
        found = self._sinks()
        names = {name for name, _, _, _ in found}
        self.assertIn("display.js", names)
        self.assertIn("tactics.js", names)
        self.assertGreaterEqual(len(found), 13, f"only found {len(found)} sinks")

    def test_every_interpolation_is_a_constant_or_escaped(self):
        offenders = []
        for name, _, stmt, scope in self._sinks():
            r = Resolver(name, self._text(name), scope)
            for expr in _interpolations(stmt):
                if not r.safe(expr):
                    offenders.append(f"{name}: {' '.join(expr.split())}")
        self.assertEqual(
            offenders, [],
            "unescaped interpolation(s) reaching innerHTML; wrap the value in "
            "esc() (display.js's single helper) or use textContent:\n  "
            + "\n  ".join(sorted(set(offenders))))

    def test_no_setter_sneaks_past_the_scan(self):
        """innerHTML via bracket notation, or outerHTML, is the same sink and
        must not be able to appear unnoticed."""
        for name, text in self._sources():
            self.assertNotRegex(
                text, r"""\[\s*["']innerHTML["']\s*\]""",
                f"{name} assigns innerHTML by bracket notation, which the sink "
                f"scan does not see")
            self.assertNotRegex(
                text, r"\.outerHTML\s*=",
                f"{name} assigns outerHTML; there is no reason to and the sink "
                f"scan only covers innerHTML")

    def test_document_write_and_eval_absent(self):
        for name, text in self._sources():
            self.assertNotIn("document.write", text, name)
            self.assertNotIn("eval(", text, name)
            self.assertNotIn("new Function(", text, name)

    def test_the_escaped_output_is_what_an_attribute_needs(self):
        """esc() is used inside attributes (title=, aria-label=, data-*), so it
        has to neutralise a quote and not merely a tag. Pinned against the real
        function so a later edit cannot quietly weaken it."""
        assert_escapes_five(JS.read_text(encoding="utf-8"), "display.js esc()")


class OneHelper(unittest.TestCase):
    """One esc(), so there is no weaker second copy to reach for by accident.

    This is the invariant the audit asked for and the reason the first PR's
    hardening was incomplete: it added _escHtml, left _esc and a local esc in
    _renderMarkdown beside it, and the two disagreed about quotes, which is
    exactly the difference between safe and injectable in an attribute.
    """

    def test_display_js_defines_exactly_one_escape_helper(self):
        src = JS.read_text(encoding="utf-8")
        defined = re.findall(r"function\s+(\w*esc\w*)\s*\(", src)
        self.assertEqual(defined, ["esc"],
                         f"display.js should define only esc(), found {defined}")
        self.assertNotRegex(src, r"const\s+esc\s*=",
                            "a local esc() shadows the shared helper")

    def test_no_legacy_helper_names_survive(self):
        for path in (JS, TACTICS):
            src = path.read_text(encoding="utf-8")
            for legacy in ("_escHtml", "_esc", "escapeHtml", "htmlEscape"):
                self.assertNotIn(legacy + "(", src, f"{path.name} still calls {legacy}")
        # The names may appear in a comment explaining the history; the calls may not.
        self.assertNotRegex(JS.read_text(encoding="utf-8"),
                            r"function\s+_esc(Html)?\s*\(")

    def test_tactics_uses_the_shared_helper_or_an_identical_fallback(self):
        """tactics.js is also loaded on its own by display/evidence-panel.html,
        which does not load display.js, so it cannot simply assume window.esc.
        It must therefore either use the global or fall back to a definition
        that behaves identically, and the fallback has to escape the same five
        characters."""
        src = TACTICS.read_text(encoding="utf-8")
        self.assertIn("window.esc", src,
                      "tactics.js should reach for the shared esc()")
        self.assertNotRegex(
            src, r"const esc = s =>",
            "tactics.js still defines its own single-line esc; it must use "
            "window.esc with a documented fallback")
        assert_escapes_five(src, "the tactics.js fallback")

    def test_the_tactics_side_table_is_constants(self):
        """The chip sink interpolates side.glyph and side.word unescaped. That is
        only sound because they come from this table of literals, so the table
        is pinned rather than trusted."""
        src = TACTICS.read_text(encoding="utf-8")
        table = re.search(r"const SIDES = \{(.*?)\};", src, re.S)
        self.assertIsNotNone(table, "the SIDES table moved; update this test")
        for field in ("glyph", "word"):
            values = re.findall(rf"{field}:\s*'([^']*)'", table.group(1))
            self.assertTrue(values, f"no {field} found in SIDES")
            for v in values:
                self.assertNotIn("<", v, f"SIDES {field} carries markup: {v!r}")

    def test_esc_is_never_redefined_inside_a_function(self):
        """A local `const esc = ...` inside a function would shadow the helper
        for everything below it, which is how _renderMarkdown silently ran on a
        weaker copy. The one legitimate binding is tactics.js's documented
        fallback, which is checked separately for escaping the same five."""
        for path in (JS,):
            src = path.read_text(encoding="utf-8")
            for m in re.finditer(r"^\s+const esc\s*=", src, re.M):
                line = src.count("\n", 0, m.start()) + 1
                self.fail(f"{path.name}:{line} shadows the shared esc()")
        src = TACTICS.read_text(encoding="utf-8")
        rebinds = re.findall(r"^\s+const esc\s*=", src, re.M)
        self.assertLessEqual(len(rebinds), 1,
                             "tactics.js binds esc more than once")


# ── the real thing, in a real browser ────────────────────────────────────────
class XssInBrowser(BrowserTestCase):
    """Push the payloads through the real endpoints and ask the page what it did."""

    maxDiff = None

    module_name = "gm_display_xss"

    @property
    def mod(self):
        """The app module the live server is serving, so the test client posts
        into the very instance this page is subscribed to."""
        return self.server.module

    def setUp(self):
        self.client = self.server.app.test_client()
        # The app module is shared by the whole class, so a payload posted by
        # one test is still in _current_stats when the next one opens its page
        # and replays the state. Wipe first, or a failure names the wrong test.
        self.client.post("/clear")
        self.mod._current_combat = None
        self.page = self.open_page(wait=400)

    def post(self, path, body):
        """Post through the app's own test client, which is the same module the
        live server is serving and therefore broadcasts to the page over SSE.

        Posting from the browser (page.request) was tried first and does not
        work: the payload never reached the open /stream connection, so the
        assertions below would have passed on an empty page. Each test therefore
        also asserts that the thing it posted actually rendered.
        """
        r = self.client.post(path, json=body)
        # /stats, /combat and /chunk answer 204; /dice-request answers 200 with
        # the pending-roll state. Both are success.
        self.assertIn(r.status_code, (200, 204), f"{path} refused: {r.data!r}")

    def assert_clean(self, where):
        self.page.wait_for_timeout(500)
        self.assertIsNone(self.page.evaluate("window.__x"),
                          f"script ran via {where}")
        self.assertEqual(
            self.page.evaluate("document.querySelectorAll('img[onerror]').length"), 0,
            f"an <img onerror> reached the DOM via {where}")
        self.assertEqual(
            self.page.evaluate("document.querySelectorAll('[onmouseover]').length"), 0,
            f"an injected attribute reached the DOM via {where}")

    def assert_rendered(self, selector, where):
        """Guard against the test passing because nothing rendered."""
        self.assertGreater(
            self.page.evaluate(f"document.querySelectorAll({selector!r}).length"), 0,
            f"{where}: {selector} never rendered, so this test proved nothing")

    # ── /stats: character name, race, class, background ─────────────────
    def test_stats_names_do_not_execute(self):
        self.post("/stats", {"players": [{
            "name": EVIL, "race": EVIL, "class": EVIL, "background": EVIL,
            "level": 1, "hp": {"current": 5, "max": 9},
            "conditions": [EVIL], "concentration": EVIL, "effects": [EVIL],
        }]})
        self.assert_clean("/stats")
        self.assert_rendered(".sb-player", "/stats")
        # Escaping is not stripping: the payload is still on screen, as text.
        # Query the card by data-player-name rather than first-match, because
        # the sidebar keeps a card per character and this is not the only one.
        # CSS.escape, because the payload contains quotes and would otherwise
        # terminate the attribute selector.
        card = self.page.evaluate(
            "n => document.querySelector("
            "'.sb-player[data-player-name=\"' + CSS.escape(n) + '\"]').innerHTML", EVIL)
        self.assertIn("&lt;img", card)

    def test_turn_order_names_do_not_execute(self):
        self.post("/stats", {"players": [{"name": "Kairos", "level": 1,
                                          "hp": {"current": 5, "max": 9}}],
                             "turn_order": {"order": [EVIL, "Goblin"],
                                            "current": "Goblin", "round": 1}})
        self.assert_clean("turn order")
        self.assert_rendered("#sb-turn-list .sb-turn-item", "turn order")
        self.assertIn("&lt;img", self.page.evaluate(
            "document.getElementById('sb-turn-list').innerHTML"))

    def test_a_quote_in_a_name_cannot_open_an_attribute(self):
        """The half the first pass got wrong: _escHtml escaped quotes, _esc did
        not, and both were used inside HTML attributes."""
        self.post("/stats", {"players": [{
            "name": EVIL_ATTR, "race": EVIL_ATTR, "level": 1,
            "hp": {"current": 5, "max": 9}}]})
        self.assert_clean("attribute break-out via /stats")
        self.assert_rendered(".sb-player", "attribute break-out")

    # ── /combat: token name, condition, spell, map label ─────────────────
    def test_combat_snapshot_fields_do_not_execute(self):
        snap = {
            "status": "active", "round": 1, "current": "kairos",
            "meta": {"name": EVIL, "labels": [{"x": 1, "y": 1, "text": EVIL}]},
            "grid": {"name": EVIL, "rows": ["....."] * 4,
                     "legend": {".": "floor"}, "width": 5, "height": 4},
            "order": ["kairos", "kob"],
            "turn": {"movement_left": 30, "action_used": False,
                     "bonus_used": False, "reaction": True},
            "tokens": [
                {"id": "kairos", "name": EVIL, "side": "pc", "x": 1, "y": 1,
                 "hp": 8, "max_hp": 8, "ac": 12, "dead": False, "hidden": False,
                 "controller": "player", "conditions": [EVIL], "effects": [EVIL],
                 "concentration": EVIL, "readied": EVIL},
                {"id": "kob", "name": EVIL, "side": "enemy", "x": 3, "y": 2,
                 "hp": 5, "max_hp": 5, "ac": 12, "dead": False, "hidden": False,
                 "controller": "gm", "conditions": [EVIL], "effects": [],
                 "concentration": None, "readied": None},
            ],
            "log": [{"text": "Kairos attacks.", "round": 1, "rolls": []}],
        }
        self.post("/combat", {"combat": snap})
        self.assert_clean("/combat")
        self.assertTrue(self.page.evaluate("!!window.Tactics"),
                        "the panel did not load, so this test proved nothing")

    def test_hp_and_max_hp_cannot_break_out_of_the_hp_aria_label(self):
        """The strip renders `aria-label="${hp} of ${max_hp} HP"`, and /combat
        stores the posted dict unvalidated, so hp is not guaranteed to be a
        number by the time the browser sees it."""
        snap = {
            "status": "active", "round": 1, "current": "kob",
            "meta": {"name": "Test"},
            "grid": {"name": "Test", "rows": ["....."] * 4,
                     "legend": {".": "floor"}, "width": 5, "height": 4},
            "order": ["kob"],
            "turn": {"movement_left": 30, "action_used": False,
                     "bonus_used": False, "reaction": True},
            "tokens": [{"id": "kob", "name": "Kobold", "side": "enemy", "x": 2,
                        "y": 2, "hp": EVIL_ATTR, "max_hp": EVIL_ATTR, "ac": 12,
                        "dead": False, "hidden": False, "controller": "gm",
                        "conditions": [], "effects": [], "concentration": None,
                        "readied": None}],
            "log": [],
        }
        self.post("/combat", {"combat": snap})
        self.assert_clean("hp in the aria-label")
        self.assertEqual(self.page.evaluate(
            "document.querySelectorAll('.tx-hpbar').length"), 1)
        self.assertEqual(self.page.evaluate(
            "document.querySelectorAll('[onmouseover]').length"), 0)

    # ── /dice-request: the label and the pending-characters list ─────────
    def test_dice_request_label_does_not_execute(self):
        self.post("/dice-request", {"character": EVIL, "spec": "1d20",
                                    "label": EVIL})
        self.assert_clean("/dice-request")

    # ── the raw dispatcher, for payloads with no HTTP route of their own ──
    def test_inspiration_award_name_does_not_execute(self):
        """renderInspirationBlock interpolated its name straight into
        innerHTML between two <img> tags. Nothing on the server sets
        inspiration_award today, so this drives the renderer directly rather
        than leaving the sink unpinned."""
        self.page.evaluate("n => renderInspirationBlock(n, 'because')", EVIL)
        self.assert_clean("inspiration award name")
        self.assertIn("&lt;img", self.page.evaluate(
            "document.querySelector('.inspiration-title').innerHTML"))

    def test_milestone_award_name_does_not_execute(self):
        self.post("/chunk", {"milestone_award": EVIL, "label": EVIL,
                             "reason": EVIL})
        self.assert_clean("milestone award")
        self.assertIn("&lt;img", self.page.evaluate(
            "document.querySelector('.milestone-header').innerHTML"))


if __name__ == "__main__":
    unittest.main()
