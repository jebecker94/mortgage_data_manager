# mortgage-data-manager

Loan-level links between U.S. public mortgage datasets that share no common identifier.

This is a personal research setup, published as is. The main contribution is the matching
workflows: proof-of-concept methods for linking the same mortgage across HMDA, FHA, FHFA,
the GSE and Ginnie Mae loan-level disclosures, UMBS and FHLB data.
The download and cleaning code is what feeds them.

## Matching reports

Start with the **[matching overview](matching/README.md)**: what links to what, the match
rate of every crosswalk, and the denominator behind each rate. Each report covers the
method, results and validation on held-out fields.

| Crosswalk | Links | Years | Match rate | Report |
|---|---|---|---|---|
| hmda_fha | HMDA ↔ FHA endorsements | 2010–2025 | 91% of HMDA FHA originations (2018–2025) | [method & validation](matching/hmda_fha_matching.md) |
| hmda_fhfa | HMDA ↔ FHFA public use database | 2008–2025 | 97% of HMDA GSE sales (2018–2025) | [method & validation](matching/hmda_fhfa_matching.md) |
| hmda_gse | HMDA ↔ Fannie Mae and Freddie Mac loan-level | 2018–2025 | 92% of HMDA GSE sales | [method & validation](matching/hmda_gse_matching.md) |
| hmda_gnma | HMDA ↔ Ginnie Mae loan-level | 2018–2025 | FHA 79%, VA 57%, USDA 69% | [method & validation](matching/hmda_gnma_matching.md) |
| hmda_sellers_purchasers | HMDA originations ↔ HMDA purchase records | 2007–2025 | 71% of purchase records (2018–2025) | [method & validation](matching/hmda_sellers_purchasers_matching.md) |
| hmda_fhlb | HMDA ↔ FHLB Acquired Member Assets | 2009–2025 | 57–60% of AMA loans | [method & validation](matching/hmda_fhlb_matching.md) |
| fhfa_mbs | FHFA ↔ GSE loan-level | 2018–2025 | 95–96% of FHFA loans | [method & validation](matching/fhfa_mbs_matching.md) |
| mbs_umbs | GSE loan-level ↔ UMBS disclosures | 2019–2025 | 99.6–99.8% of matchable loans | [method & validation](matching/mbs_umbs_matching.md) |
| fhlb_mbs | FHLB AMA ↔ Ginnie Mae loan-level | 2009–2025 | 97% of the issuer's Ginnie Mae loans | [method & validation](matching/fhlb_mbs_matching.md) |
| master | every post-2018 edge, one row per HMDA loan | 2018–2025 | 46.4M loans | [method & validation](matching/master_crosswalk.md) |

## Using the code

[Getting started](getting_started.md) covers installation and configuration;
[Manual downloads](manual_downloads.md) lists the sources that must be fetched by hand.
