-- =====================================================================
-- Diagnostics -- run these BEFORE trusting the tuned query
-- =====================================================================
-- Replace :business_date with a real date you run for.

-- ---------------------------------------------------------------------
-- [D0] How much does [T1] actually buy you?
-- Ratio of raw driver rows to deduped driver rows == the multiple of
-- wasted join work in the original query. If this is 1.0, [T1] is free
-- but not a win, and you should focus on [T5] + file layout instead.
-- ---------------------------------------------------------------------
SELECT
    count(*)                                                    AS raw_rows,
    count(DISTINCT concat_ws('|',
        coalesce(Source_fileBusinessDate, Use_fileBusinessDate),
        coalesce(Source_RunId,            Use_RunId),
        coalesce(Source_RunType,          Use_RunType),
        concat(ifnull(source_id,0), '_', ifnull(use_id,0))))    AS deduped_rows,
    count(*) / count(DISTINCT concat_ws('|',
        coalesce(Source_fileBusinessDate, Use_fileBusinessDate),
        coalesce(Source_RunId,            Use_RunId),
        coalesce(Source_RunType,          Use_RunType),
        concat(ifnull(source_id,0), '_', ifnull(use_id,0))))    AS waste_multiple
FROM collateraliq_silver.snuallocation_waterfall
WHERE Source_filebusinessdate = :business_date
   OR Use_filebusinessdate    = :business_date;


-- ---------------------------------------------------------------------
-- [D1] Dimension fan-out check -- gates whether you can delete [T5].
-- Any row where rows > distinct_keys means that dimension multiplies
-- your fact rows, and the original outer QUALIFY was silently picking an
-- arbitrary survivor (non-deterministic results!).
-- Fix any offender with the same dedup-CTE pattern used for `wab`.
-- ---------------------------------------------------------------------
SELECT 'MasterAsset' AS tbl, count(*) AS rows,
       count(DISTINCT concat_ws('|', Id, filebusinessdate)) AS distinct_keys
FROM collateraliq_bronze.MasterAsset WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterTrade', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterTrade WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterSecurityPosition', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterSecurityPosition WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterBookingAccount', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterBookingAccount WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterCounterparty', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterCounterparty WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterLECounterparty', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterLECounterparty WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterIssuer', count(*), count(DISTINCT concat_ws('|', Id, filebusinessdate))
FROM collateraliq_bronze.MasterIssuer WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterAssetPrice', count(*), count(DISTINCT concat_ws('|', MasterAssetId, filebusinessdate))
FROM collateraliq_bronze.MasterAssetPrice WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterOrgUnitRollup', count(*), count(DISTINCT concat_ws('|', OrgUnit, filebusinessdate))
FROM collateraliq_bronze.MasterOrgUnitRollup WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'MasterLegalEntity', count(*), count(DISTINCT concat_ws('|', SrcLegalEntityId, filebusinessdate))
FROM collateraliq_bronze.MasterLegalEntity WHERE filebusinessdate = :business_date
UNION ALL
SELECT 'mbaexternallinkage', count(*), count(DISTINCT concat_ws('|', MasterBookingAcntId, filebusinessdate))
FROM collateraliq_bronze.masterbookingaccountexternallinkage WHERE filebusinessdate = :business_date
ORDER BY rows / distinct_keys DESC;


-- ---------------------------------------------------------------------
-- [D2] MasterFX fan-out -- the joins match on CcyTo only.
-- If distinct_keys < rows, every FX join multiplies your row count.
-- ---------------------------------------------------------------------
SELECT count(*) AS rows,
       count(DISTINCT concat_ws('|', CcyTo, fileBusinessDate)) AS distinct_ccyto_date
FROM collateraliq_bronze.MasterFX
WHERE fileBusinessDate = :business_date;


-- ---------------------------------------------------------------------
-- [D3] Delta stats coverage. Data skipping only indexes the FIRST 32
-- columns by default. If filebusinessdate or the join Ids sit past
-- position 32 in these wide bronze tables, they have NO min/max stats
-- and file skipping does nothing -- which would explain a lot.
-- ---------------------------------------------------------------------
SHOW TBLPROPERTIES collateraliq_bronze.MasterAsset;      -- look for delta.dataSkippingNumIndexedCols
DESCRIBE DETAIL collateraliq_bronze.MasterAsset;         -- numFiles, sizeInBytes, partitionColumns, clusteringColumns
DESCRIBE DETAIL collateraliq_bronze.MasterTrade;
DESCRIBE DETAIL collateraliq_bronze.MasterSecurityPosition;
DESCRIBE DETAIL collateraliq_silver.snuallocation_waterfall;


-- ---------------------------------------------------------------------
-- [D4] Equivalence check -- old vs new must produce identical output.
-- Run after wiring both into temp views old_out / new_out.
-- Both counts must be 0.
-- ---------------------------------------------------------------------
-- SELECT count(*) AS in_old_not_new FROM (SELECT * FROM old_out EXCEPT SELECT * FROM new_out);
-- SELECT count(*) AS in_new_not_old FROM (SELECT * FROM new_out EXCEPT SELECT * FROM old_out);


-- ---------------------------------------------------------------------
-- [D5] One-time file layout fixes, if D3 shows bad layout.
-- ---------------------------------------------------------------------
-- Liquid clustering (DBR 13.3+, preferred):
--   ALTER TABLE collateraliq_bronze.MasterAsset CLUSTER BY (filebusinessdate, Id);
--   OPTIMIZE collateraliq_bronze.MasterAsset;
-- Classic Z-ORDER (if partitioned by filebusinessdate already):
--   OPTIMIZE collateraliq_bronze.MasterAsset ZORDER BY (Id);
-- Widen stats coverage if key cols are past column 32:
--   ALTER TABLE collateraliq_bronze.MasterAsset
--     SET TBLPROPERTIES (delta.dataSkippingNumIndexedCols = 64);
--   -- then rewrite to regenerate stats: OPTIMIZE <table>;

-- ---------------------------------------------------------------------
-- [D6] Stats for CBO join reordering (matters a lot at 33 joins).
-- ---------------------------------------------------------------------
-- ANALYZE TABLE collateraliq_bronze.MasterAsset COMPUTE STATISTICS FOR ALL COLUMNS;
-- ... repeat for each dimension and the driver.
-- Confirm on the cluster/warehouse:
--   SET spark.sql.cbo.enabled;
--   SET spark.sql.cbo.joinReorder.enabled;
--   SET spark.sql.autoBroadcastJoinThreshold;   -- default 10MB
