-- =====================================================================
-- snuallocation_waterfall_ciqdash1
-- TUNED + FULL DEDUP LAYER -- Databricks / Delta Lake
-- =====================================================================
-- Supersedes 02_tuned.sql. Every joined table now gets a CTE that
-- guarantees exactly one row per join key, so the query has a provable
-- 1:1 row grain end to end.
--
-- WHAT CHANGED VS 02_tuned.sql
--   [T6] One dedup CTE per (table, join grain). Fixes the MasterFX
--        fan-out you confirmed, and pre-empts the same problem in every
--        other dimension.
--   [T7] Each dim CTE is PROJECTED to only the columns actually used.
--        The window function then sorts narrow rows instead of full
--        bronze records -- this is most of why the dedup layer is cheap.
--   [T8] THE OUTER `QUALIFY` IS GONE. With the driver at 1 row per key
--        ([T1]) and every dimension at <=1 row per key ([T6]), each LEFT
--        JOIN adds at most one row, so the result is already 1 row per
--        (fileBusinessDate, RunId, RunType, SNU_Id). Removing it deletes
--        a full shuffle + sort of the widest form of the dataset.
--        This is the single biggest win in the file.
--
-- ---------------------------------------------------------------------
-- READ BEFORE RUNNING -- three things you must set
-- ---------------------------------------------------------------------
-- (A) DELETE THE CTEs YOU DON'T NEED. Every dedup CTE costs a shuffle +
--     sort. For any table where 04_dedup_investigation.sql [Q3] shows
--     rows == distinct_keys, DELETE its CTE and join the bronze table
--     directly -- otherwise you are paying for a window that removes
--     nothing. Only MasterFX is confirmed to need one so far.
--
-- (B) SET THE FX BASE CURRENCY. See [FX-1] below. Until 04 [Q1] tells
--     you why MasterFX is duplicated, the dedup rule there is a GUESS.
--
-- (C) CHECK THE EXTERNAL LINKAGE GRAIN. See [EL-1] below. Same issue.
--
-- ---------------------------------------------------------------------
-- DEDUP CONVENTION
-- ---------------------------------------------------------------------
-- Latest file version wins:  ORDER BY filecreateddate DESC
-- This mirrors your existing rule in the masterwabrate subquery and the
-- outer QUALIFY. Where a table also has LastModifiedDateTimeUTC, add it
-- as a tiebreaker (as masterwabrate already does) -- otherwise ties are
-- broken arbitrarily and results stay non-deterministic. 04 [Q4] tells
-- you which tables have these columns.
--
-- ---------------------------------------------------------------------
-- [DATE] markers: uncomment for a single-business-date run. Strongly
-- recommended -- without them each dedup window scans and sorts the full
-- history of every bronze table.
-- =====================================================================

WITH

-- =====================================================================
-- DRIVER  [T1]
-- =====================================================================
snu AS (
    SELECT
        coalesce(Source_fileBusinessDate, Use_fileBusinessDate) AS SNU_fileBusinessDate,
        coalesce(Source_fileCreatedDate,  Use_fileCreatedDate)  AS SNU_fileCreatedDate,
        coalesce(Source_RunId,            Use_RunId)            AS SNU_RunId,
        coalesce(Source_RunType,          Use_RunType)          AS SNU_RunType,
        concat(ifnull(source_id, 0), '_', ifnull(use_id, 0))    AS SNU_Id,
        *
    FROM collateraliq_silver.snuallocation_waterfall
    -- [DATE] OR-form on raw columns, not coalesce(...), so Delta can prune
    -- WHERE Source_filebusinessdate = :business_date
    --    OR Use_filebusinessdate    = :business_date
    QUALIFY ROW_NUMBER() OVER (
              PARTITION BY coalesce(Source_fileBusinessDate, Use_fileBusinessDate),
                           coalesce(Source_RunId,            Use_RunId),
                           coalesce(Source_RunType,          Use_RunType),
                           concat(ifnull(source_id, 0), '_', ifnull(use_id, 0))
              ORDER BY coalesce(Source_fileCreatedDate, Use_fileCreatedDate) DESC) = 1
),

-- =====================================================================
-- DIMENSIONS  [T6][T7] -- one row per join key, projected to used columns
-- =====================================================================

-- grain: (Id, filebusinessdate)
dim_asset AS (
    SELECT Id, filebusinessdate,
           Ticker, BBGGlobalId, SecurityDescription, BBGCollateralType,
           CountryIssueISO, CountryofRisk, ProdRegion, LongCompanyName,
           ProdMaturityDate, Ccy, UnitOfPrice, HQLALevelInd,
           MasterIssuerId                       -- join key onto MasterIssuer
    FROM collateraliq_bronze.MasterAsset
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY Id, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (Id, filebusinessdate)
dim_trade AS (
    SELECT Id, filebusinessdate,
           TradeMaturityDate, TransactionType, DepoLocation,
           SrcBookingAcntName, DealType, DealSubType, InterestRate,
           Haircut, SrcCptyShortCode,
           MasterBookingAcntId, MasterCptyId    -- join keys
    FROM collateraliq_bronze.MasterTrade
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY Id, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (Id, filebusinessdate)
dim_secpos AS (
    SELECT Id, filebusinessdate,
           BalanceType,
           MasterBookingAcntId                  -- join key
    FROM collateraliq_bronze.MasterSecurityPosition
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY Id, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (OrgUnit, filebusinessdate)
dim_orgunit AS (
    SELECT OrgUnit, filebusinessdate,
           RollupLevel3Name, RollupLevel4Name, RollupLevel5Name,
           RollupLevel6Name, RollupLevel7Name, RollupLevel8Name,
           RollupLevel9Name, RollupLevel10Name, RollupLevel11Name,
           OrgUnitLongName
    FROM collateraliq_bronze.MasterOrgUnitRollup
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY OrgUnit, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (Id, filebusinessdate) -- used by all four booking-account joins
dim_bookacct AS (
    SELECT Id, filebusinessdate,
           AcntType, AcntName
    FROM collateraliq_bronze.MasterBookingAccount
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY Id, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (MasterBookingAcntId, filebusinessdate)
-- [EL-1] >>> VERIFY <<< A booking account plausibly has SEVERAL external
-- linkages, one per external system. If 04 [Q2] shows multiple
-- ExternalIdType values, "latest file version" picks an ARBITRARY
-- linkage -- which silently changes which counterparty resolves. In that
-- case add the correct filter instead, e.g.:
--     WHERE ExternalIdType = '<the system you actually want>'
-- and drop the QUALIFY if that makes the key unique.
dim_bookacct_ext AS (
    SELECT MasterBookingAcntId, filebusinessdate,
           ExternalIdType
    FROM collateraliq_bronze.masterbookingaccountexternallinkage
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY MasterBookingAcntId, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (Id, filebusinessdate)
dim_cpty AS (
    SELECT Id, filebusinessdate,
           CptySrcSystem, Purpose, CptyName,
           MasterLECptyId                       -- join key
    FROM collateraliq_bronze.MasterCounterparty
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY Id, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (CptyId, filebusinessdate)
-- The s_pbmc / u_pbmc joins contribute NO output columns -- their only
-- effect is `COALESCE(mt.MasterCptyId, pbmc.CptyId)`, i.e. "does this
-- ExternalIdType exist as a CptyId?". That makes it a pure existence
-- check, so DISTINCT is both correct and cheaper than a ROW_NUMBER.
dim_cpty_ids AS (
    SELECT DISTINCT CptyId, filebusinessdate
    FROM collateraliq_bronze.MasterCounterparty
    -- [DATE] WHERE filebusinessdate = :business_date
),

-- grain: (Id, filebusinessdate)
dim_lecpty AS (
    SELECT Id, filebusinessdate,
           LECptyName, ParentLECptyId
    FROM collateraliq_bronze.MasterLECounterparty
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY Id, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (LECptyId, filebusinessdate) -- the parent lookup
dim_lecpty_parent AS (
    SELECT LECptyId, filebusinessdate,
           LECptyName, ParentLECptyId
    FROM collateraliq_bronze.MasterLECounterparty
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY LECptyId, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (SrcLegalEntityId, filebusinessdate)
dim_legalentity AS (
    SELECT SrcLegalEntityId, filebusinessdate,
           FullName, ShortCode
    FROM collateraliq_bronze.MasterLegalEntity
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY SrcLegalEntityId, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (MasterAssetId, filebusinessdate)
dim_assetprice AS (
    SELECT MasterAssetId, filebusinessdate,
           InterestAccrued, CleanPrice, PrincipalFactor
    FROM collateraliq_bronze.MasterAssetPrice
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY MasterAssetId, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (Id, filebusinessdate)
dim_issuer AS (
    SELECT Id, filebusinessdate,
           UltimateParentCompanyName
    FROM collateraliq_bronze.MasterIssuer
    -- [DATE] WHERE filebusinessdate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY Id, filebusinessdate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (CcyTo, fileBusinessDate)   <<-- THE CONFIRMED FAN-OUT
-- [FX-1] >>> SET THIS BEFORE RUNNING <<<
-- Run 04 [Q1] first. Two possible causes, two different fixes:
--
--   (a) Duplicates differ by CcyFrom (most likely -- a rates table is
--       normally keyed on the PAIR). Then "latest file version" picks an
--       arbitrary currency pair and the Rate is meaningless. The correct
--       fix is to pin the base currency and DELETE the QUALIFY:
--           WHERE CcyFrom = 'USD'      -- USDCADFX implies USD base
--       If (CcyFrom, CcyTo, date) is then unique, no window is needed at
--       all and this CTE becomes a cheap filter.
--
--   (b) Duplicates are genuine file re-deliveries with the same CcyFrom.
--       Then the QUALIFY below is correct as written.
--
-- Shipped with (b) active and (a) commented, because (b) preserves your
-- current column semantics. If 04 [Q1] shows multiple CcyFrom values,
-- switch to (a) -- and be aware that this CHANGES the Rate values your
-- dashboard has been showing, because they were arbitrary before.
dim_fx AS (
    SELECT CcyTo, fileBusinessDate, Rate
    FROM collateraliq_bronze.MasterFX
    -- [FX-1](a)  WHERE CcyFrom = 'USD'
    -- [DATE] AND fileBusinessDate = :business_date
    QUALIFY ROW_NUMBER() OVER (PARTITION BY CcyTo, fileBusinessDate
                               ORDER BY filecreateddate DESC) = 1
),

-- grain: (masterassetid, filebusinessdate) -- your original rule, hoisted
-- from the two duplicated inline subqueries  [T2]
dim_wab AS (
    SELECT masterassetid, filebusinessdate,
           ProdSpecialType, WabRate, WabRateSrc
    FROM (
        SELECT *,
               CASE WHEN WABRateSrc = 'CIBC Weighted Avg. Borrow' THEN 1
                    WHEN WABRateSrc = 'Datalend Average Rate'     THEN 2
                    ELSE 3
               END AS rankedsrc
        FROM collateraliq_bronze.masterwabrate
        -- [DATE] WHERE filebusinessdate = :business_date
    )
    QUALIFY ROW_NUMBER() OVER (
              PARTITION BY masterassetid, filebusinessdate
              ORDER BY filecreateddate DESC, rankedsrc ASC,
                       LastModifiedDateTimeUTC DESC) = 1
)

-- =====================================================================
-- FACT + ENRICHMENT
-- =====================================================================
SELECT
    -- [OPT] Replacing snu.* with an explicit column list is a further
    -- direct cut in shuffle bytes. Left as-is: I can't tell which of the
    -- driver's columns are consumed downstream.
    snu.*,

    -- MasterAsset (source)
    s_ma.Ticker              AS SOURCE_MasterAsset_Ticker,
    s_ma.BBGGlobalId         AS SOURCE_MasterAsset_BBGGlobalId,
    s_ma.SecurityDescription AS SOURCE_MasterAsset_SecurityDescription,
    s_ma.BBGCollateralType   AS SOURCE_MasterAsset_BBGCollateralType,
    s_ma.CountryIssueISO     AS SOURCE_MasterAsset_CountryIssueISO,
    s_ma.CountryofRisk       AS SOURCE_MasterAsset_CountryofRisk,
    s_ma.ProdRegion          AS SOURCE_MasterAsset_ProdRegion,
    s_ma.LongCompanyName     AS SOURCE_MasterAsset_LongCompanyName,
    s_ma.ProdMaturityDate    AS SOURCE_MasterAsset_ProdMaturityDate,
    s_ma.Ccy                 AS SOURCE_MasterAsset_Ccy,
    s_ma.UnitOfPrice         AS SOURCE_MasterAsset_UnitOfPrice,
    s_ma.HQLALevelInd        AS SOURCE_MasterAsset_HQLALevelInd,
    -- MasterAsset (use)
    u_ma.Ticker              AS USE_MasterAsset_Ticker,
    u_ma.BBGGlobalId         AS USE_MasterAsset_BBGGlobalId,
    u_ma.SecurityDescription AS USE_MasterAsset_SecurityDescription,
    u_ma.BBGCollateralType   AS USE_MasterAsset_BBGCollateralType,
    u_ma.CountryIssueISO     AS USE_MasterAsset_CountryIssueISO,
    u_ma.CountryofRisk       AS USE_MasterAsset_CountryofRisk,
    u_ma.ProdRegion          AS USE_MasterAsset_ProdRegion,
    u_ma.LongCompanyName     AS USE_MasterAsset_LongCompanyName,
    u_ma.ProdMaturityDate    AS USE_MasterAsset_ProdMaturityDate,
    u_ma.Ccy                 AS USE_MasterAsset_Ccy,
    u_ma.UnitOfPrice         AS USE_MasterAsset_UnitOfPrice,
    u_ma.HQLALevelInd        AS USE_MasterAsset_HQLALevelInd,

    -- MasterIssuer
    s_mi.UltimateParentCompanyName AS SOURCE_MasterIssuer_UltimateParentCompanyName,
    u_mi.UltimateParentCompanyName AS USE_MasterIssuer_UltimateParentCompanyName,

    -- MasterTrade (source)
    s_mt.TradeMaturityDate AS SOURCE_MasterTrade_TradeMaturityDate,
    s_mt.TransactionType   AS SOURCE_MasterTrade_TransactionType,
    s_mt.DepoLocation      AS SOURCE_MasterTrade_DepoLocation,
    coalesce(s_mt.SrcBookingAcntName, s_mba_sp.AcntName) AS SOURCE_MasterTrade_SrcBookingAcntName,
    s_mt.DealType          AS SOURCE_MasterTrade_DealType,
    s_mt.DealSubType       AS SOURCE_MasterTrade_DealSubType,
    s_mt.InterestRate      AS SOURCE_MasterTrade_InterestRate,
    s_mt.Haircut           AS SOURCE_MasterTrade_Haircut,
    s_mt.SrcCptyShortCode  AS SOURCE_MasterTrade_SrcCptyShortCode,
    -- MasterTrade (use)
    u_mt.TradeMaturityDate AS USE_MasterTrade_TradeMaturityDate,
    u_mt.TransactionType   AS USE_MasterTrade_TransactionType,
    u_mt.DepoLocation      AS USE_MasterTrade_DepoLocation,
    coalesce(u_mt.SrcBookingAcntName, u_mba_sp.AcntName) AS USE_MasterTrade_SrcBookingAcntName,
    u_mt.DealType          AS USE_MasterTrade_DealType,
    u_mt.DealSubType       AS USE_MasterTrade_DealSubType,
    u_mt.InterestRate      AS USE_MasterTrade_InterestRate,
    u_mt.Haircut           AS USE_MasterTrade_Haircut,
    u_mt.SrcCptyShortCode  AS USE_MasterTrade_SrcCptyShortCode,

    -- MasterOrgUnitRollup (source)
    sr.RollupLevel3Name  AS SOURCE_MasterOrgUnitRollup_RollupLevel3Name,
    sr.RollupLevel4Name  AS SOURCE_MasterOrgUnitRollup_RollupLevel4Name,
    sr.RollupLevel5Name  AS SOURCE_MasterOrgUnitRollup_RollupLevel5Name,
    sr.RollupLevel6Name  AS SOURCE_MasterOrgUnitRollup_RollupLevel6Name,
    sr.RollupLevel7Name  AS SOURCE_MasterOrgUnitRollup_RollupLevel7Name,
    sr.RollupLevel8Name  AS SOURCE_MasterOrgUnitRollup_RollupLevel8Name,
    sr.RollupLevel9Name  AS SOURCE_MasterOrgUnitRollup_RollupLevel9Name,
    sr.RollupLevel10Name AS SOURCE_MasterOrgUnitRollup_RollupLevel10Name,
    sr.RollupLevel11Name AS SOURCE_MasterOrgUnitRollup_RollupLevel11Name,
    sr.OrgUnitLongName   AS SOURCE_MasterOrgUnitRollup_OrgUnitLongName,
    -- MasterOrgUnitRollup (use)
    ur.RollupLevel3Name  AS USE_MasterOrgUnitRollup_RollupLevel3Name,
    ur.RollupLevel4Name  AS USE_MasterOrgUnitRollup_RollupLevel4Name,
    ur.RollupLevel5Name  AS USE_MasterOrgUnitRollup_RollupLevel5Name,
    ur.RollupLevel6Name  AS USE_MasterOrgUnitRollup_RollupLevel6Name,
    ur.RollupLevel7Name  AS USE_MasterOrgUnitRollup_RollupLevel7Name,
    ur.RollupLevel8Name  AS USE_MasterOrgUnitRollup_RollupLevel8Name,
    ur.RollupLevel9Name  AS USE_MasterOrgUnitRollup_RollupLevel9Name,
    ur.RollupLevel10Name AS USE_MasterOrgUnitRollup_RollupLevel10Name,
    ur.RollupLevel11Name AS USE_MasterOrgUnitRollup_RollupLevel11Name,
    ur.OrgUnitLongName   AS USE_MasterOrgUnitRollup_OrgUnitLongName,

    -- MasterBookingAccount
    coalesce(s_mba.AcntType, s_mba_sp.AcntType) AS SOURCE_MasterBookingAccount_AcntType,
    coalesce(u_mba.AcntType, u_mba_sp.AcntType) AS USE_MasterBookingAccount_AcntType,

    -- MasterCounterparty
    s_mc.CptySrcSystem AS SOURCE_MasterCounterparty_CptySrcSystem,
    s_mc.Purpose       AS SOURCE_MasterCounterparty_Purpose,
    s_mc.CptyName      AS SOURCE_MasterCounterparty_CptyName,
    u_mc.CptySrcSystem AS USE_MasterCounterparty_CptySrcSystem,
    u_mc.Purpose       AS USE_MasterCounterparty_Purpose,
    u_mc.CptyName      AS USE_MasterCounterparty_CptyName,

    -- MasterLECounterparty
    s_mlc.LECptyName     AS SOURCE_MasterLECounterparty_LECptyName,
    s_mlc.ParentLECptyId AS SOURCE_MasterLECounterparty_ParentLECptyId,
    s_mpc.LECptyName     AS SOURCE_MasterLECounterparty_ParentLECptyName,
    u_mlc.LECptyName     AS USE_MasterLECounterparty_LECptyName,
    u_mpc.ParentLECptyId AS USE_MasterLECounterparty_ParentLECptyId,  -- [?BUG] source side uses s_mlc, not s_mpc. Preserved as transcribed -- verify.
    u_mpc.LECptyName     AS USE_MasterLECounterparty_ParentLECptyName,

    -- MasterLegalEntity
    s_mle.FullName  AS SOURCE_MasterLegalEntity_FullName,
    s_mle.ShortCode AS SOURCE_MasterLegalEntity_ShortCode,
    u_mle.FullName  AS USE_MasterLegalEntity_FullName,
    u_mle.ShortCode AS USE_MasterLegalEntity_ShortCode,

    -- MasterAssetPrice
    s_map.InterestAccrued AS SOURCE_MasterAssetPrice_InterestAccrued,
    s_map.CleanPrice      AS SOURCE_MasterAssetPrice_CleanPrice,
    s_map.PrincipalFactor AS SOURCE_MasterAssetPrice_PrincipalFactor,
    u_map.InterestAccrued AS USE_MasterAssetPrice_InterestAccrued,
    u_map.CleanPrice      AS USE_MasterAssetPrice_CleanPrice,
    u_map.PrincipalFactor AS USE_MasterAssetPrice_PrincipalFactor,

    -- MasterSecurityPosition
    s_msp.BalanceType AS SOURCE_MasterSecurityPosition_BalanceType,
    u_msp.BalanceType AS USE_MasterSecurityPosition_BalanceType,

    -- MasterFX
    s_fx.Rate  AS SOURCE_MasterFX_Rate,
    u_fx.Rate  AS USE_MasterFX_Rate,
    FXCAD.Rate AS USDCADFX,

    -- MasterWabRate
    s_wab.ProdSpecialType AS SOURCE_MasterWabRate_ProdSpecialType,
    s_wab.WabRate         AS SOURCE_MasterWabRate_WabRate,
    s_wab.WabRateSrc      AS SOURCE_MasterWabRate_WabRateSrc,
    u_wab.ProdSpecialType AS USE_MasterWabRate_ProdSpecialType,
    u_wab.WabRate         AS USE_MasterWabRate_WabRate,
    u_wab.WabRateSrc      AS USE_MasterWabRate_WabRateSrc

FROM snu

LEFT JOIN dim_asset s_ma
       ON snu.Source_MasterAssetId    = s_ma.Id
      AND snu.Source_filebusinessdate = s_ma.filebusinessdate
LEFT JOIN dim_asset u_ma
       ON snu.Use_MasterAssetId       = u_ma.Id
      AND snu.Use_filebusinessdate    = u_ma.filebusinessdate

LEFT JOIN dim_trade s_mt
       ON snu.Source_MasterTradeId    = s_mt.Id
      AND snu.Source_filebusinessdate = s_mt.filebusinessdate
LEFT JOIN dim_trade u_mt
       ON snu.Use_MasterTradeId       = u_mt.Id
      AND snu.Use_filebusinessdate    = u_mt.filebusinessdate

LEFT JOIN dim_secpos s_msp
       ON snu.Source_MasterSecurityPositionId = s_msp.Id
      AND snu.Source_filebusinessdate         = s_msp.filebusinessdate
LEFT JOIN dim_secpos u_msp
       ON snu.Use_MasterSecurityPositionId    = u_msp.Id
      AND snu.Use_filebusinessdate            = u_msp.filebusinessdate

LEFT JOIN dim_orgunit sr
       ON snu.Source_SnURaw_OrgUnit   = sr.OrgUnit
      AND snu.Source_filebusinessdate = sr.filebusinessdate
LEFT JOIN dim_orgunit ur
       ON snu.Use_SnURaw_OrgUnit      = ur.OrgUnit
      AND snu.Use_filebusinessdate    = ur.filebusinessdate

-- [T3] date taken from the driver, not chained through the intermediate,
-- so the predicate stays pushable to the dimension scan
LEFT JOIN dim_bookacct s_mba
       ON s_mt.MasterBookingAcntId    = s_mba.Id
      AND snu.Source_filebusinessdate = s_mba.filebusinessdate
LEFT JOIN dim_bookacct u_mba
       ON u_mt.MasterBookingAcntId    = u_mba.Id
      AND snu.Use_filebusinessdate    = u_mba.filebusinessdate

LEFT JOIN dim_bookacct s_mba_sp
       ON s_msp.MasterBookingAcntId   = s_mba_sp.Id
      AND snu.Source_filebusinessdate = s_mba_sp.filebusinessdate
LEFT JOIN dim_bookacct u_mba_sp
       ON u_msp.MasterBookingAcntId   = u_mba_sp.Id
      AND snu.Use_filebusinessdate    = u_mba_sp.filebusinessdate

LEFT JOIN dim_bookacct_ext s_mbae
       ON s_msp.MasterBookingAcntId   = s_mbae.MasterBookingAcntId
      AND snu.Source_filebusinessdate = s_mbae.filebusinessdate
LEFT JOIN dim_bookacct_ext u_mbae
       ON u_msp.MasterBookingAcntId   = u_mbae.MasterBookingAcntId
      AND snu.Use_filebusinessdate    = u_mbae.filebusinessdate

-- existence check only -- contributes CptyId to the COALESCE below
LEFT JOIN dim_cpty_ids s_pbmc
       ON s_mbae.ExternalIdType       = s_pbmc.CptyId          -- [?] verify column name
      AND snu.Source_filebusinessdate = s_pbmc.filebusinessdate
LEFT JOIN dim_cpty_ids u_pbmc
       ON u_mbae.ExternalIdType       = u_pbmc.CptyId          -- [?] verify column name
      AND snu.Use_filebusinessdate    = u_pbmc.filebusinessdate

-- [T3] COALESCE kept on the ID, removed from the DATE
LEFT JOIN dim_cpty s_mc
       ON COALESCE(s_mt.MasterCptyId, s_pbmc.CptyId) = s_mc.Id
      AND snu.Source_filebusinessdate               = s_mc.filebusinessdate
LEFT JOIN dim_cpty u_mc
       ON COALESCE(u_mt.MasterCptyId, u_pbmc.CptyId) = u_mc.Id
      AND snu.Use_filebusinessdate                  = u_mc.filebusinessdate

LEFT JOIN dim_lecpty s_mlc
       ON s_mc.MasterLECptyId         = s_mlc.Id
      AND snu.Source_filebusinessdate = s_mlc.filebusinessdate
LEFT JOIN dim_lecpty u_mlc
       ON u_mc.MasterLECptyId         = u_mlc.Id
      AND snu.Use_filebusinessdate    = u_mlc.filebusinessdate

LEFT JOIN dim_lecpty_parent s_mpc
       ON s_mlc.ParentLECptyId        = s_mpc.LECptyId
      AND snu.Source_filebusinessdate = s_mpc.filebusinessdate
LEFT JOIN dim_lecpty_parent u_mpc
       ON u_mlc.ParentLECptyId        = u_mpc.LECptyId
      AND snu.Use_filebusinessdate    = u_mpc.filebusinessdate

LEFT JOIN dim_legalentity s_mle
       ON snu.Source_SnURaw_LegalEntityId = s_mle.SrcLegalEntityId
      AND snu.Source_filebusinessdate     = s_mle.filebusinessdate
LEFT JOIN dim_legalentity u_mle
       ON snu.Use_SnURaw_LegalEntityId    = u_mle.SrcLegalEntityId
      AND snu.Use_filebusinessdate        = u_mle.filebusinessdate

LEFT JOIN dim_assetprice s_map
       ON snu.Source_MasterAssetId    = s_map.MasterAssetId
      AND snu.Source_filebusinessdate = s_map.filebusinessdate
LEFT JOIN dim_assetprice u_map
       ON snu.Use_MasterAssetId       = u_map.MasterAssetId
      AND snu.Use_filebusinessdate    = u_map.filebusinessdate

-- [T6] fan-out fixed: dim_fx is now 1 row per (CcyTo, fileBusinessDate)
LEFT JOIN dim_fx s_fx
       ON s_ma.Ccy                    = s_fx.CcyTo
      AND snu.Source_filebusinessdate = s_fx.fileBusinessDate
LEFT JOIN dim_fx u_fx
       ON u_ma.Ccy                    = u_fx.CcyTo
      AND snu.Use_filebusinessdate    = u_fx.fileBusinessDate
LEFT JOIN dim_fx FXCAD
       ON snu.Source_filebusinessdate = FXCAD.fileBusinessDate
      AND FXCAD.CcyTo = 'CAD'

LEFT JOIN dim_issuer s_mi
       ON s_ma.MasterIssuerId         = s_mi.Id
      AND snu.Source_filebusinessdate = s_mi.filebusinessdate
LEFT JOIN dim_issuer u_mi
       ON u_ma.MasterIssuerId         = u_mi.Id
      AND snu.Use_filebusinessdate    = u_mi.filebusinessdate

LEFT JOIN dim_wab s_wab
       ON s_wab.masterassetid    = s_ma.Id
      AND s_wab.filebusinessdate = s_ma.filebusinessdate
LEFT JOIN dim_wab u_wab
       ON u_wab.masterassetid    = u_ma.Id
      AND u_wab.filebusinessdate = u_ma.filebusinessdate

-- [T8] No outer QUALIFY. Driver is 1 row per key; every dimension is
-- <=1 row per key; therefore the result is 1 row per key by construction.
-- Verify once with 06_verify.sql before deleting this comment.
;
