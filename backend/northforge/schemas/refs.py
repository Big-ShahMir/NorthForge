"""Reference syntax for wiring workflow step configuration to prior data.

A step's per-type configuration fields (see ``schemas/steps.py``) accept
either a literal value or a *reference* string that points at a declared
workflow input or a prior step's output field:

- ``"$input.<name>"`` refers to a declared workflow input named ``<name>``.
- ``"$step.<step_id>.<field>"`` refers to the ``<field>`` output of the step
  identified by ``<step_id>``.

Anything else (including non-string values) is a literal and ``parse_ref``
returns ``None`` for it. A string that starts with ``$`` but does not match
either form is almost certainly a typo, so ``parse_ref`` raises
``ValueError`` rather than silently treating it as a literal.

This module is intentionally standalone: it has no dependency on the step or
workflow schemas so it can be imported from the semantic validation layer
without creating an import cycle.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

# Kept in sync with the step id pattern in ``schemas/steps.py`` (STEP_ID_PATTERN).
_NAME = r"[a-zA-Z_][a-zA-Z0-9_]*"
_STEP_ID = r"[a-z][a-z0-9_]{0,63}"

_INPUT_REF_RE = re.compile(rf"^\$input\.({_NAME})$")
_STEP_REF_RE = re.compile(rf"^\$step\.({_STEP_ID})\.({_NAME})$")

RefKind = Literal["input", "step"]


@dataclass(frozen=True, slots=True)
class Ref:
    """A parsed reference to a workflow input or a prior step's output field."""

    kind: RefKind
    name: str
    field: str | None = None


def parse_ref(value: object) -> Ref | None:
    """Parse a step configuration value as a reference.

    Returns ``None`` when ``value`` is not a reference at all (any non-string
    value, or a string that does not start with ``$``): it should be treated
    as a literal. Raises ``ValueError`` when ``value`` starts with ``$`` but
    does not match either recognised reference form.
    """
    if not isinstance(value, str) or not value.startswith("$"):
        return None

    input_match = _INPUT_REF_RE.match(value)
    if input_match:
        return Ref(kind="input", name=input_match.group(1), field=None)

    step_match = _STEP_REF_RE.match(value)
    if step_match:
        return Ref(kind="step", name=step_match.group(1), field=step_match.group(2))

    raise ValueError(
        f"malformed reference {value!r}: expected '$input.<name>' or '$step.<step_id>.<field>'"
    )


def format_ref(ref: Ref) -> str:
    """Render a ``Ref`` back into its string form."""
    if ref.kind == "input":
        return f"$input.{ref.name}"
    if ref.field is None:
        raise ValueError("a 'step' reference requires a field")
    return f"$step.{ref.name}.{ref.field}"
