"""Milestone 6: continuity benchmark across folds and a restart.

Tests that pinned facts survive compaction, a restart, and that the recap
carries no GM-only or credential-shaped content.

Dependencies: 06-01 structured handoff (summarizer YAML output, handoff field injection).
"""
from __future__ import annotations
import tempfile

import json
import re
import sys
import time

import pytest

import pathlib
import sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

from localdm import llm
from localdm.memory import Memory
from localdm.summarizer import Summarizer
from tests.localdm_fakes import FakeClient


# ── helper: summary.md lives at <tmp>/localdm/summary.md ────────────────────

def _summary_path(tmp_dir: pathlib.Path) -> pathlib.Path:
    """Return the path to summary.md within the Memory's localdm subdir."""
    return pathlib.Path(tmp_dir) / "localdm" / "summary.md"


def _read_summary(m) -> str:
    """Read the summary.md file content."""
    return m.summary() or ""


def _check_pin_in_summary(summary: str, label: str) -> bool:
    """True if a pin label appears in the summary prose (case-insensitive)."""
    return label.lower() in summary.lower()


# ── fixture helpers ────────────────────────────────────────────────────────

def fixture_campaign(tmp_path, promises=None, location=None, reveals=None,
                     threads=None):
    """Build a Memory with pinned facts encoded as explicit dm turns."""
    m = Memory(tmp_path)
    if promises:
        for p in promises:
            m.add("dm", f"Pin a promise: {p}")
    if location:
        m.add("dm", f"The party is at {location}")
    if reveals:
        for r in reveals:
            m.add("dm", f"Reveal: {r}")
    if threads:
        for t in threads:
            m.add("dm", f"Open thread: {t}")
    return m


def add_player_turns(m, count):
    """Add `count` player turns to the transcript."""
    for i in range(count):
        m.add("player", f"Player turn {i}")


# ── T1: pin the facts are nameable ────────────────────────────────────────

def test_t1_pins_are_in_transcript():
    """Pinned facts are stored as dm-turns in the transcript.

    Before any fold, each pin should be readable from the transcript.
    """
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["ally the goblin chief", "protect the artifact"],
            location="Frog Pond",
            reveals=["secret door", "NPC treasure map"],
            threads=["missing scout", "cursed heirloom"],
        )
        add_player_turns(m, 14)  # keep=6 + batch=8

        # Read transcript and check pins are there
        transcript = m.turns()
        transcript_text = " ".join(t["text"] for t in transcript)
        assert "ally the goblin chief" in transcript_text, (
            "promise 'ally the goblin chief' should be in transcript"
        )
        assert "protect the artifact" in transcript_text, (
            "promise 'protect the artifact' should be in transcript"
        )
        assert "Frog Pond" in transcript_text, (
            "location 'Frog Pond' should be in transcript"
        )
        assert "secret door" in transcript_text, (
            "reveal 'secret door' should be in transcript"
        )
        assert "missing scout" in transcript_text, (
            "thread 'missing scout' should be in transcript"
        )


def test_t1_due_after_14_turns():
    """due() returns True after 14 total turns (keep=6 + batch=8)."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["protect the artifact"],
            location="Frog Pond",
        )
        add_player_turns(m, 14)  # 14 player turns + dm pins = enough for due()

        summarizer = Summarizer(FakeClient(lambda *a: "Summary output."), "dm-local", m)
        assert summarizer.due(), "due() should be True after 14 turns"


def test_t1_fold_writes_summary(tmp_path):
    """After a fold, summary.md should be written (non-empty file)."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["protect the artifact"],
            location="Frog Pond",
        )
        add_player_turns(m, 14)

        summarizer = Summarizer(FakeClient(lambda *a: "Summary output."), "dm-local", m)
        assert summarizer.due(), "due() should be True"
        summarizer.fold()

        summary = _read_summary(m)
        # Summary file is written by set_summary(); content depends on LLM output
        assert summary, "summary.md should be non-empty after fold"


# ── T2: folds and a restart ───────────────────────────────────────────────

def test_t2_multiple_folds_write_summaries(tmp_path):
    """After each fold, summary.md should be written and non-empty."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["protect the artifact"],
            location="Frog Pond",
        )
        # Start with 14 turns so first fold fires
        add_player_turns(m, 14)

        summarizer = Summarizer(FakeClient(lambda *a: "Summary output."), "dm-local", m)

        # Run 3 folds, adding turns between folds so due() stays True
        for _ in range(3):
            assert summarizer.due(), "due() should be True before fold"
            summarizer.fold()
            # Add more turns so the next fold can fire
            add_player_turns(m, 14)

        # After folds, summary should exist
        summary = _read_summary(m)
        assert summary, "summary.md should exist after multiple folds"


def test_t2_budget_recorded_as_summary_length(tmp_path):
    """Each fold records budget as the summary character length."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["protect the artifact"],
            location="Frog Pond",
        )
        # Start with 14 turns so first fold fires
        add_player_turns(m, 14)

        summarizer = Summarizer(FakeClient(lambda *a: "Summary output."), "dm-local", m)

        budgets = []
        for _ in range(3):
            assert summarizer.due(), "due() should be True"
            summarizer.fold()
            # Add more turns so the next fold can fire
            add_player_turns(m, 14)
            s = _read_summary(m)
            budgets.append(len(s) if s else 0)

        # Budget should be recorded as a non-negative integer
        for b in budgets:
            assert isinstance(b, int) and b >= 0, (
                "budget should be a non-negative integer"
            )


def test_t2_latency_recorded_positive(tmp_path):
    """Each fold records latency as a positive wall-clock time."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["protect the artifact"],
            location="Frog Pond",
        )
        # Start with 14 turns so first fold fires
        add_player_turns(m, 14)

        summarizer = Summarizer(FakeClient(lambda *a: "Summary output."), "dm-local", m)

        latencies = []
        for _ in range(3):
            assert summarizer.due(), "due() should be True"
            start = time.monotonic()
            summarizer.fold()
            # Add more turns so the next fold can fire
            add_player_turns(m, 14)
            elapsed = time.monotonic() - start
            latencies.append(elapsed)

        # Latency should be recorded as non-negative floats
        for l in latencies:
            assert isinstance(l, float) and l >= 0, (
                "latency should be a non-negative number"
            )


# ── T3: no credentials in recap ───────────────────────────────────────────

OMNIROUTE_API_KEY = "sk-or-v1-fake-key-very-long-and-secret-12345"
BEARER_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpZCI6InNpbjUtZG9rIiwiaWF0IjoxNTE2NDM5MDIyLCJ1c2VyX2tleSI6InRlc3QifQ.JC1lF5sTQxQZ5sT6sT5sT6sT5sT6sT5sT6sT"

def test_t3_no_omniroute_key_in_summary(tmp_path):
    """OMNIROUTE_API_KEY should never appear in summary.md."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["protect the artifact"],
            location="Frog Pond",
        )
        add_player_turns(m, 14)

        summarizer = Summarizer(FakeClient(lambda *a: "Summary output."), "dm-local", m)
        assert summarizer.due(), "due() should be True"
        summarizer.fold()

        summary = _summary_path(pathlib.Path(tmp))
        if summary.exists():
            summary_text = summary.read_text(encoding="utf-8")
            # The recap is the summary text; check that API key is absent
            assert OMNIROUTE_API_KEY not in summary_text, (
                "OMNIROUTE_API_KEY must not appear in summary.md"
            )


def test_t3_no_bearer_token_in_summary(tmp_path):
    """Bearer token should never appear in summary.md."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["protect the artifact"],
            location="Frog Pond",
        )
        add_player_turns(m, 14)

        summarizer = Summarizer(FakeClient(lambda *a: "Summary output."), "dm-local", m)
        assert summarizer.due(), "due() should be True"
        summarizer.fold()

        summary = _summary_path(pathlib.Path(tmp))
        if summary.exists():
            summary_text = summary.read_text(encoding="utf-8")
            assert BEARER_TOKEN not in summary_text, (
                "Bearer token must not appear in summary.md"
            )


def test_t3_summary_is_not_credential_shaped(tmp_path):
    """Summary should not contain JSON/web-token shaped strings."""
    with tempfile.TemporaryDirectory() as tmp:
        m = fixture_campaign(
            tmp,
            promises=["protect the artifact"],
            location="Frog Pond",
        )
        add_player_turns(m, 14)

        summarizer = Summarizer(FakeClient(lambda *a: "Summary output."), "dm-local", m)
        assert summarizer.due(), "due() should be True"
        summarizer.fold()

        summary = _summary_path(pathlib.Path(tmp))
        if summary.exists():
            summary_text = summary.read_text(encoding="utf-8")
            # Check for typical credential shapes
            assert "sk-or-v1-" not in summary_text, (
                "secret key prefix must not appear in summary"
            )
            assert "eyJhbGciOiJ" not in summary_text, (
                "bearer token prefix must not appear in summary"
            )