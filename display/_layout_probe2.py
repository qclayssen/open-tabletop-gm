"""Throwaway: does hiding a rail actually give the prose more room, and what
overlaps what. Not part of the suite; delete when done."""
from playwright.sync_api import sync_playwright

URL = "http://localhost:5001/"

PROBE = """() => {
  const R = el => { if (!el) return null; const b = el.getBoundingClientRect();
    return {l: Math.round(b.left), r: Math.round(b.right), t: Math.round(b.top),
            b: Math.round(b.bottom), w: Math.round(b.width), h: Math.round(b.height)}; };
  const by = id => document.getElementById(id);
  const ts = by('text-scroll'), cs = getComputedStyle(ts);
  // overlap area between two rects
  const A = (a, c) => { if (!a||!c) return 0;
    const w = Math.max(0, Math.min(a.r,c.r)-Math.max(a.l,c.l));
    const h = Math.max(0, Math.min(a.b,c.b)-Math.max(a.t,c.t)); return w*h; };
  const items = {};
  for (const id of ['dm-help-btn','input-panel','displays-rail','sidebar-toggle',
                    'audio-controls','phone-mode-btn','overview-link','corner-logo','conn-status']) {
    const el = by(id);
    items[id] = el && getComputedStyle(el).display !== 'none' ? R(el) : null;
  }
  // every pair that intersects
  const clashes = [];
  const keys = Object.keys(items);
  for (let i=0;i<keys.length;i++) for (let j=i+1;j<keys.length;j++) {
    const a = A(items[keys[i]], items[keys[j]]);
    if (a > 4) clashes.push(`${keys[i]} x ${keys[j]} = ${Math.round(a)}px2`);
  }
  // first sidebar card vs the sidebar toggle
  const card = document.querySelector('#sidebar .sb-player');
  return {
    vw: innerWidth, vh: innerHeight,
    padL: parseFloat(cs.paddingLeft), padR: parseFloat(cs.paddingRight),
    padT: parseFloat(cs.paddingTop), padB: parseFloat(cs.paddingBottom),
    col: R(by('text-content')),
    deadL: R(by('text-content')).l - 0,
    items, clashes,
    toggleVsCard: A(items['sidebar-toggle'], card ? R(card) : null),
    toggleVsFirstName: A(items['sidebar-toggle'],
      document.querySelector('#sidebar .sb-name') ? R(document.querySelector('#sidebar .sb-name')) : null),
    classes: document.getElementById('text-scroll').className,
  };
}"""


def run(pw, label, size, actions):
    b = pw.chromium.launch()
    ctx = b.new_context(viewport={"width": size[0], "height": size[1]})
    page = ctx.new_page()
    page.goto(URL, wait_until="load")
    page.wait_for_timeout(1200)
    actions(page)
    page.wait_for_timeout(700)
    m = page.evaluate(PROBE)
    print(f"--- {label} @ {size[0]}x{size[1]}")
    print(f"    pad  L{m['padL']:.0f} R{m['padR']:.0f} T{m['padT']:.0f} B{m['padB']:.0f}"
          f"   column w={m['col']['w']}  ({100*m['col']['w']/m['vw']:.1f}% of width)")
    print(f"    rails occupy: left 210 + right 300 = 510px; reserved "
          f"{m['padL']+m['padR']:.0f}px;  slack {m['padL']+m['padR']-510:.0f}px")
    if m["clashes"]:
        print("    OVERLAPS: " + "; ".join(m["clashes"]))
    if m["toggleVsCard"]:
        print(f"    sidebar toggle sits on the first card: {m['toggleVsCard']:.0f}px2")
    ctx.close(); b.close()


def main():
    with sync_playwright() as pw:
        run(pw, "as shipped", (1920, 1080), lambda p: None)
        run(pw, "sidebar hidden", (1920, 1080),
            lambda p: p.evaluate("document.getElementById('sidebar-toggle').click()"))
        run(pw, "settings collapsed", (1920, 1080),
            lambda p: p.evaluate("document.getElementById('controls-toggle-row').click()"))
        run(pw, "both rails hidden", (1920, 1080),
            lambda p: p.evaluate("document.getElementById('sidebar-toggle').click();"
                                 "document.getElementById('controls-toggle-row').click()"))
        run(pw, "input panel expanded", (1920, 1080),
            lambda p: p.click("#input-panel-header"))
        run(pw, "input expanded + 1280x720", (1280, 720),
            lambda p: p.click("#input-panel-header"))
        run(pw, "input expanded 1440x900", (1440, 900),
            lambda p: p.click("#input-panel-header"))


if __name__ == "__main__":
    main()