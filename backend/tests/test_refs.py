from __future__ import annotations

import pytest

from northforge.schemas.refs import Ref, format_ref, parse_ref


@pytest.mark.parametrize("value", [None, 123, True, 4.5, ["$input.x"], {"a": 1}])
def test_non_string_values_are_literals(value: object) -> None:
    assert parse_ref(value) is None


@pytest.mark.parametrize("value", ["", "plain text", "not a ref", "input.name"])
def test_non_dollar_strings_are_literals(value: str) -> None:
    assert parse_ref(value) is None


def test_parses_input_reference() -> None:
    assert parse_ref("$input.contract_id") == Ref(kind="input", name="contract_id", field=None)


def test_parses_step_reference() -> None:
    assert parse_ref("$step.retrieve.chunks") == Ref(kind="step", name="retrieve", field="chunks")


@pytest.mark.parametrize(
    "value",
    [
        "$input",
        "$input.",
        "$input.bad-name",
        "$input.name.extra",
        "$step",
        "$step.retrieve",
        "$step.retrieve.",
        "$step.Retrieve.field",
        "$step.retrieve.bad-field",
        "$foo.bar",
        "$",
    ],
)
def test_malformed_dollar_strings_raise(value: str) -> None:
    with pytest.raises(ValueError, match="malformed reference"):
        parse_ref(value)


def test_format_ref_round_trips_input() -> None:
    ref = Ref(kind="input", name="contract_id", field=None)
    formatted = format_ref(ref)
    assert formatted == "$input.contract_id"
    assert parse_ref(formatted) == ref


def test_format_ref_round_trips_step() -> None:
    ref = Ref(kind="step", name="retrieve", field="chunks")
    formatted = format_ref(ref)
    assert formatted == "$step.retrieve.chunks"
    assert parse_ref(formatted) == ref


def test_format_ref_requires_field_for_step_kind() -> None:
    ref = Ref(kind="step", name="retrieve", field=None)
    with pytest.raises(ValueError, match="requires a field"):
        format_ref(ref)


def test_ref_is_frozen() -> None:
    ref = Ref(kind="input", name="x", field=None)
    with pytest.raises(AttributeError):
        ref.name = "y"  # type: ignore[misc]
