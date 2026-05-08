"""
fetch_hk_market_data.py — 每日港股市场数据抓取 (iFinD)

输出: daily/{YYYY-MM-DD}/
    market_data.json   — HSI / 波动率 / 南向 / AH 溢价 + regime_score
    news_today.json    — 当日港股新闻 (100-200 篇)
    themes.json        — watchlist 主题板块表现
    ipo_recent.json    — 过去 30 天港股新上市
    errors.log         — 失败字段日志
    run_summary.json   — 成功/失败字段清单

用法:
    python scripts/fetch_hk_market_data.py
    python scripts/fetch_hk_market_data.py --dry-run
    python scripts/fetch_hk_market_data.py --date 2026-05-08

【骨架版本 — 多处函数名/字段未验证, 见代码内 TODO 注释】
"""
from __future__ import annotations

import sys
import os
import json
import math
import argparse
import traceback
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Any, Optional

# Windows 控制台 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ============================================================================
# 路径 & 项目根
# ============================================================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

# ============================================================================
# .env 加载 (复用 full_data_pull.py 的逻辑, 路径 = src/data_sources/ifind/.env)
# ============================================================================
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

_ENV_PATH = PROJECT_ROOT / "src" / "data_sources" / "ifind" / ".env"
_load_env(_ENV_PATH)


# ============================================================================
# 错误收集器
# ============================================================================
class RunRecorder:
    """记录每个字段的成功/失败, 最终输出 run_summary.json + errors.log"""
    def __init__(self, out_dir: Path, dry_run: bool):
        self.out_dir = out_dir
        self.dry_run = dry_run
        self.results: dict[str, dict[str, Any]] = {}
        self.errors_log: list[str] = []

    def ok(self, field: str, detail: str = "") -> None:
        self.results[field] = {"status": "ok", "detail": detail}
        print(f"  ✓ {field}  {detail}")

    def skip(self, field: str, reason: str) -> None:
        self.results[field] = {"status": "skipped", "reason": reason}
        print(f"  ~ {field}  SKIP: {reason}")

    def fail(self, field: str, err: str) -> None:
        self.results[field] = {"status": "fail", "error": err}
        line = f"[{datetime.now().isoformat()}] {field}: {err}"
        self.errors_log.append(line)
        print(f"  ✗ {field}  {err}")

    def write(self) -> None:
        if self.dry_run:
            print("\n[dry-run] 不写 run_summary.json / errors.log")
            print(json.dumps(self.results, ensure_ascii=False, indent=2))
            return
        self.out_dir.mkdir(parents=True, exist_ok=True)
        (self.out_dir / "run_summary.json").write_text(
            json.dumps({
                "generated_at": datetime.now().isoformat(),
                "fields": self.results,
                "n_ok": sum(1 for r in self.results.values() if r["status"] == "ok"),
                "n_skipped": sum(1 for r in self.results.values() if r["status"] == "skipped"),
                "n_fail": sum(1 for r in self.results.values() if r["status"] == "fail"),
            }, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )
        if self.errors_log:
            (self.out_dir / "errors.log").write_text(
                "\n".join(self.errors_log), encoding="utf-8"
            )


# ============================================================================
# 工具: 写 JSON (尊重 dry-run)
# ============================================================================
# ============================================================================
# iFinD 返回值通用解包 — 兼容 OrderedDict 和带属性的对象
# ============================================================================
_DEBUG_PRINTED: set[str] = set()

def _hq_unpack(result: Any, debug_tag: str = "") -> "object":
    """
    解包 THS_HistoryQuotes 的返回. 已知形态:
      A) OrderedDict: {'errorcode':0, 'errmsg':'Ok', 'tables':[{'thscode':..., 'time':[...], 'table':{'close':[...]}}]}
      B) 对象: result.errorcode, result.data (DataFrame)

    返回一个简单对象, 带 .errorcode / .errmsg / .closes (list[float]) / .opens / .times
    第一次调用某 tag 时会把原始 keys 打到 stdout 方便调试.
    """
    import pandas as pd

    class _Out:
        errorcode = -1
        errmsg = "unknown"
        closes: list[float] = []
        opens: list[float] = []
        times: list[Any] = []
        raw = None

    out = _Out()
    out.raw = result

    # 一次性打印结构探针
    if debug_tag and debug_tag not in _DEBUG_PRINTED:
        _DEBUG_PRINTED.add(debug_tag)
        if hasattr(result, "__dict__") or hasattr(result, "errorcode"):
            print(f"  [debug:{debug_tag}] type={type(result).__name__} "
                  f"attrs={[a for a in dir(result) if not a.startswith('_')][:8]}")
        else:
            try:
                print(f"  [debug:{debug_tag}] type={type(result).__name__} "
                      f"keys={list(result.keys())}")
                if "tables" in result and result["tables"]:
                    t0 = result["tables"][0]
                    print(f"  [debug:{debug_tag}] tables[0].keys={list(t0.keys())}")
                    if "table" in t0 and isinstance(t0["table"], dict):
                        print(f"  [debug:{debug_tag}] table.keys={list(t0['table'].keys())}")
            except Exception as e:
                print(f"  [debug:{debug_tag}] introspect failed: {e}")

    # 形态 B: 对象
    if hasattr(result, "errorcode"):
        out.errorcode = int(result.errorcode)
        out.errmsg = str(getattr(result, "errmsg", ""))
        df = getattr(result, "data", None)
        if df is not None and hasattr(df, "columns"):
            if "close" in df.columns:
                out.closes = [float(x) for x in df["close"].dropna().tolist()]
            if "open" in df.columns:
                out.opens = [float(x) for x in df["open"].dropna().tolist()]
            time_col = next((c for c in df.columns if c.lower() in ("time", "date", "thsdate")), None)
            if time_col:
                out.times = df[time_col].tolist()
        return out

    # 形态 A: OrderedDict
    if isinstance(result, dict):
        out.errorcode = int(result.get("errorcode", -1))
        out.errmsg = str(result.get("errmsg", ""))
        tables = result.get("tables") or []
        if tables:
            t0 = tables[0]
            tbl = t0.get("table", {}) if isinstance(t0, dict) else {}
            if isinstance(tbl, dict):
                if "close" in tbl:
                    out.closes = [float(x) for x in tbl["close"] if x is not None]
                if "open" in tbl:
                    out.opens = [float(x) for x in tbl["open"] if x is not None]
            if isinstance(t0, dict) and "time" in t0:
                out.times = list(t0["time"])
        return out

    out.errmsg = f"unknown return type: {type(result).__name__}"
    return out


def _hq_unpack_batch(result: Any, debug_tag: str = "") -> dict[str, dict[str, list]]:
    """
    解多 code 的 THS_HistoryQuotes 返回. 已确认形态:
        OrderedDict {
          'errorcode': 0, 'errmsg': '',
          'tables': [
            {'thscode': '0085.HK', 'time': [...], 'table': {'close': [...]}},
            {'thscode': '0522.HK', 'time': [...], 'table': {'close': [...]}},
            ...
          ]
        }
    返回 {ths_code: {"times": [...], "closes": [...]}}, errorcode != 0 时返回 {}.
    """
    out: dict[str, dict[str, list]] = {}

    # 形态 A: OrderedDict / dict
    if isinstance(result, dict):
        if int(result.get("errorcode", -1)) != 0:
            return out
        for t in result.get("tables") or []:
            if not isinstance(t, dict):
                continue
            code = str(t.get("thscode", "")).strip()
            if not code:
                continue
            tbl = t.get("table") if isinstance(t.get("table"), dict) else {}
            closes_raw = tbl.get("close") or []
            closes = [float(x) for x in closes_raw if x is not None]
            times = [str(x) for x in (t.get("time") or [])]
            if closes:
                out[code] = {"times": times, "closes": closes}
        return out

    # 形态 B: 对象 + DataFrame (按 thscode 分组)
    if hasattr(result, "errorcode"):
        if int(result.errorcode) != 0:
            return out
        df = getattr(result, "data", None)
        if df is None or not hasattr(df, "columns"):
            return out
        if "thscode" in df.columns and "close" in df.columns:
            time_col = next((c for c in df.columns if c.lower() in ("time", "date", "thsdate")), None)
            for code, sub in df.groupby("thscode"):
                closes = [float(x) for x in sub["close"].dropna().tolist()]
                times = [str(x) for x in sub[time_col].tolist()] if time_col else [str(i) for i in range(len(closes))]
                if closes:
                    out[str(code)] = {"times": times, "closes": closes}
        return out

    return out


# ----------------------------------------------------------------------------
# Daily 历史聚合工具 (扫 daily/{YYYY-MM-DD}/market_data.json 累加跨日字段)
# ----------------------------------------------------------------------------
def _scan_daily_market_history(end: date, look_back_days: int) -> list[tuple[date, dict]]:
    """
    读 daily/{YYYY-MM-DD}/market_data.json, 返回 [(date, parsed_json), ...] 按日升序.
    end 含, 起点 = end - look_back_days.
    """
    daily_root = PROJECT_ROOT / "daily"
    if not daily_root.exists():
        return []
    out: list[tuple[date, dict]] = []
    cutoff = end - timedelta(days=look_back_days)
    for sub in sorted(daily_root.iterdir()):
        if not sub.is_dir():
            continue
        try:
            d = datetime.strptime(sub.name, "%Y-%m-%d").date()
        except Exception:
            continue
        if not (cutoff <= d <= end):
            continue
        f = sub / "market_data.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            out.append((d, data))
        except Exception:
            continue
    return out


def _aggregate_southbound_30d(today: date, today_total_hkd: float) -> dict:
    """
    扫 daily/ 历史的 southbound_today.total_net_inflow_hkd, 加今日值, 累计 30 个交易日.
    daily/ 目录按交易日生成 (周末/节假日不跑), 所以最近 30 个 daily 文件夹 ≈ 30 个交易日.
    """
    history = _scan_daily_market_history(today - timedelta(days=1), look_back_days=60)
    series: list[dict] = []
    for d, data in history:
        v = (data.get("southbound_today") or {}).get("total_net_inflow_hkd")
        if v is not None:
            series.append({"date": d.isoformat(), "value_hkd": float(v)})
    series.append({"date": today.isoformat(), "value_hkd": float(today_total_hkd)})
    series.sort(key=lambda r: r["date"])
    last_30 = series[-30:]
    total = sum(r["value_hkd"] for r in last_30)
    return {
        "as_of": today.isoformat(),
        "n_days_actual": len(last_30),
        "n_days_target": 30,
        "earliest": last_30[0]["date"] if last_30 else None,
        "latest": last_30[-1]["date"] if last_30 else None,
        "cumulative_net_inflow_hkd": total,
        "daily_avg_hkd": total / len(last_30) if last_30 else 0.0,
        "note": f"按 daily/ 累加 {len(last_30)} 个交易日 (期望 30); 不足 30 时即用实际值",
    }


def _aggregate_ah_premium_60d(today: date, today_wavg_pct: float) -> dict:
    """
    扫 daily/ 历史的 ah_premium_today.weighted_avg_premium_pct, 加今日值, 60 日序列 + 百分位.
    """
    history = _scan_daily_market_history(today - timedelta(days=1), look_back_days=120)
    series: list[dict] = []
    for d, data in history:
        v = (data.get("ah_premium_today") or {}).get("weighted_avg_premium_pct")
        if v is not None:
            series.append({"date": d.isoformat(), "premium_pct": float(v)})
    series.append({"date": today.isoformat(), "premium_pct": float(today_wavg_pct)})
    series.sort(key=lambda r: r["date"])
    last_60 = series[-60:]
    if not last_60:
        return {}
    cur = last_60[-1]["premium_pct"]
    vals = [r["premium_pct"] for r in last_60]
    avg = sum(vals) / len(vals)
    pct_rank = sum(1 for v in vals if v <= cur) / len(vals)
    return {
        "as_of": today.isoformat(),
        "n_days_actual": len(last_60),
        "n_days_target": 60,
        "current_pct": cur,
        "60d_avg_pct": avg,
        "60d_min_pct": min(vals),
        "60d_max_pct": max(vals),
        "60d_pct_rank": pct_rank,
        "series": last_60,
        "convention": "A_over_H (positive = A 比 H 贵)",
        "note": f"按 daily/ 累加 {len(last_60)} 个交易日 (期望 60)",
    }


def _detect_ipo_cache_missing(today: date) -> dict:
    """
    只检测 ipo_d30_returns.csv 是否缺失 (不调 subprocess, 不影响 iFinD 登录态).
    供 fetch_market_data 在抓数过程中调用; 实际刷新在 main() 末尾 (Logout 之后).
    """
    src_csv = PROJECT_ROOT / "data" / "raw" / "ifind" / "ifind_ipo_info.csv"
    cache_csv = PROJECT_ROOT / "data" / "derived" / "ipo_d30_returns.csv"

    if not src_csv.exists():
        return {"status": "skip", "reason": f"源 CSV 不存在: {src_csv}", "n_missing": 0}

    import pandas as pd
    df_src = pd.read_csv(src_csv, encoding="utf-8-sig")
    cache_codes: set[str] = set()
    if cache_csv.exists():
        try:
            df_cache = pd.read_csv(cache_csv, encoding="utf-8-sig")
            cache_codes = set(df_cache["thscode"].astype(str).tolist())
        except Exception:
            pass

    code_col = next((c for c in df_src.columns if str(c).endswith("f001")), None)
    date_col = next((c for c in df_src.columns if str(c).endswith("f003")), None)
    if not code_col or not date_col:
        return {"status": "skip", "reason": f"源 CSV 列名异常: {list(df_src.columns)[:6]}", "n_missing": 0}

    threshold = today - timedelta(days=35)
    missing: list[str] = []
    for _, row in df_src.iterrows():
        code = str(row[code_col]).strip()
        if not code.endswith(".HK") or "_" in code:
            continue
        if code in cache_codes:
            continue
        ld = None
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
            try:
                ld = datetime.strptime(str(row[date_col])[:10], fmt).date()
                break
            except Exception:
                continue
        if ld and ld <= threshold:
            missing.append(code)

    if not missing:
        return {"status": "up_to_date", "n_cached": len(cache_codes), "n_missing": 0}

    return {
        "status": "needs_refresh",
        "n_cached": len(cache_codes),
        "n_missing": len(missing),
        "missing_sample": missing[:10],
        "note": "实际刷新会在 daily 主流程末尾 (主进程 Logout 后) 调子进程跑 build_ipo_returns_cache.py",
    }


def _refresh_ipo_cache_subprocess() -> dict:
    """
    调 subprocess 跑 build_ipo_returns_cache.py 增量更新.
    必须在主进程 THS_iFinDLogout 之后调, 否则子进程的 logout 会注销主进程同账号 session.
    """
    import subprocess
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "build_ipo_returns_cache.py")]
    print(f"\n[post-daily] 调 build_ipo_returns_cache.py 增量更新 IPO 缓存...")
    try:
        proc = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True,
            timeout=900, text=True, encoding="utf-8", errors="replace",
        )
        ok = proc.returncode == 0
        # 打印末尾几行供观察
        tail = (proc.stdout or "")[-500:]
        for line in tail.splitlines()[-8:]:
            print(f"  | {line}")
        return {
            "status": "refreshed" if ok else "refresh_failed",
            "subprocess_returncode": proc.returncode,
            "stdout_tail": (proc.stdout or "")[-500:],
            "stderr_tail": (proc.stderr or "")[-300:],
        }
    except subprocess.TimeoutExpired:
        return {"status": "timeout"}
    except Exception as e:
        return {"status": "exc", "error": f"{type(e).__name__}: {e}"}


def write_json(path: Path, payload: Any, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would write {path.name}: "
              f"{len(json.dumps(payload, ensure_ascii=False))} chars")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8"
    )


# ============================================================================
# (a) market_data.json — HSI/vol/南向/AH + regime_score
# ============================================================================
def fetch_market_data(rec: RunRecorder, today: date, *, dry_run: bool = False) -> dict[str, Any]:
    """
    需要的字段:
      - hsi_close_60d:        HSI.HI 过去 60 个交易日收盘
      - hsi_60d_return:       (close[-1]/close[0]-1)
      - hsi_60d_vol_annual:   日收益 std * sqrt(252)
      - hsi_60d_vol_pct_rank: 当前波动率在过去 1 年(252 日)的百分位
      - southbound_30d_net:   港股通过去 30 日累计南向净买入 (亿港元)
      - hsahp_60d:            恒生 AH 溢价指数过去 60 日
      - regime_score:         调用 nacs_model.compute_regime_score
    """
    from iFinDPy import THS_HistoryQuotes  # TODO: 确认函数名 (也可能是 THS_HQ)

    out: dict[str, Any] = {"as_of": today.isoformat()}

    edate = today.strftime("%Y-%m-%d")
    sdate_60 = (today - timedelta(days=120)).strftime("%Y-%m-%d")  # 多取些给交易日过滤
    sdate_1y = (today - timedelta(days=400)).strftime("%Y-%m-%d")  # 1 年 + 缓冲

    # ---- HSI 60 日收盘 + 60 日波动率 + 1 年波动率百分位 ----
    try:
        # 探针验证: 恒生指数在 iFinD 的代码是 HSI.HK (不是 HSI.HI)
        # 第 3 参数 jsonparam 可以为空字符串; 个股/HSI 都通
        r = THS_HistoryQuotes('HSI.HK', 'close', '', sdate_1y, edate)
        u = _hq_unpack(r, debug_tag="HSI.HK")
        if u.errorcode != 0:
            raise RuntimeError(f"errorcode={u.errorcode} errmsg={u.errmsg}")
        if not u.closes:
            raise RuntimeError(f"close 数组为空 (errmsg={u.errmsg})")
        closes_full = u.closes
        closes_60 = closes_full[-60:]
        out["hsi_close_60d"] = closes_60
        out["hsi_60d_return"] = closes_60[-1] / closes_60[0] - 1

        # 60 日年化波动率
        rets_60 = [closes_60[i] / closes_60[i - 1] - 1 for i in range(1, len(closes_60))]
        mean = sum(rets_60) / len(rets_60)
        var = sum((x - mean) ** 2 for x in rets_60) / (len(rets_60) - 1)
        vol_60 = math.sqrt(var) * math.sqrt(252)
        out["hsi_60d_vol_annual"] = vol_60

        # 1 年滚动 60 日波动率百分位
        rolling_vols = []
        rets_full = [closes_full[i] / closes_full[i - 1] - 1
                     for i in range(1, len(closes_full))]
        for end in range(60, len(rets_full) + 1):
            window = rets_full[end - 60:end]
            m = sum(window) / 60
            v = sum((x - m) ** 2 for x in window) / 59
            rolling_vols.append(math.sqrt(v) * math.sqrt(252))
        # 取最近 252 个 (1 年)
        ref = rolling_vols[-252:] if len(rolling_vols) > 252 else rolling_vols
        rank = sum(1 for v in ref if v <= vol_60) / len(ref)
        out["hsi_60d_vol_pct_rank"] = rank

        rec.ok("hsi", f"60d_ret={out['hsi_60d_return']:.2%} vol={vol_60:.2%} pctrank={rank:.0%}")
    except Exception as e:
        rec.fail("hsi", f"{type(e).__name__}: {e}")

    # ---- 港股通南向资金 当日净流入 (p04275) ----
    # type=1 沪港通, type=2 深港通; f003=当日资金净流入(港元)
    # 注: 30 日累计需要每天 daily 累积或循环 30 次, 此处只取当日截面
    try:
        from iFinDPy import THS_DR
        sb_total = 0.0
        sb_breakdown = {}
        for ttype, label in [(1, "shanghai"), (2, "shenzhen")]:
            r = THS_DR(
                'p04275',
                f'type={ttype};sdate={edate};edate={edate}',
                ','.join([f'p04275_f{i:03d}:Y' for i in range(1, 13)]),
                'format:dataframe'
            )
            if getattr(r, "errorcode", -1) != 0:
                raise RuntimeError(f"type={ttype} ec={r.errorcode} msg={r.errmsg}")
            df = r.data
            if df is None or len(df) == 0:
                sb_breakdown[label] = {"net_inflow_hkd": 0.0, "n_stocks": 0}
                continue
            # f003 是文本含 "--" 等, 转 numeric 跳过非数
            import pandas as pd
            f003 = pd.to_numeric(df["p04275_f003"], errors="coerce")
            net = float(f003.sum())
            sb_breakdown[label] = {"net_inflow_hkd": net, "n_stocks": int(f003.notna().sum())}
            sb_total += net
        out["southbound_today"] = {
            "as_of": edate,
            "total_net_inflow_hkd": sb_total,
            "breakdown": sb_breakdown,
            "note": "p04275 截面数据",
        }
        rec.ok("southbound_today", f"total={sb_total/1e8:.2f} 亿港元")

        # 30 日累计 (扫 daily/ 历史 + 今日)
        try:
            sb30 = _aggregate_southbound_30d(today, sb_total)
            out["southbound_30d"] = sb30
            rec.ok("southbound_30d",
                   f"cum={sb30['cumulative_net_inflow_hkd']/1e8:.2f} 亿/{sb30['n_days_actual']}d "
                   f"avg={sb30['daily_avg_hkd']/1e8:.2f} 亿/d")
        except Exception as e2:
            rec.fail("southbound_30d", f"{type(e2).__name__}: {e2}")
    except Exception as e:
        rec.fail("southbound_today", f"{type(e).__name__}: {e}")

    # ---- 跨境理财通南向 月度 (EDB S032219215) — 非港股通, 仅作宏观参考 ----
    try:
        from iFinDPy import THS_EDB
        edb_start = (today - timedelta(days=400)).strftime("%Y-%m-%d")
        r = THS_EDB('S032219215', '', edb_start, edate)
        if getattr(r, "errorcode", -1) != 0:
            raise RuntimeError(f"ec={r.errorcode} msg={r.errmsg}")
        df = r.data.sort_values("time").reset_index(drop=True)
        recs = [{"month_end": str(row["time"])[:10], "value": float(row["value"])}
                for _, row in df.iterrows() if str(row.get("value")) not in ("nan", "None")]
        out["wmc_southbound_monthly"] = recs[-12:]
        rec.ok("wmc_southbound", f"{len(recs)} 月; 最近 {recs[-1] if recs else 'N/A'}")
    except Exception as e:
        rec.fail("wmc_southbound", f"{type(e).__name__}: {e}")

    # ---- AH 溢价 — 用 p03508 当日 AH 比对加权平均 (替代 HSAHP) ----
    try:
        from iFinDPy import THS_DR
        import pandas as pd
        edate_compact = today.strftime("%Y%m%d")
        r = THS_DR(
            'p03508',
            f'date={edate_compact}',
            'jydm:Y,jydm_mc:Y,'
            + ','.join([f'p03508_f{i:03d}:Y' for i in range(1, 17)]),
            'format:dataframe'
        )
        if getattr(r, "errorcode", -1) != 0:
            raise RuntimeError(f"ec={r.errorcode} msg={r.errmsg}")
        df = r.data
        # f004 是 H 相对 A 的溢价/折价百分比 (H 折价时为负).
        # 取反对齐 HSAHP 习惯: A 相对 H 溢价 (A 比 H 贵则正).
        prem_h_over_a = pd.to_numeric(df["p03508_f004"], errors="coerce")
        prem = -prem_h_over_a
        h_amt = pd.to_numeric(df["p03508_f008"], errors="coerce")
        a_amt = pd.to_numeric(df["p03508_f014"], errors="coerce")
        weight = (h_amt.fillna(0) + a_amt.fillna(0))
        valid = prem.notna() & (weight > 0)
        wavg = float((prem[valid] * weight[valid]).sum() / weight[valid].sum())
        simple_avg = float(prem[prem.notna()].mean())
        out["ah_premium_today"] = {
            "as_of": today.isoformat(),
            "n_pairs": int(prem.notna().sum()),
            "weighted_avg_premium_pct": wavg,      # A 相对 H 溢价 (HSAHP 习惯, 已取反)
            "simple_avg_premium_pct": simple_avg,  # 同上, 简单平均
            "convention": "A_over_H (positive = A 比 H 贵)",
            "raw_field": "p03508_f004 取反",
            "note": "p03508 自算 (非 HSAHP 官方指数)",
        }
        rec.ok("ah_premium", f"A_over_H weighted={wavg:.2f}% n={prem.notna().sum()}")

        # 60 日序列 + 百分位 (扫 daily/ 历史 + 今日)
        try:
            ah60 = _aggregate_ah_premium_60d(today, wavg)
            if ah60:
                out["ah_premium_60d"] = ah60
                rec.ok("ah_premium_60d",
                       f"cur={ah60['current_pct']:.2f}% avg={ah60['60d_avg_pct']:.2f}% "
                       f"pctrank={ah60['60d_pct_rank']:.0%} n={ah60['n_days_actual']}d")
        except Exception as e2:
            rec.fail("ah_premium_60d", f"{type(e2).__name__}: {e2}")
    except Exception as e:
        rec.fail("ah_premium", f"{type(e).__name__}: {e}")

    # ---- IPO 缓存检测 (实际刷新延后到 main() 末尾, Logout 之后) ----
    try:
        ipo_check = _detect_ipo_cache_missing(today)
        out["ipo_cache_check"] = ipo_check
        rec.ok("ipo_cache_check",
               f"status={ipo_check.get('status')} n_missing={ipo_check.get('n_missing', 0)} "
               f"n_cached={ipo_check.get('n_cached', '?')}")
    except Exception as e:
        rec.fail("ipo_cache_check", f"{type(e).__name__}: {e}")

    # ---- regime_score (依赖历史 IPO 30d 收益缓存) ----
    # regime_score 用 [today-120, today-30] 窗口的港股 IPO 30 日收益中位数,
    # 缓存由 scripts/build_ipo_returns_cache.py 生成,
    # 文件: data/derived/ipo_d30_returns.csv
    try:
        from src.nacs_model import compute_regime_score
        ipo_cache = PROJECT_ROOT / "data" / "derived" / "ipo_d30_returns.csv"
        if not ipo_cache.exists():
            rec.fail("regime_score", f"IPO 收益缓存缺失: {ipo_cache} (跑 scripts/build_ipo_returns_cache.py)")
        else:
            df_cache = pd.read_csv(ipo_cache, encoding="utf-8-sig")
            historical_ipos: list[tuple[date, float]] = []
            for _, r in df_cache.iterrows():
                ret = r.get("return_d30")
                ld_raw = r.get("listing_date")
                if pd.isna(ret) or pd.isna(ld_raw):
                    continue
                try:
                    ld = datetime.strptime(str(ld_raw)[:10], "%Y-%m-%d").date()
                except Exception:
                    continue
                historical_ipos.append((ld, float(ret)))
            score = compute_regime_score(historical_ipos, today)
            if score is None:
                rec.fail("regime_score", f"样本不足 (cache n={len(historical_ipos)}, 窗口 [t-120,t-30])")
            else:
                out["regime_score"] = score
                # 顺带写一些诊断信息
                window_start = today - timedelta(days=120)
                window_end = today - timedelta(days=30)
                in_window = [r for d, r in historical_ipos if window_start <= d <= window_end]
                out["regime_score_n"] = len(in_window)
                out["regime_score_window"] = [window_start.isoformat(), window_end.isoformat()]
                rec.ok("regime_score", f"{score:.2%} (n={len(in_window)})")
    except Exception as e:
        rec.fail("regime_score", f"{type(e).__name__}: {e}")

    return out


# ============================================================================
# (b) news_today.json — 100-200 篇当日港股新闻
# ============================================================================
def fetch_news(rec: RunRecorder, today: date) -> list[dict[str, Any]]:
    """
    iFinD 没有可直接拉取新闻全文的 API. 探针确认:
      - THS_iEvent / THS_iResearch: account type is not supported (-5100)
      - THS_iwencai(query, 'news'): ec=0 但 tables[0]['table'] 实际无内容
      - THS_WC(query, 'news'): 返回 1 行 "查看明细" 占位 (链接, 非全文)
    用户已确认: "iFinD 未提及独立的资讯 API 端点".

    建议的替代源 (后续接入):
      - FT 中文网 / 财新 RSS
      - Bloomberg API (付费)
      - akshare ak.stock_news_em()
      - 港交所披露易官网爬虫 (公司公告)
    """
    rec.skip("news", "iFinD 无独立新闻 API; 见函数 docstring 里的替代源建议")
    return []


# ============================================================================
# (c) themes.json — 主题板块表现
# ============================================================================
# 主题 → 港股相关指数代码
#   ⚠ 港股没有同花顺概念板块编码体系 (确认自用户), 只能用:
#     - 中证港股通指数 (.CSI 后缀): 930967.CSI 港股通信息技术综合 / 931573.CSI 港股通科技 / 931574.CSI 港股通TMT
#     - 恒生行业指数 (.HK 后缀): HSTECH.HK 恒生科技 / HSCI.HK 恒生综合 / HSCIIT.HK 恒生综合-资讯科技
#   5 个 AI 主题在港股都没有专属指数, 全部用 HSTECH.HK 作粗代理.
#   TODO 升级: 改成「主题代表股票组合」(每个主题挑 3-5 只代表股, 算等权 close 序列), 精度更高
DEFAULT_THEMES = {
    "ai_server":        {"label": "AI 服务器",     "ths_code": "HSTECH.HK",  "proxy_note": "粗代理: 用恒生科技指数"},
    "llm":              {"label": "大模型",        "ths_code": "HSTECH.HK",  "proxy_note": "粗代理: 用恒生科技指数"},
    "humanoid_robot":   {"label": "人形机器人",    "ths_code": "HSTECH.HK",  "proxy_note": "粗代理: 港股无对应指数"},
    "semi_localization":{"label": "半导体国产替代","ths_code": "930967.CSI", "proxy_note": "中证港股通信息技术综合"},
    "ai_driving":       {"label": "AI 智能驾驶",   "ths_code": "HSTECH.HK",  "proxy_note": "粗代理: 港股新势力车 / 科技股"},
}

def load_watchlist() -> dict[str, dict]:
    """优先读 data/watchlist.json, 回退 DEFAULT_THEMES."""
    wl_path = PROJECT_ROOT / "data" / "watchlist.json"
    if wl_path.exists():
        try:
            data = json.loads(wl_path.read_text(encoding="utf-8"))
            return data.get("themes_to_track", DEFAULT_THEMES)
        except Exception:
            pass
    return DEFAULT_THEMES


def _resolve_theme_quote_code(meta: dict) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """
    解析 watchlist entry 实际拉行情用的代码.

    Schema 兼容:
      v1 (旧):  {"ths_code": "HSTECH.HK", ...}
      v2 (新):  {"iv_bkid": "011007_305848", "fallback_quote_code": "HSTECH.HK", ...}

    返回: (quote_code 用于 THS_HistoryQuotes, iv_bkid 元数据, proxy_status 元数据)
    """
    iv_bkid = meta.get("iv_bkid")
    if iv_bkid:
        # v2: iv_bkid 不能直接喂 THS_HistoryQuotes (已确认无效命令),
        # 暂用 fallback_quote_code 拉行情, iv_bkid 仅作 metadata 留给 Step 2 用
        quote_code = meta.get("fallback_quote_code")
        proxy_status = meta.get("proxy_status", "iv_bkid_pending_constituents_api")
        return quote_code, iv_bkid, proxy_status
    # v1: 老格式
    return meta.get("ths_code"), None, None


# ----------------------------------------------------------------------------
# Step 2 — 成分股月度缓存 + 等权合成主题 close 序列
# ----------------------------------------------------------------------------
_CONSTITUENTS_CACHE_PATH = PROJECT_ROOT / "data" / "theme_constituents_cache.json"


def _load_constituents_cache() -> dict:
    if _CONSTITUENTS_CACHE_PATH.exists():
        try:
            return json.loads(_CONSTITUENTS_CACHE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"_schema_version": "1.0", "constituents": {}}


def _save_constituents_cache(cache: dict, dry_run: bool = False) -> None:
    if dry_run:
        return
    _CONSTITUENTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _CONSTITUENTS_CACHE_PATH.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _fetch_theme_constituents(bkid: str, asof: date, *, dry_run: bool = False) -> list[str]:
    """
    用 THS_DR('p03291', ...) 拉板块成分股 (Step 2 winner 接口, 用户确认).
    月度缓存到 data/theme_constituents_cache.json — 同 (bkid, YYYY-MM) 一个月只查 1 次.

    返回港股 ths_code 列表 (e.g. ['0085.HK', '0522.HK', ...]).
    失败抛 RuntimeError.

    返回字段语义 (verify_p03291.py 跑过):
        p03291_f001 = date
        p03291_f002 = ths_code   ← 取这列
        p03291_f003 = 简称
        p03291_f004 = 简称
    """
    month_key = asof.strftime("%Y-%m")
    cache = _load_constituents_cache()
    constituents = cache.setdefault("constituents", {})
    entry_key = f"{bkid}__{month_key}"
    if entry_key in constituents:
        cached = constituents[entry_key]
        codes_cached = cached.get("codes") or []
        if codes_cached:
            return list(codes_cached)

    from iFinDPy import THS_DR
    edate_compact = asof.strftime("%Y%m%d")
    r = THS_DR(
        'p03291',
        f'date={edate_compact};blockname={bkid};iv_type=allcontract',
        'p03291_f001:Y,p03291_f002:Y,p03291_f003:Y,p03291_f004:Y',
        'format:dataframe',
    )
    ec = getattr(r, "errorcode", -1)
    if ec != 0:
        raise RuntimeError(f"p03291 失败 ec={ec} msg={getattr(r,'errmsg','')}")
    df = getattr(r, "data", None)
    if df is None or len(df) == 0:
        raise RuntimeError(f"p03291 返回空 df, bkid={bkid}")
    code_col = next((c for c in df.columns if "f002" in str(c)), None)
    if not code_col:
        raise RuntimeError(f"p03291 无 f002 列, columns={list(df.columns)}")
    codes_raw = [str(x).strip() for x in df[code_col].dropna().tolist()]
    codes_hk = [c for c in codes_raw if c.upper().endswith(".HK")]
    if not codes_hk:
        raise RuntimeError(f"p03291 返回 0 只港股, bkid={bkid}, raw_sample={codes_raw[:3]}")

    constituents[entry_key] = {
        "bkid": bkid,
        "month": month_key,
        "fetched_at": datetime.now().isoformat(),
        "n": len(codes_hk),
        "codes": codes_hk,
        "source": "THS_DR_p03291",
    }
    _save_constituents_cache(cache, dry_run=dry_run)
    return codes_hk


def _compose_theme_close_series(
    codes: list[str], sdate: str, edate: str, *, debug_tag: str = ""
) -> tuple[list[str], list[float], int]:
    """
    批量调 THS_HistoryQuotes 拉 close, 等权合成主题 close 序列.

    合成规则:
        1) 每只股票按其首个有效 close 归一化为 1 (消除价格量纲)
        2) 按 time 对齐 (DataFrame), 缺失用 forward-fill (停牌沿用上日)
        3) 行 mean = 等权指数

    返回 (times, composed_closes, n_used). n_used = 实际合成用的成分股数.
    失败抛 RuntimeError.
    """
    from iFinDPy import THS_HistoryQuotes
    import pandas as pd

    if not codes:
        raise RuntimeError("成分股列表为空")
    codes_str = ",".join(codes)
    r = THS_HistoryQuotes(codes_str, "close", "", sdate, edate)
    per = _hq_unpack_batch(r, debug_tag=debug_tag)
    if not per:
        raise RuntimeError(f"批量行情返回空 (codes_n={len(codes)}, ec={getattr(r,'errorcode',None)} msg={getattr(r,'errmsg','')})")

    series_list = []
    for code, data in per.items():
        closes = data.get("closes", [])
        times = data.get("times", [])
        if len(closes) < 2:
            continue
        if len(times) != len(closes):
            times = [str(i) for i in range(len(closes))]
        s = pd.Series(closes, index=[str(t)[:10] for t in times], name=code)
        first_valid = next((v for v in closes if v and v > 0), None)
        if not first_valid:
            continue
        s = s / first_valid
        # 同一日重复(理论上不会有, 安全起见保险一下)
        s = s[~s.index.duplicated(keep="last")]
        series_list.append(s)

    if len(series_list) < 2:
        raise RuntimeError(f"合成失败: 有效股票数 {len(series_list)} < 2")

    df = pd.concat(series_list, axis=1).sort_index()
    df = df.ffill()
    composed = df.mean(axis=1, skipna=True).dropna()
    if len(composed) < 2:
        raise RuntimeError(f"合成 close 序列长度 {len(composed)} < 2")

    return list(composed.index), [float(x) for x in composed.values], len(series_list)


def fetch_themes(rec: RunRecorder, today: date, *, dry_run: bool = False) -> dict[str, Any]:
    """
    每主题: 当日 / 5d / 20d / 60d 涨跌. Step 2 实现.

    路径选择:
      1. 有 iv_bkid → THS_DR(p03291) 拉成分股 (月度缓存) → 批量 THS_HistoryQuotes
         → 等权合成 close 序列  (proxy_status='composed')
      2. 合成失败 (接口错/成分股不足/批量失败) → 回退 fallback_quote_code 单 code 拉
         (proxy_status='composition_failed_using_fallback')
      3. v1 老 schema (ths_code) → 直接单 code 拉
    """
    from iFinDPy import THS_HistoryQuotes
    themes = load_watchlist()
    edate = today.strftime("%Y-%m-%d")
    sdate = (today - timedelta(days=120)).strftime("%Y-%m-%d")

    out: dict[str, Any] = {}
    for key, meta in themes.items():
        quote_code_fallback, iv_bkid, _ = _resolve_theme_quote_code(meta)
        composed_ok = False
        composed_n = 0
        composed_codes_sample: list[str] = []
        compose_err: Optional[str] = None
        closes: list[float] = []
        used_code: Optional[str] = None

        # ---- 路径 1: 合成 (仅当有 iv_bkid) ----
        if iv_bkid:
            try:
                codes = _fetch_theme_constituents(iv_bkid, today, dry_run=dry_run)
                composed_codes_sample = codes[:5]
                _, closes_synth, n_used = _compose_theme_close_series(
                    codes, sdate, edate, debug_tag=f"theme.{key}.compose"
                )
                closes = closes_synth
                composed_n = n_used
                composed_ok = True
                used_code = f"compose({iv_bkid}, n={n_used}/{len(codes)})"
            except Exception as e:
                compose_err = f"{type(e).__name__}: {e}"

        # ---- 路径 2/3: 单 code 拉 (fallback / v1) ----
        if not composed_ok:
            single_code = quote_code_fallback
            if not single_code:
                rec.fail(f"theme.{key}", f"无可用代码 (ths_code/fallback 均缺); compose_err={compose_err}")
                out[key] = {"label": meta.get("label"), "iv_bkid": iv_bkid,
                            "error": "no_code", "compose_err": compose_err}
                continue
            try:
                r = THS_HistoryQuotes(single_code, 'close', '', sdate, edate)
                u = _hq_unpack(r, debug_tag=f"theme.{key}.fallback")
                if u.errorcode != 0:
                    raise RuntimeError(f"errorcode={u.errorcode} errmsg={u.errmsg}")
                closes = u.closes
                used_code = single_code
            except Exception as e:
                rec.fail(f"theme.{key}", f"fallback 也失败: {type(e).__name__}: {e}; compose_err={compose_err}")
                out[key] = {"label": meta.get("label"), "iv_bkid": iv_bkid,
                            "error": str(e), "compose_err": compose_err}
                continue

        if len(closes) < 2:
            rec.fail(f"theme.{key}", f"close 序列太短 {len(closes)}; compose_err={compose_err}")
            out[key] = {"label": meta.get("label"), "iv_bkid": iv_bkid,
                        "error": "short_series", "compose_err": compose_err}
            continue

        def ret(n: int) -> Optional[float]:
            if len(closes) <= n:
                return None
            return closes[-1] / closes[-1 - n] - 1

        entry: dict[str, Any] = {
            "label": meta.get("label"),
            "ths_code": used_code,
            "proxy_note": meta.get("proxy_note"),
            "ret_1d":  ret(1),
            "ret_5d":  ret(5),
            "ret_20d": ret(20),
            "ret_60d": ret(60),
        }
        if iv_bkid:
            entry["iv_bkid"] = iv_bkid
            entry["proxy_status"] = "composed" if composed_ok else "composition_failed_using_fallback"
            if composed_ok:
                entry["composition_n_constituents"] = composed_n
                entry["composition_sample"] = composed_codes_sample
            else:
                entry["compose_err"] = compose_err
        out[key] = entry

        r1 = entry["ret_1d"]
        r60 = entry["ret_60d"]
        tag = ("composed n=" + str(composed_n)) if composed_ok else "fallback"
        if r1 is not None and r60 is not None:
            rec.ok(f"theme.{key}", f"1d={r1*100:.2f}% 60d={r60*100:.2f}% [{tag}]")
        else:
            rec.ok(f"theme.{key}", f"1d={r1} 60d={r60} [{tag}]")
    return out


# ============================================================================
# (d) ipo_recent.json — 过去 30 天港股新上市
# ============================================================================
def fetch_ipo_recent(rec: RunRecorder, today: date) -> list[dict[str, Any]]:
    """
    用 p05310 首发信息一览 (full_data_pull.py 已验证可行).
    上市日期字段: 需要从 p05310_f00X 中找 "上市日期" 列 (待确认 f编号).
    """
    try:
        from iFinDPy import THS_DR
        sdate = (today - timedelta(days=45)).strftime("%Y%m%d")  # 多 15 天 buffer
        edate = today.strftime("%Y%m%d")

        # 关键字段 (基于 full_data_pull.py 的猜测):
        #   f001=thscode, f002=简称, f003=上市日期(?), 其他待 full_data_pull 输出确认
        # TODO: 跑过 full_data_pull.py 后, 把"上市日期"列的真实编号补进来
        fields = ",".join([f"p05310_f{i:03d}:Y" for i in [1, 2, 3, 4, 5]])
        r = THS_DR(
            'p05310',
            f'ttype=1;sdate={sdate};edate={edate};sfzx=1',
            fields,
            'format:dataframe'
        )
        if r.errorcode != 0:
            raise RuntimeError(f"errorcode={r.errorcode} errmsg={r.errmsg}")
        df = r.data
        if df is None or len(df) == 0:
            print(f"  [debug:p05310] 返回 df 为空; sdate={sdate} edate={edate} errmsg={r.errmsg}")
            return []
        print(f"  [debug:p05310] 列名={list(df.columns)}  行数={len(df)}")
        print(f"  [debug:p05310] head:\n{df.head(3).to_string()}")

        # 对每只新股拉首日/5日/30日表现
        from iFinDPy import THS_HistoryQuotes
        rows: list[dict[str, Any]] = []
        # 假设 f001 是 thscode, f003 是上市日期 — 待确认
        code_col = next((c for c in df.columns if "f001" in c), df.columns[0])
        date_col = next((c for c in df.columns if "f003" in c), df.columns[2])
        for _, row in df.iterrows():
            code = str(row[code_col])
            list_dt_raw = str(row[date_col])[:10]
            list_dt: Optional[date] = None
            for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
                try:
                    list_dt = datetime.strptime(list_dt_raw, fmt).date()
                    break
                except Exception:
                    continue
            if list_dt is None:
                continue

            entry: dict[str, Any] = {
                "thscode": code,
                "listing_date": list_dt.isoformat(),
                "name": str(row.get(next((c for c in df.columns if "f002" in c), ""), "")),
            }
            # 拉上市后行情
            try:
                start = list_dt.strftime("%Y-%m-%d")
                end = (list_dt + timedelta(days=60)).strftime("%Y-%m-%d")
                rq = THS_HistoryQuotes(code, 'close,open', '', start, end)
                uq = _hq_unpack(rq, debug_tag="ipo_quote")
                if uq.errorcode == 0 and uq.closes and uq.opens:
                    closes, opens = uq.closes, uq.opens
                    entry["d1_return"] = closes[0] / opens[0] - 1   # 首日(收/开)
                    entry["d5_return"] = closes[4] / opens[0] - 1 if len(closes) > 4 else None
                    entry["d30_return"] = closes[29] / opens[0] - 1 if len(closes) > 29 else None
            except Exception as e:
                entry["quote_error"] = str(e)
            rows.append(entry)

        rec.ok("ipo_recent", f"{len(rows)} 只新股")
        return rows
    except Exception as e:
        rec.fail("ipo_recent", f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return []


# ============================================================================
# 主流程
# ============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="每日港股市场数据抓取")
    parser.add_argument("--date", default=None, help="参考日 YYYY-MM-DD (默认今天)")
    parser.add_argument("--dry-run", action="store_true", help="不写文件, 只打印")
    parser.add_argument("--out-root", default="daily", help="输出根目录")
    args = parser.parse_args()

    today = (datetime.strptime(args.date, "%Y-%m-%d").date()
             if args.date else date.today())
    out_dir = PROJECT_ROOT / args.out_root / today.isoformat()
    print(f"参考日: {today}  输出: {out_dir}  dry_run={args.dry_run}\n")

    # 登录
    from iFinDPy import THS_iFinDLogin, THS_iFinDLogout
    user = os.environ.get("IFIND_USERNAME", "")
    pwd = os.environ.get("IFIND_PASSWORD", "")
    if not user or not pwd:
        print(f"❌ 未读到 IFIND_USERNAME / IFIND_PASSWORD (检查 {_ENV_PATH})")
        return 2
    code = THS_iFinDLogin(user, pwd)
    if code not in (0, -201):
        print(f"❌ iFinD 登录失败: {code}")
        return 3
    print("✓ iFinD 登录成功\n")

    rec = RunRecorder(out_dir, dry_run=args.dry_run)

    try:
        # (a)
        print("[1/4] market_data ...")
        market = fetch_market_data(rec, today, dry_run=args.dry_run)
        write_json(out_dir / "market_data.json", market, args.dry_run)

        # (b)
        print("\n[2/4] news_today ...")
        news = fetch_news(rec, today)
        write_json(out_dir / "news_today.json",
                   {"as_of": today.isoformat(), "items": news}, args.dry_run)

        # (c)
        print("\n[3/4] themes ...")
        themes = fetch_themes(rec, today, dry_run=args.dry_run)
        write_json(out_dir / "themes.json",
                   {"as_of": today.isoformat(), "themes": themes}, args.dry_run)

        # (d)
        print("\n[4/4] ipo_recent ...")
        ipos = fetch_ipo_recent(rec, today)
        write_json(out_dir / "ipo_recent.json",
                   {"as_of": today.isoformat(), "ipos": ipos}, args.dry_run)
    finally:
        rec.write()
        THS_iFinDLogout()

    # ---- 主进程 Logout 后, 如检测到 IPO 缓存缺失则起子进程刷新 ----
    if not args.dry_run:
        ipo_check = (rec.results.get("ipo_cache_check") or {})
        if ipo_check.get("status") == "ok":
            # 从 market 输出里取检测细节
            check_detail = (locals().get("market") or {}).get("ipo_cache_check") or {}
            if check_detail.get("status") == "needs_refresh":
                refresh_result = _refresh_ipo_cache_subprocess()
                # 把刷新结果追加到 run_summary
                summary_path = out_dir / "run_summary.json"
                try:
                    payload = json.loads(summary_path.read_text(encoding="utf-8"))
                    payload["ipo_cache_refresh"] = refresh_result
                    summary_path.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                        encoding="utf-8")
                except Exception as e:
                    print(f"  [warn] 无法回写 ipo_cache_refresh 到 run_summary: {e}")
                print(f"  [post-daily] ipo_cache_refresh status={refresh_result.get('status')}")

    n_ok = sum(1 for r in rec.results.values() if r["status"] == "ok")
    n_fail = sum(1 for r in rec.results.values() if r["status"] == "fail")
    print(f"\n完成: {n_ok} ok / {n_fail} fail")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
