#!/usr/bin/env python3
"""Utility functions for MBS-FHFA matching."""

from __future__ import annotations


def get_base_path():
    """Get the base path for data based on environment variable or default.

    Set the DATA_BASE_PATH environment variable to override the default.

    Returns:
        Base path for data access
    """
    from decouple import config

    return config("DATA_BASE_PATH", default="/path/to/data")
