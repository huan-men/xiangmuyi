# -*- coding: utf-8 -*-
"""
03_load.py —— 清洗层 CSV 幂等写入 MySQL

契约：
    输入  data/clean/clean_<批次>.csv
    输出  MySQL 表 job_post（幂等键 = position_id + batch_date）
    日志  logs/load_<批次>.log

幂等验收标准：
    连跑两次，第二次输出「新增 0 / 更新 0 / 未变 14736」，COUNT(*) 不变。

为什么不用 df.to_sql：
    to_sql(if_exists="append") 每跑一次就整批再插一遍，跑两次行数翻倍；
    改用 INSERT ... ON DUPLICATE KEY UPDATE 后，已存在的行被覆盖而不是新增。

用法：
    python src/03_load.py            # 自动取 data/clean 下最新的批次
    python src/03_load.py 20240107   # 指定批次
"""

import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# Windows 控制台默认 GBK，中文日志会乱码，这里强制按 UTF-8 输出
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 让 src/ 下的脚本能 import 项目根目录的 config 包
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql

from config.settings import (
    DATA_CLEAN,
    DB_NAME,
    DB_PASSWORD,
    LOG_DIR,
    SQL_DIR,
    db_config,
)

TABLE = "job_post"

# 清洗层列名 -> 数据库列名
COLUMN_MAP = {
    "id": "src_id",
    "positionId": "position_id",
    "positionName": "position_name",
    "category": "category",
    "bigcategory": "big_category",
    "companyId": "company_id",
    "companyFullName": "company_name",
    "companySize": "company_size",
    "industryField": "industry_field",
    "financeStage": "finance_stage",
    "positionType": "position_type",
    "city": "city",
    "district": "district",
    "salary": "salary_raw",
    "salary_min": "salary_min",
    "salary_max": "salary_max",
    "workYear": "work_year",
    "work_year_level": "work_year_level",
    "education": "education",
    "education_level": "education_level",
    "jobNature": "job_nature",
    "positionAdvantage": "position_advantage",
    "createTime": "publish_time",
    "create_at": "crawl_time",
}

INSERT_COLS = [
    "src_id", "position_id", "position_name", "category", "big_category",
    "company_id", "company_name", "company_size", "industry_field", "finance_stage",
    "position_type", "city", "district", "salary_raw", "salary_min", "salary_max",
    "work_year", "work_year_level", "education", "education_level",
    "job_nature", "position_advantage", "publish_time", "crawl_time", "batch_date",
]

INT_COLS = {"src_id", "salary_min", "salary_max", "work_year_level", "education_level"}

# 命中冲突时不更新的列：唯一键组成部分 + 不在插入列表里的 etl_time
NO_UPDATE = {"position_id", "batch_date"}

CHUNK_SIZE = 1000


def log(lines, msg):
    print(msg)
    lines.append(msg)


def strip_sql_comments(script: str) -> str:
    kept = []
    for line in script.splitlines():
        if line.strip().startswith("--"):
            continue
        kept.append(line)
    return "\n".join(kept)


def find_clean_file(batch: str | None) -> Path:
    if not DATA_CLEAN.exists():
        raise SystemExit(f"错误：目录不存在 {DATA_CLEAN}")
    if batch:
        target = DATA_CLEAN / f"clean_{batch}.csv"
        if not target.exists():
            raise SystemExit(f"错误：找不到 {target}")
        return target
    files = sorted(DATA_CLEAN.glob("clean_*.csv"))
    if not files:
        raise SystemExit(f"错误：{DATA_CLEAN} 下没有 clean_*.csv，请先运行 02_clean.py")
    return files[-1]


def read_clean(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(
        csv_path,
        encoding="utf-8",
        dtype=str,
        keep_default_na=False,
        na_values=[""],
    )
    df = df.rename(columns=COLUMN_MAP)
    missing = [c for c in INSERT_COLS if c != "batch_date" and c not in df.columns]
    if missing:
        raise SystemExit(f"错误：清洗层缺少列 {missing}")
    return df


def to_value(v, is_int: bool):
    if v is None:
        return None
    if isinstance(v, float):
        if pd.isna(v):
            return None
        return int(v) if is_int else str(v)
    s = str(v).strip()
    if s == "" or s.lower() in {"nan", "none"}:
        return None
    if is_int:
        try:
            return int(float(s))
        except (TypeError, ValueError):
            return None
    return s


def build_insert_sql() -> str:
    cols = ", ".join(f"`{c}`" for c in INSERT_COLS)
    placeholders = ", ".join(["%s"] * len(INSERT_COLS))
    updates = ", ".join(
        f"`{c}`=VALUES(`{c}`)" for c in INSERT_COLS if c not in NO_UPDATE
    )
    return f"INSERT INTO `{TABLE}` ({cols}) VALUES ({placeholders}) ON DUPLICATE KEY UPDATE {updates}"


def ensure_schema(conn):
    with conn.cursor() as cur:
        cur.execute(
            f"CREATE DATABASE IF NOT EXISTS `{DB_NAME}` "
            f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci"
        )
        # 这一步连接没指定 database，必须先切库，否则建表会报 1046 No database selected
        cur.execute(f"USE `{DB_NAME}`")
    conn.commit()

    ddl_file = SQL_DIR / "01_create_tables.sql"
    if not ddl_file.exists():
        raise SystemExit(f"错误：找不到建表脚本 {ddl_file}")
    ddl = strip_sql_comments(ddl_file.read_text(encoding="utf-8"))
    statements = [s.strip() for s in ddl.split(";") if s.strip()]
    with conn.cursor() as cur:
        for stmt in statements:
            cur.execute(stmt)
    conn.commit()


def count_batch(conn, batch_date: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{TABLE}` WHERE batch_date = %s", (batch_date,))
        return int(cur.fetchone()[0])


def main():
    started = datetime.now()
    lines = []

    if not DB_PASSWORD:
        raise SystemExit(
            "错误：.env 里的 DB_PASSWORD 是空的。\n"
            "请在项目根目录的 .env 中填好 DB_PASSWORD（就是 mysql -u root -p 时输的那个密码）。"
        )

    batch_arg = sys.argv[1] if len(sys.argv) > 1 else None
    csv_path = find_clean_file(batch_arg)
    batch_raw = csv_path.stem.split("_")[1]          # 20240107
    batch_date = f"{batch_raw[:4]}-{batch_raw[4:6]}-{batch_raw[6:]}"  # 2024-01-07

    log(lines, f"清洗层文件 : {csv_path}")
    log(lines, f"批次日期   : {batch_date}")
    log(lines, f"目标数据库 : {DB_NAME}.{TABLE}")

    df = read_clean(csv_path)
    n = len(df)
    log(lines, f"读取记录   : {n:,} 行 x {len(df.columns)} 列")

    rows = []
    for rec in df.to_dict("records"):
        batch_row = []
        for c in INSERT_COLS:
            if c == "batch_date":
                batch_row.append(batch_date)
            else:
                batch_row.append(to_value(rec.get(c), c in INT_COLS))
        rows.append(tuple(batch_row))
    log(lines, f"待写入     : {len(rows):,} 行 x {len(INSERT_COLS)} 列")

    # 1) 建库（不指定 database 先连）
    conn = pymysql.connect(**db_config(with_db=False))
    try:
        ensure_schema(conn)
    finally:
        conn.close()

    # 2) 连库写入
    conn = pymysql.connect(**db_config(with_db=True))
    try:
        before = count_batch(conn, batch_date)
        log(lines, f"写入前     : 本批次已有 {before:,} 行")

        sql = build_insert_sql()
        affected = 0
        with conn.cursor() as cur:
            for i in range(0, len(rows), CHUNK_SIZE):
                part = rows[i:i + CHUNK_SIZE]
                cur.executemany(sql, part)
                affected += cur.rowcount
                done = min(i + CHUNK_SIZE, len(rows))
                log(lines, f"  进度 {done:>6,}/{len(rows):,}  本块影响 {cur.rowcount:>6,} 行")
        conn.commit()

        after = count_batch(conn, batch_date)
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT COUNT(DISTINCT position_id) FROM `{TABLE}` WHERE batch_date = %s",
                (batch_date,),
            )
            distinct_pid = int(cur.fetchone()[0])
    finally:
        conn.close()

    # 3) 结果解读
    #    MySQL 对 ON DUPLICATE KEY UPDATE 的返回值：插入=1 / 更新=2 / 无变化=0
    inserted = after - before
    updated = (affected - inserted) // 2
    unchanged = n - inserted - updated

    log(lines, "")
    log(lines, "=========== 写库结果 ===========")
    log(lines, f"新增        : {inserted:>6,} 行")
    log(lines, f"更新        : {updated:>6,} 行")
    log(lines, f"未变        : {unchanged:>6,} 行")
    log(lines, f"本批次总行数: {after:>6,} 行")
    log(lines, f"唯一岗位数  : {distinct_pid:>6,} 个")
    log(lines, f"耗时        : {(datetime.now() - started).total_seconds():.2f} 秒")
    log(lines, "")
    if inserted == 0 and updated == 0:
        log(lines, "结论：本次没有任何变化 —— 幂等验证通过（重跑不会制造重复数据）。")
    elif inserted == n:
        log(lines, "结论：首次导入，全部为新增行。再跑一次应该全是 0，即为幂等。")
    else:
        log(lines, "结论：增量更新。已有行被覆盖，行数没有膨胀。")

    # 4) 落日志
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"load_{batch_raw}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write(f"\n===== {started:%Y-%m-%d %H:%M:%S} 运行 =====\n")
        f.write("\n".join(lines) + "\n")
    print(f"\n日志已写入: {log_file}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
