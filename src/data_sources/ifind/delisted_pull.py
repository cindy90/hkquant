"""
iFinD 港股退市信息拉取 (反幸存者偏差)

数据源: THS_DR('p02565') — 港股退市股票一览
输出:
    data/raw/ifind/ifind_delisted_hk_raw.csv   (原始字段, 供调试)
    data/raw/ifind/ifind_delisted_hk.csv       (语义化列名, 下游消费)

列: stock_code, company_name, delisting_date, delisting_reason, is_acquired

下游消费: load_to_db.load_delisted() -> ipo_master.is_delisted/delisting_date/is_acquired

注: 必须在已注册 iFinD 客户端的环境中运行 (CI/远程不可)
     原因: iFindPy 仅通过本地 .pth 注册, 无 PyPI 分发

典型用法:
    python src/data_sources/ifind/delisted_pull.py

跑完后用 ETL loader 灌库:
    python -m src.data_sources.ifind.load_to_db --tables delisted
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 让 src/ 在 sys.path 中以便 import log 模块
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from log import get_logger, setup_cli_logging

_log = get_logger("ifind.delisted_pull")

# Windows GBK -> UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# =============================================================================
# 配置 (与 full_data_pull.py 保持一致)
# =============================================================================

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_DIR = Path(os.environ.get(
    "IFIND_OUTPUT_DIR",
    str(PROJECT_ROOT / "data" / "raw" / "ifind"),
))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_PATH_RAW = OUTPUT_DIR / "ifind_delisted_hk_raw.csv"
OUTPUT_PATH = OUTPUT_DIR / "ifind_delisted_hk.csv"
DB_PATH = PROJECT_ROOT / "data" / "nacs_real.db"


def _load_env(env_path: Path) -> None:
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


# =============================================================================
# 退市原因 -> is_acquired 推断
# =============================================================================
# 基于 iFinD p02565 报表的"退市原因"字段常见值
# 包含以下关键词视为被收购/私有化 (is_acquired=1)
_ACQUIRED_KEYWORDS = (
    "私有化", "要约收购", "收购", "合并", "吸收合并",
    "被收购", "协议安排", "换股", "全面收购",
)


def _infer_is_acquired(reason: str | None) -> int:
    """从退市原因推断是否为被收购退市."""
    if not reason:
        return 0
    for kw in _ACQUIRED_KEYWORDS:
        if kw in reason:
            return 1
    return 0


# =============================================================================
# 字段映射: p02565 原始字段 -> 语义列名
# =============================================================================
# 注: 字段含义需根据首次运行返回的数据确认, 此处为合理推测
# 运行后如发现偏差, 需更新映射

from data_sources.ifind.field_mappings import P02565_DELISTED


def _map_columns(df) -> dict:
    """将 p02565 DataFrame 的列映射到语义名称, 返回列名映射 dict."""
    mapping = {}
    for raw_col, sem_col in P02565_DELISTED.items():
        if raw_col in df.columns:
            mapping[raw_col] = sem_col
    return mapping


# =============================================================================
# 交叉比对: 退市股 vs ipo_master
# =============================================================================

def _cross_check_with_db(df) -> None:
    """打印退市股与 ipo_master 的交叉比对摘要."""
    import sqlite3

    if not DB_PATH.exists():
        _log.warning("DB 不存在 (%s), 跳过交叉比对", DB_PATH)
        return

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        ipo_codes = {
            r["stock_code"]
            for r in conn.execute("SELECT stock_code FROM ipo_master").fetchall()
        }
        delisted_codes = set(df["stock_code"].dropna().unique())

        matched = delisted_codes & ipo_codes
        unmatched = delisted_codes - ipo_codes

        _log.info("=" * 50)
        _log.info("交叉比对摘要:")
        _log.info("  退市总数: %d", len(delisted_codes))
        _log.info("  ipo_master 总数: %d", len(ipo_codes))
        _log.info("  匹配 (退市且在 universe): %d", len(matched))
        _log.info("  不匹配 (退市但不在 universe): %d", len(unmatched))
        if matched:
            _log.info("  匹配的退市股: %s", sorted(matched))
    finally:
        conn.close()


# =============================================================================
# 主流程
# =============================================================================

def main() -> int:
    setup_cli_logging("INFO")
    _load_env(Path(__file__).parent / ".env")

    username = os.environ.get("IFIND_USERNAME", "")
    password = os.environ.get("IFIND_PASSWORD", "")
    if not (username and password):
        _log.error("未读到 IFIND_USERNAME / IFIND_PASSWORD")
        return 1

    try:
        from iFinDPy import (  # type: ignore[import-not-found]
            THS_iFinDLogin, THS_iFinDLogout, THS_DR,
        )
    except ImportError as e:
        _log.error("iFindPy 不可用 (%s)", e)
        _log.error("本地 iFinD 客户端必须先注册 (写 .pth 到 site-packages), "
                    "见 src/data_sources/ifind/README.md")
        return 1

    import pandas as pd

    code = THS_iFinDLogin(username, password)
    if code not in (0, -201):
        _log.error("登录失败: %s", code)
        return 1
    _log.info("iFinD 登录成功")

    # -------------------------------------------------------------------
    # 拉取 p02565: 港股退市股票一览
    # -------------------------------------------------------------------
    _log.info("拉取 THS_DR('p02565') — 港股退市股票一览")
    result = THS_DR(
        'p02565', '',
        'jydm:Y,jydm_mc:Y,p02565_f001:Y,p02565_f002:Y,p02565_f003:Y,'
        'p02565_f004:Y,p02565_f009:Y,p02565_f010:Y,p02565_f011:Y,'
        'p02565_f005:Y,p02565_f006:Y,p02565_f007:Y,p02565_f008:Y',
        'format:dataframe'
    )

    errorcode = getattr(result, 'errorcode', -1)
    if errorcode != 0:
        errmsg = getattr(result, 'errmsg', str(result))
        _log.error("THS_DR p02565 失败: errorcode=%s, errmsg=%s", errorcode, errmsg)
        THS_iFinDLogout()
        return 1

    df_raw = result.data
    if df_raw is None or len(df_raw) == 0:
        _log.error("p02565 返回空数据")
        THS_iFinDLogout()
        return 1

    _log.info("p02565 返回 %d 条退市记录", len(df_raw))
    _log.info("原始列名: %s", df_raw.columns.tolist())
    _log.info("前 5 行预览:")
    _log.info("\n%s", df_raw.head().to_string())

    # 保存原始 CSV (供调试分析)
    df_raw.to_csv(str(OUTPUT_PATH_RAW), index=False, encoding='utf-8-sig')
    _log.info("已保存原始 CSV: %s", OUTPUT_PATH_RAW)

    # -------------------------------------------------------------------
    # 映射列名 -> 语义化 CSV
    # -------------------------------------------------------------------
    col_mapping = _map_columns(df_raw)
    _log.info("列映射: %s", col_mapping)

    # 只保留有映射的列
    mapped_cols = {raw: sem for raw, sem in col_mapping.items() if raw in df_raw.columns}
    if not mapped_cols:
        _log.error("无法映射任何列, 请检查 P02565_DELISTED 映射是否与实际列名匹配")
        _log.info("可用列: %s", df_raw.columns.tolist())
        # 仍输出空 CSV 让下游不报错
        pd.DataFrame(columns=["stock_code", "delisting_date", "delisting_reason", "is_acquired"]).to_csv(
            str(OUTPUT_PATH), index=False, encoding='utf-8-sig'
        )
        THS_iFinDLogout()
        return 1

    df_mapped = df_raw[list(mapped_cols.keys())].rename(columns=mapped_cols)

    # 推断 is_acquired (从 delisting_reason)
    if "delisting_reason" in df_mapped.columns:
        df_mapped["is_acquired"] = df_mapped["delisting_reason"].apply(_infer_is_acquired)
    elif "is_acquired" not in df_mapped.columns:
        df_mapped["is_acquired"] = 0

    # 确保必要列存在
    for col in ("stock_code", "delisting_date", "delisting_reason", "is_acquired"):
        if col not in df_mapped.columns:
            df_mapped[col] = None if col != "is_acquired" else 0

    # 输出语义化 CSV (下游 load_to_db 消费)
    output_cols = ["stock_code", "company_name", "delisting_date", "delisting_reason", "is_acquired"]
    # company_name 可能不存在, 过滤
    output_cols = [c for c in output_cols if c in df_mapped.columns]
    df_mapped[output_cols].to_csv(str(OUTPUT_PATH), index=False, encoding='utf-8-sig')
    _log.info("已保存语义化 CSV: %s (%d 行)", OUTPUT_PATH, len(df_mapped))

    # -------------------------------------------------------------------
    # 交叉比对
    # -------------------------------------------------------------------
    _cross_check_with_db(df_mapped)

    # -------------------------------------------------------------------
    # 退出
    # -------------------------------------------------------------------
    THS_iFinDLogout()
    _log.info("退市数据拉取完成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
