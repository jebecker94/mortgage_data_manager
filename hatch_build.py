"""Hatch build hook: ship non-PDF schema reference data into the wheel.

Copies every non-PDF file under repo-root ``schemas/`` into the wheel at
``mortgage_data_manager/_schemas/...`` so an installed package can resolve the GNMA
layouts, FHFA dictionaries, and Census crosswalk it needs at runtime (see
``core.config._default_schemas_dir``). PDFs are source documentation for the schema
regeneration flow and are left out to keep the wheel lean.
"""

from __future__ import annotations

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    """Force-include schemas/ reference data (sans PDFs) under mortgage_data_manager/_schemas."""

    def initialize(self, version, build_data):
        schemas = Path(self.root) / "schemas"
        if not schemas.is_dir():
            return
        force_include = build_data.setdefault("force_include", {})
        for path in schemas.rglob("*"):
            if path.is_file() and path.suffix.lower() != ".pdf":
                rel = path.relative_to(schemas).as_posix()
                force_include[str(path)] = f"mortgage_data_manager/_schemas/{rel}"
