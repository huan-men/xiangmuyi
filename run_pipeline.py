"""run_pipeline.py —— 一条命令跑完整条数据管道

顺序：01_extract -> 02_clean -> 03_load -> 04_quality_check -> 05_metrics
任一步返回非 0 即中断（04 只有 FAIL 才返回 1，WARN 不阻断）。

用法：
    python run_pipeline.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_DIR = PROJECT_ROOT / "logs"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

STEPS = [
    ("01_extract", "src/01_extract.py", "原始层落地（同批次已存在则跳过写入）"),
    ("02_clean", "src/02_clean.py", "清洗 + 规则记账"),
    ("03_load", "src/03_load.py", "幂等写入 MySQL"),
    ("04_quality", "src/04_quality_check.py", "6 项数据质量校验"),
    ("05_metrics", "src/05_metrics.py", "核心指标查询"),
]


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    started = datetime.now()
    log_path = LOG_DIR / f"pipeline_{started:%Y%m%d_%H%M%S}.log"
    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"管道开始  {started:%Y-%m-%d %H:%M:%S}")
    emit(f"项目根    {PROJECT_ROOT}")
    emit(f"解释器    {sys.executable}")
    emit("")

    failed = None
    total = 0.0

    for idx, (name, rel, desc) in enumerate(STEPS, 1):
        script = PROJECT_ROOT / rel
        emit(f"[{idx}/{len(STEPS)}] {name} —— {desc}")

        if not script.exists():
            emit(f"    FAIL 找不到脚本 {rel}")
            failed = name
            break

        t0 = time.perf_counter()
        child_env = dict(os.environ)
        child_env["PYTHONIOENCODING"] = "utf-8"
        child_env["PYTHONUTF8"] = "1"
        proc = subprocess.run(
            [sys.executable, str(script)],
            cwd=str(PROJECT_ROOT),
            env=child_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        cost = time.perf_counter() - t0
        total += cost

        for line in (proc.stdout or "").splitlines():
            emit(f"    {line}")

        if proc.returncode != 0:
            for line in (proc.stderr or "").splitlines():
                emit(f"    [stderr] {line}")
            emit(f"    FAIL 退出码 {proc.returncode}，耗时 {cost:.2f}s —— 管道中断")
            failed = name
            break

        emit(f"    OK 耗时 {cost:.2f}s")
        emit("")

    if failed:
        emit(f"管道中断：{failed} 未通过，后续步骤未执行")
        emit(f"总耗时 {total:.2f}s")
        code = 1
    else:
        emit(f"管道完成：{len(STEPS)}/{len(STEPS)} 步全部成功，总耗时 {total:.2f}s")
        code = 0

    log_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n日志：{log_path}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
