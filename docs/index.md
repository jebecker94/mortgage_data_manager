# mortgage-data-manager

Loan-level links between U.S. public mortgage datasets that share no common identifier.

This is a personal research setup, published as is. The main contribution is the matching
workflows: proof-of-concept methods for linking the same mortgage across HMDA, FHA, FHFA,
the GSE and Ginnie Mae loan-level disclosures, UMBS, FHLB and credit-risk-transfer data.
The download and cleaning code is what feeds them.

## Matching reports

Start with the **[matching overview](matching/README.md)**: what links to what, the match
rate of every crosswalk, and the denominator behind each rate. Each report covers the
method, results and validation on held-out fields.

- [hmda_fha](matching/hmda_fha_matching.md): HMDA ↔ FHA endorsements
- [hmda_fhfa](matching/hmda_fhfa_matching.md): HMDA ↔ FHFA public use database
- [hmda_gse](matching/hmda_gse_matching.md): HMDA ↔ Fannie Mae and Freddie Mac loan-level
- [hmda_gnma](matching/hmda_gnma_matching.md): HMDA ↔ Ginnie Mae loan-level
- [hmda_sellers_purchasers](matching/hmda_sellers_purchasers_matching.md): HMDA
  originations ↔ HMDA purchase records
- [hmda_fhlb](matching/hmda_fhlb_matching.md): HMDA ↔ FHLB Acquired Member Assets
- [fhfa_mbs](matching/fhfa_mbs_matching.md): FHFA ↔ GSE loan-level
- [mbs_umbs](matching/mbs_umbs_matching.md): GSE loan-level ↔ UMBS disclosures
- [crt_mbs](matching/crt_mbs_matching.md): credit-risk-transfer reference pools ↔ loan-level
- [fhlb_mbs](matching/fhlb_mbs_matching.md): FHLB AMA ↔ Ginnie Mae loan-level
- [master](matching/master_crosswalk.md): every post-2018 edge, one row per HMDA loan

## Using the code

[Getting started](getting_started.md) covers installation and configuration;
[Manual downloads](manual_downloads.md) lists the sources that must be fetched by hand.
