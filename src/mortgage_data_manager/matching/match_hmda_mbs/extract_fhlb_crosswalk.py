"""Extract FHLB crosswalk from the HMDA → FHFA → MBS → UMBS matching chain.

Identifies loans sold by Federal Home Loan Banks (primarily FHLB Chicago)
into Fannie Mae MBS pools, then links them back to HMDA origination records.

Filtering strategy:
1. Start with UMBS ILLD loans where Seller Name contains "FEDERAL HOME LOAN BANK"
2. Chain through master crosswalk to get HMDA links
3. Drop LEIs with only 1 matched loan (likely chain errors from non-FHLB lenders)
4. Use Avery lender file to keep only depository institutions (banks, thrifts, CUs)
5. Among depositories, keep those flagged as FHLB members in Avery OR those with
   enough matches (>= MIN_LOANS_WITHOUT_AVERY_FHLB) that we're confident they're
   FHLB members despite not being flagged (e.g., Rogue CU, Southpoint Financial CU)

Output columns:
- HMDAIndex: HMDA loan identifier
- activity_year: HMDA origination year
- lei: Lender LEI from HMDA
- fhfa_year: Year in FHFA data
- enterprise_flag: 1 = FNMA
- record_number: FHFA record identifier
- mbs_loan_id: FNMA MBS loan sequence number
- umbs_loan_id: FNMA UMBS loan identifier
- seller_name: FHLB seller name from UMBS ILLD
- fhfa_hmda_match_round: Match round from FHFA-HMDA probabilistic matching
"""

from __future__ import annotations

import polars as pl

from mortgage_data_manager.core.logging import configure_logging, get_logger
from mortgage_data_manager.matching.match_hmda_fhlb.config import AVERY_FILE_PATH
from mortgage_data_manager.matching.match_hmda_mbs.config import HMDAMBSMatchingConfig as Config

logger = get_logger(__name__)

# Avery TYPE codes for depository institutions
DEPOSITORY_TYPES = {10, 11, 12, 20, 21, 30, 31, 32}

# Minimum loans for a depository to be included without Avery FHLB flag
MIN_LOANS_WITHOUT_AVERY_FHLB = 50


def load_fhlb_umbs_loans() -> pl.DataFrame:
    """Load UMBS ILLD loans sold by Federal Home Loan Banks.

    Returns:
        DataFrame with columns: umbs_loan_id (Utf8), seller_name (Utf8)
    """
    illd_dir = Config.get_umbs_illd_dir("fnma")
    illd_files = sorted(illd_dir.glob("*.parquet"))

    chunks = []
    for f in illd_files:
        df = pl.read_parquet(f, columns=["Loan Identifier", "Seller Name"])
        df = df.filter(pl.col("Seller Name").str.contains("FEDERAL HOME LOAN BANK"))
        if len(df) > 0:
            chunks.append(df)

    fhlb_loans = (
        pl.concat(chunks, how="diagonal")
        .unique(subset=["Loan Identifier"], keep="first")
        .with_columns(pl.col("Loan Identifier").cast(pl.Utf8).alias("umbs_loan_id"))
        .select("umbs_loan_id", pl.col("Seller Name").alias("seller_name"))
    )
    return fhlb_loans


def load_avery_depository_leis() -> pl.DataFrame:
    """Load Avery lender file and return depository LEIs with FHLB membership info.

    Returns:
        DataFrame with columns: lei, avery_type, avery_fhlb
    """
    avery = pl.read_excel(AVERY_FILE_PATH)
    avery_latest = avery.sort("YEAR", descending=True).unique(subset=["LEI"], keep="first")
    return avery_latest.select(
        pl.col("LEI").alias("lei"),
        pl.col("TYPE").alias("avery_type"),
        pl.col("FHLB").alias("avery_fhlb"),
    )


def extract_fhlb_crosswalk() -> pl.DataFrame:
    """Extract the clean FHLB crosswalk using the chain method.

    Returns:
        DataFrame with the full crosswalk for validated FHLB member loans.
    """
    # Step 1: FHLB loans in UMBS
    logger.info("Step 1: Loading FHLB loans from UMBS ILLD...")
    fhlb_umbs = load_fhlb_umbs_loans()
    logger.info(f"  {len(fhlb_umbs):,} unique FHLB loans in UMBS")

    # Step 2: Join with master crosswalk
    logger.info("Step 2: Joining with FNMA master crosswalk...")
    master = pl.read_parquet(
        Config.get_output_path("fnma"),
        columns=["HMDAIndex", "activity_year", "record_number", "mbs_loan_id", "umbs_loan_id"],
    )
    matched = master.join(fhlb_umbs, on="umbs_loan_id", how="inner")
    logger.info(f"  {len(matched):,} loans matched through chain")

    # Step 3: Get LEIs from HMDA
    logger.info("Step 3: Looking up LEIs from HMDA...")
    hmda_parts = []
    for year in sorted(matched["activity_year"].unique().to_list()):
        year_files = sorted(Config.HMDA_SILVER_DIR.glob(f"activity_year={year}/**/*.parquet"))
        target = matched.filter(pl.col("activity_year") == year).select("HMDAIndex")
        for f in year_files:
            df = pl.read_parquet(f, columns=["HMDAIndex", "lei"])
            df = df.join(target, on="HMDAIndex", how="semi")
            if len(df) > 0:
                hmda_parts.append(df)

    hmda_leis = pl.concat(hmda_parts, how="diagonal")
    matched = matched.join(hmda_leis, on="HMDAIndex", how="left")
    logger.info(f"  {matched.filter(pl.col('lei').is_not_null())['lei'].n_unique()} unique LEIs")

    # Step 4: Filter — drop single-loan LEIs
    logger.info("Step 4: Filtering to validated FHLB members...")
    lei_counts = matched.group_by("lei").agg(pl.len().alias("n_fhlb_loans"))

    single_leis = lei_counts.filter(pl.col("n_fhlb_loans") == 1)
    logger.info(f"  Dropping {len(single_leis)} single-loan LEIs (likely chain errors)")

    multi_leis = lei_counts.filter(pl.col("n_fhlb_loans") >= 2)

    # Step 5: Filter — Avery depository + FHLB membership check
    avery = load_avery_depository_leis()
    multi_with_avery = multi_leis.join(avery, on="lei", how="left")

    # Keep: depositories that are FHLB members per Avery
    is_depository = pl.col("avery_type").is_in(list(DEPOSITORY_TYPES))
    is_fhlb_member = pl.col("avery_fhlb").is_not_null()
    has_enough_loans = pl.col("n_fhlb_loans") >= MIN_LOANS_WITHOUT_AVERY_FHLB

    keep = multi_with_avery.filter(is_depository & (is_fhlb_member | has_enough_loans))

    dropped_non_dep = multi_with_avery.filter(~is_depository)
    dropped_no_fhlb = multi_with_avery.filter(is_depository & ~is_fhlb_member & ~has_enough_loans)

    logger.info(
        f"  Dropping {len(dropped_non_dep)} non-depository LEIs ({dropped_non_dep['n_fhlb_loans'].sum()} loans)"
    )
    logger.info(
        f"  Dropping {len(dropped_no_fhlb)} depositories without FHLB flag and <{MIN_LOANS_WITHOUT_AVERY_FHLB} loans ({dropped_no_fhlb['n_fhlb_loans'].sum()} loans)"
    )

    keep_leis = keep.select("lei")
    result = matched.join(keep_leis, on="lei", how="semi")

    # Step 6: Add FHFA fields from FHFA-HMDA crosswalk
    logger.info("Step 5: Adding FHFA crosswalk fields...")
    fhfa_hmda = pl.read_parquet(
        Config.FHFA_HMDA_CROSSWALK,
        columns=["HMDAIndex", "fhfa_year", "enterprise_flag", "match_round"],
    ).rename({"match_round": "fhfa_hmda_match_round"})

    result = result.join(fhfa_hmda, on="HMDAIndex", how="left")

    # Select and order final columns
    result = result.select(
        "HMDAIndex",
        "activity_year",
        "lei",
        "fhfa_year",
        "enterprise_flag",
        "record_number",
        "mbs_loan_id",
        "umbs_loan_id",
        "seller_name",
        "fhfa_hmda_match_round",
    )

    # Summary
    logger.info(f"Final crosswalk: {len(result):,} loans, {result['lei'].n_unique()} LEIs")
    logger.info("By year:")
    for row in (
        result.group_by("activity_year")
        .agg(pl.len().alias("loans"), pl.col("lei").n_unique().alias("leis"))
        .sort("activity_year")
        .iter_rows(named=True)
    ):
        logger.info(f"  {row['activity_year']}: {row['loans']:>6,} loans, {row['leis']:>3} LEIs")

    logger.info("By seller:")
    for row in (
        result.group_by("seller_name")
        .agg(pl.len().alias("loans"))
        .sort("loans", descending=True)
        .iter_rows(named=True)
    ):
        logger.info(f"  {row['seller_name']}: {row['loans']:,}")

    return result


def save_fhlb_crosswalk(df: pl.DataFrame) -> None:
    """Save the FHLB crosswalk to the crosswalk output directory."""
    output_path = Config.HMDA_MBS_CROSSWALK_OUTPUT_DIR / "fhlb_crosswalk_fnma.parquet"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(output_path, compression="zstd")
    logger.info(f"Saved to {output_path}")


if __name__ == "__main__":
    configure_logging(level="INFO")
    crosswalk = extract_fhlb_crosswalk()
    save_fhlb_crosswalk(crosswalk)
