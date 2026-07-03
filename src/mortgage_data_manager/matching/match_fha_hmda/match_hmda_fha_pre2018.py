"""Match HMDA and FHA records for the pre-2018 period.

Created on Sun Apr  2 17:04:57 2023
Last Updated: 2025-06-04
@author: Jonathan Becker
"""

# Import Packages

from __future__ import annotations

import datetime
import gc
import glob
from pathlib import Path

import addfips
import numpy as np
import pandas as pd
import polars as pl
import pyarrow.parquet as pq

from mortgage_data_manager.core.logging import configure_logging, get_logger

logger = get_logger(__name__)


#%% Local Functions
# Prepare Merge Data
def clean_fha_lenders(fha_folder: str | Path, match_folder: str | Path, fha_file_basename: str, file_suffix: str | None = None) :
    """Clean Lenders in FHA data files.

    Args:
        fha_folder: Folder where raw FHA data is saved.
        match_folder: Folder where intermediate match data will be saved.
        fha_file_basename: Name of FHA combined file (w/o folder).
        file_suffix: Suffix appended to the output filename for match files.

    Returns:
        None.
    """
    # Keep Columns
    keep_cols = [
        'Originating Mortgagee',
        'Originating Mortgagee Number',
        'Sponsor Name',
        'Sponsor Number',
        'Non Profit Number',
        'Year',
        'Month',
    ]

    # Clean FHA Originations
    fha_file = f'{fha_folder}/{fha_file_basename}'
    df = pd.read_parquet(fha_file, columns=keep_cols)

    # Drop August 2014 Observations (No Originator Names/IDs)
    df = df.loc[~((df['Year'] == 2014) & (df['Month'] == 8))]

    # Consolidate
    df['Count'] = df.groupby(keep_cols, dropna=False)['Year'].transform('count')
    df = df.drop_duplicates()

    #
    origs = df[['Originating Mortgagee','Originating Mortgagee Number','Year','Month']].drop_duplicates()
    origs = origs.loc[~pd.isna(origs['Originating Mortgagee Number'])]
    spons = df[['Sponsor Name','Sponsor Number','Year','Month']].drop_duplicates()
    spons = spons.loc[~pd.isna(spons['Sponsor Number'])]

    # Combine Originators and Sponsors
    unique_origs = df[['Originating Mortgagee','Originating Mortgagee Number','Count']]
    unique_origs = unique_origs.rename(columns = {'Originating Mortgagee': 'Institution Name',
                                                  'Originating Mortgagee Number': 'Institution Number'})
    unique_spons = df[['Sponsor Name','Sponsor Number','Count']]
    unique_spons = unique_spons.rename(columns = {'Sponsor Name': 'Institution Name',
                                                  'Sponsor Number': 'Institution Number'})
    unique_insts = pd.concat([unique_origs, unique_spons])
    del unique_origs, unique_spons

    # Combine and Keep Uniques
    unique_insts = unique_insts.loc[~pd.isna(unique_insts['Institution Number'])]
    unique_insts['Count'] = unique_insts.groupby(['Institution Name','Institution Number'])['Count'].transform('sum')
    unique_insts = unique_insts.drop_duplicates()
    unique_insts['Number Distinct Names'] = unique_insts.groupby(['Institution Number'])['Institution Name'].transform('nunique')

    # Rank by Most Common
    unique_insts['Rank'] = unique_insts.groupby(['Institution Number'])['Count'].rank(method='dense', ascending=False)

    # Combine Institutions
    insts = unique_insts[['Institution Number']].drop_duplicates()
    for rank in range(1, 7) :
        temp = unique_insts.query(f'Rank == {rank}')
        temp = temp[['Institution Name', 'Institution Number']]
        temp = temp.rename(columns = {'Institution Name': f'Institution Name (v{rank})'})
        insts = insts.merge(temp, on = ['Institution Number'], how='left')

    # Save Institutions
    insts = insts.sort_values(by = ['Institution Number'])
    insts.to_csv(f'{match_folder}/fha_unique_institutions{file_suffix}.csv',
                 index = False,
                 sep = '|',
                 )

# Prepare FHA Data Pre-2018
def prepare_fha_pre2018_merge_data(fha_folder: str | Path, match_folder: str | Path, fha_file_basename: str, file_suffix: str | None = None, save: bool = False) :
    """Prepare FHA data for merge with HMDA."""
    # Clean FHA Originations
    fha_file = f'{fha_folder}/{fha_file_basename}'
    df = pl.scan_parquet(fha_file)

    # Filter to Pre-2018
    df = df.filter([pl.col('Year') < 2018])

    # Drop Columns
    drop_cols = [
        'Down Payment Source',
        'Interest Rate',
        'Non Profit Number',
        'Product Type',
        'Property Type',
        'Property City',
    ]
    df = df.drop(drop_cols)

    # Create Datetime
    df = df.with_columns(
        pl.col('Year').cast(pl.Utf8).str.zfill(4),
        pl.col('Month').cast(pl.Utf8).str.zfill(2),
    )
    df = df.with_columns(
        pl.concat_str([pl.col('Year'), pl.col('Month')], separator='-').str.to_datetime(format='%Y-%m', strict=False).alias('Date')
    )
    df = df.drop(['Year', 'Month'])

    # Iterate over rows and add FIPS codes to unique_counties
    af = addfips.AddFIPS()
    unique_counties = df.select(["Property State", "Property County"]).unique()
    county_map = []
    for row in unique_counties.collect().iter_rows() :
        df_row = pl.LazyFrame({"Property State": [row[0]], "Property County": [row[1]]})
        fips = af.get_county_fips(row[1], row[0])
        df_row = df_row.with_columns(
            pl.lit(fips).alias("FIPS")
        )
        county_map.append(df_row)
    county_map = pl.concat(county_map, how='diagonal_relaxed')
    county_map = county_map.sort(["FIPS",'Property State','Property County'])
    df = df.join(county_map, on=["Property State", "Property County"], how="left")
    df = df.drop(['Property State', 'Property County'])

    # Save
    if save :
        df.sink_parquet(f'{match_folder}/fha_match_data{file_suffix}.parquet')

    # Return DataFrame
    return df

# Prepare FHA Data Pre-2018
def prepare_fha_merge_data(fha_folder: str | Path, match_folder: str | Path, fha_file_basename: str, file_suffix: str | None = None, save: bool = False) :
    """Prepare FHA data for merge with HMDA."""
    # Clean FHA Originations
    fha_file = f'{fha_folder}/{fha_file_basename}'
    df = pl.scan_parquet(fha_file)

    # Filter to Pre-2018
    df = df.filter([pl.col('Year') < 2018])

    # Drop Columns
    drop_cols = [
        'Down Payment Source',
        'Interest Rate',
        'Non Profit Number',
        'Product Type',
        'Property Type',
        'Property City',
        'Property County',
        'Property State',
        # 'Property Zip',
    ]
    df = df.drop(drop_cols)

	# Create Rounded Loan Amount (Rounded to nearest 1000s)
    df = df.with_columns(
        (1000*(pl.col('Mortgage Amount').cast(pl.Int32)/1000).round()).cast(pl.Int32).alias('Mortgage_Amount_Rounded')
    )

    # Create Purchase Dummy
    df = df.with_columns(
        (pl.col('Loan Purpose').str.to_lowercase()=='purchase').cast(pl.Int16).alias('i_Purchase')
    )

    # Save
    if save :
        save_file = f'{match_folder}/fha_match_data{file_suffix}.parquet'
        df.sink_parquet(save_file)

    # Return DataFrame
    return df

# Prepare Merge Data
def prepare_hmda_merge_data(hmda_folder: str | Path, match_folder: str | Path, file_suffix: str | None = None) :
    """Prepare HMDA data for merge with FHA.

    Args:
        hmda_folder: Folder where raw HMDA data is stored.
        match_folder: Folder where intermediate match data will be saved.
        file_suffix: Suffix to add to file names for match files.

    Returns:
        None.
    """
	# Prepare HMDA Data
    keep_cols = [
        'as_of_year',
        'state_code',
        'county_code',
        'census_tract_number',
        'loan_purpose',
        'loan_amount_000s',
        'respondent_id',
        'agency_code',
        'sequence_number',
    ]

    # Get HMDA Files
    hmda_files = glob.glob(f'{hmda_folder}/loans/*.parquet')
    hmda_files = [file for file in hmda_files if any(str(year) in file for year in range(2010, 2018))]

    # Load HMDA Data
    df = []
    for hmda_file in hmda_files :
        df_a = pl.scan_parquet(hmda_file)
        df_a = df_a.filter([pl.col('action_taken')==1, pl.col('loan_type')==2])
        df_a = df_a.select(keep_cols)
        df.append(df_a)
    df = pl.concat(df, rechunk=True, how='diagonal_relaxed')

    # Combine state and county to create fips
    df = df.with_columns(
        (pl.col('state_code') + pl.col('county_code')).cast(pl.Utf8).alias('FIPS')
    )

    # Multiply Loan Amount by 1000
    df = df.with_columns(
        (1000*pl.col('loan_amount_000s')).cast(pl.Int32).alias('Mortgage_Amount_Rounded')
    )

    # Create Purchase Dummy
    df = df.with_columns(
        (pl.col('loan_purpose')==1).cast(pl.Int16).alias('i_Purchase')
    )

    # Return Data
    return df

    # # Read and Combine HMDA Years
    # df = []
    # for year in range(2010, 2023+1) :

    #     # Display Progress
    #     file = HMDALoader.get_hmda_files(hmda_folder, min_year=year, max_year=year, extension='parquet')[0]
    #     print('Reading data from file:', file)

    #     # Read Yearly Data w/ Select Columns
    #     yearly_columns = pq.read_schema(file).names
    #     use_columns = [x for x in yearly_columns if x in keep_cols]
    #     df_a = pq.read_table(file,
    #                          columns = use_columns,
    #                          filters = [('action_taken','==',1),
    #                                     ('loan_type','==',2)],
    #                          ).to_pandas()

    #     # Read Yearly Files
    #     if year <= 2017 :
    #         df_a = df_a.merge(state_fips, left_on = 'state_code', right_on = 'State FIPS', how = 'left')
    #         df_a = df_a.drop(columns = ['state_code','State FIPS'])
    #         df_a = df_a.rename(columns = {'State Name': 'state_code'})
    #         df_a['loan_amount'] = 1000*df_a['loan_amount']
    #     if year >= 2018 :
    #         df_a = df_a.loc[df_a['reverse_mortgage'] != 1]
    #         df_a = df_a.loc[df_a['total_units'] <= 4]

    #     # Drop Columns
    #     drop_cols = ['action_taken','loan_type','reverse_mortgage','total_units']
    #     for col in drop_cols :
    #         if col in df_a.columns :
    #             df_a = df_a.drop(columns = [col])

    #     # Append Yearly Data
    #     df.append(df_a)
    #     del df_a

    # # Combine Yearly Data
    # df = pd.concat(df)

	# # Merge in Zip Codes
    # zip_tract_file = "/path/to/data/crosswalks/zip_tract_crosswalk_2010-2022.csv"
    # zip_tracts = pd.read_csv(zip_tract_file, sep='|', dtype={'TRACT':'str'})
    # df = df.merge(zip_tracts, left_on=['census_tract'], right_on=['TRACT'], how='left')
    # df = df.drop(columns=['census_tract','TRACT'])
    # del zip_tracts

    # # Convert Strings
    # string_columns = ['respondent_id', 'state_code', 'HMDAIndex', 'lei']
    # for string_column in string_columns :
    #     df[string_column] = df[string_column].astype('str')

# Match FHA/HMDA: Round 1
def match_fha_hmda_pre2018_round1(match_folder: str | Path, file_suffix: str | None = None) :

    # FHA/HMDA File Names
    fha_file = f'{match_folder}/fha_match_data.parquet'
    hmda_file = f'{match_folder}/hmda_match_data.parquet'

    # Import FHA/HMDA Data
    df_fha = pq.read_table(fha_file,
                           filters = [('Year','<=',2018)],
                           ).to_pandas()
    df_hmda = pq.read_table(hmda_file,
                            filters = [('activity_year','<',2018)],
                            ).to_pandas()

    # FHA Sample Selection
    df_fha = df_fha[(df_fha['Year'] < 2018) | (df_fha['Month'] == 1)]
    df_fha['Loan Amount (1000)'] = 1000*np.round(df_fha['Mortgage Amount']/1000)
    df_fha['fips'] = df_fha['fips'].astype('Int32')

    # Match
    df = []
    for year in range(2010, 2017+1) :

        # Display Progress
        logger.info(f'Conducting FHA-to-HMDA matches for year: {year}')

        # Yearly FHA/HMDA Data
        df_temp1 = df_hmda.loc[df_hmda['activity_year'] == year]
        df_temp2 = df_fha.loc[(df_fha['Year'] == year) | ((df_fha['Year'] == year-1) & (df_fha['Month'] == 12)) | ((df_fha['Year'] == year+1) & (df_fha['Month'] <= 2))]

     	# Match Missing Zip Codes
        match_hmda = df_temp1.loc[pd.isna(df_temp1['ZIP'])]
        matches = match_hmda.merge(df_temp2,
                                    left_on = ['state_code','loan_amount'],
                                    right_on = ['Property State', 'Loan Amount (1000)'],
                                    how = 'inner',
                                    )
        matches_nozip = matches.loc[(matches['fips'] == matches['county_code']) | pd.isna(matches['fips']) | pd.isna(matches['county_code'])]

     	# Merge FHA-HMDA Data for HMDA Observations w/ Interest Rates
        df_temp1 = df_temp1.loc[~pd.isna(df_temp1['ZIP'])]
        matches = df_temp1.merge(df_temp2,
                                left_on = ['state_code', 'loan_amount', 'ZIP'],
                                right_on = ['Property State', 'Loan Amount (1000)', 'Property Zip'],
                                how = 'inner',
                                )
        matches = matches.loc[(matches['fips'] == matches['county_code']) | pd.isna(matches['fips']) | pd.isna(matches['county_code'])]

        # Concatenate All Matches
        matches = pd.concat([matches, matches_nozip])
        df.append(matches)
        del df_temp1, df_temp2, matches, match_hmda, matches_nozip

    # Concatenate
    df = pd.concat(df)
    del df_fha, df_hmda
    gc.collect()

    # Loan Purpose
    df = df[~((df['loan_purpose'] == 1) & (df['Loan Purpose'].isin(['Refi_FHA','Refi_Conv_Curr'])))]
    df = df[~((df['loan_purpose'].isin([2,3])) & (df['Loan Purpose'] == 'Purchase'))]

    # Keep Unique Matches and Drop Unnecessary Variables
    hmda_loan_id = ['respondent_id','agency_code','sequence_number','HMDAIndex']
    df['CountLoanFHA'] = df.groupby(['FHA Index'])['FHA Index'].transform('count')
    df['CountLoanHMDA'] = df.groupby(hmda_loan_id, dropna = False)['loan_amount'].transform('count')
    df = df.query('CountLoanFHA == 1 & CountLoanHMDA == 1')
    df = df.drop(columns = ['CountLoanFHA','CountLoanHMDA'])

    # Master Mortgagee Name and Number
    df['Master Number'] = df['Originating Mortgagee Number']
    df['Master Number'] = df['Master Number'].fillna(df['Sponsor Number'])

    # Match Lenders: Round 1
    hmda_lender_vars = ['respondent_id','agency_code']
    df['Count Mortgagee'] = df.groupby(['Master Number'])['FHA Index'].transform('nunique')
    df['Count LEI'] = df.groupby(hmda_lender_vars, dropna=False)['HMDAIndex'].transform('nunique')
    df['Count Matches (FHA)'] = df.groupby(['Master Number']+hmda_lender_vars, dropna=False)['FHA Index'].transform('nunique')
    df['Count Matches (HMDA)'] = df.groupby(['Master Number']+hmda_lender_vars, dropna=False)['HMDAIndex'].transform('nunique')
    df['Fraction Matches (FHA)'] = df['Count Matches (FHA)']/df['Count Mortgagee']
    df['Fraction Matches (HMDA)'] = df['Count Matches (HMDA)']/df['Count LEI']
    df = df.loc[df['Fraction Matches (FHA)'] >= .01]
    df = df.loc[df['Fraction Matches (HMDA)'] >= .01]

# Identify HUD Originators
def identify_hud_originators(hmda_folder: str | Path, match_folder: str | Path, fha_folder: str | Path, fha_file_basename: str, file_suffix: str | None = None) :

    # Load HMDA
    df_hmda = prepare_hmda_merge_data(hmda_folder, match_folder, file_suffix='_pre2018')

    # Keep Only HMDA Observations with Agency Code 7 and no hyphen in the third character spot
    df_hmda = df_hmda.filter(pl.col('agency_code')=='7')
    df_hmda = df_hmda.filter(pl.col('respondent_id').str.slice(2,1) != '-')

    # Keep Unique HMDA Respondents
    hmda_lenders = df_hmda.select(['respondent_id']).unique().collect().to_series()
    hmda_prefixes = hmda_lenders.str.slice(0,5)

    # Load FHA
    df_fha = prepare_fha_merge_data(fha_folder, match_folder, fha_file_basename, save=False)

    # Combine FHA Lenders
    df_fha_lenders_1 = df_fha.select(['Originating Mortgagee Number','Originating Mortgagee','Date'])
    df_fha_lenders_1 = df_fha_lenders_1.rename({'Originating Mortgagee Number': 'Institution Number',
                                                'Originating Mortgagee': 'Institution Name'})
    df_fha_lenders_2 = df_fha.select(['Sponsor Number','Sponsor Name','Date'])
    df_fha_lenders_2 = df_fha_lenders_2.rename({'Sponsor Number': 'Institution Number',
                                                'Sponsor Name': 'Institution Name'})
    df_fha_lenders = pl.concat([df_fha_lenders_1, df_fha_lenders_2], how="diagonal")
    df_fha_lenders = df_fha_lenders.filter(pl.col('Date') != datetime.datetime(2014, 8, 1)) # Drop August 2014, which has low data quality
    # df_fha_lenders = df_fha_lenders.unique().drop_nulls()
    # df_fha_lenders = df_fha_lenders.filter(pl.col('Institution Name')!='')
    # df_fha_lenders = df_fha_lenders.sort(by=['Institution Number']).collect()
    # df_fha_lenders = df_fha_lenders.with_columns(
    #     pl.col('Date').min().over(['Institution Number','Institution Name']).alias('Date_First')
    # )
    # df_fha_lenders = df_fha_lenders.with_columns(
    #     pl.col('Date').max().over(['Institution Number','Institution Name']).alias('Date_Last')
    # )
    df_fha_lenders = df_fha_lenders.drop(['Date'])
    df_fha_lenders = df_fha_lenders.unique().drop_nulls()
    df_fha_lenders = df_fha_lenders.filter(pl.col('Institution Name')!='')
    df_fha_lenders = df_fha_lenders.sort(by=['Institution Number']).collect()
    del df_fha_lenders_1, df_fha_lenders_2

    # Create Master ID: The sponsor number where not missing, otherwise the originating mortgagee number
    df_fha = df_fha.with_columns(
        pl.when(~pl.col('Sponsor Number').is_null())
        .then(pl.col('Sponsor Number'))
        .otherwise(pl.col('Originating Mortgagee Number'))
        .cast(pl.Int32)
        .cast(pl.Utf8)
        .str.zfill(5)
        .alias('MasterNumber')
    )

    # Keep FHA observations where MasterNumber is in respondent_id_5
    df_fha = df_fha.filter(pl.col('MasterNumber').is_in(hmda_prefixes))

    # Return Data
    return df_fha, df_hmda


# Match FHA/HMDA: Round 1
def match_fha_hmda_pre2018_round1_old(match_folder: str | Path, file_suffix: str | None = None) :

    # FHA/HMDA File Names
    fha_file = f'{match_folder}/fha_match_data.parquet'
    hmda_file = f'{match_folder}/hmda_match_data.parquet'

    # Import FHA/HMDA Data
    df_fha = pq.read_table(fha_file,
                           filters = [('Year','<=',2018)],
                           ).to_pandas()
    df_hmda = pq.read_table(hmda_file,
                            filters = [('activity_year','<',2018)],
                            ).to_pandas()

    # FHA Sample Selection
    df_fha = df_fha[(df_fha['Year'] < 2018) | (df_fha['Month'] == 1)]
    df_fha['Loan Amount (1000)'] = 1000*np.round(df_fha['Mortgage Amount']/1000)
    df_fha['fips'] = df_fha['fips'].astype('Int32')

    # Match
    df = []
    for year in range(2010, 2017+1) :

        # Display Progress
        logger.info(f'Conducting FHA-to-HMDA matches for year: {year}')

        # Yearly FHA/HMDA Data
        df_temp1 = df_hmda.loc[df_hmda['activity_year'] == year]
        df_temp2 = df_fha.loc[(df_fha['Year'] == year) | ((df_fha['Year'] == year-1) & (df_fha['Month'] == 12)) | ((df_fha['Year'] == year+1) & (df_fha['Month'] <= 2))]

     	# Match Missing Zip Codes
        match_hmda = df_temp1.loc[pd.isna(df_temp1['ZIP'])]
        matches = match_hmda.merge(df_temp2,
                                    left_on = ['state_code','loan_amount'],
                                    right_on = ['Property State', 'Loan Amount (1000)'],
                                    how = 'inner',
                                    )
        matches_nozip = matches.loc[(matches['fips'] == matches['county_code']) | pd.isna(matches['fips']) | pd.isna(matches['county_code'])]

     	# Merge FHA-HMDA Data for HMDA Observations w/ Interest Rates
        df_temp1 = df_temp1.loc[~pd.isna(df_temp1['ZIP'])]
        matches = df_temp1.merge(df_temp2,
                                left_on = ['state_code', 'loan_amount', 'ZIP'],
                                right_on = ['Property State', 'Loan Amount (1000)', 'Property Zip'],
                                how = 'inner',
                                )
        matches = matches.loc[(matches['fips'] == matches['county_code']) | pd.isna(matches['fips']) | pd.isna(matches['county_code'])]

        # Concatenate All Matches
        matches = pd.concat([matches, matches_nozip])
        df.append(matches)
        del df_temp1, df_temp2, matches, match_hmda, matches_nozip

    # Concatenate
    df = pd.concat(df)
    del df_fha, df_hmda
    gc.collect()

    # Loan Purpose
    df = df[~((df['loan_purpose'] == 1) & (df['Loan Purpose'].isin(['Refi_FHA','Refi_Conv_Curr'])))]
    df = df[~((df['loan_purpose'].isin([2,3])) & (df['Loan Purpose'] == 'Purchase'))]

    # Keep Unique Matches and Drop Unnecessary Variables
    hmda_loan_id = ['respondent_id','agency_code','sequence_number','HMDAIndex']
    df['CountLoanFHA'] = df.groupby(['FHA Index'])['FHA Index'].transform('count')
    df['CountLoanHMDA'] = df.groupby(hmda_loan_id, dropna = False)['loan_amount'].transform('count')
    df = df.query('CountLoanFHA == 1 & CountLoanHMDA == 1')
    df = df.drop(columns = ['CountLoanFHA','CountLoanHMDA'])

    # Master Mortgagee Name and Number
    df['Master Number'] = df['Originating Mortgagee Number']
    df['Master Number'] = df['Master Number'].fillna(df['Sponsor Number'])

    # Match Lenders: Round 1
    hmda_lender_vars = ['respondent_id','agency_code']
    df['Count Mortgagee'] = df.groupby(['Master Number'])['FHA Index'].transform('nunique')
    df['Count LEI'] = df.groupby(hmda_lender_vars, dropna=False)['HMDAIndex'].transform('nunique')
    df['Count Matches (FHA)'] = df.groupby(['Master Number']+hmda_lender_vars, dropna=False)['FHA Index'].transform('nunique')
    df['Count Matches (HMDA)'] = df.groupby(['Master Number']+hmda_lender_vars, dropna=False)['HMDAIndex'].transform('nunique')
    df['Fraction Matches (FHA)'] = df['Count Matches (FHA)']/df['Count Mortgagee']
    df['Fraction Matches (HMDA)'] = df['Count Matches (HMDA)']/df['Count LEI']
    df = df.loc[df['Fraction Matches (FHA)'] >= .01]
    df = df.loc[df['Fraction Matches (HMDA)'] >= .01]


#%% Main Routine
if __name__ == '__main__' :

    configure_logging(level="INFO")

    # Folder Structure
    fha_folder = '../fha_data_manager/data'
    hmda_folder = '../hmda_data_manager/data/clean'
    match_folder = './data/intermediate'

    # Create Match Folder
    Path(match_folder).mkdir(parents=True, exist_ok=True)

    # Get HMDA Files
    hmda_files = glob.glob(f'{hmda_folder}/loans/*.parquet')
    hmda_files = [file for file in hmda_files if any(str(year) in file for year in range(2013, 2016))]

    # Load HMDA Data
    df_hmda = prepare_hmda_merge_data(hmda_folder, match_folder, file_suffix='_pre2018')

    # Data Preparation
    fha_file_basename = 'fha_combined_sf_originations_201006-202502.parquet'

    # Load FHA Data
    df_fha = prepare_fha_merge_data(fha_folder, match_folder, fha_file_basename, file_suffix='_pre2018', save=True)
    df_fha = df_fha.filter([pl.col('Date') >= datetime.datetime(2013, 1, 1), pl.col('Date') <= datetime.datetime(2015, 12, 31)])

    # Temporary: Create State FIPS and use subset of HMDA respondents
    df_hmda = df_hmda.filter(pl.col('agency_code')=='7')
    df_hmda = df_hmda.filter(pl.col('respondent_id').str.slice(2,1) != '-')
    df_fha = df_fha.with_columns(
        pl.col('FIPS').str.slice(0,2).alias('State FIPS')
    )
    df_hmda = df_hmda.with_columns(
        pl.col('FIPS').str.slice(0,2).alias('State FIPS')
    )

    # Loop through each FIPS code and match to HMDA
    df = []
    # Keep only fips starting with '01'
    # unique_fips = df_fha.select(pl.col('FIPS')).unique().sort(by=['FIPS'])
    # unique_fips = unique_fips.filter(pl.col('FIPS').str.starts_with('01')).collect().to_series().drop_nulls()
    # unique_fips = df_fha.select(pl.col('FIPS')).unique().sort(by=['FIPS']).collect().to_series().drop_nulls()
    unique_fips = df_fha.select(pl.col('State FIPS')).unique().sort(by=['State FIPS']).collect().to_series().drop_nulls()
    for fips in unique_fips :
        logger.info(fips)
        # stop
        # df_fha_a = df_fha.filter(pl.col('FIPS') == fips).collect()
        # df_hmda_a = df_hmda.filter(pl.col('FIPS') == fips).collect()
        df_fha_a = df_fha.filter(pl.col('State FIPS') == fips).collect()
        df_hmda_a = df_hmda.filter(pl.col('State FIPS') == fips).collect()
        df_a = df_hmda_a.join(df_fha_a, left_on=['Mortgage_Amount_Rounded','i_Purchase','FIPS','as_of_year'], right_on=['Mortgage_Amount_Rounded','i_Purchase','FIPS','Year'], how='inner')
        df.append(df_a)
    df = pl.concat(df, rechunk=True, how='diagonal_relaxed')
    df = df.sort(by=['FIPS','as_of_year','Mortgage_Amount_Rounded','i_Purchase'])
    # df.sink_parquet(f'{match_folder}/fha_hmda_pre2018_matches.parquet')

    # Create Master ID: The sponsor number where not '', otherwise the originating mortgagee number
    df = df.with_columns(
        pl.when(~pl.col('Sponsor Number').is_null())
        .then(pl.col('Sponsor Number'))
        .otherwise(pl.col('Originating Mortgagee Number'))
        .alias('MasterNumber')
    )

    # Count the number of loans from each master number, and create column with counts in the original dataframe
    df = df.with_columns(
        pl.col('FHA Index').n_unique().over(['MasterNumber']).alias('CountLoanFHA')
    )
    df = df.with_columns(
        pl.struct(['sequence_number','as_of_year','agency_code','respondent_id']).n_unique().over(['agency_code','respondent_id']).alias('CountLoanHMDA')
    )
    df = df.with_columns(
        pl.col('FHA Index').n_unique().over(['MasterNumber','respondent_id','agency_code']).alias('CountLoanMatchesFHA')
    )
    df = df.with_columns(
        pl.struct(['sequence_number','as_of_year','agency_code','respondent_id']).n_unique().over(['MasterNumber','respondent_id','agency_code']).alias('CountLoanMatchesHMDA')
    )

    # Originator Matches
    matches = df.filter(pl.col('CountLoanMatchesFHA')/pl.col('CountLoanFHA')>=0.75)
    matches = matches.filter(pl.col('CountLoanMatchesHMDA')/pl.col('CountLoanHMDA')>=0.75)
    matches = matches.filter(pl.col('CountLoanFHA')>=10)
    matches = matches.filter(pl.col('CountLoanHMDA')>=10)
    matches = matches.select(['MasterNumber','respondent_id','agency_code','CountLoanMatchesFHA','CountLoanMatchesHMDA','CountLoanFHA','CountLoanHMDA'])
    matches = matches.unique()
    matches = matches.sort(by=['MasterNumber','respondent_id','agency_code'])

    # Originator Matches
    df_matches = df.filter(pl.col('CountLoanMatchesFHA')/pl.col('CountLoanFHA')>=0.75)
    df_matches = df_matches.filter(pl.col('CountLoanMatchesHMDA')/pl.col('CountLoanHMDA')>=0.75)
    df_matches = df_matches.with_columns(
        pl.struct(['FHA Index']).count().over(['FHA Index']).alias('CountLoan_FHA')
    )
    df_matches = df_matches.with_columns(
        pl.struct(['as_of_year','respondent_id','agency_code']).count().over(['as_of_year','respondent_id','agency_code','sequence_number']).alias('CountLoan_HMDA')
    )
    df_matches.select(['CountLoan_FHA','CountLoan_HMDA']).describe()

    # Note to self: For agency_code==7, respondent_id values either include a hyphen in the third character spot or not.
    # If not, the first five digits are the same as the originator ID from the FHA data.
    # There do not to be relationships between the respondent_id and the originating mortgagee number for other institution types.
    # Performing matches with this in mind allows us to incorporate originator information immediately for about 20% of observations.
