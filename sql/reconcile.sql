-- reconcile.sql
-- The same checks as src/reconcile.py, in SQLite.
-- Run with: python src/run_sql.py  (loads the CSVs, then runs each "-- name:" block)
-- Tables loaded by run_sql.py: security_master, custodian, accounting (cusip stored as TEXT)

-- name: setup
-- Normalize IDs: an all-digit CUSIP shorter than 9 chars lost its leading zeros.
-- substr('000000000' || cusip, -9) left-pads to 9 characters.
-- GLOB '*[^0-9]*' matches anything containing a non-digit, so NOT GLOB = digits only.
DROP TABLE IF EXISTS acct_norm;
CREATE TABLE acct_norm AS
SELECT *,
       cusip AS raw_cusip,
       CASE WHEN length(cusip) < 9 AND cusip NOT GLOB '*[^0-9]*'
            THEN substr('000000000' || cusip, -9) ELSE cusip END AS norm_cusip
FROM accounting;

-- One row per CUSIP (duplicates are reported separately below)
DROP TABLE IF EXISTS acct;
CREATE TABLE acct AS
SELECT * FROM acct_norm
WHERE rowid IN (SELECT MIN(rowid) FROM acct_norm GROUP BY norm_cusip);

-- name: id_format
SELECT norm_cusip AS cusip, 'id_format' AS break_type, raw_cusip AS accounting_value
FROM acct_norm
WHERE raw_cusip <> norm_cusip
GROUP BY norm_cusip;

-- name: duplicate_position
SELECT norm_cusip AS cusip, 'duplicate_position' AS break_type, COUNT(*) AS row_count
FROM acct_norm
GROUP BY norm_cusip
HAVING COUNT(*) > 1;

-- name: missing_in_accounting
-- LEFT JOIN keeps every custodian row; where no accounting row matched, a.* is NULL.
SELECT c.cusip, 'missing_in_accounting' AS break_type
FROM custodian c
LEFT JOIN acct a ON a.norm_cusip = c.cusip
WHERE a.norm_cusip IS NULL;

-- name: missing_at_custodian
SELECT a.norm_cusip AS cusip, 'missing_at_custodian' AS break_type
FROM acct a
LEFT JOIN custodian c ON c.cusip = a.norm_cusip
WHERE c.cusip IS NULL;

-- name: timing_difference
SELECT c.cusip, 'timing_difference' AS break_type,
       c.as_of_date AS custodian_value, a.as_of_date AS accounting_value
FROM custodian c
JOIN acct a ON a.norm_cusip = c.cusip
WHERE c.as_of_date <> a.as_of_date;

-- name: value_mismatch
-- Tolerance = MAX($1.00, 0.01% of the custodian value). Timing rows are excluded
-- because their value differences are explained by the date, not a separate break.
SELECT c.cusip, 'market_value_mismatch' AS break_type,
       c.market_value AS custodian_value, a.market_value AS accounting_value,
       ROUND(a.market_value - c.market_value, 2) AS difference
FROM custodian c JOIN acct a ON a.norm_cusip = c.cusip
WHERE c.as_of_date = a.as_of_date
  AND ABS(a.market_value - c.market_value) > MAX(1.0, 0.0001 * ABS(c.market_value))
UNION ALL
SELECT c.cusip, 'book_value_mismatch',
       c.book_value, a.book_value, ROUND(a.book_value - c.book_value, 2)
FROM custodian c JOIN acct a ON a.norm_cusip = c.cusip
WHERE c.as_of_date = a.as_of_date
  AND ABS(a.book_value - c.book_value) > MAX(1.0, 0.0001 * ABS(c.book_value))
UNION ALL
SELECT c.cusip, 'accrued_interest_mismatch',
       c.accrued_interest, a.accrued_interest, ROUND(a.accrued_interest - c.accrued_interest, 2)
FROM custodian c JOIN acct a ON a.norm_cusip = c.cusip
WHERE c.as_of_date = a.as_of_date
  AND ABS(a.accrued_interest - c.accrued_interest) > MAX(1.0, 0.0001 * ABS(c.accrued_interest));

-- name: rating_mismatch
-- Three-way join: custodian + accounting + security master.
SELECT c.cusip, 'rating_mismatch' AS break_type,
       c.rating AS custodian_value, a.rating AS accounting_value, m.rating AS master_value
FROM custodian c
JOIN acct a            ON a.norm_cusip = c.cusip
JOIN security_master m ON m.cusip = c.cusip
WHERE a.rating <> m.rating OR c.rating <> m.rating;

-- name: mv_dollar_impact
SELECT ROUND(SUM(ABS(a.market_value - c.market_value)), 2) AS abs_impact,
       ROUND(SUM(a.market_value - c.market_value), 2)      AS net_impact
FROM custodian c JOIN acct a ON a.norm_cusip = c.cusip
WHERE c.as_of_date = a.as_of_date
  AND ABS(a.market_value - c.market_value) > MAX(1.0, 0.0001 * ABS(c.market_value));
