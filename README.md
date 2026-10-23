# Mortgage Data Manager

Loan-level links between U.S. public mortgage datasets that share no common identifier.

This is my personal research setup, published as is. The main contribution is the
**matching workflows**: proof-of-concept methods, each with a validation report, for linking
the same mortgage across HMDA, FHA, FHFA, the GSE and Ginnie Mae loan-level disclosures,
UMBS, FHLB and credit-risk-transfer data. The download and cleaning code is what feeds
them. It is written for my machine and my research needs, and it is not a maintained
general-purpose data library.

## The matchers

HMDA is the spine: it covers nearly every origination and is the only source with borrower
demographics, income and lender identity, but it stops at origination. The agency files
carry exact terms, credit scores and performance, but no demographics. Each crosswalk
recovers the link from loan characteristics, keeps only pairs that are unambiguous, and is
checked against fields the match did not use.

| Crosswalk | Links | Years | Match rate |
|---|---|---|---|
| [hmda_fha](docs/matching/hmda_fha_matching.md) | HMDA ↔ FHA endorsements | 2010–2025 | 91% of HMDA FHA originations (2018–2025) |
| [hmda_fhfa](docs/matching/hmda_fhfa_matching.md) | HMDA ↔ FHFA public use database | 2008–2025 | 97% of HMDA GSE sales (2018–2025) |
| [hmda_gse](docs/matching/hmda_gse_matching.md) | HMDA ↔ Fannie Mae and Freddie Mac loan-level | 2018–2025 | 92% of HMDA GSE sales |
| [hmda_gnma](docs/matching/hmda_gnma_matching.md) | HMDA ↔ Ginnie Mae loan-level | 2018–2025 | FHA 79%, VA 57%, USDA 69% |
| [hmda_sellers_purchasers](docs/matching/hmda_sellers_purchasers_matching.md) | HMDA originations ↔ HMDA purchase records | 2007–2025 | 71% of purchase records (2018–2025) |
| [hmda_fhlb](docs/matching/hmda_fhlb_matching.md) | HMDA ↔ FHLB Acquired Member Assets | 2009–2025 | 57–60% of AMA loans |
| [fhfa_mbs](docs/matching/fhfa_mbs_matching.md) | FHFA ↔ GSE loan-level | 2018–2025 | 95–96% of FHFA loans |
| [mbs_umbs](docs/matching/mbs_umbs_matching.md) | GSE loan-level ↔ UMBS disclosures | 2019–2025 | 99.6–99.8% of matchable loans |
| [crt_mbs](docs/matching/crt_mbs_matching.md) | Freddie Mac CRT reference pools ↔ loan-level | 2013–2026 | 99.2% of CRT loans |
| [fhlb_mbs](docs/matching/fhlb_mbs_matching.md) | FHLB AMA ↔ Ginnie Mae loan-level | 2009–2025 | 97% of the issuer's Ginnie Mae loans |
| [master](docs/matching/master_crosswalk.md) | every post-2018 edge, one row per HMDA loan | 2018–2025 | 46.4M loans |

Start with the [matching overview](docs/matching/README.md): what links to what, and the
denominator behind every rate. Each linked page gives the method, results and validation.

The crosswalks themselves are not distributed. They are built from the source data on
your own machine by `mortgage-data match <workflow> run`.

## Using it

Python 3.12+:

```bash
uv pip install -e ".[all]"
mortgage-data info                      # what is configured and what is on disk
mortgage-data hmda pipeline post2018 --min-year 2020 --max-year 2024
mortgage-data match info                # the matching workflows
```

Data paths and credentials go in a `.env` file; see [Getting Started](docs/getting_started.md).
Expect friction: some sources (Fannie Mae, Freddie Mac, UMBS) must be downloaded by hand
under their terms of use ([instructions](docs/manual_downloads.md)), agency websites change
without notice, and building everything takes over 1 TB of disk.

More: [CLI reference](docs/cli_reference.md) · [Architecture](docs/architecture.md) ·
[API reference](docs/api/core.md)

## Development

Developed with [Claude Code](https://claude.ai/code), Anthropic's coding agent, under my
direction and review.

## License

MIT.
