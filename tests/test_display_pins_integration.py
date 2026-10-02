"""Pins fetched for an active map reach the real combat board and open notes."""
from __future__ import annotations

import unittest

from tests._browser import BrowserTestCase
from tests.display_settle import present


class DisplayPinsIntegration(BrowserTestCase):
    server_kind = "static"

    def test_revealed_pin_renders_and_opens_note_without_hidden_pin(self):
        page = self.open_page(size=(1440, 900), wait=0)
        pins = [
            {"id": "shown1", "x": 2, "y": 3, "label": "Fog Bank",
             "kind": "note", "target": "notes/harbour.md"},
        ]
        hidden = {"id": "gm1", "x": 4, "y": 5, "label": "THE-TRAITOR",
                  "kind": "note", "target": "notes/harbour.md"}
        page.evaluate("""({pins, hidden}) => {
          const original = window.fetch;
          window.fetch = url => {
            if (url === '/pins/harbour-map')
              return Promise.resolve({ok: true, json: () => Promise.resolve({pins})});
            if (String(url).startsWith('/pins/note?')) {
              window.__noteFetched = String(url);
              return Promise.resolve({ok: true, text: () => Promise.resolve('The hidden cove.')});
            }
            return original(url);
          };
          const state = JSON.parse(JSON.stringify(window.__SNAP));
          state.meta.slug = 'harbour-map';
          window.__hiddenPinFixture = hidden;
          Tactics.update(state);
        }""", {"pins": pins, "hidden": hidden})
        present(page, "() => !!document.querySelector('#tx-board [data-pin-id=\"shown1\"]')",
                "the revealed pin to render")

        rendered = page.locator("#tx-board .tx-pin-layer").evaluate("e => e.textContent")
        self.assertIn("Fog Bank", rendered)
        self.assertNotIn(hidden["id"], page.locator("#tx-board .tx-pin-layer").inner_html())
        self.assertNotIn(hidden["label"], rendered)

        page.locator('#tx-board [data-pin-id="shown1"]').click()
        present(page, "() => !!window.__noteFetched", "the note route to be requested")
        self.assertIn("id=shown1", page.evaluate("window.__noteFetched"))
        self.assertIn("map=harbour-map", page.evaluate("window.__noteFetched"))
