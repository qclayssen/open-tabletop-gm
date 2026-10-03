#!/usr/bin/env python3
"""check_py_floor.py -- is the Python 3.10 floor actually met, and is this check armed?

The floor
---------
`CLAUDE.md` says code must run on Python 3.10 and "on Windows with a non-UTF-8
locale". The Windows half rests on `encoding="utf-8"` discipline and is not
checked anywhere. The version half used to rest on nothing either: the test
workflow was removed in `6e78d4d` and the one before it ran a
`macos x {3.10, 3.13}` matrix, so after that commit the floor was an
honour-system rule. This file is the version half's job.

Why a script and not just the CI job
------------------------------------
Because a check that cannot fail guards nothing, and this file is the piece that
could quietly stop working. `--selftest` writes post-floor fixtures to a temporary
directory and asserts the checker rejects them, on whatever interpreter is
running it. If the checker's regex or its file walk stops matching, the selftest
goes red in the same run that the real check would have passed.

What this does and does not prove
---------------------------------
It proves **syntax**. `compile()` under the running interpreter rejects
`except*` (3.11), PEP 695 `type` aliases and type-parameter lists (3.12),
`enum.StrEnum` bases are invisible to it and so on.

It does **not** prove APIs. `enum.StrEnum`, `datetime.UTC`, `itertools.batched`
and `typing.Self` are all names, not grammar: nothing here sees them. They are
caught by *running* the suite on 3.10, which is what the floor CI job does. So
this script is half a floor check and says so rather than letting the green
check mark stand for the whole claim.

The consequence is that this script is only a floor proof when the interpreter
running it is the floor or older. On 3.14 it is a portability lint. It prints
which one it is for exactly that reason, and `--require-floor` makes it a stop
instead of a note.

Usage
-----
    python3 scripts/check_py_floor.py              # check every tracked .py
    python3 scripts/check_py_floor.py --selftest   # prove this checker can fail
    python3 scripts/check_py_floor.py --require-floor

What the selftest cannot do
---------------------------
It cannot catch a mutation that disables its own reporting. Proven, not
assumed: replacing `failures.append(...)` with `pass`, or `if failures:` with
`if False:`, leaves `--selftest` exiting 0 with "SELFTEST FAILED" printed on every
fixture. No in-process check can observe that. The floor CI job therefore greps
the selftest's output for `SELFTEST FAILED` and fails the step on it, rather than
trusting the exit code alone.

Six mutants were run against this selftest and four are caught
(`check_py_floor.py --selftest` on the mutants, in the PR body). The two that
survive are the two above, and they survive for the reason above.

Exit codes: 0 clean, 1 a post-floor construct or a failed selftest,
2 the interpreter is above the floor and --require-floor was asked for.
"""
from __future__ import annotations

import argparse
import pathlib
import subprocess
import sys
import tempfile

#: The documented floor. Written as a tuple so `sys.version_info[:2] >= FLOOR`
#: is the comparison, not a string compare on "3.10" vs "3.9".
FLOOR = (3, 10)

#: `git ls-files '*.py'`: tracked files only, so `__pycache__`, editor
#: droppings and anything a previous run left in the tree are not in scope. A
#: checker that also read untracked files would report on files nobody ships.
GLOB = "*.py"


def tracked_pythons(root: pathlib.Path) -> list[pathlib.Path]:
    """Every tracked .py under `root`, as paths relative to it."""
    result = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--", GLOB],
                            capture_output=True, text=True, encoding="utf-8",
                            check=True)
    return [pathlib.Path(name) for name in result.stdout.split("\0") if name]


def compile_failures(root: pathlib.Path,
                     files: list[pathlib.Path]) -> list[tuple[pathlib.Path, SyntaxError]]:
    """Files that do not parse under the running interpreter. Empty means clean."""
    out: list[tuple[pathlib.Path, SyntaxError]] = []
    for rel in files:
        source = (root / rel).read_text(encoding="utf-8")
        try:
            compile(source, str(rel), "exec")
        except SyntaxError as error:
            out.append((rel, error))
    return out


# --- selftest ---------------------------------------------------------------
#
# Three groups, and the reason they are not one group.
#
# `impossible` fixtures are rejected by every Python. They are what gives this
# selftest teeth *above* the floor: the first version of this file had only
# version-boundary fixtures, and it was proved false-green by neutering the
# detection loop and re-running it on 3.14, where every post-floor construct
# legitimately compiles. Five "ok" lines, exit 0, from a checker that was
# detecting nothing. A selftest that can only fail on the floor is a selftest
# that reports nothing on the machine most people develop on.
#
# `valid_from` fixtures discriminate the version boundary. At or below the floor
# they must be rejected; above it they must be accepted. That half only says
# anything at or below the floor, which is where the CI job runs this, and the
# job says so rather than leaving it implied.
#
# Controls the floor permits are what catch an over-eager checker: one that
# rejected everything would pass every negative fixture above. `match` is the
# newest thing 3.10 allows, and `ok_pep604_union_in_annotation` catches a checker
# that pattern-matched on `|` -- `int | None` is inside a *string* annotation, so
# it is never evaluated and compiles on every version.

#: (name, source, first version that accepts it).
VALID_FROM: tuple[tuple[str, str, tuple[int, int]], ...] = (
    # `match` landed in 3.10, so it is the newest thing the floor permits.
    ("ok_match_statement_310", "match 1:\n    case 1:\n        pass\n", (3, 10)),
    ("ok_pep604_union_in_annotation", 'def f(v: "int | None"):\n    return v\n', (3, 0)),
    ("bad_except_star_311", "try:\n    pass\nexcept* ValueError:\n    pass\n", (3, 11)),
    ("bad_pep695_type_alias_312", "type Alias = int\n", (3, 12)),
    ("bad_pep695_type_params_312", "def f[T](v: T) -> T:\n    return v\n", (3, 12)),
)

#: Rejected by every Python. `(99, 0)` is "no released version", so the
#: expectation derived from `sys.version_info` is always "must be rejected".
IMPOSSIBLE: tuple[tuple[str, str], ...] = (
    ("impossible_unclosed_paren", "def broken(\n"),
    ("impossible_bare_else", "else:\n    pass\n"),
)


def _git(root: pathlib.Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True, encoding="utf-8")


def selftest() -> int:
    """Prove the whole pipeline fails on bad input, on whatever interpreter runs it.

    Covers `tracked_pythons` as well as `compile_failures`: a checker whose file
    walk matches nothing returns no failures for any input, which is the same
    false green as a checker that detects nothing.
    """
    running = sys.version_info[:2]
    failures: list[str] = []

    def record(name: str, detail: str) -> None:
        failures.append(f"{name}: {detail}")
        print(f"  SELFTEST FAILED: {name} ({detail})")

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        _git(root, "init", "-q")
        _git(root, "config", "user.email", "selftest@example.invalid")
        _git(root, "config", "user.name", "selftest")

        # Every fixture lands in a real repository, so the walk under test is the
        # same walk the real check uses: `git ls-files`, tracked files only.
        # Staged by name rather than with `add -A`, because `untracked.py` has to
        # stay untracked: a checker that read untracked files would report on
        # files nobody ships.
        for name, source, _ in VALID_FROM:
            (root / f"{name}.py").write_text(source, encoding="utf-8")
        for name, source in IMPOSSIBLE:
            (root / f"{name}.py").write_text(source, encoding="utf-8")
        (root / "not_python.txt").write_text("ignore me\n", encoding="utf-8")
        (root / "untracked.py").write_text("def also_broken(\n", encoding="utf-8")
        _git(root, "add", "--",
             *[f"{name}.py" for name, _, _ in VALID_FROM],
             *[f"{name}.py" for name, _ in IMPOSSIBLE],
             "not_python.txt")

        found = {p.name for p in tracked_pythons(root)}
        want = {f"{name}.py" for name, _, _ in VALID_FROM}
        want |= {f"{name}.py" for name, _ in IMPOSSIBLE}
        if found != want:
            record("tracked_pythons", f"found {sorted(found)}, wanted {sorted(want)}")
        else:
            print(f"  ok: tracked_pythons found exactly {len(want)} tracked .py "
                  "and ignored not_python.txt and untracked.py")

        rejected = {p.name for p, _ in compile_failures(root, tracked_pythons(root))}

        for name, source, since in VALID_FROM:
            filename = f"{name}.py"
            compiled = filename not in rejected
            should_compile = running >= since
            if compiled != should_compile:
                record(filename, f"{'compiles' if compiled else 'rejected'}, but "
                                 f"{'should compile' if should_compile else 'should be rejected'} "
                                 f"(valid from {since[0]}.{since[1]}, running "
                                 f"{running[0]}.{running[1]})")
            else:
                print(f"  ok: {filename} "
                      f"({'compiles' if compiled else 'rejected'}, as expected)")

        for name, _ in IMPOSSIBLE:
            filename = f"{name}.py"
            if filename not in rejected:
                record(filename, "compiled, but no Python accepts it")
            else:
                print(f"  ok: {filename} (rejected, as expected on every version)")

    if failures:
        print(f"check-py-floor: the checker itself is broken: "
              f"{len(failures)} fixture(s) disagreed. A check that cannot fail "
              "guards nothing, so this is a stop rather than a warning.")
        for line in failures:
            print(f"  - {line}")
        return 1
    print(f"check-py-floor: selftest passed, {len(VALID_FROM) + len(IMPOSSIBLE)} "
          f"fixtures, so this checker walks and detects on "
          f"{running[0]}.{running[1]}.")
    if running > FLOOR:
        print("  Note: the version-boundary half of this selftest only "
              "discriminates at or below the floor. The CI job runs it on "
              f"{FLOOR[0]}.{FLOOR[1]}, where it does.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(prog="check_py_floor.py",
                                 description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(pathlib.Path(__file__).resolve().parent.parent),
                    help="repository root (default: the parent of scripts/)")
    ap.add_argument("--selftest", action="store_true",
                    help="prove this checker rejects post-floor source, then exit")
    ap.add_argument("--require-floor", action="store_true",
                    help="exit 2 unless the running interpreter is the floor or older")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    running = sys.version_info[:2]
    floor_text = f"{FLOOR[0]}.{FLOOR[1]}"
    running_text = f"{running[0]}.{running[1]}"
    at_floor = running <= FLOOR
    print(f"check-py-floor: floor {floor_text}, running "
          f"{sys.version.split()[0]} ({'at or below' if at_floor else 'ABOVE'} the floor)")
    if not at_floor:
        print("  This run is a portability lint, not a floor proof: a construct "
              "newer than the floor parses here. The API half of the floor is "
              "proven by running the suite on the floor, not by this script.")
    if args.require_floor and not at_floor:
        print(f"check-py-floor: --require-floor and the interpreter is {running_text}.")
        return 2

    root = pathlib.Path(args.root)
    files = tracked_pythons(root)
    if not files:
        print(f"check-py-floor: no tracked {GLOB} under {root}. That is either an "
              "empty repo or a broken `git ls-files`, and treating it as clean "
              "would be the same false green as a check that ran on nothing.")
        return 1

    failures = compile_failures(root, files)
    if failures:
        print(f"check-py-floor: {len(failures)} of {len(files)} tracked {GLOB} "
              f"file(s) do not compile on {running_text}:")
        for rel, error in failures:
            print(f"  - {rel}:{error.lineno}: {error.msg}")
        return 1
    print(f"check-py-floor: {len(files)} tracked {GLOB} files compile on "
          f"{running_text}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())