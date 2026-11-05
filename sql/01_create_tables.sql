-- ============================================================
-- 01_create_tables.sql
-- 招聘岗位清洗表（项目一的落库层，角色相当于数仓的明细层 DWD）
--
-- 设计要点：
-- 1. 幂等键 = (position_id, batch_date)
--    同一岗位同一批次只存一条；不同批次 = 不同快照，可并存。
--    这样以后每天增量抓一批，数据只增不改历史，指标按 batch_date 取最新。
-- 2. batch_date 同时是项目二 Hive 表的分区键，先在这里埋好。
-- 3. etl_time 用 DEFAULT CURRENT_TIMESTAMP，且**不参与** ON DUPLICATE KEY UPDATE：
--    重跑时这一列不变，才能真正验证「重跑不产生差异」。
-- 4. varchar 长度按实测最长值上浮（positionName 48 → 128 留余量）。
-- ============================================================

CREATE TABLE IF NOT EXISTS job_post (
    id                 BIGINT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '自增主键，无业务含义',
    src_id             INT UNSIGNED    DEFAULT NULL          COMMENT '源文件行 id',
    position_id        VARCHAR(16)     NOT NULL              COMMENT '拉勾岗位 ID（业务主键）',
    position_name      VARCHAR(128)    NOT NULL              COMMENT '岗位名称',
    category           VARCHAR(32)     DEFAULT NULL          COMMENT '岗位类别（细分）',
    big_category       VARCHAR(32)     DEFAULT NULL          COMMENT '岗位大类',
    company_id         VARCHAR(16)     DEFAULT NULL          COMMENT '公司 ID',
    company_name       VARCHAR(128)    DEFAULT NULL          COMMENT '公司全称',
    company_size       VARCHAR(16)     DEFAULT NULL          COMMENT '公司规模',
    industry_field     VARCHAR(128)    DEFAULT NULL          COMMENT '行业领域',
    finance_stage      VARCHAR(16)     DEFAULT NULL          COMMENT '融资阶段',
    position_type      VARCHAR(128)    DEFAULT NULL          COMMENT '职位类型',
    city               VARCHAR(32)     NOT NULL              COMMENT '城市',
    district           VARCHAR(32)     DEFAULT NULL          COMMENT '区/县',
    salary_raw         VARCHAR(16)     DEFAULT NULL          COMMENT '原始薪资串，如 20k-30k',
    salary_min         INT UNSIGNED    DEFAULT NULL          COMMENT '月薪下限（元）',
    salary_max         INT UNSIGNED    DEFAULT NULL          COMMENT '月薪上限（元）',
    work_year          VARCHAR(8)      DEFAULT NULL          COMMENT '经验要求原文',
    work_year_level    TINYINT         DEFAULT NULL          COMMENT '经验编码 0~5，-1=不限',
    education          VARCHAR(8)      DEFAULT NULL          COMMENT '学历要求原文',
    education_level    TINYINT         DEFAULT NULL          COMMENT '学历编码 0~4',
    job_nature         VARCHAR(8)      DEFAULT NULL          COMMENT '工作性质',
    position_advantage VARCHAR(128)    DEFAULT NULL          COMMENT '职位亮点',
    publish_time       DATETIME        DEFAULT NULL          COMMENT '岗位发布时间',
    crawl_time         DATETIME        DEFAULT NULL          COMMENT '抓取时间',
    batch_date         DATE            NOT NULL              COMMENT '数据批次（=抓取日期），项目二 Hive 分区键',
    etl_time           DATETIME        NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '入库时间（重跑不变）',
    PRIMARY KEY (id),
    UNIQUE KEY uk_position_batch (position_id, batch_date),
    KEY idx_batch (batch_date),
    KEY idx_city (city),
    KEY idx_salary (salary_min, salary_max),
    KEY idx_work_year (work_year_level)
) ENGINE = InnoDB
  DEFAULT CHARSET = utf8mb4
  COLLATE = utf8mb4_general_ci
  COMMENT = '招聘岗位清洗表（幂等键 position_id + batch_date）';
