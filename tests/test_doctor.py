"""Startup doctor: every failing check names a cause and a fix, and the run is bounded."""

from __future__ import annotations

import pathlib
import time

import doctor


def _quiet(**kwargs):
    """A run whose local checks pass, so only the probe under test can fail."""
    root = kwargs.pop("campaign_root", None)
    if root is None:
        root = pathlib.Path(kwargs.pop("tmp", "/tmp"))
    env = {"GM_DM_MODEL": "same", "GM_ADVISOR_MODEL": "same"}
    env.update(kwargs.pop("env", {}))
    return doctor.run(
        env=env,
        python_version=kwargs.pop("python_version", (3, 14)),
        flask_present=kwargs.pop("flask_present", True),
        campaign_root=root,
        srd_message=kwargs.pop("srd_message", ""),
        budget=kwargs.pop("budget", 5),
        per_call=kwargs.pop("per_call", 1),
        **kwargs,
    )


def test_missing_module_is_the_defect():
    """Collection of this file is the before-fix failure when doctor.py is absent."""
    assert hasattr(doctor, "run")


def test_passing_checks_print_nothing(tmp_path):
    camp = tmp_path / "campaigns"
    camp.mkdir()
    (camp / "demo").mkdir()
    (camp / "demo" / "state.md").write_text("ok\n", encoding="utf-8")

    def probe(role, model, timeout):
        return None

    rows = _quiet(campaign_root=camp, probe=probe)
    assert doctor.format_report(rows) == ""
    assert all(row.status == "ok" for row in rows)


def test_old_python_names_the_version_and_the_install():
    rows = _quiet(python_version=(3, 13), probe=lambda *a: None, campaign_root=_camp())
    text = doctor.format_report(rows)
    assert "python" in text
    assert "3.13" in text
    assert "3.14" in text


def test_missing_flask_names_pip_install(tmp_path):
    rows = _quiet(flask_present=False, probe=lambda *a: None, campaign_root=_one(tmp_path))
    text = doctor.format_report(rows)
    assert "flask" in text.lower()
    assert "pip3 install flask" in text


def test_missing_campaign_root_names_the_setting(tmp_path):
    missing = tmp_path / "nope" / "campaigns"
    rows = _quiet(campaign_root=missing, probe=lambda *a: None)
    text = doctor.format_report(rows)
    assert "GM_CAMPAIGN_ROOT" in text
    assert "cause:" in text
    assert "fix:" in text


def test_srd_failure_keeps_the_rebuild_command(tmp_path):
    rows = _quiet(
        campaign_root=_one(tmp_path),
        srd_message="SRD data not built: grid combat needs it.",
        probe=lambda *a: None,
    )
    text = doctor.format_report(rows)
    assert "build_srd.py" in text
    assert "cause:" in text


def test_http_401_maps_to_the_key_without_the_body(tmp_path):
    def probe(role, model, timeout):
        return "http-401"

    rows = _quiet(campaign_root=_one(tmp_path), probe=probe)
    text = doctor.format_report(rows)
    assert "GM_LLM_KEY" in text
    assert "upstream body" not in text
    assert text.count("http-401") == 0


def test_http_404_names_the_model_setting(tmp_path):
    rows = _quiet(campaign_root=_one(tmp_path), probe=lambda *a: "http-404")
    text = doctor.format_report(rows)
    assert "GM_DM_MODEL" in text


def test_http_429_says_quota_was_not_measured(tmp_path):
    rows = _quiet(campaign_root=_one(tmp_path), probe=lambda *a: "http-429")
    text = doctor.format_report(rows)
    assert "not measured" in text


def test_unset_reasoning_warns_for_a_mandatory_model(tmp_path):
    rows = _quiet(
        campaign_root=_one(tmp_path),
        env={"GM_DM_MODEL": "gpt-oss", "GM_ADVISOR_MODEL": "gpt-oss"},
        probe=lambda *a: None,
    )
    text = doctor.format_report(rows)
    assert "WARN reasoning" in text
    assert "GM_REASONING=medium" in text


def test_qwen_default_does_not_warn(tmp_path):
    rows = _quiet(
        campaign_root=_one(tmp_path),
        env={"GM_DM_MODEL": "qwen3.5", "GM_ADVISOR_MODEL": "qwen3.5"},
        probe=lambda *a: None,
    )
    assert doctor.format_report(rows) == ""


def test_reasoning_set_prints_nothing(tmp_path):
    rows = _quiet(
        campaign_root=_one(tmp_path),
        env={"GM_DM_MODEL": "gpt-oss", "GM_ADVISOR_MODEL": "gpt-oss", "GM_REASONING": "medium"},
        probe=lambda *a: None,
    )
    assert doctor.format_report(rows) == ""


def test_no_answer_budget_names_gm_reasoning(tmp_path):
    rows = _quiet(campaign_root=_one(tmp_path), probe=lambda *a: "no-answer-budget")
    text = doctor.format_report(rows)
    assert "GM_REASONING" in text


def test_cold_start_retries_once_then_ok(tmp_path):
    calls = {"n": 0}

    def probe(role, model, timeout):
        calls["n"] += 1
        if calls["n"] == 1:
            return "unreachable"
        return None

    rows = _quiet(campaign_root=_one(tmp_path), probe=probe)
    assert calls["n"] == 2
    assert doctor.format_report(rows) == ""


def test_hanging_probe_returns_inside_the_budget(tmp_path):
    calls = {"n": 0}

    def probe(role, model, timeout):
        calls["n"] += 1
        time.sleep(5)
        return None

    started = time.monotonic()
    rows = _quiet(campaign_root=_one(tmp_path), probe=probe, budget=0.45, per_call=0.15)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0
    assert calls["n"] == 2
    text = doctor.format_report(rows)
    assert "timed out" in text.lower()
    assert "fix:" in text


def test_budget_spent_does_not_start_a_second_probe(tmp_path):
    calls = {"n": 0}

    def probe(role, model, timeout):
        calls["n"] += 1
        time.sleep(5)
        return None

    started = time.monotonic()
    _quiet(campaign_root=_one(tmp_path), probe=probe, budget=0.2, per_call=0.2)
    assert time.monotonic() - started < 1.0
    assert calls["n"] == 1


def test_distinct_models_are_checked_separately(tmp_path):
    seen = []

    def probe(role, model, timeout):
        seen.append((role, model))
        return None

    _quiet(
        campaign_root=_one(tmp_path),
        env={"GM_DM_MODEL": "dm", "GM_ADVISOR_MODEL": "advisor"},
        probe=probe,
    )
    assert ("dm", "dm") in seen
    assert ("advisor", "advisor") in seen


def test_main_exits_nonzero_when_a_row_fails(tmp_path, capsys):
    code = doctor.main(
        [],
        env={"GM_DM_MODEL": "same", "GM_ADVISOR_MODEL": "same"},
        python_version=(3, 14),
        flask_present=True,
        campaign_root=_one(tmp_path),
        srd_message="",
        probe=lambda *a: "http-401",
        budget=2,
        per_call=1,
    )
    assert code == 1
    assert "GM_LLM_KEY" in capsys.readouterr().out


def _one(tmp_path: pathlib.Path) -> pathlib.Path:
    camp = tmp_path / "campaigns"
    (camp / "demo").mkdir(parents=True)
    (camp / "demo" / "state.md").write_text("ok\n", encoding="utf-8")
    return camp


def _camp():
    return pathlib.Path("/tmp/doctor-unused")
