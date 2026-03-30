# Historical CSV Data

Place Xero Profit & Loss CSV exports here for migration into JSON snapshots.

## Expected CSV format

The CSV format matches what Xero exports from **Reports > Profit and Loss**:

```
Account,2023
10001 - Offering EFT,"$245,000.00"
40100 - Ministry Staff Salaries,"$82,000.00"
...
```

### Column layout

| Column | Description |
|--------|-------------|
| Column 0 (Account) | Account code + name, e.g. `10001 - Offering EFT` |
| Column 1+ | Period amounts (year or month labels) |

### Naming convention

Name files with the year included so the migration script can auto-detect it:

- `profit_and_loss_2020.csv`
- `profit_and_loss_2021.csv`
- `pl_2022.csv`
- `2023_annual.csv`

Any filename containing a four-digit year (2020-2024) will work.

### Supported formats

- **Encoding**: UTF-8, UTF-8 with BOM, Latin-1
- **Amounts**: `$1,500.00`, `1500.00`, `(500.00)` for negatives, `-` or blank for zero
- **Account format**: `10001 - Offering EFT` (code + name) or just `Offering EFT` (name only)
- **Headers**: Title rows like "Profit & Loss" and blank rows are skipped automatically
- **Summary rows**: Rows starting with "Total", "Net", or "Gross" are skipped

### Account codes

Account codes must match entries in `config/chart_of_accounts.yaml`. Both current and legacy accounts are supported. See `template.csv` for all valid current account codes.

## Running the migration

```bash
# From project root
python -m scripts.migrate_historical

# With options
python -m scripts.migrate_historical --input-dir data/historical --output-dir data/snapshots --save-report migration_report.json
```

## Verifying results

```bash
python -m scripts.verify_migration
```

## Files

- `template.csv` — Empty template showing all current account codes
- `sample_2023.csv` — Sample data demonstrating the format (not real figures)
