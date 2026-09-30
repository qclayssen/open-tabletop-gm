"""safeio.py: crash-safe persistence for small state files (stdlib only).

Writes go to a temp file in the same directory, are fsynced, then renamed over
the target with os.replace, so a kill mid-write leaves either the old file or
the new one, never a torn one. The previous good copy is kept as `<name>.bak`.

Reads never turn a corrupt file into an empty default silently. A file that
fails to parse is quarantined (renamed `<name>.corrupt-<timestamp>`), the `.bak`
is tried, and only if that fails too does the caller's default come back, with a
warning on stderr either way.
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import tempfile
import time


def _warn(msg: str) -> None:
    print(f"[safeio] WARNING: {msg}", file=sys.stderr)


def atomic_write_text(path, text: str, backup: bool = True) -> None:
    """Atomically replace `path` with `text` (UTF-8), keeping `path.bak`."""
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".",
                               suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        if backup and path.exists():
            bak = path.with_name(path.name + ".bak")
            try:
                shutil.copy2(path, bak)
            except OSError as e:
                _warn(f"could not refresh {bak}: {e}")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_json(path, data, backup: bool = True, **dump_kw) -> None:
    dump_kw.setdefault("indent", 2)
    atomic_write_text(path, json.dumps(data, **dump_kw), backup=backup)


def quarantine(path) -> pathlib.Path | None:
    """Rename a corrupt file to `<name>.corrupt-<timestamp>`; return the new path."""
    path = pathlib.Path(path)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(f"{path.name}.corrupt-{stamp}")
    n = 1
    while dest.exists():
        dest = path.with_name(f"{path.name}.corrupt-{stamp}-{n}")
        n += 1
    try:
        os.replace(path, dest)
    except OSError as e:
        _warn(f"could not quarantine {path}: {e}")
        return None
    return dest


def _parse(path: pathlib.Path, expect):
    data = json.loads(path.read_text(encoding="utf-8"))
    if expect is not None and not isinstance(data, expect):
        raise ValueError(f"expected {expect.__name__}, got {type(data).__name__}")
    return data


def load_json_safe(path, default=None, expect=dict):
    """Load JSON from `path`, recovering from corruption without silent loss.

    Missing file: `default` (a first run, not an error). Corrupt file: it is
    quarantined, the `.bak` is tried, and a stderr warning says what happened.
    Only when both are unusable does `default` come back, still with a warning.
    """
    path = pathlib.Path(path)
    fallback = {} if default is None and expect is dict else default
    if not path.exists():
        return fallback
    try:
        return _parse(path, expect)
    except (ValueError, OSError, UnicodeDecodeError) as e:
        moved = quarantine(path)
        _warn(f"{path} is corrupt ({e}); "
              f"kept as {moved.name if moved else '(could not move)'}")
    bak = path.with_name(path.name + ".bak")
    if bak.exists():
        try:
            data = _parse(bak, expect)
        except (ValueError, OSError, UnicodeDecodeError) as e:
            _warn(f"backup {bak} is also unusable ({e})")
        else:
            _warn(f"recovered {path.name} from {bak.name}; changes since the "
                  f"last good save are lost")
            atomic_write_json(path, data, backup=False)
            return data
    else:
        _warn(f"no backup for {path.name}; starting from defaults")
    return fallback


def read_jsonl_tolerant(path) -> tuple[list, list]:
    """Read a JSONL file, skipping torn or non-object lines.

    Returns (records, bad) where bad is a list of (line_number, text). The
    file is not modified. Callers report `bad` so a torn tail is never silent.
    """
    path = pathlib.Path(path)
    if not path.exists():
        return [], []
    records, bad = [], []
    text = path.read_text(encoding="utf-8", errors="replace")
    for i, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
        except ValueError:
            bad.append((i, line[:80]))
            continue
        if not isinstance(rec, dict):
            bad.append((i, line[:80]))
            continue
        records.append(rec)
    if bad:
        _warn(f"{path}: skipped {len(bad)} unreadable line(s): "
              f"{', '.join(str(n) for n, _ in bad)}")
    return records, bad
