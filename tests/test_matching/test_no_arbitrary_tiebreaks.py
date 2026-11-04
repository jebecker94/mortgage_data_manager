"""Guard: no crosswalk or matcher input rests on an arbitrary tie-break.

A match decided by row order, an unstable sort, ``keep="first"/"last"/"any"``, a tied
plurality, or a "total" id key appended to break a score tie is arbitrary, and so is
keeping one of several source rows that disagree. Ties are dropped (the target goes
unmatched) and disagreeing duplicates are excluded instead.

This test scans the modules that produce crosswalks, gold linkages and matcher inputs
for tie-break-shaped calls and fails on any without a ``# tie-safe: <proof>`` comment
on the call's line or the line above. The comment must say why the pick cannot land on
a tie (one row per key by construction, zero ties at the selected value, ...).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "mortgage_data_manager"

#: Modules whose picks decide a link or a value a matcher compares.
_SCOPE: tuple[Path, ...] = (
    _SRC / "matching",
    _SRC / "combined",
    _SRC / "core" / "reference",
    _SRC / "sources" / "fnma" / "import_silver.py",
    _SRC / "sources" / "fhlmc" / "import_silver.py",
    _SRC / "sources" / "fhfa" / "schemas.py",
)
#: Diagnostics and presentation, which write no crosswalk.
_EXCLUDED_FILES = frozenset({"validation.py", "validation_common.py", "cli.py"})

_MARKER = "# tie-safe:"
#: Methods that return one element of a group or frame.
_PICKS = frozenset(
    {"first", "last", "mode", "arg_max", "arg_min", "is_first_distinct", "is_last_distinct"}
)
_ARBITRARY_KEEP = frozenset({"first", "last", "any"})


def _scoped_files() -> list[Path]:
    files: list[Path] = []
    for entry in _SCOPE:
        candidates = sorted(entry.rglob("*.py")) if entry.is_dir() else [entry]
        files += [p for p in candidates if p.name not in _EXCLUDED_FILES]
    return files


def _keep_value(call: ast.Call) -> str | None:
    for kw in call.keywords:
        if kw.arg == "keep" and isinstance(kw.value, ast.Constant):
            return str(kw.value.value)
    return None


def _is_tie_break(call: ast.Call) -> bool:
    if not isinstance(call.func, ast.Attribute):
        return False
    name = call.func.attr
    keep = _keep_value(call)
    if keep in _ARBITRARY_KEEP:
        return True
    if name in _PICKS:
        return True
    if name in {"unique", "drop_duplicates"}:
        # A whole-row unique keeps nothing arbitrary; a keyed one keeps "any" by default.
        keyed = bool(call.args) or any(kw.arg == "subset" for kw in call.keywords)
        return keyed and keep != "none"
    if name == "head" and call.args:
        first = call.args[0]
        return isinstance(first, ast.Constant) and first.value == 1
    return False


def find_unmarked(source: str) -> list[int]:
    """Line numbers of tie-break-shaped calls with no ``# tie-safe:`` comment.

    Args:
        source: Python source text.

    Returns:
        1-indexed line numbers, one per offending call.
    """
    lines = source.splitlines()
    hits: list[int] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _is_tie_break(node):
            # The method name's line, which for a chained call is not the chain's first line.
            line = node.func.end_lineno or node.lineno
            nearby = lines[max(line - 2, 0) : line]
            if not any(_MARKER in text for text in nearby):
                hits.append(line)
    return sorted(set(hits))


def test_scanner_flags_the_idioms() -> None:
    bad = "\n".join(
        [
            'a = lf.unique(subset=["k"])',
            'b = lf.unique("k", keep="first")',
            "c = lf.sort('s').group_by('k').first()",
            "d = pl.col('x').mode().first()",
            "e = df.head(1)",
            "f = lf.filter(pl.col('k').is_first_distinct())",
        ]
    )
    assert find_unmarked(bad) == [1, 2, 3, 4, 5, 6]


def test_scanner_accepts_safe_forms() -> None:
    ok = "\n".join(
        [
            "a = lf.unique()",
            'b = lf.unique(subset=["k"], keep="none")',
            "# tie-safe: one row per key by construction",
            "c = lf.group_by('k').agg(pl.col('x').first())",
            "d = lf.group_by('k').agg(pl.col('x').first())  # tie-safe: x is constant per k",
        ]
    )
    assert find_unmarked(ok) == []


@pytest.mark.parametrize("path", _scoped_files(), ids=lambda p: str(p.relative_to(_SRC)))
def test_no_unmarked_tie_breaks(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    unmarked = find_unmarked(path.read_text(encoding="utf-8"))
    detail = "\n".join(f"  {path.name}:{n}: {lines[n - 1].strip()}" for n in unmarked)
    assert not unmarked, (
        "tie-break-shaped calls without a '# tie-safe: <proof>' comment -- drop the tie "
        f"or exclude the disagreeing duplicates instead:\n{detail}"
    )
