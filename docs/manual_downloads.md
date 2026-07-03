# Manual Download Guide

This document provides instructions for data sources that require manual download due to registration requirements, institutional access, or terms of service restrictions.

## Overview

Most data sources in this package support automated downloads. However, some sources require manual intervention:

| Data Source | Reason | Registration Required | Cost |
|-------------|--------|----------------------|------|
| **Fannie Mae (FNMA)** | Terms of Service | Yes (free) | Free for research |
| **Freddie Mac (FHLMC)** | Terms of Service | Yes (free) | Free for non-commercial |
| **UMBS** | Via FNMA/FHLMC | See above | See above |
| **Ginnie Mae (GNMA)** | Authentication | Yes (free) | Free |

---

## Fannie Mae Single-Family Loan Performance Data

### Access Requirements

- **Registration**: Required (free account)
- **Platform**: Data Dynamics
- **Terms**: Prohibits redistribution without written consent; research/academic use permitted

### How to Download

1. **Visit the Data Portal**

   Go to: https://capitalmarkets.fanniemae.com/credit-risk-transfer/single-family-credit-risk-transfer/fannie-mae-single-family-loan-performance-data

2. **Create an Account**

   - Click "Access the Data" or similar link to Data Dynamics
   - Register with email and create a password
   - Accept Terms and Conditions

3. **Download Options**

   - **Full Dataset**: Use "Entire Single Family Dataset" grid for one-click download of all acquisition and performance files
   - **By Quarter**: Download individual quarterly files
   - **API Access**: Available for programmatic access after registration

4. **Place Files**

   Download files to: `data/fnma/raw/`

   Expected structure:
   ```
   data/fnma/raw/
   ├── Acquisition_*.txt
   └── Performance_*.txt
   ```

### Dataset Details

- **Coverage**: 30-year and less, fully amortizing, fixed-rate single-family mortgages
- **Excludes**: ARMs, balloon, interest-only, government-insured, HARP, non-standard loans
- **Update Frequency**: Quarterly (on or after the 20th following quarter-end)
- **Fields**: 110 fields as of 2024

### Support

For questions: Visit the Fannie Mae Capital Markets website or contact through their support portal.

---

## Freddie Mac Single-Family Loan-Level Dataset

### Access Requirements

- **Registration**: Required (free account)
- **Platform**: Clarity Data Intelligence
- **Terms**: Free for non-commercial, academic/research use; licensing required for commercial redistribution

### How to Download

1. **Visit the Data Portal**

   Go to: https://www.freddiemac.com/research/datasets/sf-loanlevel-dataset

2. **Register for Clarity Data Intelligence**

   - Click on the Clarity Data Intelligence link
   - Or go directly to: https://capitalmarkets.freddiemac.com/clarity
   - Create a free account
   - Accept Terms and Conditions

3. **Navigate to Downloads**

   - Sign in to Clarity
   - Go to the SFLLD (Single-Family Loan-Level Dataset) Data Download page
   - Choose your download format:
     - **Full Dataset**: All historical data
     - **Standard Dataset by Year**: Annual files
     - **Non-Standard Dataset**: Special loan types
     - **Sample Files**: For testing

4. **Place Files**

   Download files to: `data/fhlmc/raw/`

   Expected structure:
   ```
   data/fhlmc/raw/
   ├── historical_data_*.txt      # Standard dataset
   ├── historical_data_time_*.txt # Time series
   └── sample_*.txt               # Sample files
   ```

### Dataset Details

- **Coverage**: ~54.8 million mortgages (1999-2024)
- **Datasets Available**:
  - Standard Dataset (fixed-rate, fully amortizing)
  - Non-Standard Dataset (ARMs, balloons, etc.)
  - RPL (Re-Performing Loans) Mapping File
- **Update Frequency**: Quarterly

### Support

For technical issues: SF_LLD_Technology_Support@freddiemac.com

---

## UMBS (Uniform Mortgage-Backed Securities)

### About UMBS Data

UMBS are passthrough securities issued by both Fannie Mae and Freddie Mac since June 2019 as part of the Single Security Initiative. UMBS data is available through both enterprises' platforms.

### Access Options

#### Option 1: Through Fannie Mae

- **Portal**: https://capitalmarkets.fanniemae.com/mortgage-backed-securities/single-family-mbs
- **Registration**: Same as Fannie Mae loan data (see above)
- **Data Available**: MBS issuance, monthly disclosures, supplemental information

#### Option 2: Through Freddie Mac

- **Portal**: https://capitalmarkets.freddiemac.com/mbs/products/umbs
- **Registration**: Same as Freddie Mac loan data (see above)
- **Data Available**: CRT issuance, monthly disclosures, MBS information

### Place Files

Download files to: `data/umbs/raw/`

---

## Ginnie Mae Disclosure Data

### Access Requirements

- **Registration**: Required for bulk downloads (free)
- **Platform**: Ginnie Mae Bulk Download Portal

### How to Download

1. **Visit the Bulk Download Portal**

   Go to: https://bulk.ginniemae.gov/

2. **Create a Download Profile** (if needed)

   - Visit: https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/test_file_download.aspx
   - Register for access

3. **Available Data**

   - Daily, weekly, factor, and monthly disclosure information
   - MBS data (Single Family, Multifamily, Multi-class)
   - HMBS (HECM-backed Securities) data
   - REMIC and Platinum security data

4. **File Layouts**

   Reference layouts at: https://www.ginniemae.gov/data_and_reports/disclosure_data/Pages/bulk_data_download_layout.aspx

### Note on Automated Downloads

The `mortgage-data gnma download` command supports automated downloads but requires credentials. Configure your credentials before use:

```bash
# Set credentials (email and user ID)
mortgage-data gnma download all monthly
# You will be prompted for credentials on first run
```

### Support

For questions: InvestorInquiries@hud.gov

---

## Quick Reference: All Data Sources

### Fully Automated (No Registration)

| Source | CLI Command | Notes |
|--------|-------------|-------|
| HMDA | `mortgage-data hmda download --min-year 2020 --max-year 2024` | CFPB public data |
| FHA | `mortgage-data fha download` | HUD public data |
| FHFA | `mortgage-data fhfa download all` | Public PUDB data |

### Registration Required (Free)

| Source | CLI Command | Registration |
|--------|-------------|--------------|
| GNMA | `mortgage-data gnma download all monthly` | Email + User ID |
| FNMA | Manual download | Data Dynamics account |
| FHLMC | Manual download | Clarity Data Intelligence account |
| UMBS | Manual download | Via FNMA or FHLMC |

---

## After Manual Download

Once you've downloaded files manually, use the import commands to process them:

```bash
# Fannie Mae
mortgage-data fnma bronze

# Freddie Mac
mortgage-data fhlmc import bronze

# UMBS
mortgage-data umbs bronze
```

These commands will convert the raw files to the bronze (Parquet) format for efficient analysis.
