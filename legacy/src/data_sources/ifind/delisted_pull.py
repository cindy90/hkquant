"""
iFinD 港股退市/停牌信息拉取 (反幸存者偏差)

输出: data/raw/ifind/ifind_delisted_hk.csv
列:    stock_code, delisting_date, delisting_reason, is_acquired

下游消费: load_to_db.load_delisted() → ipo_master.is_delisted/delisting_date/is_acquired

⚠ 注: 必须在已注册 iFinD 客户端的环境中运行 (CI/远程不可)
       原因: iFindPy 仅通过本地 .pth 注册, 无 PyPI 分发

典型用法 (你本地):
    cd src/data_sources/ifind
    python delisted_pull.py

跑完后用 ETL loader 灌库:
    python -m src.data_sources.ifind.load_to_db --tables delisted
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Windows GBK → UTF-8
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
OUTPUT_PATH = OUTPUT_DIR / "ifind_delisted_hk.csv"


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
# 主流程 (骨架; 实际 iFinD 字段需按客户端「数据浏览器」核对)
# =============================================================================

def main() -> int:
    _load_env(Path(__file__).parent / ".env")

    username = os.environ.get("IFIND_USERNAME", "")
    password = os.environ.get("IFIND_PASSWORD", "")
    if not (username and password):
        print("❌ 未读到 IFIND_USERNAME / IFIND_PASSWORD")
        return 1

    try:
        from iFinDPy import (  # type: ignore[import-not-found]
            THS_iFinDLogin, THS_iFinDLogout, THS_DataPool,
        )
    except ImportError as e:
        print(f"❌ iFindPy 不可用 ({e})")
        print("   本地 iFinD 客户端必须先注册 (写 .pth 到 site-packages),")
        print("   见 src/data_sources/ifind/README.md")
        return 1

    code = THS_iFinDLogin(username, password)
    if code not in (0, -201):
        print(f"❌ 登录失败: {code}")
        return 1
    print("✓ iFinD 登录成功")

    # -------------------------------------------------------------------
    # TODO (人工核对): iFinD 港股退市板块的 dataPool name
    # 候选:
    #   - "退市股票", "退市港股"
    #   - "stock_delisted_hk"
    # 字段:
    #   - thscode, security_name, delisting_date, delisting_reason
    # 实际名称需在 iFinD 客户端「数据浏览器」搜索"退市"确认
    # -------------------------------------------------------------------
    BLOCK_NAME = "退市港股"  # ← 占位, 跑前必须人工核对

    print(f"  拉取 dataPool: {BLOCK_NAME}")
    try:
        result = THS_DataPool(
            "block",
            f";{BLOCK_NAME}",
            "thscode:Y,security_name:Y",
        )
    except Exception as e:
        print(f"❌ THS_DataPool 调用失败: {e}")
        THS_iFinDLogout()
        return 1

    # iFinD 返回结构因接口而异; 解析逻辑随 BLOCK_NAME 调整
    print(f"  返回类型: {type(result).__name__}")
    print(f"  返回内容预览: {str(result)[:300]}")

    # TODO: 解析为标准 CSV 列 [stock_code, delisting_date, delisting_reason, is_acquired]
    # 占位: 输出空 CSV 让下游 ETL 不报错
    OUTPUT_PATH.write_text(
        "stock_code,delisting_date,delisting_reason,is_acquired\n",
        encoding="utf-8",
    )
    print(f"  ⚠ 输出占位空 CSV: {OUTPUT_PATH}")
    print(f"  实际拉数需要在 iFinD 客户端核对 BLOCK_NAME 与字段后改本脚本")

    THS_iFinDLogout()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
