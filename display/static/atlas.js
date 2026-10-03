/* atlas.js: the overview map page (/atlas/<slug>).

   Draws numbered markers over the overview image and a legend of the same
   places. The server already dropped every unrevealed pin
   (scripts/overview_map.py revealed()), so nothing here decides what is shown.
   Every label goes in through textContent; this file has no HTML sink.

   Pins are fractions of the image (0..1). The stage takes the spec's extent as
   its aspect ratio and the image fills it exactly, so a fraction of the stage
   is the same fraction of the image. If the art's own aspect differs from the
   extent, the image is visibly stretched rather than the pins being silently
   misplaced: the same choice tactics/scenes.py makes for its marker. */
(function () {
  'use strict';

  function readSpec() {
    const node = document.getElementById('atlas-data');
    try { return JSON.parse(node.textContent); } catch (e) { return null; }
  }

  function render(spec) {
    const stage = document.getElementById('atlas-stage');
    const layer = document.getElementById('atlas-pins');
    const list = document.getElementById('atlas-list');
    const empty = document.getElementById('atlas-empty');
    const w = spec.extent[0], h = spec.extent[1];
    stage.style.setProperty('--atlas-w', String(w));
    stage.style.setProperty('--atlas-h', String(h));

    const pins = Array.isArray(spec.pins) ? spec.pins : [];
    pins.forEach(function (pin, i) {
      const n = String(i + 1);
      const mark = document.createElement('span');
      mark.className = 'atlas-pin';
      mark.style.left = (pin.x * 100) + '%';
      mark.style.top = (pin.y * 100) + '%';
      mark.textContent = n;
      mark.dataset.pinId = pin.id;
      layer.appendChild(mark);

      const item = document.createElement('li');
      item.className = 'atlas-place';
      item.dataset.pinId = pin.id;
      const num = document.createElement('span');
      num.className = 'atlas-place-num';
      num.textContent = n;
      const label = document.createElement('span');
      label.className = 'atlas-place-label';
      label.textContent = pin.label;
      item.append(num, label);
      list.appendChild(item);
    });
    empty.hidden = pins.length > 0;
  }

  /* No stage to draw on means there is no map behind the page.
   *
   * `/?view=map` is one of the four launcher windows and renders this same page
   * with a sentence in place of the map when the campaign has no overview spec
   * yet — the stage, the pins layer and the legend are simply not in the
   * document. The script is still loaded, and it read the empty spec the server
   * sent alongside the note and went straight on to dereference a null stage.
   *
   * So the guard is here rather than in the template's own conditional: this
   * file is asked to render a spec, and a spec with nothing to render is a
   * thing that is now true of it. */
  const spec = readSpec();
  if (spec && document.getElementById('atlas-stage')) render(spec);
})();
