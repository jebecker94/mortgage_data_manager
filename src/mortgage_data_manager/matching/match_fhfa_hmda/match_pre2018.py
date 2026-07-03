"""FHFA pre-2018 matching workflow (2 rounds)."""

from __future__ import annotations

from pathlib import Path

import polars as pl

from mortgage_data_manager.core.logging import get_logger
from mortgage_data_manager.matching.match_fhfa_hmda.utils import (
    assign_pre2018_hmda_index,
)

logger = get_logger(__name__)


class FHFAPre2018Workflow:
    """Orchestrates the FHFA pre-2018 matching process (2 rounds).

    This workflow implements matching between HMDA and FHFA for years 2009-2017
    using a 2-round approach:

    - **Round 1 (same-year, exact income)**: HMDA[Y] × FHFA[Y], merge on
      tract + loan_type + exact income, asymmetric loan-amount tolerance.
      HMDA purchaser_type ∉ {0, 2, 4} — drops loans HMDA reported as not
      sold (pt=0), Ginnie (pt=2), and Farmer Mac (pt=4).
    - **Round 2 (1-year lag, AMFI-deflated income)**: HMDA[Y-1] × FHFA[Y]
      residual after R1. FHFA's `borrower_annual_income` is AMFI-inflated
      for prior-year originations (per 2016 SF File C dictionary, Field 11),
      so we deflate FHFA income to origination year using
      `local_area_median_income` × `AMFI_orig / AMFI_acq`, rounded to the
      nearest $1k, then require ±$1k tolerance vs HMDA income. HMDA
      purchaser_type ∉ {1, 2, 3, 4} — drops same-year direct GSE sales
      (pt=1 FNMA, pt=3 FHLMC, which would be in FHFA[Y-1] not FHFA[Y]).
    """

    # FHFA silver → canonical raw-CSV column-name mapping. Keeping the
    # canonical names lets the downstream R1/R2 logic stay legible
    # (and consistent with the public 2016 SF File C dictionary).
    _FHFA_RENAME = {
        "borrower_annual_income": "Borrower(s) Annual Income",
        "enterprise_flag": "Enterprise Flag",
        "record_number": "Record Number",
        "upb_acquisition": "Acquisition Unpaid Principal Balance (UPB)",
        "loan_purpose": "Purpose of Loan",
        "occupancy_code": "Occupancy Code",
        "property_type": "Property Type",
        "borrower_sex": "Borrower Gender",
        "co_borrower_sex": "Co-Borrower Gender",
        "borrower_race_1": "Borrower Race or National Origin 1",
        "borrower_ethnicity": "Borrower Ethnicity",
        "number_of_borrowers": "Number of Borrowers",
        "federal_guarantee": "Federal Guarantee",
        "msa_code": "Metropolitan Statistical Area (MSA) Code",
        "local_area_median_income": "Local Area Median Income",
    }

    def __init__(
        self,
        data_folder: Path | str,
        match_folder: Path | str,
        min_year: int = 2009,
        max_year: int = 2017,
        strict_purchaser_type: bool = True,
    ):
        """Initialize FHFA pre-2018 workflow.

        Args:
            data_folder: Project data root containing `fhfa/silver/sf_c/sf_c_{year}.parquet`
                and `hmda/silver/loans/period_2007_2017/activity_year={year}/file_type=d/`.
            match_folder: Folder where cleaned data and matches will be saved.
            min_year: Minimum year to include. Default 2009 (oldest year with both
                HMDA silver and FHFA silver coverage). Note that pre-2012
                uses 2000 Census tract codes; HMDA and FHFA both follow that
                convention, so same-year joins are clean within each Census
                era. The R2 1-year-lag step at the 2011/2012 boundary may
                lose some matches due to the tract-code change.
            max_year: Maximum year to include (default 2017, last pre-2018 year).
            strict_purchaser_type: If True (default), R1/R2 (same-year) require HMDA
                purchaser_type ∉ {0, 2, 4}. If False, only {2, 4} are
                dropped; pt=0 (loans HMDA marks "not sold in calendar year")
                are retained. Empirically the pt=0 same-year subset has
                ~1-2pp lower demographic agreement than the rest of R1 —
                slightly noisier but mostly genuine matches. Toggle off if
                recall matters more than precision.
        """
        self.data_folder = Path(data_folder)
        self.match_folder = Path(match_folder)
        self.min_year = min_year
        self.max_year = max_year
        self.strict_purchaser_type = strict_purchaser_type

        # Ensure match folder exists
        self.match_folder.mkdir(parents=True, exist_ok=True)

    def clean_fhfa_data(self, force: bool = False) -> None:
        """Clean FHFA data for pre-2018 matching, drawing from FHFA silver.

        Reads each year's `fhfa/silver/sf_c/sf_c_{year}.parquet`,
        constructs `census_string` (state_code + county + census_tract),
        renames silver snake_case columns to the canonical raw-CSV names
        used by R1/R2 logic, and writes a combined parquet.

        FHFA silver already filters to enterprise_flag ∈ {1, 2} (Fannie
        and Freddie) and applies missing-value cleanups.

        Args:
            force: If False, skip if output file already exists.
        """
        output_file = self.match_folder / "fhfa_pre2018_round1.parquet"

        if output_file.exists() and not force:
            logger.info(f"FHFA cleaned data already exists: {output_file}")
            return

        logger.info(
            f"Cleaning FHFA silver (sf_c) for years {self.min_year}-{self.max_year}"
        )

        silver_root = self.data_folder / "fhfa" / "silver" / "sf_c"
        frames: list[pl.LazyFrame] = []
        for year in range(self.min_year, self.max_year + 1):
            f = silver_root / f"sf_c_{year}.parquet"
            if not f.exists():
                logger.warning(f"FHFA silver missing for {year}: {f}")
                continue
            lf = pl.scan_parquet(f).filter(pl.col("enterprise_flag").is_in([1, 2]))
            lf = lf.with_columns(
                (
                    pl.col("state_code").cast(pl.Utf8).str.zfill(2)
                    + pl.col("county").cast(pl.Utf8).str.zfill(3)
                    + pl.col("census_tract").cast(pl.Utf8).str.zfill(6)
                ).alias("census_string")
            )
            # Cast numeric fields to Int64 so they join cleanly against
            # HMDA silver (which uses Int64 for amount/income). FHFA
            # silver stores `borrower_annual_income` as Float64.
            int_cols = {
                "borrower_annual_income", "upb_acquisition",
                "local_area_median_income",
            }
            select_cols = [
                pl.col("year"),
                pl.col("census_string"),
            ] + [
                (pl.col(src).cast(pl.Int64) if src in int_cols else pl.col(src)).alias(dst)
                for src, dst in self._FHFA_RENAME.items()
            ]
            frames.append(lf.select(select_cols))

        if not frames:
            raise FileNotFoundError(
                f"No FHFA silver files found under {silver_root} for "
                f"years {self.min_year}-{self.max_year}."
            )

        combined = pl.concat(frames, how="diagonal_relaxed").collect(engine="streaming")
        combined.write_parquet(output_file)
        logger.info(
            f"Saved cleaned FHFA data ({len(combined):,} rows) to {output_file}"
        )

    def clean_hmda_data(self, force: bool = False) -> None:
        """Clean HMDA data for pre-2018 matching, drawing from HMDA silver.

        Reads each year's
        `hmda/silver/loans/period_2007_2017/activity_year={year}/file_type=d/*.parquet`,
        filters to originated loans (`action_taken=1`) not sold to Ginnie
        or Farmer Mac (`purchaser_type ∉ {2, 4}`), drops non-match
        columns, and writes a combined parquet.

        Args:
            force: If False, skip if output file already exists.
        """
        output_file = self.match_folder / "hmda_pre2018_round1.parquet"

        if output_file.exists() and not force:
            logger.info(f"HMDA cleaned data already exists: {output_file}")
            return

        logger.info(
            f"Cleaning HMDA silver (period_2007_2017, file_type=d) for "
            f"years {self.min_year}-{self.max_year}"
        )

        silver_root = (
            self.data_folder / "hmda" / "silver" / "loans" / "period_2007_2017"
        )
        drop_cols = [
            "denial_reason_1", "denial_reason_2", "denial_reason_3",
            "action_taken", "msa_md", "state_code", "county_code",
            "edit_status", "application_date_indicator",
            "rate_spread", "hoepa_status", "lien_status",
            "preapproval", "tract_population",
            "tract_minority_population_percent",
            "ffiec_msa_md_median_family_income",
            "tract_to_msa_income_percentage",
            "tract_owner_occupied_units",
            "tract_one_to_four_family_units",
            "file_type",
        ]
        # R2 needs HMDA[Y-1] for the lowest acq year, so include
        # min_year-1 if it's available — otherwise R2 silently skips
        # the earliest year.
        years = list(range(self.min_year - 1, self.max_year + 1))
        frames: list[pl.LazyFrame] = []
        for year in years:
            base = silver_root / f"activity_year={year}" / "file_type=d"
            files = sorted(base.rglob("*.parquet")) if base.exists() else []
            if not files:
                if year >= self.min_year:
                    logger.warning(f"HMDA silver missing for {year}: {base}")
                continue
            lf = pl.concat(
                [pl.scan_parquet(f) for f in files], how="diagonal_relaxed"
            )
            lf = lf.filter(
                (pl.col("action_taken") == 1)
                & ~pl.col("purchaser_type").is_in([2, 4])
            )
            # Ensure a stable HMDAIndex on every year (persisted for 2017,
            # synthesized from the unique triple for 2007-2016) so the combined
            # frame has one reliable HMDA loan key — no synthetic row index.
            lf = assign_pre2018_hmda_index(lf)
            # HMDAIndex is the first column in 2017 silver (the row-index column)
            # but is appended for the synthesized years; pin it first everywhere
            # so the per-year frames share one column order for the vertical concat.
            keep = ["HMDAIndex"] + [
                c
                for c in lf.collect_schema().names()
                if c not in drop_cols and c != "HMDAIndex"
            ]
            frames.append(lf.select(keep))

        if not frames:
            raise FileNotFoundError(
                f"No HMDA silver partitions found under {silver_root}."
            )

        combined = pl.concat(frames, how="diagonal_relaxed").collect(engine="streaming")
        combined.write_parquet(output_file)
        n_dup = combined.height - combined.unique(subset=["HMDAIndex"]).height
        if n_dup:
            logger.warning(
                f"HMDA HMDAIndex is not unique: {n_dup:,} duplicate rows in the "
                f"combined pre-2018 frame — downstream 1:1 dedup may be affected."
            )
        logger.info(
            f"Saved cleaned HMDA data ({len(combined):,} rows) to {output_file}"
        )

    def clean_data(self, force: bool = False) -> None:
        """Clean both HMDA and FHFA data.

        Args:
            force: If False, skip if output files already exist.
        """
        self.clean_fhfa_data(force=force)
        self.clean_hmda_data(force=force)

    def match_round1(self) -> pl.DataFrame:
        """Execute Round 1: Exact match on income, asymmetric on loan amount.

        Merge on: census tract, loan type, exact income
        Filters:
            - loan amount: 0 <= HMDA - FHFA <= max($2k, 1% × HMDA), reflecting
              the physical model that Acquisition UPB ≤ origination amount
              after paydown, and that paydown scales with loan size
            - purpose, gender, occupancy, property type asymmetric exclusions

        Returns:
            Round 1 matches.
        """
        logger.info("Running pre-2018 matching Round 1 (exact income)")

        # Load data
        fhfa = pl.read_parquet(self.match_folder / "fhfa_pre2018_round1.parquet")
        hmda = pl.read_parquet(self.match_folder / "hmda_pre2018_round1.parquet")

        # Filter HMDA to valid census tracts and income. When
        # `strict_purchaser_type` is on (default), also drop pt=0
        # ("not originated or not sold in calendar year") — same-year
        # matches against FHFA imply the loan was sold to a GSE that
        # year, contradicting pt=0.
        hmda = hmda.filter(
            pl.col("census_tract").is_not_null()
            & pl.col("loan_type").is_not_null()
            & pl.col("income").is_not_null()
        )
        if self.strict_purchaser_type:
            hmda = hmda.filter(pl.col("purchaser_type") != 0)
        # Restrict to acquisition-year range (R2 needs HMDA[Y-1] from
        # the cleaned table; same-year R1 should not see those).
        hmda = hmda.filter(pl.col("activity_year") >= self.min_year)

        # Both HMDA `income` and FHFA `Borrower(s) Annual Income` are in
        # dollars at $1k granularity in silver, so we can join on income
        # directly without scaling.

        # Merge year by year to reduce memory
        all_matches = []
        for year in range(self.min_year, self.max_year + 1):
            logger.info(f"Processing year {year}...")

            hmda_year = hmda.filter(pl.col("activity_year") == year)
            fhfa_year = fhfa.filter(pl.col("year") == year)

            # Merge on census tract, loan type, and exact income.
            df = hmda_year.join(
                fhfa_year,
                left_on=["census_tract", "loan_type", "income"],
                right_on=["census_string", "Federal Guarantee", "Borrower(s) Annual Income"],
                how="inner"
            )

            # Purchaser/Enterprise match
            df = df.filter(
                ~((pl.col("Enterprise Flag") == 1) & (pl.col("purchaser_type") == 3))
            ).filter(
                ~((pl.col("Enterprise Flag") == 2) & (pl.col("purchaser_type") == 1))
            )

            # Loan purpose match
            df = df.filter(
                ~((pl.col("Purpose of Loan") == 1) & pl.col("loan_purpose").is_in([2, 3]))
            ).filter(
                ~((pl.col("Purpose of Loan") == 2) & (pl.col("loan_purpose") == 1))
            )

            # Asymmetric loan-amount tolerance scaled with loan size.
            # Acquisition UPB ≤ origination amount (post-paydown), so
            # diff = HMDA - FHFA ≥ 0 by physical construction. Paydown
            # is roughly proportional to balance, so upper bound is
            # max($2k, 1% × HMDA) — the floor handles small loans where
            # 1% < $1k rounding noise. See investigation
            # `investigation_hmda_fhfa_amount_pct_2026-05-08`.
            #
            # Silver stores `loan_amount` and UPB in dollars (no $1k
            # scaling needed).
            hmda_dollars = pl.col("loan_amount")
            diff = hmda_dollars - pl.col("Acquisition Unpaid Principal Balance (UPB)")
            upper = pl.max_horizontal(
                pl.lit(2000),
                (0.01 * hmda_dollars).cast(pl.Int64),
            )
            df = df.filter((diff >= 0) & (diff <= upper))

            all_matches.append(df)

        # Combine all years
        df = pl.concat(all_matches, how="diagonal")

        # Gender matching
        df = df.filter(
            ~((pl.col("co_applicant_sex") == 5) & pl.col("Co-Borrower Gender").is_in([1, 2]))
        ).filter(
            ~(pl.col("co_applicant_sex").is_in([1, 2]) & pl.col("Co-Borrower Gender").is_null())
        )

        # Occupancy matching
        df = df.filter(
            ~((pl.col("occupancy_type") == 1) & (pl.col("Occupancy Code") == 2))
        ).filter(
            ~((pl.col("occupancy_type") == 3) & (pl.col("Occupancy Code") == 1))
        )

        # Property type matching
        df = df.filter(
            ~((pl.col("property_type") == 1) & (pl.col("Property Type") == 2))
        ).filter(
            ~((pl.col("property_type") == 2) & (pl.col("Property Type") == 1))
        )

        # (Lower-bound check `loan_amount * 1000 ≥ UPB - 1000` removed —
        # subsumed by the asymmetric `diff ≥ 0` filter applied earlier
        # in the year loop.)

        # Keep unique matches
        df = df.with_columns([
            pl.col("Enterprise Flag").count().over(["year", "Enterprise Flag", "Record Number"]).alias("CountFHFA"),
            pl.col("Enterprise Flag").count().over("HMDAIndex").alias("CountHMDA")
        ]).filter(
            (pl.col("CountFHFA") == 1) & (pl.col("CountHMDA") == 1)
        )

        # Save crosswalk
        crosswalk = df.select([
            "year", "Enterprise Flag", "Record Number",
            "activity_year", "respondent_id", "agency_code", "sequence_number",
            "HMDAIndex",
        ]).with_columns(pl.lit(1, dtype=pl.UInt8).alias("match_round"))
        crosswalk.write_parquet(self.match_folder / "hmda_fhfa_matches_pre2018_round1.parquet")

        logger.info(f"Round 1 complete: {len(crosswalk):,} matches")
        return crosswalk

    def _build_amfi_panel(self, fhfa: pl.DataFrame) -> pl.DataFrame:
        """Build (msa_code, year) → AMFI panel from FHFA cleaned data.

        Picks the modal `Local Area Median Income` per (msa, year) so a
        small number of bad/sentinel rows don't drop the MSA. Drops
        sentinels (>= 9_999_999, <= 0).

        Args:
            fhfa: FHFA cleaned data (must include `Metropolitan Statistical
                Area (MSA) Code`, `year`, `Local Area Median Income`).

        Returns:
            Columns: `msa_code`, `year`, `amfi`.
        """
        msa_col = "Metropolitan Statistical Area (MSA) Code"
        amfi_col = "Local Area Median Income"

        panel = (
            fhfa
            .select(
                pl.col(msa_col).cast(pl.Utf8).alias("msa_code"),
                pl.col("year"),
                pl.col(amfi_col).cast(pl.Int64).alias("amfi"),
            )
            .filter(
                pl.col("amfi").is_not_null()
                & (pl.col("amfi") < 9_999_999)
                & (pl.col("amfi") > 0)
            )
        )

        return (
            panel
            .group_by(["msa_code", "year", "amfi"])
            .agg(pl.len().alias("n"))
            .sort(["msa_code", "year", "n"], descending=[False, False, True])
            .group_by(["msa_code", "year"], maintain_order=True)
            .agg(pl.col("amfi").first())
            .select(["msa_code", "year", "amfi"])
        )

    def match_round2(self, round1_crosswalk: pl.DataFrame) -> pl.DataFrame:
        """Execute Round 2: 1-year-lag prior-year recovery with AMFI deflation.

        FHFA `Borrower(s) Annual Income` is AMFI-inflated for prior-year
        originations (per 2016 SF File C dictionary, Field 11). To match
        prior-year HMDA we deflate FHFA income to origination year using
        the MSA `Local Area Median Income` ratio, round to the nearest
        $1k (FHFA's own rounding granularity), then require ±$1k tolerance
        vs HMDA income.

        Merge on: census tract, loan type. Purchaser_type filter:
        HMDA pt ∉ {1, 2, 3, 4} — direct-GSE-sale loans (pt=1, 3) belong
        to FHFA[Y-1] not FHFA[Y]; Ginnie/Farmer Mac (pt=2, 4) never reach
        Enterprise files.

        See `investigation_fhfa_amfi_deflation_2026-05-08.md` for the
        validation that the round-to-$1k-after-scaling deflation reverses
        FHFA's published value to within $1k for 91% of true prior-year
        matches (and within $2k for 99%).

        The numbering follows the previous 3-round design (R2 was retired
        because it added <1% recall at slightly lower precision than R1).

        Args:
            round1_crosswalk: Round 1 matches to exclude from the FHFA residual.

        Returns:
            Combined crosswalk from rounds 1 and 3.
        """
        logger.info("Running pre-2018 matching Round 2 (1-year lag, AMFI-deflated income)")

        fhfa_path = self.match_folder / "fhfa_pre2018_round1.parquet"
        hmda_path = self.match_folder / "hmda_pre2018_round1.parquet"

        # Build (msa_code, year) → AMFI lookup. Small (<1000 rows).
        amfi_panel = self._build_amfi_panel(pl.read_parquet(fhfa_path))

        # Anti-join key for already-matched FHFA records.
        matched_fhfa = round1_crosswalk.select(
            ["year", "Enterprise Flag", "Record Number"]
        )

        msa_col = "Metropolitan Statistical Area (MSA) Code"
        all_matches: list[pl.DataFrame] = []
        # R2 starts at min_year+1 (need HMDA[Y-1] which the loader covers).
        for year in range(self.min_year + 1, self.max_year + 1):
            logger.info(f"Processing year {year} (R2, lag=1)...")

            # Lazy frames per year — push filters down to disk reads.
            fhfa_year_lf = (
                pl.scan_parquet(fhfa_path)
                .filter(pl.col("year") == year)
                .join(
                    matched_fhfa.lazy(),
                    on=["year", "Enterprise Flag", "Record Number"],
                    how="anti",
                )
                .with_columns(pl.col(msa_col).cast(pl.Utf8).alias("__msa_str"))
                .join(
                    amfi_panel.filter(pl.col("year") == year - 1)
                    .select(pl.col("msa_code"),
                            pl.col("amfi").alias("__amfi_orig")).lazy(),
                    left_on="__msa_str",
                    right_on="msa_code",
                    how="left",
                )
                .filter(
                    pl.col("__amfi_orig").is_not_null()
                    & pl.col("Local Area Median Income").is_not_null()
                    & (pl.col("Local Area Median Income") > 0)
                )
            )
            hmda_prior_lf = (
                pl.scan_parquet(hmda_path)
                .filter(
                    (pl.col("activity_year") == year - 1)
                    & pl.col("census_tract").is_not_null()
                    & pl.col("loan_type").is_not_null()
                    & pl.col("income").is_not_null()
                    & ~pl.col("purchaser_type").is_in([1, 3])
                )
            )

            df_lf = hmda_prior_lf.join(
                fhfa_year_lf,
                left_on=["census_tract", "loan_type"],
                right_on=["census_string", "Federal Guarantee"],
                how="inner",
            )

            # Asymmetric loan-amount tolerance (same as R1). Silver values
            # are in dollars at $1k granularity.
            hmda_dollars = pl.col("loan_amount")
            diff = hmda_dollars - pl.col("Acquisition Unpaid Principal Balance (UPB)")
            upper = pl.max_horizontal(
                pl.lit(2000),
                (0.01 * hmda_dollars).cast(pl.Int64),
            )
            df_lf = df_lf.filter((diff >= 0) & (diff <= upper))

            # Production exclusion filters (same as R1).
            df_lf = (
                df_lf
                .filter(~((pl.col("Enterprise Flag") == 1) & (pl.col("purchaser_type") == 3)))
                .filter(~((pl.col("Enterprise Flag") == 2) & (pl.col("purchaser_type") == 1)))
                .filter(~((pl.col("Purpose of Loan") == 1) & pl.col("loan_purpose").is_in([2, 3])))
                .filter(~((pl.col("Purpose of Loan") == 2) & (pl.col("loan_purpose") == 1)))
                .filter(~((pl.col("co_applicant_sex") == 5) & pl.col("Co-Borrower Gender").is_in([1, 2])))
                .filter(~(pl.col("co_applicant_sex").is_in([1, 2]) & pl.col("Co-Borrower Gender").is_null()))
                .filter(~((pl.col("occupancy_type") == 1) & (pl.col("Occupancy Code") == 2)))
                .filter(~((pl.col("occupancy_type") == 3) & (pl.col("Occupancy Code") == 1)))
                .filter(~((pl.col("property_type") == 1) & (pl.col("Property Type") == 2)))
                .filter(~((pl.col("property_type") == 2) & (pl.col("Property Type") == 1)))
            )

            # AMFI deflation. Both FHFA and HMDA income are in dollars at
            # $1k granularity in silver. Deflate FHFA to origination year
            # (round-to-$1k AFTER scaling, matching FHFA's published
            # rounding procedure), then require ±$1k of HMDA income.
            fhfa_inc = pl.col("Borrower(s) Annual Income").cast(pl.Float64)
            scale = (
                pl.col("__amfi_orig").cast(pl.Float64)
                / pl.col("Local Area Median Income").cast(pl.Float64)
            )
            deflated_dollars = (
                (fhfa_inc * scale / 1000.0).round(0) * 1000
            ).cast(pl.Int64)
            df_lf = df_lf.filter(
                (deflated_dollars - pl.col("income").cast(pl.Int64)).abs() <= 1000
            )

            # Materialize candidates for this year via streaming engine.
            year_df = df_lf.collect(engine="streaming")

            # 1:1 dedup within this acq year (sufficient because each
            # FHFA loan only appears in one acq year).
            year_df = year_df.with_columns([
                pl.len().over(["Enterprise Flag", "Record Number"]).alias("CountFHFA"),
                pl.len().over("HMDAIndex").alias("CountHMDA"),
            ]).filter(
                (pl.col("CountFHFA") == 1) & (pl.col("CountHMDA") == 1)
            )

            logger.info(f"  R2 year {year}: {len(year_df):,} new matches")
            all_matches.append(year_df)

        if not all_matches:
            logger.info("Round 2 complete: 0 new matches (no eligible year-pairs)")
            return round1_crosswalk

        df = pl.concat(all_matches, how="diagonal_relaxed")

        crosswalk2 = df.select([
            "year", "Enterprise Flag", "Record Number",
            "activity_year", "respondent_id", "agency_code", "sequence_number",
            "HMDAIndex",
        ]).with_columns(pl.lit(2, dtype=pl.UInt8).alias("match_round"))

        full_crosswalk = pl.concat([round1_crosswalk, crosswalk2], how="diagonal")
        full_crosswalk.write_parquet(self.match_folder / "hmda_fhfa_matches_pre2018_round2.parquet")

        logger.info(f"Round 2 complete: {len(crosswalk2):,} new matches")
        logger.info(f"Total matches (R1+R2): {len(full_crosswalk):,}")

        return full_crosswalk

    def run_all_rounds(self, clean_first: bool = True) -> pl.DataFrame:
        """Execute both matching rounds sequentially.

        Args:
            clean_first: Whether to clean data before matching.

        Returns:
            Complete crosswalk from rounds 1 and 3.
        """
        if clean_first:
            self.clean_data()

        # Round 1: same-year, exact income
        round1 = self.match_round1()

        # Round 2: 1-year lag, AMFI-deflated income
        full = self.match_round2(round1)

        logger.info("All rounds completed!")
        return full

    def create_final_dataset(
        self,
        output_file: Path | str | None = None
    ) -> pl.DataFrame:
        """Create final matched dataset with all variables.

        Args:
            output_file: If provided, save to this file.

        Returns:
            Complete matched dataset.
        """
        logger.info("Creating final matched dataset")

        # Load crosswalk: prefer the R1+R2 file, fall back to R1-only.
        round2_file = self.match_folder / "hmda_fhfa_matches_pre2018_round2.parquet"
        round1_file = self.match_folder / "hmda_fhfa_matches_pre2018_round1.parquet"
        crosswalk_file = round2_file if round2_file.exists() else round1_file
        crosswalk = pl.read_parquet(crosswalk_file)

        # Load source data
        hmda = pl.read_parquet(self.match_folder / "hmda_pre2018_round1.parquet")
        fhfa = pl.read_parquet(self.match_folder / "fhfa_pre2018_round1.parquet")

        # Merge
        result = (
            crosswalk
            .join(
                hmda,
                on="HMDAIndex",
                how="inner"
            )
            .join(
                fhfa,
                on=["year", "Enterprise Flag", "Record Number"],
                how="inner"
            )
        )

        if output_file:
            result.write_parquet(output_file)
            logger.info(f"Saved final dataset with {len(result):,} matches to {output_file}")

        return result
