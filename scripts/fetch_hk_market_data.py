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
def fetch_market_data(rec: RunRecorder, today: date) -> dict[str, Any]:
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
            "note": "p04275 截面数据; 30 日累计需 daily 文件夹按天累加",
        }
        rec.ok("southbound_today", f"total={sb_total/1e8:.2f} 亿港元")
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
            "note": "p03508 自算 (非 HSAHP 官方指数); 60 日序列需 daily 文件夹按天累加",
        }
        rec.ok("ah_premium", f"A_over_H weighted={wavg:.2f}% n={prem.notna().sum()}")
    except Exception as e:
        rec.fail("ah_premium", f"{type(e).__name__}: {e}")

    # ---- regime_score (依赖 ipo_recent.json 的历史 IPO 30d 收益) ----
    # 注意: regime_score 用的是 [t-120, t-30] 的港股 IPO 30 日收益,
    # 这里需要更长的回溯, 不能只用本次 ipo_recent (那是过去 30 天).
    # TODO: 从历史数据库 / data/raw/ifind/ipo_d30_returns.csv 读历史 IPO 30d 收益
    try:
        from src.nacs_model import compute_regime_score
        # 占位: 需要 List[Tuple[listing_date, return_d30]]
        historical_ipos: list[tuple[date, float]] = []  # TODO: 接入真实历史数据
        score = compute_regime_score(historical_ipos, today)
        if score is None:
            rec.fail("regime_score", "样本不足 / 历史数据未接入")
        else:
            out["regime_score"] = score
            rec.ok("regime_score", f"{score:.2%}")
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


def fetch_themes(rec: RunRecorder, today: date) -> dict[str, Any]:
    """每个主题: 当日 / 5d / 20d / 60d 涨跌."""
    from iFinDPy import THS_HistoryQuotes
    themes = load_watchlist()
    edate = today.strftime("%Y-%m-%d")
    sdate = (today - timedelta(days=120)).strftime("%Y-%m-%d")

    out: dict[str, Any] = {}
    for key, meta in themes.items():
        code = meta.get("ths_code")
        if not code:
            rec.fail(f"theme.{key}", "ths_code 未配置 (TODO)")
            out[key] = {"label": meta.get("label"), "error": "no_code"}
            continue
        try:
            r = THS_HistoryQuotes(code, 'close', '', sdate, edate)
            u = _hq_unpack(r, debug_tag=f"theme.{key}")
            if u.errorcode != 0:
                raise RuntimeError(f"errorcode={u.errorcode} errmsg={u.errmsg}")
            closes = u.closes
            if len(closes) < 2:
                raise RuntimeError(f"close 序列太短: {len(closes)}")

            def ret(n: int) -> Optional[float]:
                if len(closes) <= n:
                    return None
                return closes[-1] / closes[-1 - n] - 1

            out[key] = {
                "label": meta.get("label"),
                "ths_code": code,
                "proxy_note": meta.get("proxy_note"),
                "ret_1d":  ret(1),
                "ret_5d":  ret(5),
                "ret_20d": ret(20),
                "ret_60d": ret(60),
            }
            r1 = out[key]["ret_1d"]
            r60 = out[key]["ret_60d"]
            rec.ok(f"theme.{key}",
                   f"1d={r1*100:.2f}% 60d={r60*100:.2f}%" if r1 is not None and r60 is not None
                   else f"1d={r1} 60d={r60}")
        except Exception as e:
            rec.fail(f"theme.{key}", f"{type(e).__name__}: {e}")
            out[key] = {"label": meta.get("label"), "error": str(e)}
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
        market = fetch_market_data(rec, today)
        write_json(out_dir / "market_data.json", market, args.dry_run)

        # (b)
        print("\n[2/4] news_today ...")
        news = fetch_news(rec, today)
        write_json(out_dir / "news_today.json",
                   {"as_of": today.isoformat(), "items": news}, args.dry_run)

        # (c)
        print("\n[3/4] themes ...")
        themes = fetch_themes(rec, today)
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

    n_ok = sum(1 for r in rec.results.values() if r["status"] == "ok")
    n_fail = sum(1 for r in rec.results.values() if r["status"] == "fail")
    print(f"\n完成: {n_ok} ok / {n_fail} fail")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
