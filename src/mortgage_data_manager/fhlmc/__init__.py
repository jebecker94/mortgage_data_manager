"""FHLMC Data Manager - Freddie Mac historical mortgage data processing.

This package provides tools for downloading, processing, and analyzing
Freddie Mac mortgage data through a medallion architecture (raw → bronze → silver).

Quick Start:
    # CLI usage
    $ mortgage-data fhlmc --help
    $ mortgage-data fhlmc schemas list
    $ mortgage-data fhlmc bronze load -t origination -y 2024

    # Library usage
    from mortgage_data_manager.fhlmc.config import FHLMCConfig
    from mortgage_data_manager.fhlmc.schemas import load_all_schemas
"""

from mortgage_data_manager.fhlmc.config import FHLMCConfig
from mortgage_data_manager.fhlmc.schemas import load_all_schemas

__all__ = [
    'FHLMCConfig',
    'load_all_schemas',
]
