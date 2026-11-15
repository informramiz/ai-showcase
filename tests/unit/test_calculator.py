import pytest

from sourcebook.domain.errors import ValidationError
from sourcebook.services.calculator import evaluate, format_number


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("75 * 3", 225),
        ("(45 * 12) + 300", 840),
        ("1,200 / 4", 300),
        ("-2 ** 2", -4),
        ("+7 // 2", 3),
        ("10 % 4 - 1", 1),
        ("2.5 * 2", 5),
    ],
)
def test_evaluates_arithmetic(expression: str, expected: float) -> None:
    assert evaluate(expression) == expected


@pytest.mark.parametrize(
    ("expression", "message"),
    [
        ("1 / 0", "Division by zero"),
        ("2 ** 100", "Exponent is too large"),
        ("__import__('os')", "Only numbers"),
        ("abs(-1)", "Only numbers"),
        ("'a' * 3", "Only numbers"),
        ("1 +", "Not a valid arithmetic expression"),
        ("9" * 201, "too long"),
        ("1e308 * 10", "not a finite number"),
    ],
)
def test_rejects_unsafe_or_invalid_input(expression: str, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        evaluate(expression)


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (225.0, "225"),
        (1234567.0, "1,234,567"),
        (2.5, "2.5"),
        (1 / 3, "0.333333"),
        (1e16, "10,000,000,000,000,000"),
    ],
)
def test_format_number(value: float, text: str) -> None:
    assert format_number(value) == text


@pytest.mark.parametrize("expression", ["1e308 ** 2", "(-1) ** 0.5", "10 ** 400"])
def test_unrepresentable_results_are_validation_errors(expression: str) -> None:
    with pytest.raises(ValidationError):
        evaluate(expression)


def test_small_numbers_keep_their_precision() -> None:
    assert format_number(0.00001) == "1e-05"


def test_nested_complex_results_are_validation_errors() -> None:
    with pytest.raises(ValidationError):
        evaluate("((-1) ** 0.5) // 2")


def test_values_below_one_keep_significant_digits() -> None:
    assert format_number(0.000149) == "0.000149"
