"""schemas.py: the field types behind <campaign>/combat/encounter.json.

Every value in the saved encounter is described by a Field: a small object that
knows one type's rules, so validation, coercion and the shape of the document
are written down once instead of being re-implied at every call site.

Why this exists: the engine refuses to save a fight it cannot trust. Before this
module that was a flat list of hand-written checks in state.validate(), and the
Token dataclass itself carried no contract, so a mistyped key from a sheet
reader reached the encounter file as a TypeError at load time, twenty commands
into a session, with no field name in the message.

How it is used:

    TOKEN_SCHEMA.coerce(d)      # normalise, then check every field
    Token.from_dict(d)          # coerce + validate on the way in
    Token.to_dict()             # validate on the way out (catches a bad edit)

Two rules the rest of the engine relies on:

  * ``None`` is load-bearing. Initiative, concentration, base_speed, hit dice
    and several ``extra`` values are legitimately unset, and ``base_speed``
    reads ``None`` as "no adjustment" while ``0`` means "frozen". A field that
    coerces ``None`` to ``0`` silently turns every old save into a fight where
    nobody can move. Wrap anything optional in ``OptionalField``, or use
    ``AnyField`` where the value is free-form by design (``extra``).
  * Derived data is never stored. Derived values are recomputed on load from
    the token's own base stats; a derived number that survives in the file is a
    stale number waiting for a rules change to contradict it.

Anything free-form (a system-specific ``extra``, an attack spec, an effect) is
``AnyField`` or ``ListField(AnyField())`` on purpose: those dicts gain keys at
runtime from rules and effects code, and a closed schema there would reject
live campaigns.
"""

from __future__ import annotations

import ast
import re

__all__ = [
    "TOKEN_SCHEMA",
    "AnyField",
    "BooleanField",
    "DictField",
    "Field",
    "FormulaField",
    "ListField",
    "NumberField",
    "OptionalField",
    "SchemaError",
    "SchemaField",
    "SetField",
    "StringField",
    "ability_mod",
    "die_average",
]


class SchemaError(ValueError):
    """A value does not fit its field. Names the field, so the message is usable
    by the GM without opening the file."""


# ─── helpers ──────────────────────────────────────────────────────────────────

def ability_mod(score) -> int:
    """5e ability modifier from a score: floor((score - 10) / 2).

    Floor, not truncate, and never shared with a die average: 9 is -1 and 8 is
    -1, while the average of a d8 is 4.5 and rounds up to 5.
    """
    return (int(score) - 10) // 2


def die_average(die: str) -> int:
    """The average roll of a hit die: d6 3, d8 4, d10 5, d12 6. Round half up,
    which is what a player means by "average" (PHB p7: 17 -> 9, 15 -> 8)."""
    m = re.fullmatch(r"\s*(\d*)\s*d\s*(\d+)\s*", str(die or ""), re.I)
    sides = int(m.group(2)) if m else 10
    return (sides + 1) // 2


_DICE = re.compile(r"^\s*\d*d\d+\s*([+-]\s*\d+)?\s*$", re.I)


# ─── fields ───────────────────────────────────────────────────────────────────

class Field:
    """Base type. Accepts anything, changes nothing.

    Subclasses override ``validate`` (raise ``SchemaError`` or return None) and
    ``coerce`` (return the normalised value). ``clean`` is the pair: normalise
    first, then check what the normaliser produced, so a validator can rely on
    the type being right.
    """

    def __init__(self, default=None):
        self.default = default

    def coerce(self, value):
        return value

    def validate(self, value, path: str = "") -> None:
        return None

    def clean(self, value, path: str = ""):
        """Coerce, then validate what the coercer produced."""
        v = self.coerce(value)
        self.validate(v, path)
        return v

    def where(self, path: str) -> str:
        return path or "value"

    def fail(self, message: str, path: str) -> SchemaError:
        return SchemaError(f"{self.where(path)}: {message}")

    def __repr__(self):
        return f"{type(self).__name__}()"


class AnyField(Field):
    """Free-form. The value is stored as it came in, only the JSON types are
    checked (so a stray object cannot reach json.dump and blow up on save)."""

    def validate(self, value, path: str = "") -> None:
        if value is None or isinstance(value, (str, int, float, bool)):
            return
        if isinstance(value, (list, tuple)):
            for i, item in enumerate(value):
                AnyField().validate(item, f"{path}[{i}]")
            return
        if isinstance(value, dict):
            for k, v in value.items():
                if not isinstance(k, str):
                    raise self.fail(f"key {k!r} is not a string", path)
                AnyField().validate(v, f"{path}.{k}")
            return
        raise self.fail(f"{type(value).__name__} cannot be stored as JSON", path)


class OptionalField(Field):
    """``None`` passes through unchanged, anything else goes to the inner field.

    Use for a value that is not set yet, and for one where ``None`` and ``0``
    mean different things (``TurnState.base_speed``).
    """

    def __init__(self, inner: Field, default=None):
        super().__init__(default)
        self.inner = inner

    def coerce(self, value):
        return None if value is None else self.inner.coerce(value)

    def validate(self, value, path: str = "") -> None:
        if value is not None:
            self.inner.validate(value, path)

    def __repr__(self):
        return f"OptionalField({self.inner!r})"


class NumberField(Field):
    """An int or float. ``integer=True`` refuses 1.5; ``min``/``max`` are
    inclusive bounds. A numeric string ("18") is accepted and converted,
    because a sheet reader or a GM adjustment can hand over text."""

    def __init__(self, min=None, max=None, integer: bool = False, default=None):
        super().__init__(default)
        self.min, self.max, self.integer = min, max, integer

    def coerce(self, value):
        if isinstance(value, bool) or value is None:
            return value
        if isinstance(value, str):
            try:
                value = float(value) if ("." in value or "e" in value.lower()) else int(value)
            except ValueError:
                return value                      # left as-is: validate() names it
        if self.integer and isinstance(value, float) and not value.is_integer():
            return value
        if self.integer and isinstance(value, float):
            return int(value)
        return value

    def validate(self, value, path: str = "") -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise self.fail(f"expected a number, got {value!r}", path)
        if self.integer and not float(value).is_integer():
            raise self.fail(f"expected a whole number, got {value!r}", path)
        if self.min is not None and value < self.min:
            raise self.fail(f"{value} is below the minimum of {self.min}", path)
        if self.max is not None and value > self.max:
            raise self.fail(f"{value} is above the maximum of {self.max}", path)

    def __repr__(self):
        bits = [k for k in ("min", "max") if getattr(self, k) is not None]
        if self.integer:
            bits.append("integer")
        return f"NumberField({', '.join(bits)})"


class StringField(Field):
    """Text. ``choices`` is a closed set (used for enums); ``strip``/``lower``
    normalise, which is what a condition list or a damage type needs."""

    def __init__(self, choices=None, pattern=None, strip: bool = False,
                 lower: bool = False, default=None):
        super().__init__(default)
        self.choices = tuple(choices) if choices else None
        self.pattern = re.compile(pattern) if pattern else None
        self.strip, self.lower = strip, lower

    def coerce(self, value):
        if not isinstance(value, str):
            return value
        out = value.strip() if self.strip else value
        return out.lower() if self.lower else out

    def validate(self, value, path: str = "") -> None:
        if not isinstance(value, str):
            raise self.fail(f"expected text, got {value!r}", path)
        if self.choices and value not in self.choices:
            raise self.fail(f"{value!r} is not one of {', '.join(map(str, self.choices))}", path)
        if self.pattern and not self.pattern.search(value):
            raise self.fail(f"{value!r} does not match {self.pattern.pattern}", path)

    def __repr__(self):
        return f"StringField(choices={self.choices})" if self.choices else "StringField()"


class BooleanField(Field):
    """A flag. Accepts the usual spellings from a sheet or a JSON client."""

    _YES = {"true", "yes", "y", "1", "on"}
    _NO = {"false", "no", "n", "0", "off"}

    def coerce(self, value):
        if isinstance(value, str):
            low = value.strip().lower()
            if low in self._YES:
                return True
            if low in self._NO:
                return False
        return value

    def validate(self, value, path: str = "") -> None:
        if not isinstance(value, bool):
            raise self.fail(f"expected true or false, got {value!r}", path)


class ListField(Field):
    """A JSON array. The items are checked, not reshaped beyond coercion."""

    def __init__(self, item: Field = None, default=None):
        super().__init__(default if default is not None else [])
        self.item = item or AnyField()

    def coerce(self, value):
        if isinstance(value, (list, tuple)):
            return [self.item.coerce(v) for v in value]
        return value

    def validate(self, value, path: str = "") -> None:
        if not isinstance(value, (list, tuple)):
            raise self.fail(f"expected a list, got {value!r}", path)
        for i, v in enumerate(value):
            self.item.validate(v, f"{path}[{i}]")


class SetField(ListField):
    """A list with no duplicates, kept in first-seen order.

    JSON has no set, so conditions and resistances serialise as arrays; the
    uniqueness is the part that matters, because ``add_condition`` tests
    membership before appending and a duplicate would print twice.
    """

    def coerce(self, value):
        out = super().coerce(value)
        if not isinstance(out, list):
            return out
        seen, uniq = set(), []
        for v in out:
            key = v if isinstance(v, (str, int, float, bool, type(None))) else repr(v)
            if key not in seen:
                seen.add(key)
                uniq.append(v)
        return uniq


class DictField(Field):
    """A JSON object whose values all share one field type (saves, skill
    bonuses, damage types). Keys stay free strings."""

    def __init__(self, value: Field = None, default=None):
        super().__init__(default if default is not None else {})
        self.value_field = value or AnyField()

    def coerce(self, value):
        if isinstance(value, dict):
            return {k: self.value_field.coerce(v) for k, v in value.items()}
        return value

    def validate(self, value, path: str = "") -> None:
        if not isinstance(value, dict):
            raise self.fail(f"expected an object, got {value!r}", path)
        for k, v in value.items():
            self.value_field.validate(v, f"{path}.{k}")


class SchemaField(Field):
    """A JSON object described by other fields.

    ``required`` names keys that must be present. Unknown keys are an error by
    default: on a Token a typo would otherwise be silently dropped by
    ``Token(**d)``, and a misspelt ``conditionz`` is a condition the engine
    never applies.
    """

    def __init__(self, fields: dict, required=(), allow_unknown: bool = False, default=None):
        super().__init__(default if default is not None else {})
        self.fields = dict(fields)
        self.required = tuple(required)
        self.allow_unknown = allow_unknown

    def coerce(self, value):
        if not isinstance(value, dict):
            return value
        out = dict(value)
        for name, f in self.fields.items():
            if name in out:
                out[name] = f.coerce(out[name])
        return out

    def validate(self, value, path: str = "") -> None:
        if not isinstance(value, dict):
            raise self.fail(f"expected an object, got {value!r}", path)
        for name in self.required:
            if name not in value:
                raise self.fail(f"is missing the required field {name!r}", path)
        for name, v in value.items():
            f = self.fields.get(name)
            if f is None:
                if not self.allow_unknown:
                    known = ", ".join(sorted(self.fields))
                    raise self.fail(f"unknown field {name!r} (known: {known})", path)
                continue
            f.validate(v, f"{path}.{name}" if path else name)

    def __repr__(self):
        return f"SchemaField({len(self.fields)} fields)"


# ─── formulas ─────────────────────────────────────────────────────────────────

_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv, ast.Mod)
_FUNCS = {"min": min, "max": max, "abs": abs, "int": int, "round": round}


class FormulaField(NumberField):
    """A number that a documented expression can produce from a token's base
    stats, with the expression written next to it.

    The expression is evaluated by walking the AST of this module's own string
    over a closed set of names. ``eval`` is never used: a FormulaField is fed
    constants from here, but a formula read out of a sheet or a GM command is
    untrusted text, and an untrusted expression must not be able to reach
    ``eval``.

    ``evaluate`` is for construction time only (building a token), never on a
    value loaded from disk: derived data is recomputed, not trusted from the
    file.
    """

    def __init__(self, expr: str, variables: tuple = (), deterministic: bool = True,
                 integer: bool = True, min=None, max=None):
        super().__init__(min=min, max=max, integer=integer)
        self.expr, self.variables, self.deterministic = expr, tuple(variables), deterministic

    def evaluate(self, env: dict = None):
        """The expression's value for ``env`` (the declared variables)."""
        env = env or {}
        missing = [v for v in self.variables if v not in env]
        if missing:
            raise SchemaError(f"formula {self.expr!r} needs {', '.join(missing)}")
        return _eval(ast.parse(self.expr, mode="eval").body, env)

    def validate(self, value, path: str = "") -> None:
        # A formula field holds the number it evaluated to. When the document
        # still holds a dice string ("2d6+3") the sheet reader has not resolved
        # it yet, which is a normal intermediate state, not an error.
        if isinstance(value, str) and _DICE.match(value):
            return
        super().validate(value, path)

    def __repr__(self):
        return f"FormulaField({self.expr!r})"


def _eval(node, env):
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)):
            raise SchemaError("formula may only use numbers")
        return node.value
    if isinstance(node, ast.Name):
        if node.id not in env:
            raise SchemaError(f"formula uses unknown value {node.id!r}")
        return env[node.id]
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand, env)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and isinstance(node.op, _BINOPS):
        a, b = _eval(node.left, env), _eval(node.right, env)
        if isinstance(node.op, ast.Add):
            return a + b
        if isinstance(node.op, ast.Sub):
            return a - b
        if isinstance(node.op, ast.Mult):
            return a * b
        if isinstance(node.op, ast.FloorDiv):
            return a // b
        if isinstance(node.op, ast.Mod):
            return a % b
        raise SchemaError(f"formula cannot use {type(node.op).__name__}")
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS:
        if node.keywords:
            raise SchemaError("formula functions take positional arguments only")
        return _FUNCS[node.func.id](*[_eval(a, env) for a in node.args])
    raise SchemaError(f"formula cannot use {type(node).__name__}")


# ─── the Token document ───────────────────────────────────────────────────────
#
# The type contract for one creature on the board. Range and shape live here;
# the checks that span fields (hp within 0..max_hp, nobody standing in a wall,
# the turn order naming real tokens) stay in state.validate(), which is the only
# place that knows about the map.

_SIDE = ("pc", "ally", "enemy", "neutral")          # cross-checked in validate()

TOKEN_SCHEMA = SchemaField({
    "id": StringField(strip=True),
    "name": StringField(),
    "side": StringField(choices=_SIDE),
    "x": NumberField(integer=True),                # negative is legal: a grid can
    "y": NumberField(integer=True),                # be offset, and validate() owns bounds
    "hp": NumberField(min=0, integer=True),
    "max_hp": NumberField(min=0, integer=True),    # 0 until prepare_derived estimates it
    "ac": NumberField(min=0, integer=True),
    "speed": NumberField(min=0, integer=True, default=30),
    "swim_speed": NumberField(min=0, integer=True),
    "temp_hp": NumberField(min=0, integer=True),
    "dex_mod": NumberField(integer=True),          # the initiative modifier
    "controller": StringField(strip=True),
    "initiative": OptionalField(NumberField(integer=True)),
    "init_roll": OptionalField(NumberField(integer=True)),
    "conditions": SetField(StringField(strip=True, lower=True)),
    # Attack specs and effects are written by the rules layer, which adds keys
    # at runtime (reach, rider, flags, unparsed). AnyField, deliberately.
    "attacks": ListField(AnyField()),
    "saves": DictField(NumberField(integer=True)),
    "resistances": SetField(StringField(strip=True, lower=True)),
    "immunities": SetField(StringField(strip=True, lower=True)),
    "vulnerabilities": SetField(StringField(strip=True, lower=True)),
    "condition_immunities": SetField(StringField(strip=True, lower=True)),
    "death_saves": DictField(NumberField(min=0, integer=True)),
    "stable": BooleanField(),
    "dead": BooleanField(),
    "reaction_used": BooleanField(),
    "dodging": BooleanField(),
    "concentration": OptionalField(StringField()),
    "effects": ListField(AnyField()),
    "reactions": StringField(choices=("ask", "auto", "off")),
    "source": DictField(AnyField()),               # {"kind": "srd" | "sheet", ...}
    "extra": AnyField(),                           # system-specific, never closed
}, required=("id", "name", "side", "x", "y", "hp", "max_hp", "ac"))

# max_hp is estimated only when the source had no value for it (a hand-made
# token, or a sheet with no HP line). PHB p7: average the first die, add the
# fixed CON bonus each later level, all as the average.
MAX_HP_FORMULA = FormulaField("die_average + con_mod * (level - 1)",
                              variables=("die_average", "con_mod", "level"))
