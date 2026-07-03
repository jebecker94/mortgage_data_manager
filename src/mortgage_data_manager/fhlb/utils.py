"""FHLB utility functions and mappings.

This module contains utility functions for FHLB data processing, including
column name standardization mappings.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

# =============================================================================
# AMA Column Name Standardization
# =============================================================================
# This mapping standardizes all historical column names to the 2019+ naming convention
# based on the 2024 FHLBank Public Use Database field definitions.
#
# Source: data/fhfa/dictionary_files/fhlb/2024-pudb-definitions.pdf
# Effective: January 1, 2019

# Column name mapping: Old Name -> New Name (2019+ standard)
FHLB_COLUMN_MAPPING = {
    # Core identification fields
    'Year': 'Year',  # Consistent across all years
    'LoanNumber': 'LoanCharacteristicsID',  # 2009
    'Loan Number': 'LoanCharacteristicsID',  # 2010-2014 (with space)
    'AssignedID': 'LoanCharacteristicsID',  # 2015-2016, 2018
    'Assigned ID': 'LoanCharacteristicsID',  # 2017 (with space)
    'LoanCharacteristicsID': 'LoanCharacteristicsID',  # 2019+ (already standard)

    # Bank/Institution fields
    'Bank': 'Bank',  # 2017+ and current
    'FHLBank': 'Bank',  # 2015-2016
    'FHLBankID': 'FHLBankID',  # Keep as-is (different from loan ID)
    'FHFBID': 'FHFBID',  # Keep as-is (seller institution ID)

    # Geographic fields
    'FIPSStateCode': 'FIPSStateNumericCode',
    'FIPSStateNumericCode': 'FIPSStateNumericCode',  # Already standard
    'FIPSCountyCode': 'FIPSCountyCode',  # Consistent
    'MSA': 'CoreBasedStatisticalAreaCode',
    'CoreBasedStatisticalAreaCode': 'CoreBasedStatisticalAreaCode',  # Already standard
    'Tract': 'CensusTractIdentifier',
    'CensusTractIdentifier': 'CensusTractIdentifier',  # Already standard
    'FeatureID': 'FeatureID',  # Removed in 2019, keep as-is for historical

    # Census/Income fields
    'MinPer': 'CensusTractMinorityRatioPercent',
    'CensusTractMinorityRatioPercent': 'CensusTractMinorityRatioPercent',
    'TraMedY': 'CensusTractMedFamIncomeAmount',
    'CensusTractMedFamIncomeAmount': 'CensusTractMedFamIncomeAmount',
    'LocMedY': 'LocalAreaMedianIncomeAmount',
    'LocalAreaMedianIncomeAmount': 'LocalAreaMedianIncomeAmount',
    'Income': 'TotalMonthlyIncomeAmount',
    'TotalMonthlyIncomeAmount': 'TotalMonthlyIncomeAmount',
    'CurAreY': 'HUDMedianIncomeAmount',
    'HUDMedianIncomeAmount': 'HUDMedianIncomeAmount',

    # Loan amount/value fields
    'UPB': 'LoanAcquisitionActualUPBAmt',
    'LoanAcquisitionActualUPBAmt': 'LoanAcquisitionActualUPBAmt',
    'LTV': 'LTVRatioPercent',
    'LTVRatioPercent': 'LTVRatioPercent',
    'Amount': 'NoteAmount',
    'NoteAmount': 'NoteAmount',

    # Date fields
    'MortDate': 'NoteDate',
    'NoteDate': 'NoteDate',
    'AcquDate': 'LoanAcquisitionDate',
    'AcqDate': 'LoanAcquisitionDate',  # 2015-2016 variant
    'LoanAcquistionDate': 'LoanAcquisitionDate',  # Typo in some years
    'LoanAcquisitionDate': 'LoanAcquisitionDate',

    # Loan characteristics
    'Purpose': 'LoanPurposeType',
    'LoanPurposeType': 'LoanPurposeType',
    'Product': 'ProductCategoryName',
    'ProductCategoryName': 'ProductCategoryName',
    'FedGuar': 'MortgageType',
    'MortgageType': 'MortgageType',
    'Term': 'ScheduledTotalPaymentCount',
    'ScheduledTotalPaymentCount': 'ScheduledTotalPaymentCount',
    'AmorTerm': 'LoanAmortizationMaxTermMonths',
    'LoanAmortizationMaxTermMonths': 'LoanAmortizationMaxTermMonths',

    # Seller/Institutional fields
    'SellType': 'MortgageLoanSellerInstType',
    'MortgageLoanSellerInstType': 'MortgageLoanSellerInstType',

    # Borrower count and characteristics
    'NumBor': 'BorrowerCount',
    'BorrowerCount': 'BorrowerCount',
    'First': 'BorrowerFirstTimeHomebuyer',
    'BorrowerFirstTimeHomebuyer': 'BorrowerFirstTimeHomebuyer',

    # Borrower 1 demographics
    'BoRace': 'Borrower1Race1Type',
    'Borrower1Race1Type': 'Borrower1Race1Type',
    'BoSex': 'Borrower1SexType',
    'Borrower1SexType': 'Borrower1SexType',
    'BoAge': 'Borrower1AgeAtApplicationYears',
    'Borrower1AgeAtApplicationYears': 'Borrower1AgeAtApplicationYears',
    'BoEth': 'Borrower1EthnicityType',
    'Borrower1EthnicityType': 'Borrower1EthnicityType',
    'Race2': 'Borrower1Race2Type',
    'Borrower1Race2Type': 'Borrower1Race2Type',
    'Race3': 'Borrower1Race3Type',
    'Borrower1Race3Type': 'Borrower1Race3Type',
    'Race4': 'Borrower1Race4Type',
    'Borrower1Race4Type': 'Borrower1Race4Type',
    'Race5': 'Borrower1Race5Type',
    'Borrower1Race5Type': 'Borrower1Race5Type',

    # Borrower 2 demographics
    'CoRace': 'Borrower2Race1Type',
    'Borrower2Race1Type': 'Borrower2Race1Type',
    'CoSex': 'Borrower2SexType',
    'Borrower2SexType': 'Borrower2SexType',
    'CoAge': 'Borrower2AgeAtApplicationYears',
    'Borrower2AgeAtApplicationYears': 'Borrower2AgeAtApplicationYears',
    'CoEth': 'Borrower2EthnicityType',
    'Borrower2EthnicityType': 'Borrower2EthnicityType',
    'CoRace2': 'Borrower2Race2Type',
    'Corace2': 'Borrower2Race2Type',  # Lowercase variant
    'Borrower2Race2Type': 'Borrower2Race2Type',
    'CoRace3': 'Borrower2Race3Type',
    'Corace3': 'Borrower2Race3Type',  # Lowercase variant
    'Borrower2Race3Type': 'Borrower2Race3Type',
    'CoRace4': 'Borrower2Race4Type',
    'Corace4': 'Borrower2Race4Type',  # Lowercase variant
    'Borrower2Race4Type': 'Borrower2Race4Type',
    'CoRace5': 'Borrower2Race5Type',
    'Corace5': 'Borrower2Race5Type',  # Lowercase variant
    'Borrower2Race5Type': 'Borrower2Race5Type',

    # Property fields
    'Occup': 'PropertyUsageType',
    'PropertyUsageType': 'PropertyUsageType',
    'NumUnits': 'PropertyUnitCount',
    'PropertyUnitCount': 'PropertyUnitCount',
    'PropType': 'PropertyType',
    'PropertyType': 'PropertyType',

    # Financial ratios
    'Rate': 'NoteRatePercent',
    'NoteRatePercent': 'NoteRatePercent',
    'Front': 'HousingExpenseRatioPercent',
    'HousingExpenseRatioPercent': 'HousingExpenseRatioPercent',
    'Back': 'TotalDebtExpenseRatioPercent',
    'TotalDebtExpenseRatioPercent': 'TotalDebtExpenseRatioPercent',

    # Credit scores
    'Borrower Credit Score': 'Borrower1CreditScoreValue',  # 2009-2014 (with spaces)
    'BoCreditScor': 'Borrower1CreditScoreValue',
    'BoCreditScore': 'Borrower1CreditScoreValue',  # Full version
    'Borrower1CreditScoreValue': 'Borrower1CreditScoreValue',
    'Co Borrower Credit Score': 'Borrower2CreditScoreValue',  # 2009-2014 (with spaces)
    'Co-Borrower Credit Score': 'Borrower2CreditScoreValue',  # 2010-2013 (with hyphen)
    'CoBorrower Credit Score': 'Borrower2CreditScoreValue',  # 2014 (no hyphen)
    'CoBoCreditScor': 'Borrower2CreditScoreValue',
    'CoBoCreditScore': 'Borrower2CreditScoreValue',  # Full version 2018
    'CoCreditScor': 'Borrower2CreditScoreValue',
    'CoCreditScore': 'Borrower2CreditScoreValue',  # Full version 2016
    'Borrower2CreditScoreValue': 'Borrower2CreditScoreValue',

    # PMI
    'PMI': 'PMICoveragePercent',
    'PMICoveragePercent': 'PMICoveragePercent',

    # Employment
    'Self': 'EmploymentBorrowerSelfEmployed',
    'EmploymentBorrowerSelfEmployed': 'EmploymentBorrowerSelfEmployed',

    # ARM fields
    'ArmIndex': 'IndexSourceType',
    'ARMIndex': 'IndexSourceType',
    'IndexSourceType': 'IndexSourceType',
    'ArmMarg': 'MarginRatePercent',
    'ARMMarg': 'MarginRatePercent',
    'MarginRatePercent': 'MarginRatePercent',

    # Other loan fields
    'PrepayP': 'PrepaymentPenaltyExpirationDate',
    'PrepaymentPenaltyExpirationDate': 'PrepaymentPenaltyExpirationDate',
    'HOEPA': 'HOEPALoanStatusType',
    'HOEPALoanStatusType': 'HOEPALoanStatusType',
    'LienStatus': 'LienPriorityType',
    'LienPriorityType': 'LienPriorityType',

    # Fields removed in 2019 (keep original names for historical data)
    'AcqTyp': 'AcqTyp',
    'Aff1': 'Aff1',
    'Aff2': 'Aff2',
    'Aff3': 'Aff3',
    'Aff4': 'Aff4',
    'Bed1': 'Bed1',
    'Bed2': 'Bed2',
    'Bed3': 'Bed3',
    'Bed4': 'Bed4',
    'CICA': 'CICA',
    'Coop': 'Coop',
    'FedFinStbltyPlan': 'FedFinStbltyPlan',
    'Geog': 'Geog',
    'GSEREO': 'GSEREO',
    'IncRat': 'IncRat',
    'Rent1': 'Rent1',
    'Rent2': 'Rent2',
    'Rent3': 'Rent3',
    'Rent4': 'Rent4',
    'RentUt1': 'RentUt1',
    'RentUt2': 'RentUt2',
    'RentUt3': 'RentUt3',
    'RentUt4': 'RentUt4',
    'SpcHsgGoals': 'SpcHsgGoals',
    'TractRat': 'TractRat',
    'Tractrat': 'TractRat',  # Lowercase variant

    # Other historical variations
    'Seller': 'Seller',
    'SellCity': 'SellCity',
    'SellSt': 'SellSt',
    'Program': 'Program',
}


def standardize_fhlb_columns(df_columns: list[str]) -> dict[str, str]:
    """Create a rename mapping for FHLB AMA columns.

    Args:
        df_columns: List of column names from the dataframe

    Returns:
        Dictionary mapping old names to new standardized names
    """
    rename_map = {}
    for col in df_columns:
        if col in FHLB_COLUMN_MAPPING:
            new_name = FHLB_COLUMN_MAPPING[col]
            # Only add to map if it's actually changing
            if new_name != col:
                rename_map[col] = new_name
    return rename_map


# =============================================================================
# Utility Functions
# =============================================================================

def ensure_parent_dir(path: Path) -> None:
    """Ensures that the parent directory of the given path exists."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)


def drop_unnamed_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Drops columns from a DataFrame that start with 'Unnamed'."""
    return df.loc[:, ~df.columns.str.contains('^Unnamed')]


# =============================================================================
# FHLB Members Column Name Standardization
# =============================================================================
# This mapping standardizes historical column names for FHLB member data files.

FHLB_MEMBERS_COLUMN_MAPPING = {
    'FHFBID': 'FHFB ID',
    'FHFA ID': 'FHFB ID',
    'MEM NAME': 'MEMBER NAME',
    'MEM TYPE': 'MEMBER TYPE',
    'CHAR TYPE': 'CHARTER TYPE',
    'APPR DATE': 'APPROVAL DATE',
    'CERT': 'FDIC CERTIFICATE NUMBER',
    'FED ID': 'FEDERAL RESERVE ID',
    'OTS ID': 'OTS DOCKET NUMBER',
    'NCUA ID': 'CREDIT UNION CHARTER NUMBER',
}


def standardize_fhlb_members_columns(df_columns: list[str]) -> dict[str, str]:
    """Create a rename mapping for FHLB members columns.

    Args:
        df_columns: List of column names from the dataframe

    Returns:
        Dictionary mapping old names to new standardized names
    """
    rename_map = {}
    for col in df_columns:
        if col in FHLB_MEMBERS_COLUMN_MAPPING:
            new_name = FHLB_MEMBERS_COLUMN_MAPPING[col]
            if new_name != col:
                rename_map[col] = new_name
    return rename_map
