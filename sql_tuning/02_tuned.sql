-- =====================================================================
-- snuallocation_waterfall_ciqdash1 -- TUNED for Databricks / Delta Lake
-- =====================================================================
-- Based on the reconstruction in 01_original_reconstructed.sql.
-- VERIFY COLUMN NAMES against your real file before running.
--
-- Changes vs original, in order of expected impact:
--
--   [T1] Driver deduped BEFORE the 33 joins instead of after.
--        Original joined every file version of every snu row through all
--        33 joins, then kept 1 row per key at the very end. Now the
--        ROW_NUMBER runs on narrow base rows first. If snu averages N
--        file versions per key, this cuts join input by ~N x.
--        SEMANTICS: identical, provided no dimension fans out (see D1).
--
--   [T2] masterwabrate deduped ONCE in a CTE, joined twice.
--        Original wrote the same scan+window+sort out twice (s_wab,
--        u_wab). Spark's ReuseExchange may or may not collapse them.
--        SEMANTICS: identical.
--
--   [T3] COALESCE removed from the DATE half of the s_mc / u_mc join
--        keys. Both branches of that COALESCE always resolve to
--        snu.Source_filebusinessdate (s_mt and s_pbmc are themselves
--        joined on it), and when both are NULL the ID half of the key is
--        NULL too, so the join can't match either way. Replacing it with
--        a plain column ref restores Dynamic File Pruning on
--        MasterCounterparty.
--        SEMANTICS: identical. (COALESCE kept on the ID half.)
--
--   [T4] Optional single-business-date scoping (see :business_date).
--        SEMANTICS: CHANGES RESULTS -- filters to one date. Leave the
--        block commented out for a full-history run.
--
--   [T5] Outer QUALIFY retained by default but now redundant if no
--        dimension fans out. See the "SAFE TO DELETE" block at the
--        bottom -- removing it saves a full shuffle+sort of the widest
--        version of the dataset. Gate on diagnostics 03_diagnostics.sql.
-- =====================================================================


-- ---------------------------------------------------------------------
-- [T4] Parameter. Databricks named parameter marker.
--   DBSQL / notebooks:  supply :business_date at run time
--   or: DECLARE OR REPLACE VARIABLE business_date DATE = '2026-07-30';
--   or: notebook widget  ${business_date}
-- ---------------------------------------------------------------------

WITH

-- =====================================================================
-- [T1] Driver, deduped up front, on narrow rows.
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
    -- [T4] Uncomment for a single-date run. Written as an OR on the raw
    -- columns (NOT coalesce(...) = :d) so Delta can still prune files /
    -- partitions -- a COALESCE here would defeat data skipping.
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
-- [T2] masterwabrate: one scan, one window, joined twice below.
-- =====================================================================
wab AS (
    SELECT
        masterassetid,
        filebusinessdate,
        ProdSpecialType,
        WabRate,
        WabRateSrc
    FROM (
        SELECT *,
               CASE WHEN WABRateSrc = 'CIBC Weighted Avg. Borrow' THEN 1
                    WHEN WABRateSrc = 'Datalend Average Rate'     THEN 2
                    ELSE 3
               END AS rankedsrc
        FROM collateraliq_bronze.masterwabrate
        -- [T4] WHERE filebusinessdate = :business_date
    )
    QUALIFY ROW_NUMBER() OVER (
              PARTITION BY masterassetid, filebusinessdate
              ORDER BY filecreateddate DESC, rankedsrc ASC,
                       LastModifiedDateTimeUTC DESC) = 1
),

enriched AS (
    SELECT
        -- [OPT] snu.* carries the driver's full column set through every
        -- shuffle boundary. Replacing this with an explicit list of the
        -- columns actually consumed downstream is a direct cut in
        -- shuffle bytes. Left as-is because I can't tell which are used.
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
        u_mpc.ParentLECptyId AS USE_MasterLECounterparty_ParentLECptyId,  -- [?BUG] asymmetric with source side (s_mlc). Preserved as transcribed.
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

    LEFT JOIN collateraliq_bronze.MasterAsset s_ma
           ON snu.Source_MasterAssetId    = s_ma.Id
          AND snu.Source_filebusinessdate = s_ma.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterAsset u_ma
           ON snu.Use_MasterAssetId       = u_ma.Id
          AND snu.Use_filebusinessdate    = u_ma.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterTrade s_mt
           ON snu.Source_MasterTradeId    = s_mt.Id
          AND snu.Source_filebusinessdate = s_mt.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterTrade u_mt
           ON snu.Use_MasterTradeId       = u_mt.Id
          AND snu.Use_filebusinessdate    = u_mt.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterSecurityPosition s_msp
           ON snu.Source_MasterSecurityPositionId = s_msp.Id
          AND snu.Source_filebusinessdate         = s_msp.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterSecurityPosition u_msp
           ON snu.Use_MasterSecurityPositionId    = u_msp.Id
          AND snu.Use_filebusinessdate            = u_msp.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterOrgUnitRollup sr
           ON snu.Source_SnURaw_OrgUnit   = sr.OrgUnit
          AND snu.Source_filebusinessdate = sr.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterOrgUnitRollup ur
           ON snu.Use_SnURaw_OrgUnit      = ur.OrgUnit
          AND snu.Use_filebusinessdate    = ur.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterBookingAccount s_mba
           ON s_mt.MasterBookingAcntId    = s_mba.Id
          -- [T3-style] s_mt.filebusinessdate is always snu.Source_filebusinessdate
          -- when s_mt matched; using the driver column directly keeps the
          -- predicate pushable to the MasterBookingAccount scan.
          AND snu.Source_filebusinessdate = s_mba.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterBookingAccount u_mba
           ON u_mt.MasterBookingAcntId    = u_mba.Id
          AND snu.Use_filebusinessdate    = u_mba.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterBookingAccount s_mba_sp
           ON s_msp.MasterBookingAcntId   = s_mba_sp.Id
          AND snu.Source_filebusinessdate = s_mba_sp.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterBookingAccount u_mba_sp
           ON u_msp.MasterBookingAcntId   = u_mba_sp.Id
          AND snu.Use_filebusinessdate    = u_mba_sp.filebusinessdate

    LEFT JOIN collateraliq_bronze.masterbookingaccountexternallinkage s_mbae
           ON s_msp.MasterBookingAcntId   = s_mbae.MasterBookingAcntId
          AND snu.Source_filebusinessdate = s_mbae.filebusinessdate
    LEFT JOIN collateraliq_bronze.masterbookingaccountexternallinkage u_mbae
           ON u_msp.MasterBookingAcntId   = u_mbae.MasterBookingAcntId
          AND snu.Use_filebusinessdate    = u_mbae.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterCounterparty s_pbmc
           ON s_mbae.ExternalIdType       = s_pbmc.CptyId          -- [?] verify column
          AND snu.Source_filebusinessdate = s_pbmc.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterCounterparty u_pbmc
           ON u_mbae.ExternalIdType       = u_pbmc.CptyId          -- [?] verify column
          AND snu.Use_filebusinessdate    = u_pbmc.filebusinessdate

    -- [T3] COALESCE kept on the ID, removed from the DATE.
    LEFT JOIN collateraliq_bronze.MasterCounterparty s_mc
           ON COALESCE(s_mt.MasterCptyId, s_pbmc.CptyId) = s_mc.Id
          AND snu.Source_filebusinessdate               = s_mc.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterCounterparty u_mc
           ON COALESCE(u_mt.MasterCptyId, u_pbmc.CptyId) = u_mc.Id
          AND snu.Use_filebusinessdate                  = u_mc.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterLECounterparty s_mlc
           ON s_mc.MasterLECptyId         = s_mlc.Id
          AND snu.Source_filebusinessdate = s_mlc.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterLECounterparty u_mlc
           ON u_mc.MasterLECptyId         = u_mlc.Id
          AND snu.Use_filebusinessdate    = u_mlc.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterLECounterparty s_mpc
           ON s_mlc.ParentLECptyId        = s_mpc.LECptyId
          AND snu.Source_filebusinessdate = s_mpc.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterLECounterparty u_mpc
           ON u_mlc.ParentLECptyId        = u_mpc.LECptyId
          AND snu.Use_filebusinessdate    = u_mpc.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterLegalEntity s_mle
           ON snu.Source_SnURaw_LegalEntityId = s_mle.SrcLegalEntityId
          AND snu.Source_filebusinessdate     = s_mle.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterLegalEntity u_mle
           ON snu.Use_SnURaw_LegalEntityId    = u_mle.SrcLegalEntityId
          AND snu.Use_filebusinessdate        = u_mle.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterAssetPrice s_map
           ON snu.Source_MasterAssetId    = s_map.MasterAssetId
          AND snu.Source_filebusinessdate = s_map.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterAssetPrice u_map
           ON snu.Use_MasterAssetId       = u_map.MasterAssetId
          AND snu.Use_filebusinessdate    = u_map.filebusinessdate

    -- [D2] FX joins match on CcyTo only. If MasterFX is keyed by
    -- (CcyFrom, CcyTo, date) this FANS OUT silently and the outer QUALIFY
    -- picks an arbitrary survivor. See 03_diagnostics.sql check D2.
    LEFT JOIN collateraliq_bronze.MasterFX s_fx
           ON s_ma.Ccy                    = s_fx.CcyTo
          AND snu.Source_filebusinessdate = s_fx.fileBusinessDate
    LEFT JOIN collateraliq_bronze.MasterFX u_fx
           ON u_ma.Ccy                    = u_fx.CcyTo
          AND snu.Use_filebusinessdate    = u_fx.fileBusinessDate

    LEFT JOIN collateraliq_bronze.MasterIssuer s_mi
           ON s_ma.MasterIssuerId         = s_mi.Id
          AND snu.Source_filebusinessdate = s_mi.filebusinessdate
    LEFT JOIN collateraliq_bronze.MasterIssuer u_mi
           ON u_ma.MasterIssuerId         = u_mi.Id
          AND snu.Use_filebusinessdate    = u_mi.filebusinessdate

    LEFT JOIN collateraliq_bronze.MasterFX FXCAD
           ON snu.Source_filebusinessdate = FXCAD.fileBusinessDate
          AND FXCAD.CcyTo = 'CAD'          -- [D2] same fan-out risk

    -- [T2] one wab CTE, joined twice
    LEFT JOIN wab s_wab
           ON s_wab.masterassetid    = s_ma.Id
          AND s_wab.filebusinessdate = s_ma.filebusinessdate
    LEFT JOIN wab u_wab
           ON u_wab.masterassetid    = u_ma.Id
          AND u_wab.filebusinessdate = u_ma.filebusinessdate
)

SELECT *
FROM enriched
-- ---------------------------------------------------------------------
-- [T5] SAFE TO DELETE once 03_diagnostics.sql confirms no dimension
-- fans out. With [T1] in place the driver is already 1 row per
-- (fileBusinessDate, RunId, RunType, Id), so this window only exists to
-- absorb fan-out. Deleting it removes a full shuffle + sort of the
-- widest form of the dataset -- the single biggest remaining cost.
-- ---------------------------------------------------------------------
QUALIFY ROW_NUMBER() OVER (
          PARTITION BY SNU_fileBusinessDate, SNU_RunId, SNU_RunType, SNU_Id
          ORDER BY SNU_fileCreatedDate DESC) = 1;
