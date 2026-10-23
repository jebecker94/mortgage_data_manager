"""Typed narrowing for polars aggregation scalars.

``Series.min()``/``.max()``/``.sum()``/``.median()``/``.quantile()`` are typed
as returning the full ``PythonLiteral | None`` union, which ``int()`` and
``float()`` reject under mypy even when the series is known numeric and
non-empty. These helpers perform the runtime check the annotation demands and
hand mypy the narrow type. On a null or non-numeric scalar they raise
``TypeError``, the same failure ``int(None)`` would produce, with a clearer
message.
"""

from __future__ import annotations

from decimal import Decimal

__all__ = ["scalar_float", "scalar_int"]

#: Scalar types a numeric polars aggregation can actually produce.
_NUMERIC = (int, float, Decimal)


def scalar_int(value: object) -> int:
    """Return a polars aggregation scalar as ``int``.

    Args:
        value: The scalar returned by a Series aggregation.

    Returns:
        The value as an ``int``.

    Raises:
        TypeError: If the value is null or not numeric.
    """
    if isinstance(value, _NUMERIC):
        return int(value)
    raise TypeError(f"Expected a numeric scalar, got {value!r}")


def scalar_float(value: object) -> float:
    """Return a polars aggregation scalar as ``float``.

    Args:
        value: The scalar returned by a Series aggregation.

    Returns:
        The value as a ``float``.

    Raises:
        TypeError: If the value is null or not numeric.
    """
    if isinstance(value, _NUMERIC):
        return float(value)
    raise TypeError(f"Expected a numeric scalar, got {value!r}")
