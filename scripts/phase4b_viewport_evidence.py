"""Capture the combat panel at real viewport sizes (evidence for Phase 4b).

Not a test: this is the measurement script behind the Phase 4b PR body. It
drives display/evidence-panel.html, which feeds tactics.js a snapshot of the
shape sync.snapshot returns, and reports what the browser actually laid out --
the size of a square, whether the board fits its box, and where the action bar
is. Run it with a static server over display/:

    python3 -m http.server 8731 --directory display &
    python3 scripts/phase4b_viewport_evidence.py

Writes PNGs and a JSON report to /tmp/phase4b-evidence/.
"""
import json
import pathlib
import subprocess
import sys
import time
import urllib.request

OUT = pathlib.Path("/tmp/phase4b-evidence")
URL = "http://localhost:8731/evidence-panel.html"

# The two screens the panel is built for: a shared table display, and a phone.
VIEWPORTS = [("table", 1440, 900), ("phone", 390, 844)]

MEASURE = """() => {
  const board = document.getElementById('tx-board');
  const svg = board.querySelector('svg');
  const panel = document.getElementById('tx-panel');
  const bar = document.getElementById('tx-actions');
  const r = e => { const b = e.getBoundingClientRect();
    return {x: Math.round(b.x), y: Math.round(b.y), w: Math.round(b.width), h: Math.round(b.height)}; };
  const cell = svg ? svg.getBoundingClientRect().width / (window.__SNAP.grid.rows[0].length) : 0;
  const chips = [...document.querySelectorAll('.tx-chip')].map(c => ({
    name: c.querySelector('.tx-chip-name').textContent,
    cls: [...c.classList].filter(k => k.startsWith('tx-side-')),
    glyph: c.querySelector('.tx-side-glyph').textContent,
    stripe: getComputedStyle(c).borderLeftWidth,
  }));
  const frames = {};
  for (const g of board.querySelectorAll('.tx-tok')) {
    const name = g.getAttribute('data-id');
    const shape = g.querySelector('path, circle:not(.tx-ring)');
    frames[name] = shape ? shape.tagName : null;
  }
  return {
    viewport: [innerWidth, innerHeight],
    cell: Math.round(cell * 10) / 10,
    board: r(board), svg: r(svg), panel: r(panel), bar: r(bar),
    boardScrollsX: board.scrollWidth > board.clientWidth + 1,
    boardScrollsY: board.scrollHeight > board.clientHeight + 1,
    barSticky: getComputedStyle(bar).position,
    barBottomGap: Math.round(innerHeight - (bar.getBoundingClientRect().bottom)),
    leadOrder: [...bar.querySelectorAll('[data-tx=lead]')].map(b => b.textContent),
    firstInBar: [...bar.querySelectorAll('button')].slice(0, 4).map(b => b.textContent),
    endTurnOnScreen: (() => { const b = [...bar.querySelectorAll('button')]
        .find(x => x.textContent === 'End turn');
      if (!b) return null; const q = b.getBoundingClientRect();
      return q.top >= 0 && q.bottom <= innerHeight; })(),
    buttonHeights: [...bar.querySelectorAll('button')].slice(0, 4).map(b => Math.round(b.getBoundingClientRect().height)),
    chipStripeWidths: chips.map(c => c.stripe),
    chips, frames,
  };
}"""


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("playwright is not installed: pip install playwright")
    if not OUT.exists():
        OUT.mkdir(parents=True)
    for name, _, _ in VIEWPORTS:
        (OUT / name).mkdir(exist_ok=True)

    report = {}
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, w, h in VIEWPORTS:
            page = browser.new_page(viewport={"width": w, "height": h},
                                    device_scale_factor=2 if w < 500 else 1)
            page.goto(URL, wait_until="load")
            page.wait_for_function("window.__ready === true", timeout=10000)
            page.wait_for_timeout(250)
            report[name] = page.evaluate(MEASURE)
            page.screenshot(path=str(OUT / f"{name}.png"))
            for el, out in (("#tx-panel", "panel"), ("#tx-board", "board"),
                            ("#tx-strip", "strip")):
                page.locator(el).screenshot(path=str(OUT / name / f"{out}.png"))
            page.close()
        # The same phone, with the panel folded: the initiative strip stays.
        page = browser.new_page(viewport={"width": 390, "height": 844}, device_scale_factor=2)
        page.goto(URL, wait_until="load")
        page.wait_for_function("window.__ready === true", timeout=10000)
        page.click("#tx-min")
        page.wait_for_timeout(250)
        report["phone-folded"] = page.evaluate(MEASURE)
        page.screenshot(path=str(OUT / "phone-folded.png"))
        page.close()
        browser.close()

    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
