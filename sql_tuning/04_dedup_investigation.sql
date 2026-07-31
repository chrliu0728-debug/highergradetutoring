-- =====================================================================
-- Run FIRST. Two questions the dedup layer can't answer on its own.
-- =====================================================================

-- ---------------------------------------------------------------------
-- [Q1] WHY is MasterFX duplicated on (CcyTo, fileBusinessDate)?
-- This determines whether "keep latest file version" is the right rule
-- or whether you need a CcyFrom filter instead.
--
--   If dupes differ by CcyFrom  -> latest-file dedup picks an ARBITRARY
--                                  currency pair. You need CcyFrom = <base>.
--   If dupes differ only by filecreateddate -> latest-file dedup is correct.
-- ---------------------------------------------------------------------
SELECT
    CcyTo,
    count(*)                            AS rows,
    count(DISTINCT CcyFrom)             AS distinct_ccyfrom,
    collect_set(CcyFrom)                AS ccyfrom_values,
    count(DISTINCT filecreateddate)     AS distinct_filecreateddate,
    count(DISTINCT Rate)                AS distinct_rates
FROM collateraliq_bronze.MasterFX
WHERE fileBusinessDate = :business_date
GROUP BY CcyTo
HAVING count(*) > 1
ORDER BY rows DESC
LIMIT 50;

-- Same question for the CAD row specifically (drives USDCADFX):
SELECT * FROM collateraliq_bronze.MasterFX
WHERE fileBusinessDate = :business_date AND CcyTo = 'CAD';


-- ---------------------------------------------------------------------
-- [Q2] Same question for masterbookingaccountexternallinkage.
-- It's joined on (MasterBookingAcntId, filebusinessdate) only. A booking
-- account plausibly has MULTIPLE external linkages (one per external
-- system / ExternalIdType). If so, latest-file dedup picks an arbitrary
-- linkage and you need an ExternalIdType filter instead.
-- ---------------------------------------------------------------------
SELECT
    count(*)                                                          AS rows,
    count(DISTINCT concat_ws('|', MasterBookingAcntId, filebusinessdate)) AS distinct_keys,
    count(DISTINCT ExternalIdType)                                    AS distinct_idtypes,
    collect_set(ExternalIdType)                                       AS idtype_values
FROM collateraliq_bronze.masterbookingaccountexternallinkage
WHERE filebusinessdate = :business_date;


-- ---------------------------------------------------------------------
-- [Q3] Which tables actually NEED a dedup CTE?
-- IMPORTANT: every dedup CTE costs a shuffle + sort. Adding one to a
-- table that is already unique on its join key makes the query SLOWER.
-- Only keep the CTEs in 05_tuned_full_dedup.sql for tables that show
-- rows > distinct_keys here. Delete the rest and join the table directly.
-- (This is [D1] from 03_diagnostics.sql, extended with the two extra
-- grains that MasterCounterparty and MasterLECounterparty are joined on.)
-- ---------------------------------------------------------------------
SELECT 'MasterAsset (Id)' AS grain, count(*) AS rows,
       count(DISTINCT concat_ws('|', Id, filebusinessdate)) AS distinct_keys
FROM collateraliq_bronze.MasterAsset WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterTrade (Id)', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterTrade WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterSecurityPosition (Id)', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterSecurityPosition WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterBookingAccount (Id)', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterBookingAccount WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterOrgUnitRollup (OrgUnit)', count(*), count(DISTINCT concat_ws('|', OrgUnit, filebusinessdate))
FROM collateraliq_bronze.MasterOrgUnitRollup WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterCounterparty (Id)', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterCounterparty WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterCounterparty (CptyId)', count(*), count(DISTINCT concat_ws('|', CptyId, filebusinessdate))
FROM collateraliq_bronze.MasterCounterparty WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterLECounterparty (Id)', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterLECounterparty WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterLECounterparty (LECptyId)', count(*), count(DISTINCT concat_ws('|', LECptyId, filebusinessdate))
FROM collateraliq_bronze.MasterLECounterparty WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterLegalEntity (SrcLegalEntityId)', count(*), count(DISTINCT concat_ws('|', SrcLegalEntityId, filebusinessdate))
FROM collateraliq_bronze.MasterLegalEntity WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterAssetPrice (MasterAssetId)', count(*), count(DISTINCT concat_ws('|', MasterAssetId, filebusinessdate))
FROM collateraliq_bronze.MasterAssetPrice WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterIssuer (Id)', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterIssuer WHERE filebusinessdate = :business_date
UNION ALL SELECT 'mbaExternalLinkage (MasterBookingAcntId)', count(*), count(DISTINCT concat_ws('|', MasterBookingAcntId, filebusinessdate))
FROM collateraliq_bronze.masterbookingaccountexternallinkage WHERE filebusinessdate = :business_date
UNION ALL SELECT 'MasterFX (CcyTo)', count(*), count(DISTINCT concat_ws('|', CcyTo, fileBusinessDate))
FROM collateraliq_bronze.MasterFX WHERE fileBusinessDate = :business_date
UNION ALL SELECT 'masterwabrate (masterassetid)', count(*), count(DISTINCT concat_ws('|', masterassetid, filebusinessdate))
FROM collateraliq_bronze.masterwabrate WHERE filebusinessdate = :business_date
ORDER BY rows / distinct_keys DESC;


-- ---------------------------------------------------------------------
-- [Q4] Does every bronze table have `filecreateddate`? The dedup rule in
-- 05 assumes it. Also checks for LastModifiedDateTimeUTC (used as the
-- tiebreaker in your existing masterwabrate dedup).
-- ---------------------------------------------------------------------
-- DESCRIBE TABLE collateraliq_bronze.MasterAsset;
-- DESCRIBE TABLE collateraliq_bronze.MasterTrade;
-- ... etc. Or, faster:
SELECT table_name, column_name
FROM system.information_schema.columns
WHERE table_schema = 'collateraliq_bronze'
  AND lower(column_name) IN ('filecreateddate', 'lastmodifieddatetimeutc')
ORDER BY table_name, column_name;
