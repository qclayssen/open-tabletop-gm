from __future__ import annotations
from tests.test_invocation_journal import begin, camp, run, _next_to_kairos, records
from tactics import cli

def _hitting():
    import random
    return next(s for s in range(500) if 9 <= random.Random(s).randint(1, 20) <= 16)

def test_badface_first(camp, capsys, monkeypatch):
    monkeypatch.setattr(cli, "_fresh_seed", _hitting)
    begin(capsys)
    _next_to_kairos(camp)
    print("1", run(capsys, "attack", "frog-1", "kairos", "--roll", "99"))
    print("PEND", (camp/"combat"/"pending.json").read_text(encoding="utf-8") if (camp/"combat"/"pending.json").exists() else None)
    print("2", run(capsys, "attack", "frog-1", "kairos"))
    for r in records(camp):
        print("REC", r["seq"], r["cmd"][2], r["outcome"], r["code"], repr(r["reason"]), "resumes=", r["resumes"], "seed=", r["seed"])
