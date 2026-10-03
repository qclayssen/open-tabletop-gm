"""Throwaway layout probe. Not part of the test suite; delete when done.

Measures the live display at the desktop widths so a layout change can be judged
on numbers instead of on a screenshot someone squinted at.
"""
import json
import sys

from playwright.sync_api import sync_playwright

URL = "http://localhost:5001/"

PROBE = """() => {
  const R = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return {l: Math.round(b.left), r: Math.round(b.right), t: Math.round(b.top),
            b: Math.round(b.bottom), w: Math.round(b.width), h: Math.round(b.height)}; };
  const vis = el => { if (!el) return false; const cs = getComputedStyle(el);
    const b = el.getBoundingClientRect();
    return cs.display !== 'none' && cs.visibility !== 'hidden'
        && parseFloat(cs.opacity) > 0.01 && b.width > 0 && b.height > 0; };
  const by = id => document.getElementById(id);
  const names = ['sidebar','audio-controls','input-panel','phone-mode-btn','new-content-pill',
                 'dm-help-btn','displays-rail','world-clock','corner-logo','overview-link',
                 'sidebar-toggle','scene-indicator','text-scroll','text-content'];
  const boxes = {};
  for (const n of names) boxes[n] = vis(by(n)) ? R(by(n)) : null;

  // everything painted in the reading column's horizontal band, to find what
  // crosses it
  const col = R(by('text-content'));
  const intr = [];
  for (const el of document.querySelectorAll('body *')) {
    if (!vis(el)) continue;
    const b = el.getBoundingClientRect();
    if (b.width < 4 || b.height < 4) continue;
    const w = Math.min(b.right, col.r) - Math.max(b.left, col.l);
    const h = Math.min(b.bottom, col.b) - Math.max(b.top, col.t);
    if (w > 8 && h > 8) intr.push({sel: el.id ? '#'+el.id : el.tagName.toLowerCase()+
      (el.className && typeof el.className === 'string' ? '.'+el.className.trim().split(/\\s+/).join('.') : ''),
      area: Math.round(w*h)});
  }
  intr.sort((a,b) => b.area - a.area);

  const pill = by('new-content-pill');
  if (pill) pill.classList.add('visible');
  const dice = [...document.querySelectorAll('.dice-block, #dice-log .roll, .roll-entry')]
                 .filter(vis).map(el => ({sel: el.id||el.className, ...R(el)}));
  return {vw: innerWidth, vh: innerHeight, boxes, col, intr: intr.slice(0,14),
          pill: R(pill), dice};
}"""


def main():
    sizes = [(1920, 1080), (1600, 900), (1440, 900), (1280, 720), (1100, 700)]
    if len(sys.argv) > 1 and sys.argv[1] == "matrix":
        sizes = [(1920, 1080), (1440, 900), (1280, 720), (1100, 700), (900, 700), (768, 1024), (375, 667)]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        for w, h in sizes:
            ctx = b.new_context(viewport={"width": w, "height": h})
            page = ctx.new_page()
            page.goto(URL, wait_until="load")
            page.wait_for_timeout(1200)
            try:
                page.click("#input-panel-header")
                page.wait_for_timeout(300)
            except Exception:
                pass
            m = page.evaluate(PROBE)
            print("=" * 78)
            print(f"{w}x{h}")
            print("=" * 78)
            for k, v in m["boxes"].items():
                if v:
                    print(f"  {k:20s} l={v['l']:5d} r={v['r']:5d} t={v['t']:5d} b={v['b']:5d}  {v['w']}x{v['h']}")
                else:
                    print(f"  {k:20s} (not painted)")
            print(f"  reading column      l={m['col']['l']} r={m['col']['r']} w={m['col']['w']}")
            print(f"  reading column occupies {100*m['col']['w']/m['vw']:.1f}% of width")
            print("  things crossing the reading column:")
            for i in m["intr"]:
                print(f"     {i['area']:7d}px2  {i['sel'][:70]}")
            ctx.close()
        b.close()


if __name__ == "__main__":
    main()