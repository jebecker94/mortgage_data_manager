# GNMA Notes

## Schema Known Issues and Fixes

This document tracks known issues with Ginnie Mae's published data dictionaries and the fixes applied to make them usable.

### Background

GNMA publishes PDF data dictionaries that define fixed-width file layouts. These PDFs have structural issues that make automated parsing difficult:

1. **Non-record header rows**: Section headers (e.g., "Upfront MIP", "Annual MIP") appear as rows in the PDF tables but are not actual data fields. These provide context for subsequent fields but are lost during PDF table extraction.

2. **Duplicate column names**: Because the contextual headers are lost, fields from different sections can end up with identical names.

---

### Issue: Duplicate MIP Fields in Record Type F (UNRESOLVED)

**Affected schemas**: `nissues_combined_schema.csv`, `monthly_combined_schema.csv`

**Record type**: F (FHA MIP Distribution)

**Status**: Partially fixed. Record type F transformation fails for most files.

**Problem**: The PDF schema has THREE sections of MIP rate buckets, but section headers are lost during extraction:

1. **Upfront MIP** (items 7-27): 100, 125, 150, 175, 200, 225, Not Available
2. **Annual MIP** (items 28-69): 25, 35, 50, 55, 60, 85, 90, 110, 115, 120, 125, 145, 150, Not Available
3. **Life-of-Loan MIP** (items 91+, newer schemas only): 000, 001, 300, 380, 000, Other, 45, 70, 95, 130, 135, 155, 50, 240, 250, 75, 80, 100, 105

This creates multiple duplicate column names:
- `MIP 125` - appears in Upfront (item 10) and Annual (item 58)
- `MIP 150` - appears in Upfront (item 13) and Annual (item 64)
- `MIP Not Available` - appears in Upfront (item 25) and Annual (item 67)
- `MIP 000` - appears twice within Life-of-Loan section (items 91 and 103)
- `MIP 50` - appears in Annual (item 34) and Life-of-Loan (item 127)
- `MIP 100` - appears in Upfront (item 7) and Life-of-Loan (item 142)

**Fix needed**: Rename all duplicates with meaningful suffixes:
- First occurrence: keep as-is (Upfront MIP)
- Second occurrence: add " Annual" suffix
- Third occurrence: add " LoL" suffix (Life-of-Loan)

**Workaround**: Skip record type F when processing nissues/monthly data, or manually fix the schema CSV before transformation.

---

### Future Improvements

1. Enhance PDF table extraction in `gnma/schema/pdf_extraction.py` to detect and preserve section header rows
2. Use section context when generating column names to prevent duplicates
3. Add validation step to detect duplicate column names before writing combined schemas
4. Create a comprehensive schema fix script to rename all MIP duplicates with proper context

---

## Redundant Files: Pool Supplementals

The pool-supplemental files (`monthlySFS`, `nimonSFS`, `hmonthlyS`) are deterministic per-pool stratifications of the corresponding loan-level files (`llmon1`+`llmon2`, `dailyllmni`, `hllmon1`+`hllmon2`). After applying file-family-specific reconciliation rules they reproduce bijectively at the (Pool, group-key) level with 100% count and ≥99% UPB agreement. See `investigations/reports/investigation_gnma_supplemental_reconstruction_2026-05-02.md` for the validation.

These three prefixes are flagged `default_download: false` in `schemas/gnma/prefix_dictionary.yaml` and are skipped when the workflow resolves prefixes from `None`. Code support for the prefixes is preserved — they can still be downloaded and processed by passing them explicitly:

```bash
mortgage-data gnma download data monthlySFS nimonSFS hmonthlyS
```

`hplatmonS` (HMBS Platinum supplemental) was not validated in the same exercise — its reconstruction requires the `platcoll` mapping which is not yet in silver. It remains in the default list pending follow-up.
