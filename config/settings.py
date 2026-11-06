# -*- coding: utf-8 -*-
"""
config/settings.py —— 项目路径与数据库连接配置

为什么密码不直接写在这里：
    本文件随代码提交到仓库，而密码属于环境配置，
    写进代码就会随仓库一起公开。
    密码因此放在项目根目录的 .env，.env 已在 .gitignore 中被忽略。

优先级：系统环境变量 > .env 文件 > 下面的默认值
"""

import os
from pathlib import Path

# 项目根目录：config/ 的上一级（用 __file__ 定位，从任何目录运行都正确）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_CLEAN = PROJECT_ROOT / "data" / "clean"
LOG_DIR = PROJECT_ROOT / "logs"
SQL_DIR = PROJECT_ROOT / "sql"

_ENV_FILE = PROJECT_ROOT / ".env"


def _parse_env(path: Path) -> dict:
    """极简 .env 解析：KEY=VALUE，忽略空行与 # 注释。不引入额外依赖。"""
    conf = {}
    if not path.exists():
        return conf
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        conf[key.strip()] = value.strip()
    return conf


_env = _parse_env(_ENV_FILE)


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key) or _env.get(key) or default


DB_HOST = _get("DB_HOST", "127.0.0.1")
DB_PORT = int(_get("DB_PORT", "3306"))
DB_USER = _get("DB_USER", "root")
DB_PASSWORD = _get("DB_PASSWORD", "")
DB_NAME = _get("DB_NAME", "recruit")


def db_config(with_db: bool = True) -> dict:
    """pymysql.connect 的参数。with_db=False 用于建库前连接。"""
    cfg = {
        "host": DB_HOST,
        "port": DB_PORT,
        "user": DB_USER,
        "password": DB_PASSWORD,
        "charset": "utf8mb4",
    }
    if with_db:
        cfg["database"] = DB_NAME
    return cfg
