"""
verify_southbound_units.py — 南向资金字段单位与口径交叉验证

诊断目标 (基于 daily/2026-05-08/run_summary.json):
  southbound_today: 683.66 亿港元 (单日)
  wmc_southbound:   月度均值 16.05 (单位不明)
  → 量级差 40 倍, 怀疑 p04275_f003 单位错误或字段语义偏差

验证手段:
  A) 对今日 p04275 type=1/2 拉所有字段 (f001..f012), 看哪一列才是当日净买入(港元)
  B) 用历史已知量级锚点 (随便挑 2024-12-31, 公开口径单日通常 -200~+300 亿港元) 反推单位
  C) 与 EDB S032219215 (跨境理财通月度) 比对量级, 确认是不是同口径

输出: data/raw/ifind/probe_southbound_<today>.json + 控制台诊断结论

用法:
    python scripts/verify_southbound_units.py
    python scripts/verify_southbound_units.py --date 2026-05-08
"""
from __future__ import annotations

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import date, datetime, timedelta

# Windows 控制台 UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.fetch_hk_market_data import _load_env, _ENV_PATH, ifind_login, ifind_logout, call_with_relogin
_load_env(_ENV_PATH)


def _df_summary(df) -> dict:
    """对 df 做轻量描述: dtypes / 数值列统计."""
    import pandas as pd
    if df is None or len(df) == 0:
        return {"empty": True}
    out = {
        "n_rows": len(df),
        "columns": list(df.columns),
        "head_3": df.head(3).astype(str).to_dict("records"),
        "numeric_stats": {},
    }
    for c in df.columns:
        try:
            ser = pd.to_numeric(df[c], errors="coerce")
            n_valid = int(ser.notna().sum())
            if n_valid >= 3:
                out["numeric_stats"][str(c)] = {
                    "n_valid": n_valid,
                    "min": float(ser.min()),
                    "max": float(ser.max()),
                    "mean": float(ser.mean()),
                    "sum": float(ser.sum()),
                    "abs_max": float(ser.abs().max()),
                }
        except Exception:
            pass
    return out


def probe_p04275_today(today: date) -> dict:
    """A) 对今日 p04275 type=1/2 拉全部字段."""
    from iFinDPy import THS_DR
    edate = today.strftime("%Y%m%d")
    out = {}
    for ttype, label in [(1, "shanghai"), (2, "shenzhen")]:
        try:
            r = call_with_relogin(
                THS_DR,
                'p04275',
                f'type={ttype};sdate={edate};edate={edate}',
                ','.join([f'p04275_f{i:03d}:Y' for i in range(1, 13)]),
                'format:dataframe'
            )
            ec = getattr(r, 'errorcode', -1)
            df = getattr(r, 'data', None)
            entry = {"ec": ec, "errmsg": str(getattr(r, 'errmsg', ''))}
            if df is not None:
                entry.update(_df_summary(df))
            out[label] = entry
        except Exception as e:
            out[label] = {"exc": f"{type(e).__name__}: {e}"}
    return out


def probe_p04275_historic(anchor_dates: list[str]) -> dict:
    """B) 用历史锚点反推单位. anchor_dates = ['20241231', '20240628', ...]

    旧版仅试 'sdate=...;edate=...' 参数键, 实测三个不同日期返回完全相同的"今日快照"
    → datapool 没认这个时间参数. 这里同时试 4 种参数键, 看哪种能拉到真历史.
    """
    from iFinDPy import THS_DR
    out = {}
    # 不同参数键候选: (key_template, label)
    param_variants = [
        ('type=1;sdate={d};edate={d}',      'sdate_edate'),
        ('type=1;tradeDate={d}',            'tradeDate'),
        ('type=1;date={d}',                 'date'),
        ('type=1;reportDate={d}',           'reportDate'),
    ]
    for d in anchor_dates:
        out[d] = {}
        for tmpl, vlabel in param_variants:
            param_str = tmpl.format(d=d)
            try:
                r = call_with_relogin(
                    THS_DR,
                    'p04275',
                    param_str,
                    ','.join([f'p04275_f{i:03d}:Y' for i in range(1, 13)]),
                    'format:dataframe'
                )
                ec = getattr(r, 'errorcode', -1)
                df = getattr(r, 'data', None)
                entry = {
                    "param": param_str,
                    "ec": ec,
                    "errmsg": str(getattr(r, 'errmsg', '')),
                }
                if df is not None:
                    entry.update(_df_summary(df))
                out[d][vlabel] = entry
            except Exception as e:
                out[d][vlabel] = {"param": param_str, "exc": f"{type(e).__name__}: {e}"}
    return out


def _unpack_hq(r) -> tuple[int, str, list[dict]]:
    """解包 THS_HistoryQuotes. 兼容两种返回:
      A) dict {errorcode, errmsg, tables: [{thscode, time:[...], table:{close:[...], amount:[...]}}, ...]}
      B) 对象 r.errorcode / r.errmsg / r.data (DataFrame)
    返回 (ec, errmsg, rows: [{time, close, amount, volume, ...}, ...])
    """
    rows: list[dict] = []
    # 形态 A
    if isinstance(r, dict):
        ec = int(r.get("errorcode", -999))
        em = str(r.get("errmsg", ""))
        tables = r.get("tables") or []
        for t in tables:
            if not isinstance(t, dict):
                continue
            times = t.get("time") or []
            tbl = t.get("table") if isinstance(t.get("table"), dict) else {}
            n = len(times)
            for i in range(n):
                row = {"time": times[i] if i < len(times) else None,
                       "thscode": t.get("thscode")}
                for col, vals in (tbl or {}).items():
                    row[col] = vals[i] if i < len(vals) else None
                rows.append(row)
        return ec, em, rows
    # 形态 B
    ec = int(getattr(r, "errorcode", -999) or -999)
    em = str(getattr(r, "errmsg", "") or "")
    df = getattr(r, "data", None)
    if df is not None:
        try:
            rows = df.astype(object).where(df.notna(), None).to_dict("records")  # type: ignore[union-attr]
        except Exception:
            try:
                rows = df.to_dict("records")  # type: ignore[union-attr]
            except Exception:
                rows = []
    return ec, em, rows


def probe_single_stock_today(today: date, codes: list[str]) -> dict:
    """D) 单股当日成交额侧证.

    用 THS_HistoryQuotes 拉 codes 在 today 当日的 close/amount/volume:
      - amount = 全市场总成交额 (不分港股通买卖方向)
      - 单股净买入 ≤ 全市场成交额 是必要条件
    若 p04275 给的"单只净买入"超过 amount, 则口径必错.

    iFinD 港股成交额字段名候选: amount / amt. 都试一次取能用的.
    """
    from iFinDPy import THS_HistoryQuotes
    edate = today.strftime("%Y-%m-%d")
    sdate = (today - timedelta(days=15)).strftime("%Y-%m-%d")  # 多取几天防停牌
    out = {}
    field_str = 'close,amount,volume'
    for code in codes:
        entry = {"field_str": field_str}
        try:
            r = call_with_relogin(
                THS_HistoryQuotes, code, field_str, '', sdate, edate
            )
            ec, em, rows = _unpack_hq(r)
            entry["ec"] = ec
            entry["errmsg"] = em
            entry["raw_type"] = type(r).__name__
            entry["n_rows"] = len(rows)
            if rows:
                entry["last_row"] = {k: (str(v) if v is not None else None)
                                     for k, v in rows[-1].items()}
                entry["all_rows"] = [
                    {k: (str(v) if v is not None else None) for k, v in row.items()}
                    for row in rows
                ]
        except Exception as e:
            entry["exc"] = f"{type(e).__name__}: {e}"
        out[code] = entry
    return out


def probe_p04277(today: date) -> dict:
    """E) p04277 沪深港通成交统计 — 用户确认的正确接口.

    参数: zq=day/week/month, sdate, edate, bz=CNY/HKD, lx=类型, jylx=交易类型, iv_date.
    要点: 该接口支持历史日期 (sdate/edate 真生效), 是"南向单日净买入"的正源.

    我们试 3 组组合:
      a) zq=day,  bz=CNY/HKD, sdate=今日-30d, edate=今日 — 拿到日序列
      b) zq=week, bz=CNY,    user 示例确认可用 — 兜底
      c) lx 未明, 试不传/传 1/2 看是否影响输出
    """
    from iFinDPy import THS_DR
    out = {}
    edate_dot = today.strftime("%Y%m%d")
    sdate_dot_30 = (today - timedelta(days=30)).strftime("%Y%m%d")
    sdate_dot_90 = (today - timedelta(days=90)).strftime("%Y%m%d")
    fields = ','.join([f'p04277_f{i:03d}:Y' for i in range(1, 13)])

    variants = [
        ("daily_CNY",        f'zq=day;sdate={sdate_dot_30};edate={edate_dot};bz=CNY'),
        ("daily_HKD",        f'zq=day;sdate={sdate_dot_30};edate={edate_dot};bz=HKD'),
        ("weekly_CNY",       f'zq=week;sdate={sdate_dot_90};edate={edate_dot};bz=CNY'),
        ("daily_lx1_CNY",    f'lx=1;zq=day;sdate={sdate_dot_30};edate={edate_dot};bz=CNY'),
        ("daily_lx2_CNY",    f'lx=2;zq=day;sdate={sdate_dot_30};edate={edate_dot};bz=CNY'),
    ]
    for vlabel, param in variants:
        entry = {"param": param}
        try:
            r = call_with_relogin(THS_DR, 'p04277', param, fields, 'format:dataframe')
            ec = getattr(r, 'errorcode', -1)
            df = getattr(r, 'data', None)
            entry["ec"] = ec
            entry["errmsg"] = str(getattr(r, 'errmsg', ''))
            if df is not None and len(df):
                entry.update(_df_summary(df))
                # 完整 dump 前 20 行 + 末 5 行, 看时间排序
                try:
                    entry["all_rows"] = df.astype(str).to_dict("records")
                except Exception:
                    pass
        except Exception as e:
            entry["exc"] = f"{type(e).__name__}: {e}"
        out[vlabel] = entry
    return out


def probe_edb_wmc(today: date) -> dict:
    """C) EDB S032219215 跨境理财通南向月度量级 (作为外部参照)."""
    from iFinDPy import THS_EDB
    try:
        edb_start = (today - timedelta(days=400)).strftime("%Y-%m-%d")
        edb_end = today.strftime("%Y-%m-%d")
        r = call_with_relogin(THS_EDB, 'S032219215', '', edb_start, edb_end)
        ec = getattr(r, 'errorcode', -1)
        df = getattr(r, 'data', None)
        out = {"ec": ec, "errmsg": str(getattr(r, 'errmsg', ''))}
        if df is not None:
            out.update(_df_summary(df))
        return out
    except Exception as e:
        return {"exc": f"{type(e).__name__}: {e}"}


def diagnose(probe: dict) -> list[str]:
    """根据探针输出给出诊断结论."""
    notes: list[str] = []

    # 检查 today p04275 各列量级, 找净买入候选
    for label in ("shanghai", "shenzhen"):
        rec = probe.get("p04275_today", {}).get(label, {})
        ns = rec.get("numeric_stats", {})
        # f003 是当前代码假设的"净流入"
        f003 = ns.get("p04275_f003")
        if f003:
            sum_raw = f003["sum"]
            sum_yi = sum_raw / 1e8
            notes.append(
                f"[{label}] f003 sum={sum_raw:.0f} 原始单位 → /1e8={sum_yi:.2f} 亿\n"
                f"           (单只 abs_max={f003['abs_max']:.0f}, n_valid={f003['n_valid']})"
            )
            if abs(sum_yi) > 1500:
                notes.append(
                    f"[{label}] ⚠ /1e8={sum_yi:.0f} 亿 量级偏大. 单日港股通南向公开口径"
                    f"通常 |sum| < 500 亿. 可能 f003 不是港元而是元 (已 /1e8)."
                )

    # 与历史锚点对比 (新结构: probe['p04275_historic'][date][param_variant])
    today_sh_sum = None
    today_rec = probe.get("p04275_today", {}).get("shanghai", {})
    f003_today = (today_rec.get("numeric_stats") or {}).get("p04275_f003")
    if f003_today:
        today_sh_sum = round(f003_today["sum"], 2)

    for d, by_variant in probe.get("p04275_historic", {}).items():
        if not isinstance(by_variant, dict):
            continue
        for vlabel, rec in by_variant.items():
            ns = rec.get("numeric_stats") or {}
            f003 = ns.get("p04275_f003")
            if not f003:
                ec = rec.get("ec")
                em = rec.get("errmsg", "")[:60]
                notes.append(f"[历史 {d}/{vlabel}] 无数据 (ec={ec} msg={em!r})")
                continue
            sum_raw = f003["sum"]
            sum_yi = sum_raw / 1e8
            # 是否退化为今日快照
            same_as_today = (today_sh_sum is not None
                             and abs(sum_raw - today_sh_sum) < 1.0)
            tag = " ⚠ 退化为今日快照" if same_as_today else " ✓ 真历史数据!"
            notes.append(
                f"[历史 {d}/{vlabel}] f003 sum/1e8 = {sum_yi:.2f} 亿{tag}\n"
                f"             abs_max={f003['abs_max']:.0f}, n_valid={f003['n_valid']}"
            )

    # p04277 字段诊断 — 找哪一列才是"南向单日净买入"
    notes.append("")
    notes.append("=== p04277 字段尺度 (找单日净买入候选列) ===")
    for vlabel, rec in (probe.get("p04277") or {}).items():
        if rec.get("exc"):
            notes.append(f"  [{vlabel}] EXC: {rec['exc'][:80]}")
            continue
        ec = rec.get("ec")
        em = (rec.get("errmsg") or "")[:60]
        n = rec.get("n_rows", 0)
        cols = rec.get("columns") or []
        notes.append(f"  [{vlabel}] ec={ec} n_rows={n} cols={cols}")
        if n > 0:
            # 最后一行(最近日)各字段值, 找 |x| ~ 50-200 亿 的列 — 那是日总净买入候选
            rows = rec.get("all_rows") or []
            if rows:
                last = rows[-1]
                # 简洁打印每列尾值 + 量级
                samples = []
                for k, v in last.items():
                    if not k.startswith("p04277_f"):
                        samples.append(f"{k}={v}")
                        continue
                    try:
                        fv = float(v)
                        if abs(fv) >= 1:
                            samples.append(f"{k}={fv:.2e}")
                        else:
                            samples.append(f"{k}={fv:.4f}")
                    except Exception:
                        samples.append(f"{k}={str(v)[:15]}")
                notes.append(f"    末行: {' | '.join(samples)}")
            # 各数值列 sum 量级
            ns = rec.get("numeric_stats") or {}
            for col, st in ns.items():
                if not col.startswith("p04277_f"):
                    continue
                avg = st.get("mean", 0)
                amax = st.get("abs_max", 0)
                # 标记可能是亿元单位还是元单位
                if 1 < abs(avg) < 1e4:
                    unit_hint = "(数量级 ~亿元)"
                elif abs(avg) > 1e8:
                    unit_hint = "(数量级 ~元)"
                else:
                    unit_hint = ""
                notes.append(
                    f"    {col}: mean={avg:.3g} abs_max={amax:.3g} {unit_hint}"
                )

    # 单股侧证
    notes.append("")
    notes.append("=== 单股侧证 (单只净买入 vs 全市场成交额) ===")
    today_pp = probe.get("p04275_today", {}).get("shenzhen", {})
    head_3 = today_pp.get("head_3", []) if isinstance(today_pp, dict) else []
    sb_per_code = {h.get("p04275_f001"): h.get("p04275_f003") for h in head_3 if h}
    sh_pp = probe.get("p04275_today", {}).get("shanghai", {})
    sh_head = sh_pp.get("head_3", []) if isinstance(sh_pp, dict) else []
    for h in sh_head:
        if h:
            sb_per_code.setdefault(h.get("p04275_f001"), h.get("p04275_f003"))

    for code, rec in (probe.get("single_stock") or {}).items():
        last = rec.get("last_row") or {}
        if not last:
            ec = rec.get("ec")
            notes.append(f"  [{code}] 无行情 ec={ec} {rec.get('errmsg','')[:40]}")
            continue
        amount_raw = last.get("amount")
        try:
            amt_yi = float(amount_raw) / 1e8 if amount_raw not in (None, "None", "nan") else None
        except Exception:
            amt_yi = None
        sb_raw = sb_per_code.get(code)
        try:
            sb_yi = float(sb_raw) / 1e8 if sb_raw else None
        except Exception:
            sb_yi = None
        date_str = last.get("time") or last.get("date") or "?"
        verdict = ""
        if amt_yi is not None and sb_yi is not None:
            ratio = sb_yi / amt_yi if amt_yi else None
            if ratio is not None:
                if abs(sb_yi) > amt_yi:
                    verdict = " ❌ 净买入 > 全市场成交额, p04275 口径错误"
                elif abs(ratio) > 0.6:
                    verdict = f" ⚠ 单只净买入占成交 {ratio:.0%}, 偏极端"
                else:
                    verdict = f" ✓ 占成交 {ratio:.0%}, 合理"
        notes.append(
            f"  [{code}] {date_str}  全市场成交={amt_yi}亿  "
            f"p04275_f003 净买入={sb_yi}亿{verdict}"
        )

    return notes


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--date", type=str, help="YYYY-MM-DD; 默认今天")
    args = p.parse_args()

    today = (datetime.strptime(args.date, "%Y-%m-%d").date()
             if args.date else date.today())

    user = os.environ.get("IFIND_USERNAME"); pwd = os.environ.get("IFIND_PASSWORD")
    if not user or not pwd:
        print(f"❌ 未配置 IFIND_USERNAME / IFIND_PASSWORD ({_ENV_PATH})")
        return 2
    ifind_login(user, pwd)

    out = {
        "as_of": today.isoformat(),
        "generated_at": datetime.now().isoformat(),
        "p04275_today": {},
        "p04275_historic": {},
        "p04277": {},
        "edb_wmc": {},
        "single_stock": {},
    }

    try:
        print("[A] 探针: p04275 今日 type=1/2 全字段...")
        out["p04275_today"] = probe_p04275_today(today)

        print("\n[B] 探针: p04275 历史锚点 (4 种参数键试错)...")
        # 选 3 个不同时间点的历史锚点 (避开周末)
        anchors = ["20241231", "20241001", "20240628"]
        out["p04275_historic"] = probe_p04275_historic(anchors)

        print("\n[C] 探针: EDB S032219215 跨境理财通月度...")
        out["edb_wmc"] = probe_edb_wmc(today)

        print("\n[E] 探针: p04277 沪深港通成交统计 (user-confirmed 正确接口)...")
        out["p04277"] = probe_p04277(today)

        print("\n[D] 探针: 单股侧证 (THS_HistoryQuotes amount vs p04275_f003)...")
        # 取 p04275_today 各端 head_3 里的代码 (中国移动、中国石油、建设银行等)
        codes_set = []
        for end in ("shanghai", "shenzhen"):
            for h in (out["p04275_today"].get(end, {}) or {}).get("head_3", []) or []:
                c = h.get("p04275_f001") if h else None
                if c and c not in codes_set:
                    codes_set.append(c)
        if codes_set:
            out["single_stock"] = probe_single_stock_today(today, codes_set[:6])
    finally:
        ifind_logout()

    out_path = PROJECT_ROOT / "data" / "raw" / "ifind" / f"probe_southbound_{today.isoformat()}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    print(f"\n探针 JSON: {out_path}")
    print("\n=== 诊断结论 ===")
    notes = diagnose(out)
    if not notes:
        print("(无诊断信息 — 检查探针输出)")
    for n in notes:
        print(f"  {n}")

    print("\n=== 行动指引 ===")
    print("  1. 对照诊断结论的 sum/1e8 是否落在公开口径区间 [-500, +500] 亿")
    print("  2. 如落入: f003 单位=元, fetch_hk_market_data.py 现有 net/1e8 = 亿港元 是正确的")
    print("  3. 如 sum/1e8 远超 500 亿: f003 可能本身已经是亿元, 不该再 /1e8 (口径错)")
    print("  4. 把结论写到 fetch_hk_market_data.py L506 注释块, 加单位锚点说明")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
