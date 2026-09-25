"""Milestone 6: transcript, rolling summary and flags under <campaign>/localdm/."""
from __future__ import annotations

import pathlib
import sys
import threading

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from localdm.memory import Memory        # noqa: E402


def test_turns_round_trip_including_non_ascii(tmp_path):
    m = Memory(tmp_path)
    assert m.turns() == [] and m.summary() == "" and m.summarized() == 0
    m.add("player", "I greet Émile.")
    m.add("dm", "Émile bows.")
    assert m.turns() == [{"role": "player", "text": "I greet Émile."},
                         {"role": "dm", "text": "Émile bows."}]
    assert (tmp_path / "localdm" / "transcript.jsonl").exists()


def test_set_summary_moves_the_unsummarized_window(tmp_path):
    m = Memory(tmp_path)
    for i in range(5):
        m.add("player", f"line {i}")
    m.set_summary("Five lines happened.", 3)
    assert m.summary() == "Five lines happened." and m.summarized() == 3
    assert [t["text"] for t in m.unsummarized()] == ["line 3", "line 4"]


def test_broken_meta_reads_as_empty(tmp_path):
    m = Memory(tmp_path)
    m.dir.mkdir(parents=True)
    (m.dir / "meta.json").write_text("{nope", encoding="utf-8")
    assert m.meta() == {} and m.summarized() == 0


def test_seen_keys_persist(tmp_path):
    Memory(tmp_path).mark_seen(["start:a", "boss:a:ogre"])
    m = Memory(tmp_path)
    m.mark_seen(["start:a"])
    assert m.seen() == {"start:a", "boss:a:ogre"}


def test_concurrent_adds_never_tear_lines(tmp_path):
    m = Memory(tmp_path)
    threads = [threading.Thread(target=lambda n=n: [m.add("dm", f"{n}-{i}") for i in range(50)])
               for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(m.turns()) == 200
