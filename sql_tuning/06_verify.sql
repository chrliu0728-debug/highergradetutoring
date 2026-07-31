-- =====================================================================
-- Verification for 05_tuned_full_dedup.sql
-- =====================================================================
-- EXPECTATION SETTING: old and new will NOT match on every column, and
-- that is the point. The old query's MasterFX fan-out meant the outer
-- QUALIFY picked an arbitrary surviving row, so SOURCE_MasterFX_Rate,
-- USE_MasterFX_Rate and USDCADFX were non-deterministic. The new values
-- are the deterministic ones. Expect differences confined to the FX
-- columns (plus the external-linkage-derived counterparty columns if
-- 04 [Q2] showed that grain was also fanning out).
--
-- Any difference OUTSIDE those columns is a bug in the rewrite.
-- =====================================================================

-- Wire both queries up first:
--   CREATE OR REPLACE TEMP VIEW old_out AS <contents of 01_original_reconstructed.sql>;
--   CREATE OR REPLACE TEMP VIEW new_out AS <contents of 05_tuned_full_dedup.sql>;


-- ---------------------------------------------------------------------
-- [V1] Grain check -- proves [T8] (dropping the outer QUALIFY) was safe.
-- MUST return zero rows.
-- ---------------------------------------------------------------------
SELECT SNU_fileBusinessDate, SNU_RunId, SNU_RunType, SNU_Id, count(*) AS n
FROM new_out
GROUP BY SNU_fileBusinessDate, SNU_RunId, SNU_RunType, SNU_Id
HAVING count(*) > 1;


-- ---------------------------------------------------------------------
-- [V2] Row count must match exactly.
-- ---------------------------------------------------------------------
SELECT (SELECT count(*) FROM old_out) AS old_rows,
       (SELECT count(*) FROM new_out) AS new_rows;


-- ---------------------------------------------------------------------
-- [V3] Key set must match exactly. Both counts must be 0.
-- ---------------------------------------------------------------------
SELECT count(*) AS keys_in_old_not_new FROM (
    SELECT SNU_fileBusinessDate, SNU_RunId, SNU_RunType, SNU_Id FROM old_out
    EXCEPT
    SELECT SNU_fileBusinessDate, SNU_RunId, SNU_RunType, SNU_Id FROM new_out);

SELECT count(*) AS keys_in_new_not_old FROM (
    SELECT SNU_fileBusinessDate, SNU_RunId, SNU_RunType, SNU_Id FROM new_out
    EXCEPT
    SELECT SNU_fileBusinessDate, SNU_RunId, SNU_RunType, SNU_Id FROM old_out);


-- ---------------------------------------------------------------------
-- [V4] Full-row diff EXCLUDING the columns expected to change.
-- This is the real regression test. Both counts must be 0.
-- ---------------------------------------------------------------------
-- Databricks EXCEPT-with-exclusion:
SELECT count(*) AS rows_differing_outside_fx FROM (
    SELECT * EXCEPT (SOURCE_MasterFX_Rate, USE_MasterFX_Rate, USDCADFX) FROM old_out
    EXCEPT
    SELECT * EXCEPT (SOURCE_MasterFX_Rate, USE_MasterFX_Rate, USDCADFX) FROM new_out);

SELECT count(*) AS rows_differing_outside_fx_reverse FROM (
    SELECT * EXCEPT (SOURCE_MasterFX_Rate, USE_MasterFX_Rate, USDCADFX) FROM new_out
    EXCEPT
    SELECT * EXCEPT (SOURCE_MasterFX_Rate, USE_MasterFX_Rate, USDCADFX) FROM old_out);

-- If 04 [Q2] showed masterbookingaccountexternallinkage also fanning out,
-- add these to the EXCEPT list too, since they are downstream of it:
--   SOURCE_MasterCounterparty_CptySrcSystem, SOURCE_MasterCounterparty_Purpose,
--   SOURCE_MasterCounterparty_CptyName, USE_MasterCounterparty_CptySrcSystem,
--   USE_MasterCounterparty_Purpose, USE_MasterCounterparty_CptyName,
--   SOURCE_MasterLECounterparty_*, USE_MasterLECounterparty_*


-- ---------------------------------------------------------------------
-- [V5] How much did the FX fix actually change? Sizes the blast radius
-- for whoever consumes the dashboard.
-- ---------------------------------------------------------------------
SELECT
    count(*)                                                       AS total_rows,
    sum(CASE WHEN o.USDCADFX IS DISTINCT FROM n.USDCADFX THEN 1 ELSE 0 END)                       AS usdcadfx_changed,
    sum(CASE WHEN o.SOURCE_MasterFX_Rate IS DISTINCT FROM n.SOURCE_MasterFX_Rate THEN 1 ELSE 0 END) AS src_fx_changed,
    sum(CASE WHEN o.USE_MasterFX_Rate    IS DISTINCT FROM n.USE_MasterFX_Rate    THEN 1 ELSE 0 END) AS use_fx_changed
FROM old_out o
JOIN new_out n
  ON  o.SNU_fileBusinessDate <=> n.SNU_fileBusinessDate
  AND o.SNU_RunId            <=> n.SNU_RunId
  AND o.SNU_RunType          <=> n.SNU_RunType
  AND o.SNU_Id               <=> n.SNU_Id;


-- ---------------------------------------------------------------------
-- [V6] Stability check -- run the OLD query twice and diff it against
-- itself. If the FX fan-out was real, the old query is non-deterministic
-- and this will return non-zero. Good evidence for whoever needs to sign
-- off on the number changes in [V5].
-- ---------------------------------------------------------------------
-- CREATE OR REPLACE TEMP VIEW old_run2 AS <same as old_out>;
-- SELECT count(*) FROM (SELECT * FROM old_out EXCEPT SELECT * FROM old_run2);
