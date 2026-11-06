# -*- coding: utf-8 -*-
"""
02_clean.py —— 清洗层构建（Transform）

职责（只做这一件事）：
    读取原始层快照，按 config/rules.py 中的规则清洗，
    输出清洗层 + 一份可读的清洗日志。不写入数据库。

契约：
    输入  data/raw/raw_<批次>.csv              原始层（只读，永不修改）
    输出  data/clean/clean_<批次>.csv          清洗层
    日志  logs/clean_<批次>.log

清洗步骤（顺序固定，每步都记账）：
    [001] 丢弃长文本列
    [002] 按 positionId 去重（保留 createTime 最新）
    [003] 脏值归一（同义异形）
    [004] 薪资解析与合法性校验
    [005] 经验 / 学历编码

日志的意义：
    每一步的输入行数、输出行数与剔除原因都留痕，
    数据量为什么缩减、缩减在哪一步，无需靠推测。
"""

from __future__ import annotations

import hashlib
import logging
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

# ---------------------------------------------------------------- 路径定位
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from config.rules import (  # noqa: E402
    DEDUP_KEY,
    DEDUP_ORDER_BY,
    DROP_COLUMNS,
    EDUCATION_LEVEL,
    SALARY_MAX_VALID,
    SALARY_MIN_VALID,
    SALARY_UNIT_MULTIPLIER,
    VALUE_ALIAS,
    WORK_YEAR_LEVEL,
)

RAW_DIR = ROOT / "data" / "raw"
CLEAN_DIR = ROOT / "data" / "clean"
LOG_DIR = ROOT / "logs"
RULES_FILE = ROOT / "config" / "rules.py"
ENCODING = "utf-8"

# 薪资原文形如 25k-40k / 25K-40K / 15k - 25k
SALARY_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*[kK]\s*-\s*(\d+(?:\.\d+)?)\s*[kK]\s*$")


# ---------------------------------------------------------------- 工具函数
def setup_logger(log_path: Path) -> logging.Logger:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("clean")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def md5_of(path: Path, chunk_size: int = 1 << 20) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def latest_raw() -> Path:
    """取批次最大（文件名排序最后）的原始层快照。"""
    files = sorted(RAW_DIR.glob("raw_*.csv"))
    if not files:
        raise FileNotFoundError(f"{RAW_DIR} 下没有 raw_*.csv，请先运行 01_extract.py")
    return files[-1]


def parse_salary(value: object) -> tuple[int | None, int | None]:
    """25k-40k -> (25000, 40000)；解析失败返回 (None, None)。"""
    m = SALARY_RE.match(str(value))
    if not m:
        return None, None
    low = float(m.group(1)) * SALARY_UNIT_MULTIPLIER
    high = float(m.group(2)) * SALARY_UNIT_MULTIPLIER
    return int(round(low)), int(round(high))


# ---------------------------------------------------------------- 主流程
def main() -> int:
    try:
        src = latest_raw()
    except FileNotFoundError as e:
        print(f"[FAIL] {e}")
        return 1

    batch = src.stem.replace("raw_", "")
    log_path = LOG_DIR / f"clean_{batch}.log"
    logger = setup_logger(log_path)

    logger.info("=" * 64)
    logger.info("02_clean 开始")
    logger.info(f"输入    : {src}")
    logger.info(f"批次    : {batch}")
    logger.info(f"规则文件: {RULES_FILE}")
    logger.info(f"规则 MD5: {md5_of(RULES_FILE)}")
    logger.info(f"输入 MD5: {md5_of(src)}")

    df = pd.read_csv(src, encoding=ENCODING, dtype=str,
                     keep_default_na=False, na_values=[""])
    n_in, cols_in = len(df), df.shape[1]
    logger.info(f"[000] 读取原始层: {n_in:,} 行 x {cols_in} 列")

    # ---------------- [001] 丢弃长文本列
    to_drop = [c for c in DROP_COLUMNS if c in df.columns]
    if to_drop:
        before = df.shape[1]
        df = df.drop(columns=to_drop)
        logger.info(f"[001] 丢弃长文本列 {to_drop}：{before} 列 -> {df.shape[1]} 列")
    else:
        logger.info("[001] 无需要丢弃的列")

    # ---------------- [002] 去重
    if DEDUP_KEY not in df.columns:
        logger.error(f"[002] 去重键 {DEDUP_KEY} 不存在，终止")
        return 1
    # 排序键支持单字段或多字段：先 createTime，再 create_at 作为二级键兜底
    sort_keys = DEDUP_ORDER_BY if isinstance(DEDUP_ORDER_BY, (list, tuple)) else [DEDUP_ORDER_BY]
    ts_cols = []
    for idx, key in enumerate(sort_keys, start=1):
        col = f"_ts{idx}"
        df[col] = pd.to_datetime(df[key], errors="coerce")
        ts_cols.append(col)
    bad_ts = int(df[ts_cols].isna().any(axis=1).sum())
    df = df.sort_values(
        [DEDUP_KEY] + ts_cols,
        ascending=[True] + [False] * len(ts_cols),
    )
    df = df.drop_duplicates(subset=[DEDUP_KEY], keep="first").drop(columns=ts_cols)
    df = df.reset_index(drop=True)
    n_dedup = len(df)
    removed = n_in - n_dedup
    keys_desc = " + ".join(sort_keys)
    logger.info(
        f"[002] 按 {DEDUP_KEY} 去重（保留 {keys_desc} 最新）："
        f"{n_in:,} -> {n_dedup:,} 行，剔除 {removed:,} 行 ({removed / n_in * 100:.2f}%)"
    )
    logger.info(f"      同上岗位被多次抓取的重复记录；排序键解析失败 {bad_ts} 行")

    # ---------------- [003] 脏值归一
    alias_hits = 0
    for col, mapping in VALUE_ALIAS.items():
        if col not in df.columns:
            logger.warning(f"[003] 别名规则里的列 {col} 不存在，跳过")
            continue
        hit_mask = df[col].isin(list(mapping.keys()))
        hits = int(hit_mask.sum())
        alias_hits += hits
        if hits:
            sample = df.loc[hit_mask, col].value_counts().to_dict()
            df[col] = df[col].replace(mapping)
            logger.info(f"[003] {col} 脏值归一 {hits} 行：{sample}")
        else:
            logger.info(f"[003] {col} 无脏值命中")
    logger.info(f"[003] 脏值归一合计 {alias_hits} 行")

    # ---------------- [004] 薪资解析
    parsed = df["salary"].map(parse_salary)
    df["salary_min"] = [p[0] for p in parsed]
    df["salary_max"] = [p[1] for p in parsed]

    fail_mask = df["salary_min"].isna()
    n_fail = int(fail_mask.sum())
    if n_fail:
        bad_samples = df.loc[fail_mask, "salary"].dropna().unique()[:5].tolist()
        logger.warning(f"[004] 薪资解析失败 {n_fail} 行，样本: {bad_samples}，将剔除")

    range_mask = (
        df["salary_min"].notna()
        & ((df["salary_min"] < SALARY_MIN_VALID) | (df["salary_max"] > SALARY_MAX_VALID))
    )
    n_range = int(range_mask.sum())
    if n_range:
        logger.warning(f"[004] 薪资超出合法区间 [{SALARY_MIN_VALID}, {SALARY_MAX_VALID}] 的 {n_range} 行，将剔除")

    order_mask = df["salary_min"].notna() & (df["salary_min"] > df["salary_max"])
    n_order = int(order_mask.sum())
    if n_order:
        logger.warning(f"[004] 薪资本身下限大于上限的 {n_order} 行，将剔除")

    drop_mask = fail_mask | range_mask | order_mask
    df = df.loc[~drop_mask].reset_index(drop=True)
    logger.info(
        f"[004] 薪资解析成功 {n_dedup - n_fail:,} / 失败剔除 {n_fail:,}，"
        f"区间剔除 {n_range:,}，倒挂剔除 {n_order:,}，剩余 {len(df):,} 行"
    )

    # ---------------- [005] 经验 / 学历编码
    df["work_year_level"] = df["workYear"].map(WORK_YEAR_LEVEL)
    df["education_level"] = df["education"].map(EDUCATION_LEVEL)
    bad_wy = int(df["work_year_level"].isna().sum())
    bad_edu = int(df["education_level"].isna().sum())
    if bad_wy:
        unknown = df.loc[df["work_year_level"].isna(), "workYear"].value_counts().head(5).to_dict()
        logger.warning(f"[005] workYear 未映射 {bad_wy} 行，取值: {unknown}")
    if bad_edu:
        unknown = df.loc[df["education_level"].isna(), "education"].value_counts().head(5).to_dict()
        logger.warning(f"[005] education 未映射 {bad_edu} 行，取值: {unknown}")
    logger.info(
        f"[005] 经验编码未映射 {bad_wy} 行，学历编码未映射 {bad_edu} 行"
    )

    # ---------------- 输出
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    dst = CLEAN_DIR / f"clean_{batch}.csv"
    df.to_csv(dst, index=False, encoding=ENCODING, lineterminator="\n")

    logger.info(f"[999] 清洗完成：{n_in:,} -> {len(df):,} 行，{cols_in} -> {df.shape[1]} 列")
    logger.info(f"      输出: {dst}")
    logger.info(f"      大小: {dst.stat().st_size:,} bytes（原始层 {src.stat().st_size:,} bytes）")
    logger.info(f"      输出 MD5: {md5_of(dst)}")
    logger.info("02_clean 结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
