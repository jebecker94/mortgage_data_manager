# Manual Downloads

Most sources download with one command. Fannie Mae, Freddie Mac and UMBS data can't
be fetched automatically: each enterprise requires a free account and terms that you
accept in a browser. Ginnie Mae downloads are automatic but need account credentials in
`.env`.

| Source | How to get it | Account | Cost |
|--------|---------------|---------|------|
| HMDA | `mortgage-data hmda download` | None | Free |
| FHA | `mortgage-data fha download` | None | Free |
| FHFA | `mortgage-data fhfa download` | None | Free |
| GNMA | `mortgage-data gnma download monthly` | Ginnie Mae profile (email + secret-question answer) | Free |
| USPS crosswalk | `mortgage-data usps-crosswalk download` | HUD USPS API token (`USPS_CROSSWALK_API_KEY`) | Free |
| FHLB, HUD | `mortgage-data fhlb download`, `mortgage-data hud download` | None | Free |
| FNMA | Manual download | Data Dynamics | Free for research |
| FHLMC | Manual download | Clarity Data Intelligence | Free for non-commercial use |
| UMBS | Manual download | Via FNMA or FHLMC | Free |

After placing a file in a raw directory by hand, record it in the raw-download ledger so
it has the same record (SHA-256, size, content type, note) as an automated download:

```bash
mortgage-data raw register --source <fnma|fhlmc|umbs> --note "<where it came from>"
mortgage-data raw audit     # lists raw files not yet recorded
```

## Fannie Mae Single-Family Loan Performance Data

**Access.** A free Data Dynamics account. The terms allow research and academic use and
prohibit redistribution without Fannie Mae's written consent.

1. Go to
   https://capitalmarkets.fanniemae.com/credit-risk-transfer/single-family-credit-risk-transfer/fannie-mae-single-family-loan-performance-data
   and follow the link to Data Dynamics.
2. Register with an email and password, and accept the terms.
3. Download either the whole dataset ("Entire Single Family Dataset") or individual
   quarterly files, plus the Exclusions and HARP files if you want them.
4. Put the files in `data/fnma/raw/`, keeping Fannie Mae's file names, which the
   importer relies on:

   ```
   data/fnma/raw/
   ├── 2020Q1.zip            # one quarter
   ├── Performance_All.zip   # full history
   ├── Exclusions.zip        # loans left out of the standard dataset
   └── HARP_Files.zip        # the HARP refinance population
   ```

`mortgage-data fnma bronze` imports the per-quarter, Exclusions, HARP and multifamily
files. `Performance_All.zip` is the only source of the history before 2016 but is not
part of the default run because of its size; import it with
`mortgage-data fnma bronze -t performance_all` (`--min-year`/`--max-year` select
quarters).

**Contents.** Fixed-rate, fully amortizing single-family mortgages with terms of 30
years or less. ARMs, balloons, interest-only, government-insured, HARP and other
non-standard loans are left out of the standard dataset; the Exclusions and HARP files
carry some of them. Fannie Mae updates the files quarterly.

## Fannie Mae Multifamily Loan Performance Data

**Access.** The same free Data Dynamics account; research use permitted, no
redistribution.

1. Go to
   https://capitalmarkets.fanniemae.com/credit-risk-transfer/multifamily-credit-risk-transfer/multifamily-loan-performance-data.
2. Download both files:
   - `Multifamily.zip`: loan performance, one row per loan per month, from 2000-01 on
   - `Multifamily_DSCR.zip`: debt service coverage ratio (DSCR), one row per loan per year
3. Put them in `data/fnma/raw/` under their original names.

```bash
mortgage-data fnma bronze -t multifamily              # parquet per release under bronze/multifamily/
mortgage-data fnma silver -t multifamily --overwrite  # rebuild silver from the latest release
```

**Contents.** Fannie Mae multifamily acquisitions (DUS, Non-DUS, Bulk Delivery, Credit
Facility) from 2000 on. Unlike the single-family files, these are comma-delimited with a
header row, and dollar amounts are strings such as `"$1,234.56"` with negatives in
parentheses. Each quarterly release restates the full history, so silver keeps only the
latest release.

## Freddie Mac Single-Family Loan-Level Dataset

**Access.** A free Clarity Data Intelligence account. Free for non-commercial academic
and research use; commercial redistribution requires a license.

1. Go to https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset and follow the
   link to Clarity, or go directly to https://capitalmarkets.freddiemac.com/clarity.
2. Create an account and accept the terms.
3. Sign in and open the Single-Family Loan-Level Dataset (SFLLD) download page. It
   offers the full dataset, the standard dataset by year, the non-standard dataset
   (ARMs, balloons and other special loan types) and sample files.
4. Put the zip files in `data/fhlmc/raw/` as downloaded; the importer walks every zip
   there, including zips nested inside it, so the full set and per-year files both work:

   ```
   data/fhlmc/raw/
   ├── full_set_standard_historical_data.zip   # standard dataset, all years
   ├── non_std_historical_data.zip             # non-standard dataset
   └── rpl_historical_data.zip                 # re-performing loan mapping
   ```

**Contents.** Single-family mortgages acquired from 1999 on: fixed-rate loans in the
standard dataset, the special loan types in the non-standard dataset, and a
re-performing loan (RPL) mapping file. Updated quarterly. Technical support: SF_LLD_Technology_Support@freddiemac.com.

## UMBS (Uniform Mortgage-Backed Securities)

Since June 2019 Fannie Mae and Freddie Mac have both issued UMBS, under the Single
Security Initiative, and each publishes its own disclosures. The accounts are the same
as for the loan-level data above.

- **Fannie Mae:** https://capitalmarkets.fanniemae.com/mortgage-backed-securities/single-family-mbs
  (MBS issuance, monthly disclosures, supplemental files)
- **Freddie Mac:** https://capitalmarkets.freddiemac.com/mbs/products/umbs
  (CRT issuance, monthly disclosures, MBS information)

Put the files in `data/umbs/raw/{FNMA|FHLMC}/{prefix}/`, one folder per disclosure file
type, keeping the published names (for example
`data/umbs/raw/FNMA/FNM_ILLD/FNM_ILLD_201906.zip`).

## Ginnie Mae Disclosure Data

`mortgage-data gnma download` downloads automatically, but Ginnie Mae's APIs require a
profile token. Register for a free profile at
https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/test_file_download.aspx,
then put two keys in `.env`. There is no interactive prompt:

```bash
# .env
GNMA_EMAIL=you@example.com
GNMA_ANSWER=<the answer to your profile's secret question>
```

The command exchanges them for a `gm_up_token`, caches it, and signs in again when it
expires.

The disclosures cover single-family, multifamily and multiclass MBS, HMBS (securities
backed by reverse mortgages, HECMs), and REMIC and Platinum securities, at daily,
weekly, monthly and factor frequencies. File layouts are at
https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/bulk_data_download_layout.aspx;
questions go to InvestorInquiries@hud.gov.

## Processing downloaded files

Once the files are in place, build bronze and silver:

```bash
mortgage-data fnma pipeline
mortgage-data fhlmc pipeline
mortgage-data umbs pipeline
```
