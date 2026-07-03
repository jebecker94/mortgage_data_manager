# HMDA LEI-Level Reporting Errors

Exhaustive catalog of confirmed LEI-level reporting errors in HMDA post-2018 bronze data (2018–2024).
Detected via systematic analysis of 62.1M originated loans across 28,476 LEI-year groups.

Each error is listed per file type (a=Three-Year, b=One-Year, c=Snapshot). Errors were verified
independently per file type — a lender may have corrected an issue in one filing but not another.

Sentinel values 1111, 8888, 9999 are exemption codes, not reporting errors. Replace with NULL before analysis.

**Source**: `investigations/scripts/investigation_hmda_lei_reporting_errors_*.py`  
**See also**: `hmda.md` for high-level descriptions of each error pattern.

**Total records**: 1075

| LEI | Year | File Type | Variable | Issue | Fix |
|-----|------|-----------|----------|-------|-----|
| 25490010EMNKWA8L9813 | 2021 | a | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 25490010EMNKWA8L9813 | 2021 | b | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 25490010EMNKWA8L9813 | 2021 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 2549001UO7C3LB3SXA82 | 2019 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 2549001UO7C3LB3SXA82 | 2019 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 2549001UO7C3LB3SXA82 | 2019 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900AKNL0HLB9M9B52 | 2018 | a | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 254900AKNL0HLB9M9B52 | 2018 | c | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 254900FBWEZ3YUPOBN33 | 2019 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900FBWEZ3YUPOBN33 | 2019 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900FBWEZ3YUPOBN33 | 2019 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900G0ZC4S8EO1W523 | 2018 | a | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 254900G0ZC4S8EO1W523 | 2018 | c | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 254900J9O6LD52M7KM82 | 2024 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2019 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2019 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2019 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2020 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2020 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2020 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2021 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2021 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2021 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2022 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2022 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2023 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2023 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900JXCS783CPF1D02 | 2024 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900KLIC284KZ5U646 | 2020 | a | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 254900KLIC284KZ5U646 | 2020 | b | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 254900KLIC284KZ5U646 | 2020 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 254900PNUZSV3PBY6Z71 | 2018 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900PNUZSV3PBY6Z71 | 2018 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2019 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2019 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2019 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2020 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2020 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2020 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2021 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2021 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2021 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2022 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2022 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2023 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2023 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900Y6EUNN0KGMJW67 | 2024 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 5493001A8DPRB21YBS87 | 2018 | a | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 5493001A8DPRB21YBS87 | 2018 | c | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 5493005H3NABV4GMAG85 | 2024 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300C8GOC4OYUV0Z32 | 2019 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 549300C8GOC4OYUV0Z32 | 2019 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 549300C8GOC4OYUV0Z32 | 2019 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 549300DU662K57XBSH20 | 2019 | a | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 549300DU662K57XBSH20 | 2019 | b | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 549300DU662K57XBSH20 | 2019 | c | combined_loan_to_value_ratio | Placeholder/sentinel value | → NULL |
| 549300JGMQJ4R419LR70 | 2021 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 549300JGMQJ4R419LR70 | 2021 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 549300JGMQJ4R419LR70 | 2022 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 549300N1UWZ4871DND44 | 2019 | a | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2019 | b | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2019 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2020 | a | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2020 | b | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2020 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2021 | a | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2021 | b | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2021 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2022 | b | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300N1UWZ4871DND44 | 2022 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300TJGBPVMBWV5P74 | 2018 | a | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300TJGBPVMBWV5P74 | 2018 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300TJGBPVMBWV5P74 | 2019 | a | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300TJGBPVMBWV5P74 | 2019 | b | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300TJGBPVMBWV5P74 | 2019 | c | combined_loan_to_value_ratio | Tenths instead of percentage | CLTV × 10 |
| 549300WPK18IMSC1OM63 | 2021 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 549300WPK18IMSC1OM63 | 2021 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 549300WPK18IMSC1OM63 | 2022 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| MZJU01BGQ7J1KULQSB89 | 2019 | a | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| MZJU01BGQ7J1KULQSB89 | 2019 | b | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| MZJU01BGQ7J1KULQSB89 | 2019 | c | combined_loan_to_value_ratio | Decimal instead of percentage | CLTV × 100 |
| 254900QXDYW5OTSNTL04 | 2023 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 254900QXDYW5OTSNTL04 | 2023 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493005EYZOUVJUXAX38 | 2021 | a | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493005EYZOUVJUXAX38 | 2021 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493005EYZOUVJUXAX38 | 2021 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493005EYZOUVJUXAX38 | 2022 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493005EYZOUVJUXAX38 | 2022 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493006JISETNI0GLE61 | 2022 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493006JISETNI0GLE61 | 2022 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493007VW2EU20PZYU97 | 2021 | a | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493007VW2EU20PZYU97 | 2021 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 5493007VW2EU20PZYU97 | 2021 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 549300ELLMUEKP1JHB70 | 2022 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 549300ELLMUEKP1JHB70 | 2022 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 549300NOCASXPA34X033 | 2022 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 549300SVKVT1EF3U0N48 | 2019 | a | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 549300SVKVT1EF3U0N48 | 2019 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 549300SVKVT1EF3U0N48 | 2019 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 894500FO00PFE38X9V12 | 2022 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 894500FO00PFE38X9V12 | 2022 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 894500I2ZTY72KOPYR23 | 2022 | b | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 894500I2ZTY72KOPYR23 | 2022 | c | discount_points | Reporting in points instead of dollars | Multiply discount_points by loan_amount/100 |
| 2549001LVVJUGK9VA038 | 2018 | a | income | Raw dollars instead of thousands | income ÷ 1000 |
| 2549001LVVJUGK9VA038 | 2018 | c | income | Raw dollars instead of thousands | income ÷ 1000 |
| 5493000RRYPUX5O9MI08 | 2018 | a | income | Raw dollars instead of thousands | income ÷ 1000 |
| 5493000RRYPUX5O9MI08 | 2018 | c | income | Raw dollars instead of thousands | income ÷ 1000 |
| 5493001K7B4W8IPMZ254 | 2019 | a | income | Monthly instead of annual | income × 12 |
| 5493001K7B4W8IPMZ254 | 2019 | b | income | Monthly instead of annual | income × 12 |
| 5493001K7B4W8IPMZ254 | 2019 | c | income | Monthly instead of annual | income × 12 |
| 5493009TOEDMWVNG1442 | 2020 | a | income | Raw dollars instead of thousands | income ÷ 1000 |
| 5493009TOEDMWVNG1442 | 2020 | b | income | Raw dollars instead of thousands | income ÷ 1000 |
| 5493009TOEDMWVNG1442 | 2020 | c | income | Raw dollars instead of thousands | income ÷ 1000 |
| 549300CCELEPUO4TOE73 | 2018 | a | income | Raw dollars instead of thousands | income ÷ 1000 |
| 549300CCELEPUO4TOE73 | 2018 | c | income | Raw dollars instead of thousands | income ÷ 1000 |
| 549300CKKPTDS03YHG30 | 2018 | a | income | Raw dollars instead of thousands | income ÷ 1000 |
| 549300CKKPTDS03YHG30 | 2018 | c | income | Raw dollars instead of thousands | income ÷ 1000 |
| 549300CZYL6IDJDBUR04 | 2018 | a | income | Mixed monthly/annual (majority monthly) | income × 12 where income < threshold |
| 549300CZYL6IDJDBUR04 | 2018 | c | income | Mixed monthly/annual (majority monthly) | income × 12 where income < threshold |
| 549300DCGBXW5FJMV921 | 2018 | a | income | Monthly instead of annual | income × 12 |
| 549300DCGBXW5FJMV921 | 2018 | c | income | Monthly instead of annual | income × 12 |
| 549300DG3IV05V4C4E03 | 2024 | c | income | Unreliable (very low, not monthly) | → NULL |
| 549300DMHEHNYZ2OLB41 | 2018 | a | income | Unreliable (very low, not monthly) | → NULL |
| 549300DMHEHNYZ2OLB41 | 2018 | c | income | Unreliable (very low, not monthly) | → NULL |
| 549300EQED7LF41GHV46 | 2019 | a | income | Mixed monthly/annual (majority monthly) | income × 12 where income < threshold |
| 549300EQED7LF41GHV46 | 2019 | b | income | Mixed monthly/annual (majority monthly) | income × 12 where income < threshold |
| 549300EQED7LF41GHV46 | 2019 | c | income | Mixed monthly/annual (majority monthly) | income × 12 where income < threshold |
| 549300G3FCPL48R4HU28 | 2022 | b | income | Raw dollars instead of thousands | income ÷ 1000 |
| 549300G3FCPL48R4HU28 | 2022 | c | income | Raw dollars instead of thousands | income ÷ 1000 |
| 549300G5DVGM0ZHRQC33 | 2019 | a | income | Monthly instead of annual | income × 12 |
| 549300G5DVGM0ZHRQC33 | 2019 | b | income | Monthly instead of annual | income × 12 |
| 549300G5DVGM0ZHRQC33 | 2019 | c | income | Monthly instead of annual | income × 12 |
| 549300MZ8VZJOVC63092 | 2018 | a | income | Monthly instead of annual | income × 12 |
| 549300MZ8VZJOVC63092 | 2018 | c | income | Monthly instead of annual | income × 12 |
| 549300OLJQ30ZT21BC55 | 2022 | b | income | Monthly instead of annual | income × 12 |
| 549300OLJQ30ZT21BC55 | 2022 | c | income | Monthly instead of annual | income × 12 |
| 549300R9S3MVDV4MGF56 | 2018 | a | income | Monthly instead of annual | income × 12 |
| 549300R9S3MVDV4MGF56 | 2018 | c | income | Monthly instead of annual | income × 12 |
| 549300R9S3MVDV4MGF56 | 2019 | a | income | Monthly instead of annual | income × 12 |
| 549300R9S3MVDV4MGF56 | 2019 | b | income | Monthly instead of annual | income × 12 |
| 549300R9S3MVDV4MGF56 | 2019 | c | income | Monthly instead of annual | income × 12 |
| 549300T7HRS5DVBUPQ06 | 2024 | c | income | Unreliable (very low, not monthly) | → NULL |
| 549300V3UW6HP83URS67 | 2018 | a | income | Mixed monthly/annual (majority monthly) | income × 12 where income < threshold |
| 549300V3UW6HP83URS67 | 2018 | c | income | Mixed monthly/annual (majority monthly) | income × 12 where income < threshold |
| 549300ZPSJZO10ZAX556 | 2022 | b | income | Monthly instead of annual | income × 12 |
| 549300ZPSJZO10ZAX556 | 2022 | c | income | Monthly instead of annual | income × 12 |
| 984500C6D3DC0BE5E321 | 2024 | c | income | Monthly instead of annual | income × 12 |
| 984500EE76E4FF76X598 | 2019 | a | income | Monthly instead of annual | income × 12 |
| 984500EE76E4FF76X598 | 2019 | b | income | Monthly instead of annual | income × 12 |
| 984500EE76E4FF76X598 | 2019 | c | income | Monthly instead of annual | income × 12 |
| 2549006QWBCLPDAGO152 | 2018 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 2549006QWBCLPDAGO152 | 2018 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 2549006QWBCLPDAGO152 | 2019 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 2549006QWBCLPDAGO152 | 2019 | b | interest_rate | Placeholder/sentinel value | → NULL |
| 2549006QWBCLPDAGO152 | 2019 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 254900AKNL0HLB9M9B52 | 2018 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 254900AKNL0HLB9M9B52 | 2018 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 254900OC69XWBZ2A8244 | 2018 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 254900OC69XWBZ2A8244 | 2018 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 254900RQ2FMUUUQUGI02 | 2019 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 254900RQ2FMUUUQUGI02 | 2019 | b | interest_rate | Placeholder/sentinel value | → NULL |
| 254900RQ2FMUUUQUGI02 | 2019 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 254900XRTANBKSC2EB64 | 2018 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 254900XRTANBKSC2EB64 | 2018 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 54930022CQGV47Q8PM03 | 2019 | a | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 54930022CQGV47Q8PM03 | 2019 | b | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 54930022CQGV47Q8PM03 | 2019 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 5493002W52M3SYLFEX32 | 2018 | a | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 5493002W52M3SYLFEX32 | 2018 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 5493003ST36V2G6YFH54 | 2018 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 5493003ST36V2G6YFH54 | 2018 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 5493003ST36V2G6YFH54 | 2020 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 5493003ST36V2G6YFH54 | 2020 | b | interest_rate | Placeholder/sentinel value | → NULL |
| 5493003ST36V2G6YFH54 | 2020 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 549300A4SPX2JOQ71791 | 2018 | a | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300A4SPX2JOQ71791 | 2018 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300A4SPX2JOQ71791 | 2019 | a | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300A4SPX2JOQ71791 | 2019 | b | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300A4SPX2JOQ71791 | 2019 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300AEULLVYD8L9B04 | 2018 | a | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300AEULLVYD8L9B04 | 2018 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300AEULLVYD8L9B04 | 2019 | a | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300AEULLVYD8L9B04 | 2019 | b | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300AEULLVYD8L9B04 | 2019 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300CCELEPUO4TOE73 | 2019 | a | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300CCELEPUO4TOE73 | 2019 | b | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300CCELEPUO4TOE73 | 2019 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300JBMMNIOX7XU115 | 2018 | a | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300JBMMNIOX7XU115 | 2018 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300JGMQJ4R419LR70 | 2021 | b | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300JGMQJ4R419LR70 | 2021 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300JGMQJ4R419LR70 | 2022 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300PUBQGJ94VJ8I94 | 2018 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 549300PUBQGJ94VJ8I94 | 2018 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 549300PUBQGJ94VJ8I94 | 2019 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 549300PUBQGJ94VJ8I94 | 2019 | b | interest_rate | Placeholder/sentinel value | → NULL |
| 549300PUBQGJ94VJ8I94 | 2019 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 549300PUBQGJ94VJ8I94 | 2020 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 549300PUBQGJ94VJ8I94 | 2020 | b | interest_rate | Placeholder/sentinel value | → NULL |
| 549300PUBQGJ94VJ8I94 | 2020 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 549300WPK18IMSC1OM63 | 2021 | b | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300WPK18IMSC1OM63 | 2021 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300WPK18IMSC1OM63 | 2022 | c | interest_rate | Decimal instead of percentage | interest_rate × 100 |
| 549300ZA8T2KGQF6JK55 | 2018 | a | interest_rate | Placeholder/sentinel value | → NULL |
| 549300ZA8T2KGQF6JK55 | 2018 | c | interest_rate | Placeholder/sentinel value | → NULL |
| 254900048S1FLI3G2922 | 2024 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 2549000PNXYVRN44TV91 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 2549000PNXYVRN44TV91 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 2549000PNXYVRN44TV91 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 2549000PNXYVRN44TV91 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549000PNXYVRN44TV91 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549000PNXYVRN44TV91 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 25490021P4C9ZH17EO78 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 25490021P4C9ZH17EO78 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549005LG69OCBT9WE71 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006G3AQ9DK986571 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006L3U88MNTL7K26 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006L3U88MNTL7K26 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549006L3U88MNTL7K26 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 2549008US5LUWX2ZH247 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900ARL5FDX2OOH702 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900ARL5FDX2OOH702 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900BQVRDMH5K9QS04 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900BQVRDMH5K9QS04 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900BQVRDMH5K9QS04 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900DOBILWQF3MQ479 | 2020 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900DOBILWQF3MQ479 | 2020 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900DOBILWQF3MQ479 | 2020 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900JMPUH6DCQM3W65 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900JMPUH6DCQM3W65 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900LXHR6OP0TSZ915 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900LXHR6OP0TSZ915 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900LXHR6OP0TSZ915 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900MQ4O1DX3N88207 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900MQ4O1DX3N88207 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900MQ4O1DX3N88207 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900NZCGMT6517BA94 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900NZCGMT6517BA94 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900NZCGMT6517BA94 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900NZCGMT6517BA94 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900NZCGMT6517BA94 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900NZCGMT6517BA94 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900P7WLDQD9IUL038 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900P7WLDQD9IUL038 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900Q5026VQBAVI394 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900Q5026VQBAVI394 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900Q5026VQBAVI394 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900Q5026VQBAVI394 | 2020 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900Q5026VQBAVI394 | 2020 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900Q5026VQBAVI394 | 2020 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 254900QH6Q6RHDLHEC77 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900QH6Q6RHDLHEC77 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900QH6Q6RHDLHEC77 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900QH6Q6RHDLHEC77 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900QH6Q6RHDLHEC77 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900QH6Q6RHDLHEC77 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900QH6Q6RHDLHEC77 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900QH6Q6RHDLHEC77 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900R9A6TW85BTVS20 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900R9A6TW85BTVS20 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900R9A6TW85BTVS20 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900R9A6TW85BTVS20 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900R9A6TW85BTVS20 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900SI9TPITPEZ5332 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900SI9TPITPEZ5332 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900TM81D0YC1B9584 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900U7G0LJ4QNSFC86 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900U7G0LJ4QNSFC86 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900UFC85SQD8XYU52 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900UFC85SQD8XYU52 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900UFC85SQD8XYU52 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900UFC85SQD8XYU52 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900UFC85SQD8XYU52 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900VKT2R0R20CR104 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900VKT2R0R20CR104 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900VKT2R0R20CR104 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900WTZC5SSKIN2M11 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 254900ZW2DR74NU0ZY74 | 2024 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300032FNX4IOT2T93 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300032FNX4IOT2T93 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300032FNX4IOT2T93 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300032FNX4IOT2T93 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493000SW5GLNL7VNX12 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493000SW5GLNL7VNX12 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493000XMEVMIQFXVW32 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493000XMEVMIQFXVW32 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493000XMEVMIQFXVW32 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930025XJJO3T0M1A92 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930025XJJO3T0M1A92 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493003EAJYUK1F8B139 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493003EAJYUK1F8B139 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493003GJY287GIWFH36 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493003Q218ZJ8R1WN88 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 5493003Q218ZJ8R1WN88 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 5493003Q218ZJ8R1WN88 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 5493004M1U54OO4KKE09 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 5493004M1U54OO4KKE09 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 5493004OB0KD7VERQW98 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493004OB0KD7VERQW98 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493004OB0KD7VERQW98 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493004OB0KD7VERQW98 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493004OB0KD7VERQW98 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493004OB0KD7VERQW98 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493004OB0KD7VERQW98 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493004OB0KD7VERQW98 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930052808NIU3KIW97 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930052808NIU3KIW97 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930052808NIU3KIW97 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930052808NIU3KIW97 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930052808NIU3KIW97 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930052808NIU3KIW97 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930052808NIU3KIW97 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930052808NIU3KIW97 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930052808NIU3KIW97 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493005RDTT7HVODH436 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493005WWQ2221ORBN75 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493005WWQ2221ORBN75 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493005WWQ2221ORBN75 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930068H3KVFOUG0285 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2020 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2020 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2020 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2022 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930068H3KVFOUG0285 | 2022 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930074H8866HTIWU69 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930074H8866HTIWU69 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930074H8866HTIWU69 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930074H8866HTIWU69 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930074H8866HTIWU69 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007PAE4JN4B5BX31 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007XQ4LU452LQJ67 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493007XQ4LU452LQJ67 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 54930080168VA6Z8UX21 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 54930080168VA6Z8UX21 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300810F3G8UKNJ127 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300810F3G8UKNJ127 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300810F3G8UKNJ127 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300810F3G8UKNJ127 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300810F3G8UKNJ127 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300810F3G8UKNJ127 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493008WWKO0RD22RN65 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 5493008WWKO0RD22RN65 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A6NF3MQMOHKJ20 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A6NF3MQMOHKJ20 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A6NF3MQMOHKJ20 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A7W1NQ3BYZCB68 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A7W1NQ3BYZCB68 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A7W1NQ3BYZCB68 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A7W1NQ3BYZCB68 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A7W1NQ3BYZCB68 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A7W1NQ3BYZCB68 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A7W1NQ3BYZCB68 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300A7W1NQ3BYZCB68 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ANGNMQ8TDQAZ27 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ANGNMQ8TDQAZ27 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ANGNMQ8TDQAZ27 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300AQN2CY1MGE3J02 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300AQN2CY1MGE3J02 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ASIS4D2QDWH380 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ASIS4D2QDWH380 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ASIS4D2QDWH380 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ASIS4D2QDWH380 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ASIS4D2QDWH380 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300AUL77A8UKADB04 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300B8JSCK4ZNVX087 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300B8JSCK4ZNVX087 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300B8JSCK4ZNVX087 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300B8JSCK4ZNVX087 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300B8JSCK4ZNVX087 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300B8JSCK4ZNVX087 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300B8JSCK4ZNVX087 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300B8JSCK4ZNVX087 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300B8JSCK4ZNVX087 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300BM59EMYEJ5Q179 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300BM59EMYEJ5Q179 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300BM59EMYEJ5Q179 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300BREPLU2KKPI102 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300BREPLU2KKPI102 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300BWXVTVB5RRZN58 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300BWXVTVB5RRZN58 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300BWXVTVB5RRZN58 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300BWXVTVB5RRZN58 | 2022 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300BWXVTVB5RRZN58 | 2022 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300CIQ950OK0BS407 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300CIQ950OK0BS407 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300DM47883NLNBK72 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300DM47883NLNBK72 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300DM47883NLNBK72 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300DM47883NLNBK72 | 2020 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300DM47883NLNBK72 | 2020 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300DM47883NLNBK72 | 2020 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300DM47883NLNBK72 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300DM47883NLNBK72 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300DM47883NLNBK72 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300DTYWYXW2NOK656 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300DTYWYXW2NOK656 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300DU4JZVSGF3G964 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300DU4JZVSGF3G964 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300DU4JZVSGF3G964 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300E3QJQLKVB40W93 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300E3QJQLKVB40W93 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300E8WNDXIPI7FA49 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300E8WNDXIPI7FA49 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300EE39SNC5DXTC46 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300EE39SNC5DXTC46 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300EE39SNC5DXTC46 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300EXQ4TO1KWRTV95 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EXQ4TO1KWRTV95 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EXQ4TO1KWRTV95 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EXQ4TO1KWRTV95 | 2020 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EXQ4TO1KWRTV95 | 2020 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EXQ4TO1KWRTV95 | 2020 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EXQ4TO1KWRTV95 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EXQ4TO1KWRTV95 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EXQ4TO1KWRTV95 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300EZHVOHDWG07M20 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300EZHVOHDWG07M20 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300EZIROI6ZF7B419 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300EZIROI6ZF7B419 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300F263I3XPN55P35 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300F263I3XPN55P35 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300F263I3XPN55P35 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300FS35FQXZRU4Z45 | 2023 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300FS35FQXZRU4Z45 | 2023 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300FS35FQXZRU4Z45 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300G6RZM5T8NQJW74 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300G6RZM5T8NQJW74 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300G6RZM5T8NQJW74 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300G6RZM5T8NQJW74 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300G6RZM5T8NQJW74 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300G6RZM5T8NQJW74 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300G6RZM5T8NQJW74 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300G6RZM5T8NQJW74 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300GFM3T3SN2KS138 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300GFM3T3SN2KS138 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300GFM3T3SN2KS138 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300GXXWNCQH5XX485 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300GXXWNCQH5XX485 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H88NIQ6005M718 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H88NIQ6005M718 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H88NIQ6005M718 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H88NIQ6005M718 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H88NIQ6005M718 | 2023 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H88NIQ6005M718 | 2023 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H89M6KDLFKMQ19 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H89M6KDLFKMQ19 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H89M6KDLFKMQ19 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H89M6KDLFKMQ19 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300H89M6KDLFKMQ19 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300HGDJQ37M5BE268 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300HGDJQ37M5BE268 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300HGDJQ37M5BE268 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300HGDJQ37M5BE268 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300HGDJQ37M5BE268 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300HGDJQ37M5BE268 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300HGDJQ37M5BE268 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300HGDJQ37M5BE268 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300HIKKZQ0TVJWA08 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300HIKKZQ0TVJWA08 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300IWSSP83SNIPE63 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300IWSSP83SNIPE63 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300IWSSP83SNIPE63 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300JXIQJRQ1HME757 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300JXIQJRQ1HME757 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300K5OUR2FD1NV703 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300K6L4VXCXWN4205 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300L3C3LKXG2XBS96 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300L3C3LKXG2XBS96 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300L3C3LKXG2XBS96 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300L3C3LKXG2XBS96 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300L3C3LKXG2XBS96 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300L8JRY60EOROT34 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300L8JRY60EOROT34 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LC0BP2X1F6BE13 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LC0BP2X1F6BE13 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LC0BP2X1F6BE13 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LI67PLCC1Z3P80 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300LI67PLCC1Z3P80 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300LI67PLCC1Z3P80 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300LI67PLCC1Z3P80 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300LI67PLCC1Z3P80 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300LTO41UX0MSL179 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LTO41UX0MSL179 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LTO41UX0MSL179 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LTO41UX0MSL179 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LTO41UX0MSL179 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300LTO41UX0MSL179 | 2024 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300M6EHOPLD2SFD66 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300NZLFJGIDOXDW97 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300NZLFJGIDOXDW97 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300NZLFJGIDOXDW97 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300OARXTADS0L8L11 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300OARXTADS0L8L11 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300OARXTADS0L8L11 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300OARXTADS0L8L11 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300OARXTADS0L8L11 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ON2ILGPUVKZT28 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ON2ILGPUVKZT28 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ON2ILGPUVKZT28 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ON2ILGPUVKZT28 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ON2ILGPUVKZT28 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ON2ILGPUVKZT28 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300OP314BNT2VUU11 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300OP314BNT2VUU11 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300OP314BNT2VUU11 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300PEQH6YK4DH0490 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300PEQH6YK4DH0490 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300PEQH6YK4DH0490 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300PJX0ZHPQ614C72 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300PJX0ZHPQ614C72 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300PJX0ZHPQ614C72 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300PJX0ZHPQ614C72 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300PJX0ZHPQ614C72 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300RIR04Y64SF4306 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300RIR04Y64SF4306 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300RIR04Y64SF4306 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300RLNUFWOJLX7W13 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300RLNUFWOJLX7W13 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300RO0FORROX83086 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300RO0FORROX83086 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300RO0FORROX83086 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300S1TXPDZJPDYW17 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300S1TXPDZJPDYW17 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300S1TXPDZJPDYW17 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300S1TXPDZJPDYW17 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300S1TXPDZJPDYW17 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300SNZET0P5BZ3J56 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SNZET0P5BZ3J56 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SNZET0P5BZ3J56 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SNZET0P5BZ3J56 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300SNZET0P5BZ3J56 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300SUCQ1358EGVE89 | 2020 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SUCQ1358EGVE89 | 2020 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SUCQ1358EGVE89 | 2020 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SUCQ1358EGVE89 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SUCQ1358EGVE89 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SUCQ1358EGVE89 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300SUCQ1358EGVE89 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300SWKVL60SKWK981 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300SWKVL60SKWK981 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300SWKVL60SKWK981 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300SWKVL60SKWK981 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300T6MV7ZAIL4N325 | 2022 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300T6MV7ZAIL4N325 | 2022 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300T6MV7ZAIL4N325 | 2023 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300T6MV7ZAIL4N325 | 2023 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300T6MV7ZAIL4N325 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300TSIYX9RDYWC806 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300TSIYX9RDYWC806 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300TSIYX9RDYWC806 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300TSIYX9RDYWC806 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300TSIYX9RDYWC806 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300TYKYOVQFBZBV90 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300TYKYOVQFBZBV90 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300TYKYOVQFBZBV90 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300U620B2MZKUUZ24 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300U620B2MZKUUZ24 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300U620B2MZKUUZ24 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300UE4FFDNV7ELD54 | 2019 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300UE4FFDNV7ELD54 | 2019 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300UE4FFDNV7ELD54 | 2019 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300UE4FFDNV7ELD54 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300UE4FFDNV7ELD54 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300UE4FFDNV7ELD54 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V36YE6JCCEJB76 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300V36YE6JCCEJB76 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300V5MRNYGHSGR060 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V5MRNYGHSGR060 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V5MRNYGHSGR060 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V6F7N9BL5STN83 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V6F7N9BL5STN83 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V6F7N9BL5STN83 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V6F7N9BL5STN83 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V6F7N9BL5STN83 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V71V12DS7RW675 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V71V12DS7RW675 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V71V12DS7RW675 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300V7I9AZRM78QT92 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VG7NKKVNYENV67 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VG7NKKVNYENV67 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VG7NKKVNYENV67 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VG7NKKVNYENV67 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VG7NKKVNYENV67 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VG7NKKVNYENV67 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VG7NKKVNYENV67 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VG7NKKVNYENV67 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VHSPIDK7A5D559 | 2018 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300VHSPIDK7A5D559 | 2018 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300VMVKSQE1B7DD43 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VMVKSQE1B7DD43 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VMVKSQE1B7DD43 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VMVKSQE1B7DD43 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VMVKSQE1B7DD43 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VMVKSQE1B7DD43 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VMVKSQE1B7DD43 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300VMVKSQE1B7DD43 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300W8XIPJGDECVX07 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300W8XIPJGDECVX07 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300W8XIPJGDECVX07 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300W8XIPJGDECVX07 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300WN1LQYJX9HOU28 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300WN1LQYJX9HOU28 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300X2FI65C4A30O27 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQ8DRJT0OETE90 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQ8DRJT0OETE90 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQ8DRJT0OETE90 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2019 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2019 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2019 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2023 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2023 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XQVJ1XBNFA5536 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300XYX207IABFXL60 | 2024 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300Y4CEEGC4KCFI45 | 2020 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300Y4CEEGC4KCFI45 | 2020 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300Y4CEEGC4KCFI45 | 2020 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300YKMWXBZEEA1471 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300YKMWXBZEEA1471 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300YKMWXBZEEA1471 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300YOCINIK5RN6R57 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300YOCINIK5RN6R57 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZBBGOL4MIK0L71 | 2021 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZBBGOL4MIK0L71 | 2021 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZBBGOL4MIK0L71 | 2021 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZC48GOYCGYFE82 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZC48GOYCGYFE82 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZCR1XCLNEEI724 | 2020 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZCR1XCLNEEI724 | 2020 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZCR1XCLNEEI724 | 2020 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZCR1XCLNEEI724 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZCR1XCLNEEI724 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZCR1XCLNEEI724 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZDIZM7Y1MHKG98 | 2018 | a | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZDIZM7Y1MHKG98 | 2018 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZS1TR1B4LHX377 | 2023 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZS1TR1B4LHX377 | 2023 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 549300ZZIAVB1U6Z4S78 | 2021 | a | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZZIAVB1U6Z4S78 | 2021 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZZIAVB1U6Z4S78 | 2021 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZZIAVB1U6Z4S78 | 2022 | b | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 549300ZZIAVB1U6Z4S78 | 2022 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 562V2SM4I80MJO5HYB83 | 2022 | b | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 562V2SM4I80MJO5HYB83 | 2022 | c | lender_credits | Likely in hundreds instead of dollars | lender_credits × 100 |
| 984500B659A36CF0D469 | 2024 | c | lender_credits | Percentage points instead of dollars | lender_credits × loan_amount ÷ 100 |
| 2549003PJRW71IW7BH70 | 2023 | b | loan_term | All terms = 1 month | → NULL |
| 2549003PJRW71IW7BH70 | 2023 | c | loan_term | All terms = 1 month | → NULL |
| 254900PCEQAPPZS8KL59 | 2022 | b | loan_term | All terms = 1 month | → NULL |
| 254900PCEQAPPZS8KL59 | 2022 | c | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2020 | a | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2020 | b | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2020 | c | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2021 | a | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2021 | b | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2021 | c | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2022 | b | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2022 | c | loan_term | All terms = 1 month | → NULL |
| 254900Z5QRSHW4Y8CR51 | 2024 | c | loan_term | All terms = 1 month | → NULL |
| 549300CUF3Q2PQGM9256 | 2022 | b | loan_term | All terms = 1 month | → NULL |
| 549300CUF3Q2PQGM9256 | 2022 | c | loan_term | All terms = 1 month | → NULL |
| 549300CUF3Q2PQGM9256 | 2023 | b | loan_term | All terms = 1 month | → NULL |
| 549300CUF3Q2PQGM9256 | 2023 | c | loan_term | All terms = 1 month | → NULL |
| 549300CUF3Q2PQGM9256 | 2024 | c | loan_term | All terms = 1 month | → NULL |
| 549300MTZQME2DHKE115 | 2024 | c | loan_term | All terms = 1 month | → NULL |
| FT6J43S06X6CLJ0R0B48 | 2021 | a | loan_term | All terms = 1 month | → NULL |
| FT6J43S06X6CLJ0R0B48 | 2021 | b | loan_term | All terms = 1 month | → NULL |
| FT6J43S06X6CLJ0R0B48 | 2021 | c | loan_term | All terms = 1 month | → NULL |
| 254900U1OBL1MC6M6518 | 2024 | c | origination_charges | Reporting in points instead of dollars | Multiply origination_charges by loan_amount/100 |
| 254900ZKVJDEYHV22276 | 2024 | c | origination_charges | Reporting in points instead of dollars | Multiply origination_charges by loan_amount/100 |
| 54930092HIO8R4060L05 | 2021 | a | origination_charges | Reporting in points instead of dollars | Multiply origination_charges by loan_amount/100 |
| 54930092HIO8R4060L05 | 2021 | b | origination_charges | Reporting in points instead of dollars | Multiply origination_charges by loan_amount/100 |
| 54930092HIO8R4060L05 | 2021 | c | origination_charges | Reporting in points instead of dollars | Multiply origination_charges by loan_amount/100 |
| 549300ESJ15K604ZW846 | 2019 | a | origination_charges | Reporting in points instead of dollars | Multiply origination_charges by loan_amount/100 |
| 549300ESJ15K604ZW846 | 2019 | b | origination_charges | Reporting in points instead of dollars | Multiply origination_charges by loan_amount/100 |
| 549300ESJ15K604ZW846 | 2019 | c | origination_charges | Reporting in points instead of dollars | Multiply origination_charges by loan_amount/100 |
| 25490053IH908EDF8D65 | 2020 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 25490053IH908EDF8D65 | 2020 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 25490053IH908EDF8D65 | 2020 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 25490053IH908EDF8D65 | 2021 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 25490053IH908EDF8D65 | 2021 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 25490053IH908EDF8D65 | 2021 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 25490053IH908EDF8D65 | 2022 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 25490053IH908EDF8D65 | 2022 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 25490053IH908EDF8D65 | 2024 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 2549008SGDIASSJGSR05 | 2019 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 2549008SGDIASSJGSR05 | 2019 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 2549008SGDIASSJGSR05 | 2019 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 2549009I5SQ5XN6WFU76 | 2018 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 2549009I5SQ5XN6WFU76 | 2018 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 2549009I5SQ5XN6WFU76 | 2024 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 254900R0GC9CW77VXE28 | 2018 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 254900R0GC9CW77VXE28 | 2018 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 5493003V40VGM7YDFM54 | 2024 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 5493008XX3NZ5S384W16 | 2024 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300BTTIQWNC2CFA21 | 2018 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300BTTIQWNC2CFA21 | 2018 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300CY7WNAHKHYSJ73 | 2019 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300CY7WNAHKHYSJ73 | 2019 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300CY7WNAHKHYSJ73 | 2019 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2020 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2020 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2020 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2021 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2021 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2021 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2022 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2022 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2023 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300E5018DD89WPX79 | 2023 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300HIVO8XPBPNVG69 | 2021 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300HIVO8XPBPNVG69 | 2021 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300HIVO8XPBPNVG69 | 2021 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300KX4S1573HISS22 | 2024 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300PIL8LFAQ04XC20 | 2021 | a | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300PIL8LFAQ04XC20 | 2021 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300PIL8LFAQ04XC20 | 2021 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300PIL8LFAQ04XC20 | 2022 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300PIL8LFAQ04XC20 | 2022 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300WMQ4WI6QXK7803 | 2022 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 549300WMQ4WI6QXK7803 | 2022 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| MCXHCL35UUWDZK7NCQ61 | 2022 | b | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| MCXHCL35UUWDZK7NCQ61 | 2022 | c | prepayment_penalty_term | Years instead of months | prepayment_penalty_term × 12 |
| 2549000BG2198NFS0K59 | 2019 | a | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2019 | b | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2019 | c | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2020 | a | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2020 | b | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2020 | c | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2021 | a | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2021 | b | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2021 | c | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2022 | b | property_value | Placeholder $5K value | → NULL |
| 2549000BG2198NFS0K59 | 2022 | c | property_value | Placeholder $5K value | → NULL |
| 2549003ZYZM7FHPZV590 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 2549003ZYZM7FHPZV590 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 2549003ZYZM7FHPZV590 | 2021 | a | property_value | Placeholder $5K value | → NULL |
| 2549003ZYZM7FHPZV590 | 2021 | b | property_value | Placeholder $5K value | → NULL |
| 2549003ZYZM7FHPZV590 | 2021 | c | property_value | Placeholder $5K value | → NULL |
| 2549003ZYZM7FHPZV590 | 2022 | b | property_value | Placeholder $5K value | → NULL |
| 2549003ZYZM7FHPZV590 | 2022 | c | property_value | Placeholder $5K value | → NULL |
| 254900AHWPNJK6FS8K98 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 254900AHWPNJK6FS8K98 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 254900AKNL0HLB9M9B52 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 254900AKNL0HLB9M9B52 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 254900BQENI8J22AJ557 | 2019 | a | property_value | Placeholder $5K value | → NULL |
| 254900BQENI8J22AJ557 | 2019 | b | property_value | Placeholder $5K value | → NULL |
| 254900BQENI8J22AJ557 | 2019 | c | property_value | Placeholder $5K value | → NULL |
| 254900BQENI8J22AJ557 | 2022 | b | property_value | Placeholder $5K value | → NULL |
| 254900BQENI8J22AJ557 | 2022 | c | property_value | Placeholder $5K value | → NULL |
| 254900BQENI8J22AJ557 | 2024 | c | property_value | Placeholder $5K value | → NULL |
| 254900NSXWCE07HAP496 | 2024 | c | property_value | Placeholder $5K value | → NULL |
| 254900P86CWA5DCK0Y27 | 2020 | a | property_value | Placeholder $5K value | → NULL |
| 254900P86CWA5DCK0Y27 | 2020 | b | property_value | Placeholder $5K value | → NULL |
| 254900P86CWA5DCK0Y27 | 2020 | c | property_value | Placeholder $5K value | → NULL |
| 254900VUE5T1INQKZ414 | 2020 | a | property_value | Placeholder $5K value | → NULL |
| 254900VUE5T1INQKZ414 | 2020 | b | property_value | Placeholder $5K value | → NULL |
| 254900VUE5T1INQKZ414 | 2020 | c | property_value | Placeholder $5K value | → NULL |
| 254900YY8IQK41QDLZ95 | 2019 | a | property_value | Placeholder $5K value | → NULL |
| 254900YY8IQK41QDLZ95 | 2019 | b | property_value | Placeholder $5K value | → NULL |
| 254900YY8IQK41QDLZ95 | 2019 | c | property_value | Placeholder $5K value | → NULL |
| 5493003ST36V2G6YFH54 | 2020 | a | property_value | Placeholder $5K value | → NULL |
| 5493003ST36V2G6YFH54 | 2020 | b | property_value | Placeholder $5K value | → NULL |
| 5493003ST36V2G6YFH54 | 2020 | c | property_value | Placeholder $5K value | → NULL |
| 5493005ORRKI2S5T8912 | 2024 | c | property_value | Placeholder $5K value | → NULL |
| 5493007W1V0GCODW0238 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 5493007W1V0GCODW0238 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 5493008O3PHZLQU70I63 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 5493008O3PHZLQU70I63 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 5493008O3PHZLQU70I63 | 2019 | a | property_value | Placeholder $5K value | → NULL |
| 5493008O3PHZLQU70I63 | 2019 | b | property_value | Placeholder $5K value | → NULL |
| 5493008O3PHZLQU70I63 | 2019 | c | property_value | Placeholder $5K value | → NULL |
| 5493009I5VHHGVFQ1O54 | 2019 | a | property_value | Placeholder $5K value | → NULL |
| 5493009I5VHHGVFQ1O54 | 2019 | b | property_value | Placeholder $5K value | → NULL |
| 5493009I5VHHGVFQ1O54 | 2019 | c | property_value | Placeholder $5K value | → NULL |
| 549300CN50N3250U7V79 | 2021 | a | property_value | Placeholder $5K value | → NULL |
| 549300CN50N3250U7V79 | 2021 | b | property_value | Placeholder $5K value | → NULL |
| 549300CN50N3250U7V79 | 2021 | c | property_value | Placeholder $5K value | → NULL |
| 549300L8L9JJ29KEYQ27 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 549300L8L9JJ29KEYQ27 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 549300L8L9JJ29KEYQ27 | 2019 | a | property_value | Placeholder $5K value | → NULL |
| 549300L8L9JJ29KEYQ27 | 2019 | b | property_value | Placeholder $5K value | → NULL |
| 549300L8L9JJ29KEYQ27 | 2019 | c | property_value | Placeholder $5K value | → NULL |
| 549300MUCXO2NIUSBW51 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 549300MUCXO2NIUSBW51 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 549300OI0FPX2S8I4O26 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 549300OI0FPX2S8I4O26 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 549300PVF6X52PEPRD05 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 549300PVF6X52PEPRD05 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 549300PVF6X52PEPRD05 | 2019 | a | property_value | Placeholder $5K value | → NULL |
| 549300PVF6X52PEPRD05 | 2019 | b | property_value | Placeholder $5K value | → NULL |
| 549300PVF6X52PEPRD05 | 2019 | c | property_value | Placeholder $5K value | → NULL |
| 549300RJ5S2V4F8C4H36 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 549300RJ5S2V4F8C4H36 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 549300RM0VO0UBTMOZ05 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 549300RM0VO0UBTMOZ05 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 549300RM0VO0UBTMOZ05 | 2019 | c | property_value | Placeholder $5K value | → NULL |
| 549300ZCBPNYTPHTML33 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 549300ZCBPNYTPHTML33 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 984500EE76E4FF76X598 | 2018 | a | property_value | Placeholder $5K value | → NULL |
| 984500EE76E4FF76X598 | 2018 | c | property_value | Placeholder $5K value | → NULL |
| 2549001FVJ5MZI4YAE11 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 2549001FVJ5MZI4YAE11 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 2549002YTHQ90AS4OM73 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 2549002YTHQ90AS4OM73 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 2549002YTHQ90AS4OM73 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900C2QXQ435D8TY78 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900C2QXQ435D8TY78 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900C2QXQ435D8TY78 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900CN1DD55MJDFH69 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900CN1DD55MJDFH69 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900FYIFXD18THEZ65 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900FYIFXD18THEZ65 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900FYIFXD18THEZ65 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900FYIFXD18THEZ65 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900FYIFXD18THEZ65 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900HI61SSH8KEBQ58 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900HI61SSH8KEBQ58 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900HI61SSH8KEBQ58 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900MFGTAZXMCELW10 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900MFGTAZXMCELW10 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900R9A6TW85BTVS20 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900R9A6TW85BTVS20 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900R9A6TW85BTVS20 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900T37KTTXKCK3416 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900T37KTTXKCK3416 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900T37KTTXKCK3416 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900TG14TB8RKS3U42 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 254900TG14TB8RKS3U42 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493002578XYSRFY2C51 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493002578XYSRFY2C51 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 54930027GUR70U427Y19 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 54930027GUR70U427Y19 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 54930027GUR70U427Y19 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 54930032YM5T2EF3J715 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 54930032YM5T2EF3J715 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493003B1W77K6SD3U22 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493003B1W77K6SD3U22 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493003B1W77K6SD3U22 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493003FTQTUEICOS793 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 54930040HIIQWXRS6K39 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 54930040HIIQWXRS6K39 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 54930040HIIQWXRS6K39 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493005R24FV5DFFXW42 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493006L4FQIQFVFNS72 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493006L4FQIQFVFNS72 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493006L4FQIQFVFNS72 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493006MFBT1AI7V3W25 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493006MFBT1AI7V3W25 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493006P0JVBBAHVXV08 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493006P0JVBBAHVXV08 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493006P0JVBBAHVXV08 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493007VDBJSG0E0S678 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493007VDBJSG0E0S678 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493007VDBJSG0E0S678 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493008TK613NXC2EN84 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493008TK613NXC2EN84 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 5493008TK613NXC2EN84 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300AQN2CY1MGE3J02 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300AQN2CY1MGE3J02 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300B3J2ZHX7QFH171 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300B3J2ZHX7QFH171 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300B81YWV4GBENI49 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300B81YWV4GBENI49 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300CR6ELPCYKQRB34 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300CR6ELPCYKQRB34 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300CR6ELPCYKQRB34 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300DEVPMBR765WH45 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300DEVPMBR765WH45 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300DT7WZ1SOTNFJ62 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300DT7WZ1SOTNFJ62 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300DT7WZ1SOTNFJ62 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E02I9FR0A6YT35 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E02I9FR0A6YT35 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E5018DD89WPX79 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E5018DD89WPX79 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E5018DD89WPX79 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E51HYVR7T2SU19 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E51HYVR7T2SU19 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E8HNQO52YYHI82 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E8HNQO52YYHI82 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300E8HNQO52YYHI82 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300G3FCPL48R4HU28 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300G3FCPL48R4HU28 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300G3FCPL48R4HU28 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300GBZNWRGLS7CP89 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300GMXZWXNOS1US34 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300GMXZWXNOS1US34 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300GNIV169ZIHU012 | 2024 | c | rate_spread | Basis points instead of pp | rate_spread ÷ 100 |
| 549300GWD9H4FQ2VR805 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300GWD9H4FQ2VR805 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300H68PK1CZUVUQ73 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300H68PK1CZUVUQ73 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300H8H31LPYGJEW50 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300HQL7U3JZ4LCJ17 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300HVW3AI97UKTO72 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300HVW3AI97UKTO72 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300IZ5GOBKOERL931 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300IZ5GOBKOERL931 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300IZ5GOBKOERL931 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300K74HMS2QKZU479 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300K74HMS2QKZU479 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300KQHWNSVCE2MY88 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300KQHWNSVCE2MY88 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300KQHWNSVCE2MY88 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300KWKWPEKL40WD44 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300KWKWPEKL40WD44 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300LBSJDS5R7PIK62 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300LBSJDS5R7PIK62 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300LBSJDS5R7PIK62 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300LEOR2LO8J9RT29 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300LEOR2LO8J9RT29 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300LEOR2LO8J9RT29 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300M6EHOPLD2SFD66 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300M6EHOPLD2SFD66 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300MPGZVO0YIGL418 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300MPGZVO0YIGL418 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300MPGZVO0YIGL418 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300N6U5XCH70TOK35 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300N6U5XCH70TOK35 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300N6U5XCH70TOK35 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300ODTHTLPTSBD612 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300ODTHTLPTSBD612 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OFI1WD07JPI032 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OFI1WD07JPI032 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OFI1WD07JPI032 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OLJQ30ZT21BC55 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OLJQ30ZT21BC55 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OLJQ30ZT21BC55 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OZIFHR7THILC90 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OZIFHR7THILC90 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300OZIFHR7THILC90 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300Q3RKNDNMSDEE10 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300Q3RKNDNMSDEE10 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300Q3RKNDNMSDEE10 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300QBV10SC3TEU133 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300QNPOF1WCRKLZ94 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300QNPOF1WCRKLZ94 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300R8VPSFM828B312 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300R8VPSFM828B312 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300RBWA4J4QTPSY69 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300RBWA4J4QTPSY69 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300RBWA4J4QTPSY69 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300S19BPWGHQOYU13 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300S19BPWGHQOYU13 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300T0I2M6IPLVEN24 | 2023 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300T0I2M6IPLVEN24 | 2023 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300T73HHIEIUU5F67 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300TOOOOW36EX6R40 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300TOOOOW36EX6R40 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300TOOOOW36EX6R40 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300TTP68WVF1GME12 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300TTP68WVF1GME12 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300TTP68WVF1GME12 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300U620B2MZKUUZ24 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300U620B2MZKUUZ24 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300U620B2MZKUUZ24 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300U82B82JH54TO79 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300ULXKJUJDK2RQ54 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300ULXKJUJDK2RQ54 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300ULXKJUJDK2RQ54 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300UVXY7S004OQL53 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300UVXY7S004OQL53 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300UVXY7S004OQL53 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300VDK2EPK7QQKY80 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300VDK2EPK7QQKY80 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300VRYDYOPWK5MO08 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300W1AS8CZYV4MQ17 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300W1AS8CZYV4MQ17 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300W1AS8CZYV4MQ17 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300W5A6PQ1VJG3497 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300WHACUOETOKO184 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300WHACUOETOKO184 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300WHACUOETOKO184 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300WK15ESCDNJ5M35 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300WK15ESCDNJ5M35 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300WK15ESCDNJ5M35 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300XL5688T9WLKS84 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300XY701IELCE5Q08 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300XY701IELCE5Q08 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300XY701IELCE5Q08 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300YLIY5SO2XSK094 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300YLIY5SO2XSK094 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300Z6QWABFYI73E79 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300Z6QWABFYI73E79 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300ZWNETGFXTBBY03 | 2021 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300ZWNETGFXTBBY03 | 2021 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 549300ZWNETGFXTBBY03 | 2021 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 635400IW1QMK3FNFF894 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 635400IW1QMK3FNFF894 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 635400IW1QMK3FNFF894 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 8AI385EP1ZJCMUOZ8022 | 2020 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 8AI385EP1ZJCMUOZ8022 | 2020 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 8AI385EP1ZJCMUOZ8022 | 2020 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 8AI385EP1ZJCMUOZ8022 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| 8WH0EE09O9V05QJZ3V89 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| IWRZQFYIRJ0IMURZBB68 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| IWRZQFYIRJ0IMURZBB68 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| K0ZDN2CBIQC0EHBKNK35 | 2023 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| K0ZDN2CBIQC0EHBKNK35 | 2023 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| KV8W1JTB8FZ821S5ED75 | 2018 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| KV8W1JTB8FZ821S5ED75 | 2018 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| KV8W1JTB8FZ821S5ED75 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| KV8W1JTB8FZ821S5ED75 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| QOT5WN9RBKQTFRVKEV31 | 2022 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| QOT5WN9RBKQTFRVKEV31 | 2022 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| R7JQ9JTCFHXBQU4XIT26 | 2024 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| VNOO6EITDJ2YUEBMSZ83 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| VNOO6EITDJ2YUEBMSZ83 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| VNOO6EITDJ2YUEBMSZ83 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
| YQI2CPR3Z44KAR0HG822 | 2019 | a | rate_spread | Decimal instead of pp | rate_spread × 100 |
| YQI2CPR3Z44KAR0HG822 | 2019 | b | rate_spread | Decimal instead of pp | rate_spread × 100 |
| YQI2CPR3Z44KAR0HG822 | 2019 | c | rate_spread | Decimal instead of pp | rate_spread × 100 |
