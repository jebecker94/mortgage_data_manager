"""Hatch build hook: ship the schema text tables into the wheel.

Copies every file under repo-root ``schemas/`` except publisher documents into the
wheel at ``mortgage_data_manager/_schemas/...``, so an installed package can resolve
the tables it reads at runtime (see ``core.config._default_schemas_dir``): the GNMA,
FHLB, FHLMC and FNMA file layouts, the FHFA dictionary CSVs and masters, the Census
MD-to-CBSA crosswalk, and hand-maintained lookups such as the UMBS column mappings and
the FHLB member charter aliases. Publisher documents -- PDFs and
spreadsheets (``.xls``/``.xlsx``) -- are never redistributed: they are gitignored, and
any local copy kept for the schema regeneration flow is left out here.
"""

from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface

#: Publisher-document suffixes kept out of the wheel.
EXCLUDED_SUFFIXES = frozenset({".pdf", ".xls", ".xlsx"})


class CustomBuildHook(BuildHookInterface):
    """Force-include schemas/ text tables (no publisher documents) under _schemas."""

    def initialize(self, version, build_data):
        schemas = Path(self.root) / "schemas"
        if not schemas.is_dir():
            return
        force_include = build_data.setdefault("force_include", {})
        for path in schemas.rglob("*"):
            if path.is_file() and path.suffix.lower() not in EXCLUDED_SUFFIXES:
                rel = path.relative_to(schemas).as_posix()
                force_include[str(path)] = f"mortgage_data_manager/_schemas/{rel}"
