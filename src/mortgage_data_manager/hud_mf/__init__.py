"""HUD multifamily housing data (FHA-insured + assisted/Section 8 + portfolio).

Ingests the monthly tabular extracts HUD publishes at
https://www.hud.gov/hud-partners/multifamily-data into the medallion layout
(raw → bronze → silver). Eleven logical tables in two families bridged by one
key:

- **insured** — FHA-insured multifamily mortgages (active / terminated),
  Section 202 direct loans, and insured property addresses; keyed by the
  8-character FHA project number. Active and terminated are disjoint; Section
  202 uses a disjoint alphanumeric project-number namespace.
- **assisted** — Section 8 / assisted-housing contracts, properties, rents,
  renewals, and the active portfolio; keyed by the 9-digit REMS
  ``property_id``.
- **reference** — the Section-of-the-Act (SoA) code lookup.

``property_id`` is the universal spine and ``fha_number`` links the insured
subset (≈21% of assisted properties also carry an FHA-insured mortgage); the
property↔FHA bridge lives in the address file and the active-portfolio FHA
sheet. Distinct from the ``hud`` subpackage (USPS ZIP↔Census crosswalk) and the
``fha`` subpackage (single-family + HECM loan snapshots).

**Snapshots**: every file is a monthly point-in-time extract with no in-band
history. The download layer records each file's ``Last-Modified`` into
``raw/download_manifest.json`` and bronze stamps every row with that
``snapshot_date`` so archived vintages can be stacked into a panel.
"""

from mortgage_data_manager.hud_mf.config import (
    DATASET_MAP,
    VALID_DATASETS,
    HudMfConfig,
)

__all__ = [
    "DATASET_MAP",
    "HudMfConfig",
    "VALID_DATASETS",
]
