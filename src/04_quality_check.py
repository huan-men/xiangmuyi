# -*- coding: utf-8 -*-
"""
04_quality_check.py —— 数据质量校验（只读，不改数据）

为什么清洗之外还要单独做校验：
    02_clean 只能拦住「规则内」的脏数据；规则外的异常它拦不住
    （比如 300k-400k 的 NLP 实习生、1k-2k 的全栈工程师，都在合法区间里）。
    所以：清洗负责改数据，校验负责报警，两者不是一回事。
    校验失败不中断流程，只记录 —— 因为数据大概率还能用，但要让人知道。

契约：
    输入  data/raw/raw_<批次>.csv、data/clean/clean_<批次>.csv、MySQL 表 job_post
    输出  logs/quality_<批次>.log + 控制台报告
    退出码 0 = 没有 FAIL；1 = 有 FAIL（调度系统据此判断）

校验项：
    CHK-01 链路一致性   原始层行数 -> 清洗层行数 -> 数据库行数
    CHK-02 主键唯一性   position_id 是否有重复
    CHK-03 关键字段空值率
    CHK-04 枚举值合法性
    CHK-05 数值范围与勾稽关系
    CHK-06 分布探查（分位数报警）

用法：
    python src/04_quality_check.py
    python src/04_quality_check.py 20240107
"""

import sys
import unicodedata
from datetime import datetime
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql

from config.settings import DATA_CLEAN, DATA_RAW, DB_PASSWORD, LOG_DIR, db_config

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
TABLE = "job_post"

# 业务阈值：改口径就改这里
CORE_NULL_RATE_MAX = 0.05        # 核心字段空值率上限 5%
OTHER_NULL_RATE_MAX = 0.20       # 非核心字段空值率上限 20%
# 这些是文本列，空字符串要算作空值；DATETIME / 数值列只能判 NULL
TEXT_COLS = {"city", "position_name", "company_name", "industry_field", "district"}
SALARY_MIN_FLOOR = 1000          # 月薪下限（元）
SALARY_MAX_CEIL = 500000         # 月薪上限（元）
LOW_SALARY_HINT = 3000           # 低于这个数视为疑似实习/兼职/单位错误
HIGH_SALARY_HINT = 60000         # 高于这个数视为尾部异常

results = []


def add(cid: str, title: str, status: str, lines: list):
    results.append({"cid": cid, "title": title, "status": status, "lines": lines})


def pad_cn(text: str, width: int) -> str:
    """中文按两格宽补齐，避免报告里状态列对不齐。"""
    w = sum(2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1 for ch in str(text))
    return str(text) + " " * max(0, width - w)


def count_records(path: Path) -> int:
    """只读首列来数逻辑记录数（不信文件行数，positionDetail 里有换行）。"""
    df = pd.read_csv(path, encoding="utf-8", dtype=str, keep_default_na=False, usecols=[0])
    return len(df)


def find_files(batch: str | None):
    if batch:
        raw = DATA_RAW / f"raw_{batch}.csv"
        clean = DATA_CLEAN / f"clean_{batch}.csv"
        if not raw.exists() or not clean.exists():
            raise SystemExit(f"错误：找不到批次 {batch} 的原始层或清洗层文件")
        return batch, raw, clean
    cleans = sorted(DATA_CLEAN.glob("clean_*.csv"))
    if not cleans:
        raise SystemExit(f"错误：{DATA_CLEAN} 下没有 clean_*.csv")
    batch = cleans[-1].stem.split("_")[1]
    return batch, DATA_RAW / f"raw_{batch}.csv", cleans[-1]


def chk_consistency(conn, batch, raw_path: Path, clean_path: Path):
    raw_n = count_records(raw_path)
    clean_n = count_records(clean_path)
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{TABLE}` WHERE batch_date=%s", (batch,))
        db_n = int(cur.fetchone()[0])
        cur.execute(
            f"SELECT COUNT(DISTINCT position_id) FROM `{TABLE}` WHERE batch_date=%s", (batch,)
        )
        db_dist = int(cur.fetchone()[0])

    dropped = raw_n - clean_n
    rate = dropped / raw_n * 100 if raw_n else 0
    lines = [
        f"原始层 {raw_n:,} 行 -> 清洗层 {clean_n:,} 行（剔除 {dropped:,} 行，{rate:.2f}%）",
        f"数据库 {db_n:,} 行 / {db_dist:,} 个唯一岗位",
    ]
    ok = raw_n > 0 and clean_n > 0 and clean_n == db_n == db_dist
    if ok:
        lines.append("三层行数闭合")
    else:
        lines.append("不一致：清洗层没同步进库，或 03_load 跑的是旧批次")
    add("CHK-01", "链路一致性", PASS if ok else FAIL, lines)


def chk_primary_key(conn, batch):
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*), COUNT(DISTINCT position_id) FROM `{TABLE}` WHERE batch_date=%s", (batch,))
        total, dist = cur.fetchone()
    dup = total - dist
    lines = [f"总行数 {total:,}，唯一 position_id {dist:,}，重复 {dup:,}"]
    add("CHK-02", "主键唯一性", PASS if dup == 0 else FAIL, lines)


def chk_null_rate(conn, batch):
    core = ["city", "position_name", "salary_min", "salary_max", "work_year_level", "education_level"]
    other = ["company_name", "industry_field", "district", "publish_time"]
    with conn.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM `{TABLE}` WHERE batch_date=%s", (batch,))
        total = int(cur.fetchone()[0])
        lines = [f"样本 {total:,} 行"]
        worst = PASS
        for col in core + other:
            # 字符串列可以比 ''，DATETIME/数值列比 '' 会报 1525，必须区分类型
            blank = f"`{col}`=''" if col in TEXT_COLS else "1=0"
            cur.execute(
                f"SELECT SUM(`{col}` IS NULL OR {blank}) FROM `{TABLE}` WHERE batch_date=%s",
                (batch,),
            )
            nulls = int(cur.fetchone()[0] or 0)
            rate = nulls / total if total else 0
            limit = CORE_NULL_RATE_MAX if col in core else OTHER_NULL_RATE_MAX
            flag = ""
            if rate > limit:
                flag = "  <== 超阈值"
                worst = FAIL if col in core else (WARN if worst != FAIL else worst)
            lines.append(f"  {col:16s} 空值 {nulls:>6,}  占比 {rate*100:6.2f}%{flag}")
    add("CHK-03", "关键字段空值率", worst, lines)


def chk_enum(conn, batch):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                SUM(work_year_level NOT IN (-1,0,1,2,3,4,5)) AS bad_wy,
                SUM(education_level NOT IN (0,1,2,3,4))      AS bad_edu,
                SUM(city IS NULL OR city='' OR city LIKE '%%·%%') AS bad_city,
                SUM(job_nature NOT IN ('全职','实习','兼职'))  AS bad_nature
            FROM `{TABLE}` WHERE batch_date=%s
            """,
            (batch,),
        )
        row = cur.fetchone()
    names = ["work_year_level 越界", "education_level 越界", "city 非法", "job_nature 越界"]
    values = [int(v or 0) for v in row]
    bad = sum(values)
    lines = [f"{n}: {v:,}" for n, v in zip(names, values)]
    add("CHK-04", "枚举值合法性", PASS if bad == 0 else FAIL, lines)


def chk_range(conn, batch):
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
                SUM(salary_min < %s)          AS too_low,
                SUM(salary_max > %s)          AS too_high,
                SUM(salary_min > salary_max)  AS inverted,
                SUM(salary_min = salary_max)  AS flat,
                COUNT(*)                      AS total
            FROM `{TABLE}` WHERE batch_date=%s
            """,
            (SALARY_MIN_FLOOR, SALARY_MAX_CEIL, batch),
        )
        too_low, too_high, inverted, flat, total = [int(v or 0) for v in cur.fetchone()]

    flat_rate = flat / total if total else 0
    status = PASS
    lines = [
        f"薪资下限 < {SALARY_MIN_FLOOR:,}: {too_low:,}",
        f"薪资上限 > {SALARY_MAX_CEIL:,}: {too_high:,}",
        f"下限 > 上限（倒挂）  : {inverted:,}",
        f"上下限相等（单值）   : {flat:,}（{flat_rate*100:.2f}%）",
    ]
    if too_low or too_high or inverted:
        status = FAIL
    elif flat_rate > 0.05:
        status = WARN
        lines.append("单值区间超过 5%，可能有人只填了一个数，值得抽查")
    add("CHK-05", "数值范围与勾稽", status, lines)


def chk_distribution(conn, batch):
    # 直接用游标取，不经过 pandas.read_sql（pandas 2.x 对非 sqlalchemy 连接支持不稳）
    with conn.cursor() as cur:
        cur.execute(f"SELECT salary_min, salary_max FROM `{TABLE}` WHERE batch_date=%s", (batch,))
        rows = cur.fetchall()
    if not rows:
        add("CHK-06", "分布探查", WARN, ["没有数据，跳过"])
        return
    df = pd.DataFrame(rows, columns=["salary_min", "salary_max"])
    mid = (df["salary_min"].astype(float) + df["salary_max"].astype(float)) / 2
    p1, p50, p99 = [float(mid.quantile(q)) for q in (0.01, 0.5, 0.99)]
    low_cnt = int((df["salary_min"] < LOW_SALARY_HINT).sum())
    high_cnt = int((df["salary_max"] > HIGH_SALARY_HINT).sum())

    lines = [
        f"薪资中点 P1 / P50 / P99 = {p1:,.0f} / {p50:,.0f} / {p99:,.0f} 元",
        f"下限 < {LOW_SALARY_HINT:,} 元的岗位：{low_cnt:,} 个（疑似实习/兼职/单位填错）",
        f"上限 > {HIGH_SALARY_HINT:,} 元的岗位：{high_cnt:,} 个（专家/Leader 或填错）",
    ]
    status = PASS
    if p1 < LOW_SALARY_HINT:
        status = WARN
        lines.append("左下尾部偏低：算平均薪资时会被拉低，建议同时报中位数")
    if p99 > HIGH_SALARY_HINT * 1.5:
        status = WARN
        lines.append("右上尾部很极端：均值会被拉高，结论里必须说明口径")
    add("CHK-06", "分布探查", status, lines)


def main():
    started = datetime.now()
    if not DB_PASSWORD:
        raise SystemExit("错误：.env 里 DB_PASSWORD 为空，请先填写")

    batch_arg = sys.argv[1] if len(sys.argv) > 1 else None
    batch, raw_path, clean_path = find_files(batch_arg)

    conn = pymysql.connect(**db_config())
    try:
        chk_consistency(conn, batch, raw_path, clean_path)
        chk_primary_key(conn, batch)
        chk_null_rate(conn, batch)
        chk_enum(conn, batch)
        chk_range(conn, batch)
        chk_distribution(conn, batch)
    finally:
        conn.close()

    # ---------------- 输出报告
    out = []
    def emit(s=""):
        print(s)
        out.append(s)

    emit("=" * 70)
    emit("数据质量报告")
    emit(f"批次 {batch}   表 {TABLE}   时间 {started:%Y-%m-%d %H:%M:%S}")
    emit("=" * 70)
    for r in results:
        head = f"[{r['cid']}] {r['title']}"
        emit(f"{pad_cn(head, 40)}{r['status']}")
        for line in r["lines"]:
            emit(f"    {line}")
        emit("")

    n_pass = sum(1 for r in results if r["status"] == PASS)
    n_warn = sum(1 for r in results if r["status"] == WARN)
    n_fail = sum(1 for r in results if r["status"] == FAIL)
    overall = FAIL if n_fail else (WARN if n_warn else PASS)
    emit(f"汇总：PASS {n_pass} / WARN {n_warn} / FAIL {n_fail}   整体判定：{overall}")
    if n_warn:
        emit("说明：WARN 是「数据能用但要知道」，不阻断流程；FAIL 才需要回头修。")
    emit(f"耗时 {(datetime.now() - started).total_seconds():.2f} 秒")

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOG_DIR / f"quality_{batch}.log"
    with log_file.open("a", encoding="utf-8") as f:
        f.write("\n")
        f.write("\n".join(out) + "\n")
    print(f"\n日志已写入: {log_file}")

    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
