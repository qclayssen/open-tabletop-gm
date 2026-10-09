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

  /* No stage means there is no map behind the page.
   *
   * The note branch omits #atlas-stage (and the pins layer and the legend)
   * and still loads this script. readSpec() parses the empty spec sent with
   * that note, and an empty object is truthy, so `if (spec)` is not enough:
   * render() would throw on stage.style while the sentence is already shown.
   * The guard is the element. */
  const spec = readSpec();
  if (spec && document.getElementById('atlas-stage')) render(spec);
})();
