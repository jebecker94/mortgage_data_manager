"""State postal abbreviation <-> FIPS code tables.

One hand-authored table (`STATE_TO_FIPS`) and the views derived from it. The 50
states, DC and the five inhabited territories are all present; callers that must exclude
the territories (a BLS series set, a choropleth) filter on `TERRITORIES`.
"""

from __future__ import annotations

__all__ = [
    "STATE_TO_FIPS",
    "FIPS_TO_STATE",
    "FIPS_INT_TO_STATE",
    "TERRITORIES",
]

#: Postal abbreviation -> integer FIPS code (50 states + DC + 5 territories).
STATE_TO_FIPS: dict[str, int] = {
    "AL": 1,
    "AK": 2,
    "AZ": 4,
    "AR": 5,
    "CA": 6,
    "CO": 8,
    "CT": 9,
    "DE": 10,
    "DC": 11,
    "FL": 12,
    "GA": 13,
    "HI": 15,
    "ID": 16,
    "IL": 17,
    "IN": 18,
    "IA": 19,
    "KS": 20,
    "KY": 21,
    "LA": 22,
    "ME": 23,
    "MD": 24,
    "MA": 25,
    "MI": 26,
    "MN": 27,
    "MS": 28,
    "MO": 29,
    "MT": 30,
    "NE": 31,
    "NV": 32,
    "NH": 33,
    "NJ": 34,
    "NM": 35,
    "NY": 36,
    "NC": 37,
    "ND": 38,
    "OH": 39,
    "OK": 40,
    "OR": 41,
    "PA": 42,
    "RI": 44,
    "SC": 45,
    "SD": 46,
    "TN": 47,
    "TX": 48,
    "UT": 49,
    "VT": 50,
    "VA": 51,
    "WA": 53,
    "WV": 54,
    "WI": 55,
    "WY": 56,
    "PR": 72,
    "VI": 78,
    "GU": 66,
    "AS": 60,
    "MP": 69,
}

#: The five inhabited territories: present in the tables, absent from BLS state series
#: and from plotly's ``USA-states`` geometry.
TERRITORIES: frozenset[str] = frozenset({"PR", "VI", "GU", "AS", "MP"})

#: Zero-padded two-character FIPS string -> postal abbreviation.
FIPS_TO_STATE: dict[str, str] = {f"{fips:02d}": state for state, fips in STATE_TO_FIPS.items()}

#: Integer FIPS -> postal abbreviation, for sources that store the state as an int.
FIPS_INT_TO_STATE: dict[int, str] = {fips: state for state, fips in STATE_TO_FIPS.items()}
