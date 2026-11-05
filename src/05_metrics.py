# -*- coding: utf-8 -*-
"""
05_metrics.py —— 执行 sql/02_metrics.sql 里的指标查询，把结果打成表

为什么指标放在 .sql 而不是 .py：
    指标口径是业务资产，不属于代码。放 sql 文件里，改口径不用动脚本，
    README 里也能直接把 SQL 贴出来给人看。

用法：
    python src/05_metrics.py
    python src/05_metrics.py sql/02_metrics.sql
"""

import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pymysql

from config.settings import DB_PASSWORD, PROJECT_ROOT, SQL_DIR, db_config

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def disp_width(s: str) -> int:
    """中文字符占两格，算显示宽度，否则表格会歪。"""
    width = 0
    for ch in str(s):
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def pad(s, width, align="left"):
    s = str(s)
    space = " " * max(0, width - disp_width(s))
    return space + s if align == "right" else s + space


def print_table(headers, rows):
    if not rows:
        print("  （无数据）")
        return
    cols = list(headers)
    cells = [[("" if v is None else v) for v in row] for row in rows]
    widths = []
    for i, h in enumerate(cols):
        w = max([disp_width(h)] + [disp_width(r[i]) for r in cells])
        widths.append(w)

    print("  " + " | ".join(pad(h, widths[i]) for i, h in enumerate(cols)))
    print("  " + "-+-".join("-" * w for w in widths))
    for row in cells:
        print("  " + " | ".join(pad(row[i], widths[i]) for i in range(len(cols))))


def split_statements(text: str):
    """按分号切 SQL，同时把每条语句前面的注释当作标题。"""
    blocks = []
    for chunk in text.split(";"):
        raw_lines = chunk.splitlines()
        comments, body_lines = [], []
        for line in raw_lines:
            stripped = line.strip()
            if stripped.startswith("--"):
                content = stripped.lstrip("-").strip()
                if content and set(content) - set("=-"):
                    comments.append(content)
            elif stripped:
                body_lines.append(line)
        body = "\n".join(body_lines).strip()
        if not body:
            continue
        # 优先用「指标X：」那行当标题，否则退回第一行有效注释
        title = next((c for c in comments if c.startswith("指标")), comments[0] if comments else "")
        blocks.append((title, body, comments))
    return blocks


def main():
    if not DB_PASSWORD:
        raise SystemExit("错误：.env 里 DB_PASSWORD 为空，请先填写")

    sql_path = Path(sys.argv[1]) if len(sys.argv) > 1 else SQL_DIR / "02_metrics.sql"
    if not sql_path.is_absolute():
        sql_path = PROJECT_ROOT / sql_path
    if not sql_path.exists():
        raise SystemExit(f"错误：找不到 {sql_path}")

    blocks = split_statements(sql_path.read_text(encoding="utf-8"))
    print(f"执行 {sql_path.name}，共 {len(blocks)} 条查询\n")

    conn = pymysql.connect(**db_config())
    try:
        with conn.cursor() as cur:
            for idx, (title, body, _) in enumerate(blocks, start=1):
                if title:
                    print(f"【{idx}】{title}")
                cur.execute(body)
                if cur.description:
                    headers = [d[0] for d in cur.description]
                    rows = cur.fetchall()
                    print_table(headers, rows)
                print()
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
