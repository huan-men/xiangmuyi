# -*- coding: utf-8 -*-
"""
01_extract.py —— 原始层落地（Extract / Landing）

职责（只做这一件事）：
    把外部拿到的源文件，校验后落地为管道内的原始层快照。
    不做任何清洗、类型转换、去重、过滤。

契约：
    输入  data/raw/lagou_2024.csv      用户下载的源文件（只读）
    输出  data/raw/raw_<批次日>.csv    管道原始层（内容等价于源文件）
    日志  logs/extract_<运行日>.log

幂等：
    同一批次重复运行，检测到快照已存在且记录数一致，直接跳过。

退出码：
    0  成功（含"已存在跳过"）
    1  失败（源文件缺失 / 列名不符 / 记录数为 0）
"""

from __future__ import annotations

import hashlib
import logging
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

# ---------------------------------------------------------------- 路径定位
# 以本文件所在目录的上一级为项目根，保证在任意工作目录下运行都能定位
ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
LOG_DIR = ROOT / "logs"
SRC_FILE = RAW_DIR / "lagou_2024.csv"

# ---------------------------------------------------------------- 预期列
# 顺序敏感：源文件一旦增删列，必须第一时间失败而不是悄悄往下跑
EXPECTED_COLUMNS = [
    "id", "category", "bigcategory", "positionId", "positionName",
    "companyId", "companyFullName", "companySize", "industryField",
    "financeStage", "positionType", "createTime", "city", "district",
    "salary", "workYear", "jobNature", "education", "positionDetail",
    "positionAdvantage", "create_at",
]

BATCH_COLUMN = "create_at"  # 采集时间，用于推断批次
ENCODING = "utf-8"


def setup_logger(log_path: Path) -> logging.Logger:
    """日志同时写文件和打印到控制台。"""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("extract")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s", "%Y-%m-%d %H:%M:%S"
    )
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def md5_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """分块计算 MD5，避免一次性把大文件读进内存。"""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk_size), b""):
            h.update(block)
    return h.hexdigest()


def count_rows(path: Path) -> int:
    """只读第一列来数记录数（比整表解析快）。

    注意：不能用文件物理行数。本数据 positionDetail 含换行，
    物理行 238,919 而实际记录 21,320。
    """
    df = pd.read_csv(
        path, encoding=ENCODING, dtype=str, keep_default_na=False,
        na_values=[""], usecols=[0],
    )
    return len(df)


def infer_batch(df: pd.DataFrame) -> str:
    """从采集时间推断批次日期；取不到唯一日期就退回今天。"""
    if BATCH_COLUMN in df.columns:
        days = (
            pd.to_datetime(df[BATCH_COLUMN], errors="coerce")
            .dt.date.dropna().unique()
        )
        if len(days) == 1:
            return pd.Timestamp(days[0]).strftime("%Y%m%d")
    return datetime.now().strftime("%Y%m%d")


def main() -> int:
    if not SRC_FILE.exists():
        print(f"[FAIL] 源文件不存在: {SRC_FILE}")
        print("       请先把拉勾招聘.csv 放到 data/raw/lagou_2024.csv")
        return 1

    log_path = LOG_DIR / f"extract_{datetime.now():%Y%m%d}.log"
    logger = setup_logger(log_path)

    logger.info("=" * 64)
    logger.info("01_extract 开始")
    logger.info(f"项目根 : {ROOT}")
    logger.info(f"源文件 : {SRC_FILE}")

    src_size = SRC_FILE.stat().st_size
    src_md5 = md5_of(SRC_FILE)
    logger.info(f"源文件大小: {src_size:,} bytes")
    logger.info(f"源文件 MD5: {src_md5}")

    # 原始层：全部按字符串读入，禁止 pandas 做任何类型推断
    df = pd.read_csv(
        SRC_FILE, encoding=ENCODING, dtype=str,
        keep_default_na=False, na_values=[""],
    )
    logger.info(f"读取记录: {len(df):,} 行 x {df.shape[1]} 列")

    # 列名校验（顺序敏感）
    if list(df.columns) != EXPECTED_COLUMNS:
        logger.error("列名校验失败，终止")
        logger.error(f"  期望 ({len(EXPECTED_COLUMNS)} 列): {EXPECTED_COLUMNS}")
        logger.error(f"  实际 ({df.shape[1]} 列): {list(df.columns)}")
        return 1
    logger.info(f"列名校验通过（{len(EXPECTED_COLUMNS)} 列，顺序一致）")

    if len(df) == 0:
        logger.error("记录数为 0，终止")
        return 1

    batch = infer_batch(df)
    dst = RAW_DIR / f"raw_{batch}.csv"

    # 幂等：同批次快照已存在且记录数一致，则跳过
    if dst.exists():
        try:
            old_rows = count_rows(dst)
            if old_rows == len(df):
                logger.info(f"原始层快照已存在且一致（{old_rows:,} 行），跳过写入")
                logger.info(f"快照路径: {dst}")
                logger.info(f"批次    : {batch}")
                logger.info("01_extract 结束（跳过）")
                return 0
            logger.warning(
                f"同批次快照行数不一致（已存在 {old_rows:,} / 本次 {len(df):,}），覆盖"
            )
        except Exception as e:  # 快照损坏时直接覆盖
            logger.warning(f"旧快照读取失败（{type(e).__name__}），覆盖: {e}")

    df.to_csv(dst, index=False, encoding=ENCODING, lineterminator="\n")

    dst_md5 = md5_of(dst)
    logger.info(f"原始层写入: {dst}")
    logger.info(f"批次      : {batch}")
    logger.info(f"输出记录  : {len(df):,}")
    logger.info(f"输出 MD5  : {dst_md5}")
    if dst_md5 != src_md5:
        logger.info("输出 MD5 与源文件不同属于正常：pandas 重写会改变引号与转义策略")
    logger.info("01_extract 结束")
    return 0


if __name__ == "__main__":
    sys.exit(main())
