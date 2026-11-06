-- ============================================================
-- 02_metrics.sql —— 三个核心指标
--
-- 口径说明（每条指标标注计算方式与过滤条件）：
--
-- 1. 岗位数 = 去重后的 position_id 个数。
--    同一岗位被多次抓取只算一次，否则重复抓取会让指标虚高。
--
-- 2. 平均薪资 = (salary_min + salary_max) / 2 的算术平均，即"区间中点法"。
--    不用下限（会低估），不用上限（会高估）。
--    过滤 salary_min >= 1000：排除 1k-2k 这类疑似实习/兼职/单位填错的记录。
--    为什么同时给中位数：薪资是右偏分布，少数超高薪会把均值拉高，
--    中位数更稳，两个一起报才说得清。
--
-- 3. 城市分布 = 按 city 聚合的岗位数，取 Top 10。
--
-- 4. 始终按 batch_date = 最新批次 过滤：
--    多批次并存时，不写这一句会重复统计历史快照。
-- ============================================================


-- ------------------------------------------------------------
-- 指标一：整体概况
-- ------------------------------------------------------------
SELECT
    MAX(batch_date)                             AS batch,
    COUNT(*)                                    AS job_cnt,
    COUNT(DISTINCT company_id)                  AS company_cnt,
    COUNT(DISTINCT city)                        AS city_cnt,
    ROUND(AVG((salary_min + salary_max) / 2))   AS avg_salary,
    MIN(salary_min)                             AS min_salary,
    MAX(salary_max)                             AS max_salary
FROM job_post
WHERE batch_date = (SELECT MAX(batch_date) FROM job_post)
  AND salary_min >= 1000;


-- ------------------------------------------------------------
-- 指标二：城市分布 Top 10（岗位数 + 平均月薪）
-- ------------------------------------------------------------
SELECT
    city,
    COUNT(*)                                    AS job_cnt,
    ROUND(COUNT(*) * 100.0 /
          (SELECT COUNT(*) FROM job_post
            WHERE batch_date = (SELECT MAX(batch_date) FROM job_post)), 2) AS pct,
    ROUND(AVG((salary_min + salary_max) / 2))   AS avg_salary
FROM job_post
WHERE batch_date = (SELECT MAX(batch_date) FROM job_post)
  AND salary_min >= 1000
GROUP BY city
ORDER BY job_cnt DESC
LIMIT 10;


-- ------------------------------------------------------------
-- 指标三：按岗位方向分组（数据开发相关岗单独拎出来看）
-- ------------------------------------------------------------
SELECT
    CASE
        WHEN position_name REGEXP '大数据开发'                  THEN '01_大数据开发'
        WHEN position_name REGEXP '数据开发|数据仓库|数仓|ETL'   THEN '02_数据开发/数仓/ETL'
        WHEN position_name REGEXP '数据分析|BI'                  THEN '03_数据分析/BI'
        ELSE '04_其他技术岗'
    END                                                 AS job_group,
    COUNT(*)                                            AS job_cnt,
    ROUND(AVG((salary_min + salary_max) / 2))           AS avg_salary,
    ROUND(MIN((salary_min + salary_max) / 2))           AS min_mid,
    ROUND(MAX((salary_min + salary_max) / 2))           AS max_mid
FROM job_post
WHERE batch_date = (SELECT MAX(batch_date) FROM job_post)
  AND salary_min >= 1000
GROUP BY job_group
ORDER BY job_group;


-- ------------------------------------------------------------
-- 加分项：薪资中位数（MySQL 8 窗口函数，无 MEDIAN 函数时的手写实现）
-- 中位数 = 排序后第 (n+1)/2 位（偶数个时取中间两个的平均）
-- ------------------------------------------------------------
WITH ranked AS (
    SELECT
        (salary_min + salary_max) / 2                       AS mid,
        ROW_NUMBER() OVER (ORDER BY (salary_min + salary_max) / 2) AS rn,
        COUNT(*)     OVER ()                                AS cnt
    FROM job_post
    WHERE batch_date = (SELECT MAX(batch_date) FROM job_post)
      AND salary_min >= 1000
)
SELECT
    ROUND(AVG(mid))     AS median_salary,
    MAX(cnt)            AS sample_cnt
FROM ranked
WHERE rn IN (FLOOR((cnt + 1) / 2), CEIL((cnt + 1) / 2));
