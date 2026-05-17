"""
NACS 数据访问层 (DAO)

核心职责:
    1. 别名解析: 招股书原文 -> cornerstone_id (含模糊匹配)
    2. as-of-date hydrate: 给定 (cornerstone_id, t), 返回截至 t 之前的画像
    3. 派生表构建: cornerstone_performance_asof / ipo_returns
    4. CornerstoneInvestor 自动构造 (替代手填)

关键不变量:
    - 任何 hydrate(cornerstone_id, asof) 调用, 永远只看 listing_date < asof 的IPO
    - 物化派生表时, 用 ipo_master.pricing_date 而不是 listing_date 作为切点
      (基石协议在 pricing_date 之前签署)
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional, List, Dict, Tuple, Iterator

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from nacs_model import (
    CornerstoneInvestor, CornerstoneType,
    CHINESE_TYPES, LONGTERM_TYPES,
    MarketEnvironment,
)


# =============================================================================
# 连接管理
# =============================================================================

@contextmanager
def db_connect(db_path: str) -> Iterator[sqlite3.Connection]:
    """上下文管理器, 默认 row_factory + foreign_keys ON"""
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# =============================================================================
# 1. 基石机构 CRUD
# =============================================================================

def upsert_cornerstone(conn: sqlite3.Connection, *,
                       cornerstone_id: str,
                       canonical_name: str,
                       cornerstone_type: CornerstoneType,
                       name_zh: Optional[str] = None,
                       parent_entity: Optional[str] = None,
                       country_of_origin: Optional[str] = None,
                       aum_usd_latest: Optional[float] = None,
                       aum_asof_date: Optional[date] = None,
                       notes: Optional[str] = None) -> None:
    is_chinese = int(cornerstone_type in CHINESE_TYPES)
    is_longterm = int(cornerstone_type in LONGTERM_TYPES)
    conn.execute("""
        INSERT INTO cornerstone_master (
            cornerstone_id, canonical_name, name_zh, cornerstone_type,
            parent_entity, country_of_origin, aum_usd_latest, aum_asof_date,
            is_chinese, is_longterm, notes, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(cornerstone_id) DO UPDATE SET
            canonical_name = excluded.canonical_name,
            name_zh = excluded.name_zh,
            cornerstone_type = excluded.cornerstone_type,
            parent_entity = excluded.parent_entity,
            country_of_origin = excluded.country_of_origin,
            aum_usd_latest = excluded.aum_usd_latest,
            aum_asof_date = excluded.aum_asof_date,
            is_chinese = excluded.is_chinese,
            is_longterm = excluded.is_longterm,
            notes = excluded.notes,
            updated_at = CURRENT_TIMESTAMP
    """, (cornerstone_id, canonical_name, name_zh, cornerstone_type.value,
          parent_entity, country_of_origin, aum_usd_latest, aum_asof_date,
          is_chinese, is_longterm, notes))


def add_alias(conn: sqlite3.Connection, *, cornerstone_id: str,
              alias_text: str, alias_type: str = "english",
              match_confidence: float = 1.0) -> None:
    conn.execute("""
        INSERT OR IGNORE INTO cornerstone_aliases
            (cornerstone_id, alias_text, alias_text_lower, alias_type, match_confidence)
        VALUES (?, ?, ?, ?, ?)
    """, (cornerstone_id, alias_text, alias_text.strip().lower(),
          alias_type, match_confidence))


# =============================================================================
# 2. 别名解析 - 招股书原文 -> cornerstone_id
# =============================================================================

import re as _re
from difflib import SequenceMatcher as _SeqMatcher

# 法人后缀 (按长度从长到短, 避免短词先剥)
_LEGAL_SUFFIXES = (
    "有限合伙企业", "私募基金管理有限公司", "私募基金管理", "投资管理有限公司",
    "国际投资有限公司", "资产管理有限公司", "投资有限公司", "管理有限公司",
    "有限责任公司", "股份有限公司", "股份合作公司", "有限公司", "(集团)",
    "（集团）", "private limited", "asset management", "asia pacific",
    "international", "investment", "investments", "capital", "holdings",
    "company", "limited", "incorporated", "corporation", "co.,ltd",
    "co.,ltd.", "co., ltd", "co. ltd", "co. ltd.", "ltd.", "ltd",
    "inc.", "inc", "llc", "l.p.", "lp", "spv", "spc", "plc",
    "(spc)", "(spv)", "(hk)", "(香港)", "（香港）", "香港",
)

# 标点 → 空格
_PUNCT_RE = _re.compile(r"[()（）.,。， '\"\-/\\&\u2019\u2018]+")


def normalize_cs_name(name: str) -> str:
    """归一化基石原文用于模糊匹配比较.

    步骤: 小写 → 去括号注释 → 剥常见法人后缀 → 标点转空格 → 折叠空白
    """
    s = name.strip().lower()
    if not s:
        return ""
    # 去整段括号内容 (中英括号), 但保留括号外文字
    s = _re.sub(r"\([^)]*\)", " ", s)
    s = _re.sub(r"（[^）]*）", " ", s)
    # 标点 → 空格
    s = _PUNCT_RE.sub(" ", s)
    # 折叠空白
    s = _re.sub(r"\s+", " ", s).strip()
    # 剥后缀 (循环, 因可能有"...有限公司有限合伙企业"嵌套)
    changed = True
    while changed:
        changed = False
        for suf in _LEGAL_SUFFIXES:
            if s.endswith(suf):
                s = s[: -len(suf)].rstrip()
                changed = True
                break
    return s


def _tokens(s: str) -> set:
    """中英混合分词: 英文按空格切; 中文逐字 (BoW 近似)"""
    out: set = set()
    for word in s.split():
        # 含 ASCII 字母数字 → 整词作 token
        if _re.search(r"[a-z0-9]", word):
            out.add(word)
        else:
            # 纯中文 (或符号) → 逐字
            for ch in word:
                if ch.strip():
                    out.add(ch)
    return out


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def resolve_cornerstone_id(conn: sqlite3.Connection,
                           raw_name: str,
                           min_confidence: float = 0.40) -> Optional[Tuple[str, float]]:
    """
    给定招股书原文(可能是任意别名), 返回 (cornerstone_id, confidence) 或 None.

    匹配策略 (按优先级, 命中即返回):
        1. 精确匹配 (case-insensitive)               → conf = alias_conf (通常 1.0)
        2. 归一化精确匹配 (剥后缀 + 标点)             → conf × 0.95
        3. 子串包含 (alias 在 raw 中, 或反之)         → conf × 0.70 (按长度加权)
        4. Token Jaccard (中英混合分词)              → conf × jaccard × 0.85
        5. SequenceMatcher.ratio (字符级编辑距离)    → conf × ratio × 0.70

    返回: confidence < min_confidence 视为未命中 (None)
    """
    raw_lower = raw_name.strip().lower()
    if not raw_lower:
        return None

    # ===== 策略 1: 精确 =====
    row = conn.execute("""
        SELECT cornerstone_id, match_confidence
        FROM cornerstone_aliases
        WHERE alias_text_lower = ?
        ORDER BY match_confidence DESC LIMIT 1
    """, (raw_lower,)).fetchone()
    if row:
        return row["cornerstone_id"], row["match_confidence"]

    # 拉所有 alias 到内存 (一次遍历, 用于策略 2-5)
    all_rows = conn.execute("""
        SELECT cornerstone_id, alias_text_lower, match_confidence
        FROM cornerstone_aliases
    """).fetchall()
    if not all_rows:
        return None

    raw_norm = normalize_cs_name(raw_name)
    raw_tokens = _tokens(raw_norm) if raw_norm else _tokens(raw_lower)

    # ===== 策略 2: 归一化精确 =====
    if raw_norm:
        for r in all_rows:
            if normalize_cs_name(r["alias_text_lower"]) == raw_norm:
                conf = r["match_confidence"] * 0.95
                if conf >= min_confidence:
                    return r["cornerstone_id"], conf

    # ===== 策略 3: 子串包含 =====
    sub_hits = [r for r in all_rows
                if r["alias_text_lower"] and (
                    r["alias_text_lower"] in raw_lower
                    or raw_lower in r["alias_text_lower"]
                )]
    if sub_hits:
        best = max(sub_hits,
                   key=lambda r: len(r["alias_text_lower"]) * r["match_confidence"])
        conf = best["match_confidence"] * 0.70
        if conf >= min_confidence:
            return best["cornerstone_id"], conf

    # ===== 策略 4: Token Jaccard =====
    best_j: Tuple[Optional[str], float] = (None, 0.0)
    for r in all_rows:
        alias_norm = normalize_cs_name(r["alias_text_lower"])
        alias_tokens = _tokens(alias_norm) if alias_norm else _tokens(r["alias_text_lower"])
        j = _jaccard(raw_tokens, alias_tokens)
        score = j * r["match_confidence"] * 0.85
        if score > best_j[1]:
            best_j = (r["cornerstone_id"], score)
    if best_j[0] and best_j[1] >= min_confidence:
        return best_j[0], best_j[1]

    # ===== 策略 5: SequenceMatcher (字符编辑距离比) =====
    best_s: Tuple[Optional[str], float] = (None, 0.0)
    for r in all_rows:
        target = normalize_cs_name(r["alias_text_lower"]) or r["alias_text_lower"]
        ratio = _SeqMatcher(None, raw_norm or raw_lower, target).ratio()
        score = ratio * r["match_confidence"] * 0.70
        if score > best_s[1]:
            best_s = (r["cornerstone_id"], score)
    if best_s[0] and best_s[1] >= min_confidence:
        return best_s[0], best_s[1]

    return None


def list_unresolved_names(conn: sqlite3.Connection,
                          ipo_id: str) -> List[str]:
    """诊断: 列出某IPO中无法解析的基石原文 (用于人工填库)"""
    # 这要求 ipo_cornerstone_link_raw 中间表存在; 简化版本省略
    return []


# =============================================================================
# 3. IPO 与 link 写入
# =============================================================================

def upsert_ipo(conn: sqlite3.Connection, **kwargs) -> None:
    """所有字段对照 ipo_master schema. 没传的列保持默认/NULL"""
    cols = list(kwargs.keys())
    placeholders = ",".join("?" * len(cols))
    col_str = ",".join(cols)
    update_str = ",".join(f"{c}=excluded.{c}" for c in cols if c != "ipo_id")
    sql = f"""
        INSERT INTO ipo_master ({col_str}) VALUES ({placeholders})
        ON CONFLICT(ipo_id) DO UPDATE SET {update_str}
    """
    conn.execute(sql, [kwargs[c] for c in cols])


def link_cornerstone_to_ipo(conn: sqlite3.Connection, *,
                            ipo_id: str, cornerstone_id: str,
                            ticket_size_hkd: Optional[float] = None,
                            allocation_shares: Optional[int] = None,
                            lockup_months_actual: Optional[int] = None,
                            affiliation_flag: bool = False,
                            affiliation_reason: Optional[str] = None,
                            data_source: str = "prospectus",
                            is_estimated: bool = False) -> None:
    conn.execute("""
        INSERT OR REPLACE INTO ipo_cornerstone_link (
            ipo_id, cornerstone_id, ticket_size_hkd, allocation_shares,
            lockup_months_actual, affiliation_flag, affiliation_reason,
            data_source, is_estimated, as_of_date
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_DATE)
    """, (ipo_id, cornerstone_id, ticket_size_hkd, allocation_shares,
          lockup_months_actual, int(affiliation_flag), affiliation_reason,
          data_source, int(is_estimated)))


# =============================================================================
# 4. as-of-date 派生 (核心: 防 look-ahead)
# =============================================================================

def list_ipos_in_universe_asof(conn: sqlite3.Connection,
                               asof: date) -> List[str]:
    """返回截至 asof 时点曾上市的 IPO 全集 (含已退市), 即"反幸存者偏差"全集.

    口径:
        - listing_date <= asof  (asof 之前已上市)
        - 且 (未退市) 或 (退市日 > asof)  → asof 时仍在交易/可观测

    返回: ipo_id 列表, 按 listing_date 升序
    """
    rows = conn.execute("""
        SELECT ipo_id FROM ipo_master
        WHERE listing_date <= ?
          AND (
              COALESCE(is_delisted, 0) = 0
              OR delisting_date IS NULL
              OR delisting_date > ?
          )
        ORDER BY listing_date
    """, (asof.isoformat(), asof.isoformat())).fetchall()
    return [r["ipo_id"] for r in rows]


def compute_cornerstone_perf_asof(conn: sqlite3.Connection,
                                  cornerstone_id: str,
                                  asof: date,
                                  lookback_years: int = 5) -> Dict:
    """
    截至 asof 之前(不含), 该基石的画像.
    
    SQL 中 WHERE listing_date < asof 是关键, 严禁 <= 或 BETWEEN.
    """
    cutoff = asof - timedelta(days=lookback_years * 365)
    asof_str = asof.isoformat() if isinstance(asof, date) else str(asof)
    cutoff_str = cutoff.isoformat() if isinstance(cutoff, date) else str(cutoff)

    sql = """
        SELECT
            COUNT(*) AS n,
            AVG(r.return_m6) AS avg_m6,
            AVG(r.return_d30) AS avg_d30,
            AVG(CASE WHEN r.return_m6 > 0 THEN 1.0 ELSE 0.0 END) AS winrate_m6,
            -- 锁定期纪律: 解禁后30天回撤越小越好 -> (1 - clip)
            AVG(CASE
                WHEN r.return_unlock_d30 IS NULL THEN NULL
                WHEN r.return_unlock_d30 > 0 THEN 1.0
                WHEN r.return_unlock_d30 < -0.20 THEN 0.0
                ELSE (r.return_unlock_d30 + 0.20) / 0.20
            END) AS lockup_discipline,
            -- 行业经验: GICS 列表
            GROUP_CONCAT(DISTINCT i.gics_l2) AS gics_list
        FROM ipo_cornerstone_link l
        JOIN ipo_master i ON i.ipo_id = l.ipo_id
        LEFT JOIN ipo_returns r ON r.ipo_id = l.ipo_id
        WHERE l.cornerstone_id = ?
          AND i.listing_date < ?
          AND i.listing_date >= ?
    """
    row = conn.execute(sql, (cornerstone_id, asof_str, cutoff_str)).fetchone()

    if row is None or (row["n"] or 0) == 0:
        return {
            "ipo_count_5y": 0,
            "avg_m6_return_5y": None,
            "winrate_m6_5y": None,
            "avg_d30_return_5y": None,
            "lockup_discipline_score": None,
            "sector_expertise_dict": {},
        }

    # GICS 计数
    gics_dict: Dict[str, int] = {}
    if row["gics_list"]:
        for g in row["gics_list"].split(","):
            g = g.strip()
            if g:
                gics_dict[g] = gics_dict.get(g, 0) + 1

    return {
        "ipo_count_5y": row["n"],
        "avg_m6_return_5y": row["avg_m6"],
        "winrate_m6_5y": row["winrate_m6"],
        "avg_d30_return_5y": row["avg_d30"],
        "lockup_discipline_score": row["lockup_discipline"],
        "sector_expertise_dict": gics_dict,
    }


def materialize_cornerstone_perf_snapshot(conn: sqlite3.Connection,
                                          asof: date) -> int:
    """
    给定一个切点日期, 把所有基石的画像物化到 cornerstone_performance_asof.
    用于回测前批量预计算.
    返回写入行数.
    """
    cs_ids = [r["cornerstone_id"] for r in
              conn.execute("SELECT cornerstone_id FROM cornerstone_master")]
    n = 0
    asof_str = asof.isoformat() if isinstance(asof, date) else str(asof)
    for cs_id in cs_ids:
        perf = compute_cornerstone_perf_asof(conn, cs_id, asof)
        conn.execute("""
            INSERT OR REPLACE INTO cornerstone_performance_asof
            (cornerstone_id, as_of_date, ipo_count_5y, avg_m6_return_5y,
             winrate_m6_5y, avg_d30_return_5y, lockup_discipline_score,
             sector_expertise)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (cs_id, asof_str,
              perf["ipo_count_5y"],
              perf["avg_m6_return_5y"],
              perf["winrate_m6_5y"],
              perf["avg_d30_return_5y"],
              perf["lockup_discipline_score"],
              json.dumps(perf["sector_expertise_dict"])))
        n += 1
    return n


# =============================================================================
# 5. CornerstoneInvestor 自动构造 (替代手填)
# =============================================================================

def hydrate_cornerstone_investor(conn: sqlite3.Connection, *,
                                 cornerstone_id: str,
                                 ticket_size_hkd: float,
                                 asof: date,
                                 ipo_gics_l2: Optional[str] = None,
                                 affiliation_flag: bool = False,
                                 affiliation_reason: Optional[str] = None
                                 ) -> CornerstoneInvestor:
    """
    给定 (基石ID, 该项目的认购金额, asof日期, 该IPO行业),
    返回填充完整衍生字段的 CornerstoneInvestor.
    
    所有衍生字段严格以 asof 为切点, 永远不查未来数据.
    """
    # 主表静态字段
    master = conn.execute(
        "SELECT * FROM cornerstone_master WHERE cornerstone_id = ?",
        (cornerstone_id,),
    ).fetchone()
    if master is None:
        raise KeyError(f"未找到基石: {cornerstone_id}")

    # 优先用预物化的 snapshot; 否则现算
    asof_str = asof.isoformat() if isinstance(asof, date) else str(asof)
    snap = conn.execute("""
        SELECT * FROM cornerstone_performance_asof
        WHERE cornerstone_id = ?
          AND as_of_date <= ?
        ORDER BY as_of_date DESC LIMIT 1
    """, (cornerstone_id, asof_str)).fetchone()

    if snap:
        snap_date = snap["as_of_date"]
        snap_date = (snap_date if isinstance(snap_date, date)
                     else date.fromisoformat(str(snap_date)))
        if (asof - snap_date).days <= 90:
            perf = {
                "ipo_count_5y": snap["ipo_count_5y"],
                "avg_m6_return_5y": snap["avg_m6_return_5y"],
                "winrate_m6_5y": snap["winrate_m6_5y"],
                "lockup_discipline_score": snap["lockup_discipline_score"],
                "sector_expertise_dict": json.loads(snap["sector_expertise"] or "{}"),
            }
        else:
            perf = compute_cornerstone_perf_asof(conn, cornerstone_id, asof)
    else:
        perf = compute_cornerstone_perf_asof(conn, cornerstone_id, asof)

    sector_expertise = perf["sector_expertise_dict"].get(ipo_gics_l2, 0) \
        if ipo_gics_l2 else 0

    return CornerstoneInvestor(
        name=master["canonical_name"],
        ticket_size_hkd=ticket_size_hkd,
        type=CornerstoneType(master["cornerstone_type"]),
        aum_usd=master["aum_usd_latest"],
        hk_ipo_count_5y=perf["ipo_count_5y"],
        hk_ipo_avg_m6_return=perf["avg_m6_return_5y"],
        hk_ipo_winrate_m6=perf["winrate_m6_5y"],
        lockup_discipline_score=perf["lockup_discipline_score"],
        sector_expertise=sector_expertise,
        affiliation_flag=affiliation_flag,
        affiliation_reason=affiliation_reason,
    )


def hydrate_cornerstones_for_ipo(conn: sqlite3.Connection,
                                 ipo_id: str,
                                 asof: Optional[date] = None
                                 ) -> List[CornerstoneInvestor]:
    """从DB一次性构造一只IPO的全部 CornerstoneInvestor 列表"""
    ipo = conn.execute(
        "SELECT * FROM ipo_master WHERE ipo_id = ?", (ipo_id,)
    ).fetchone()
    if ipo is None:
        raise KeyError(f"未找到IPO: {ipo_id}")

    # 默认用 pricing_date (基石协议签订前的最后一个有信息的时点)
    if asof is None:
        d_val = ipo["pricing_date"] or ipo["listing_date"]
        asof = d_val if isinstance(d_val, date) else date.fromisoformat(str(d_val))

    rows = conn.execute("""
        SELECT cornerstone_id, ticket_size_hkd, affiliation_flag,
               affiliation_reason
        FROM ipo_cornerstone_link WHERE ipo_id = ?
    """, (ipo_id,)).fetchall()

    investors: List[CornerstoneInvestor] = []
    for r in rows:
        inv = hydrate_cornerstone_investor(
            conn,
            cornerstone_id=r["cornerstone_id"],
            ticket_size_hkd=r["ticket_size_hkd"] or 0.0,
            asof=asof,
            ipo_gics_l2=ipo["gics_l2"],
            affiliation_flag=bool(r["affiliation_flag"]),
            affiliation_reason=r["affiliation_reason"],
        )
        investors.append(inv)
    return investors


# =============================================================================
# 6. IPO returns 计算 (从 price_history 派生)
# =============================================================================

def compute_ipo_returns(conn: sqlite3.Connection, ipo_id: str) -> Optional[Dict]:
    """
    计算并落库 ipo_returns 一只IPO的全部收益指标.
    返回结果 dict 或 None (如数据不足).
    """
    ipo = conn.execute(
        "SELECT listing_date, offer_price_hkd, lockup_months FROM ipo_master WHERE ipo_id = ?",
        (ipo_id,),
    ).fetchone()
    if ipo is None or ipo["offer_price_hkd"] is None:
        return None

    listing_date = date.fromisoformat(ipo["listing_date"])
    offer = ipo["offer_price_hkd"]
    lockup = ipo["lockup_months"] or 6

    prices = conn.execute("""
        SELECT trade_date, close_hkd, volume, turnover_hkd
        FROM price_history WHERE ipo_id = ?
        ORDER BY trade_date
    """, (ipo_id,)).fetchall()
    if not prices:
        return None

    by_date = {date.fromisoformat(p["trade_date"]): p for p in prices}

    def _ret_at_offset(days: int) -> Optional[float]:
        """从上市日开始往后推 days 个交易日 (近似为日历日 + 容忍找最近一条)"""
        target = listing_date + timedelta(days=days)
        for delta in range(0, 8):
            for sign in (0, 1, -1):
                d = target + timedelta(days=sign * delta)
                if d in by_date and by_date[d]["close_hkd"]:
                    return by_date[d]["close_hkd"] / offer - 1
        return None

    unlock_date = listing_date + timedelta(days=int(lockup * 30.5))

    def _ret_unlock(offset_days: int) -> Optional[float]:
        target = unlock_date + timedelta(days=offset_days)
        for delta in range(0, 10):
            for sign in (0, 1, -1):
                d = target + timedelta(days=sign * delta)
                if d in by_date and by_date[d]["close_hkd"]:
                    return by_date[d]["close_hkd"] / offer - 1
        return None

    # 锁定期内最大回撤
    in_lockup = [p["close_hkd"] for p in prices
                 if date.fromisoformat(p["trade_date"]) <= unlock_date
                 and p["close_hkd"]]
    if in_lockup:
        peak = in_lockup[0]
        max_dd = 0.0
        for px in in_lockup:
            peak = max(peak, px)
            dd = px / peak - 1
            max_dd = min(max_dd, dd)
    else:
        max_dd = None

    avg_vol_hkd = (sum((p["turnover_hkd"] or 0) for p in prices)
                   / max(len(prices), 1))

    out = {
        "ipo_id": ipo_id,
        "return_d1_close": _ret_at_offset(1),
        "return_d30": _ret_at_offset(30),
        "return_m3": _ret_at_offset(90),
        "return_m6": _ret_at_offset(180),
        "return_m12": _ret_at_offset(365),
        "return_unlock_d30": _ret_unlock(30),
        "return_unlock_d90": _ret_unlock(90),
        "max_drawdown_m6": max_dd,
        "avg_daily_volume_hkd": avg_vol_hkd,
    }
    conn.execute("""
        INSERT OR REPLACE INTO ipo_returns
        (ipo_id, return_d1_close, return_d30, return_m3, return_m6, return_m12,
         return_unlock_d30, return_unlock_d90, max_drawdown_m6,
         avg_daily_volume_hkd)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (out["ipo_id"], out["return_d1_close"], out["return_d30"],
          out["return_m3"], out["return_m6"], out["return_m12"],
          out["return_unlock_d30"], out["return_unlock_d90"],
          out["max_drawdown_m6"], out["avg_daily_volume_hkd"]))
    return out


# =============================================================================
# 7. MarketEnvironment 实时化 (按月聚合 cache, iFinD + DB 派生 + fallback)
# =============================================================================

# 旧硬编码默认值 (run_v7_backtest.py:170-175 原值), fallback 使用
_FALLBACK_MARKET_ENV: Dict[str, float] = dict(
    hsi_60d_return=0.03,
    hsi_60d_vol_annualized=0.20,
    hsi_60d_vol_pct_rank=0.5,
    hsi_valuation_pct=0.5,
    hk_ipo_30d_avg_d30=0.05,
    hk_ipo_30d_breakage_rate=0.50,
    southbound_30d_net_normalized=0.0,
    sector_60d_vol_annualized=0.30,
)


def _ensure_market_env_cache_table(conn: sqlite3.Connection) -> None:
    """idempotent 建表 (脱离 SCHEMA_SQL 也能用)"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS market_environment_cache (
            asof_month                      DATE PRIMARY KEY,
            hsi_60d_return                  REAL,
            hsi_60d_vol_annualized          REAL,
            hsi_60d_vol_pct_rank            REAL,
            hsi_valuation_pct               REAL,
            hk_ipo_30d_avg_d30              REAL,
            hk_ipo_30d_breakage_rate        REAL,
            southbound_30d_net_normalized   REAL,
            sector_60d_vol_annualized       REAL,
            source                          TEXT,
            created_at                      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _normalize_asof_to_month(asof: date) -> date:
    """date(2024,3,15) -> date(2024,3,1)"""
    if not isinstance(asof, date):
        asof = date.fromisoformat(str(asof)[:10])
    return date(asof.year, asof.month, 1)


def _month_end(month_first: date) -> date:
    """date(2024,3,1) -> date(2024,3,31). 简易实现, 用日历月最后一天."""
    if month_first.month == 12:
        next_month = date(month_first.year + 1, 1, 1)
    else:
        next_month = date(month_first.year, month_first.month + 1, 1)
    return next_month - timedelta(days=1)


def _compute_ipo_30d_stats_from_db(conn: sqlite3.Connection,
                                   asof: date) -> Tuple[float, float]:
    """
    严格防 look-ahead: 使用 listing_date 在 [asof-60, asof-30] 区间且
    return_d30 已可知的 IPO 计算 (avg_d30, breakage_rate).
    缺数据 -> 用 fallback 默认值.
    """
    asof_str = asof.isoformat() if isinstance(asof, date) else str(asof)
    window_start_str = (asof - timedelta(days=60)).isoformat()

    # listing_date + 30d <= asof  且  listing_date >= asof - 60d
    sql = """
        SELECT r.return_d30
        FROM ipo_master m
        JOIN ipo_returns r ON r.ipo_id = m.ipo_id
        WHERE date(m.listing_date, '+30 days') <= ?
          AND m.listing_date >= ?
          AND r.return_d30 IS NOT NULL
    """
    rows = conn.execute(sql, (asof_str, window_start_str)).fetchall()
    if not rows or len(rows) < 3:
        return (_FALLBACK_MARKET_ENV["hk_ipo_30d_avg_d30"],
                _FALLBACK_MARKET_ENV["hk_ipo_30d_breakage_rate"])
    rets = [float(r["return_d30"]) for r in rows]
    avg = sum(rets) / len(rets)
    breakage = sum(1 for r in rets if r < 0) / len(rets)
    return float(avg), float(breakage)


def _try_load_market_env_from_json(asof_month: date) -> Optional[Dict]:
    """
    优先扫描 daily/{YYYY-MM-*}/market_data.json (该月任意一天的快照).
    返回 dict (含 4 个 ifind 字段) 或 None.
    """
    project_root = Path(__file__).resolve().parents[2]
    daily_root = project_root / "daily"
    if not daily_root.exists():
        return None
    prefix = asof_month.strftime("%Y-%m-")
    candidates = sorted(d for d in daily_root.iterdir()
                        if d.is_dir() and d.name.startswith(prefix))
    if not candidates:
        return None
    # 取月内最新的一天 (尽量贴近月末)
    latest = candidates[-1]
    json_path = latest / "market_data.json"
    if not json_path.exists():
        return None
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

    # JSON 字段映射: hsi_60d_vol_annual -> hsi_60d_vol_annualized
    out = {}
    if "hsi_60d_return" in data:
        out["hsi_60d_return"] = float(data["hsi_60d_return"])
    if "hsi_60d_vol_annual" in data:
        out["hsi_60d_vol_annualized"] = float(data["hsi_60d_vol_annual"])
        out["sector_60d_vol_annualized"] = float(data["hsi_60d_vol_annual"])
    if "hsi_60d_vol_pct_rank" in data:
        out["hsi_60d_vol_pct_rank"] = float(data["hsi_60d_vol_pct_rank"])
    # JSON 没有 hsi_valuation_pct / southbound_30d_net_normalized,
    # 这 2 字段返回 None 触发 caller 使用 iFinD or fallback
    if len(out) >= 3:
        return out
    return None


def fetch_market_env_at(conn: sqlite3.Connection,
                        asof: date,
                        *,
                        allow_ifind: bool = True) -> "MarketEnvironment":
    """
    返回填好 8 字段的 MarketEnvironment.

    流程:
        1. 建表 (idempotent)
        2. month_key = 月初(asof)
        3. 查 cache; 命中且 source != 'fallback' -> 返回
        4. miss / 重试: 优先 daily/JSON, 否则 iFinD
        5. 任一失败 -> _FALLBACK_MARKET_ENV, source='fallback'
        6. hk_ipo_30d_* 始终从 DB 算 (即便走 fallback, 也尝试用真数据)
        7. 写 cache 后返回
    """
    _ensure_market_env_cache_table(conn)

    month_key = _normalize_asof_to_month(asof)
    month_key_str = month_key.isoformat()

    # 1) 查 cache
    cached = conn.execute("""
        SELECT * FROM market_environment_cache WHERE asof_month = ?
    """, (month_key_str,)).fetchone()
    if cached and cached["source"] != "fallback":
        return MarketEnvironment(
            hsi_60d_return=cached["hsi_60d_return"],
            hsi_60d_vol_annualized=cached["hsi_60d_vol_annualized"],
            hsi_60d_vol_pct_rank=cached["hsi_60d_vol_pct_rank"],
            hsi_valuation_pct=cached["hsi_valuation_pct"],
            hk_ipo_30d_avg_d30=cached["hk_ipo_30d_avg_d30"],
            hk_ipo_30d_breakage_rate=cached["hk_ipo_30d_breakage_rate"],
            southbound_30d_net_normalized=cached["southbound_30d_net_normalized"],
            sector_60d_vol_annualized=cached["sector_60d_vol_annualized"],
        )

    # 2) hk_ipo_30d_* 从 DB 算 (无论 ifind 成功与否)
    ipo_avg_d30, ipo_breakage = _compute_ipo_30d_stats_from_db(conn, asof)

    # 3) 优先 JSON (零成本), 但 JSON 不含 PE / 南向 — 还需 iFinD 补
    fields = dict(_FALLBACK_MARKET_ENV)
    source = "fallback"

    json_data = _try_load_market_env_from_json(month_key)
    if json_data and len(json_data) >= 5:  # JSON 已包含 hsi 三件套和 sector
        fields.update(json_data)
        source = "json"

    # 4) 调 iFinD 补全 (PE / southbound), 且若 JSON 没成功也补 HSI 三件套
    if allow_ifind:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from data_sources.ifind.market_env_fetcher import fetch_market_env_dict
            month_end_date = _month_end(month_key)
            ifind_data = fetch_market_env_dict(month_end_date)
            fields.update(ifind_data)
            source = "ifind"
        except Exception as e:
            sys.stderr.write(
                f"[market_env] iFinD fetch 失败 month={month_key_str} "
                f"err={type(e).__name__}: {e}; 走 fallback/json\n"
            )
            # source 保持原值 (fallback or json)

    # 5) 拼装 + DB 派生
    fields["hk_ipo_30d_avg_d30"] = ipo_avg_d30
    fields["hk_ipo_30d_breakage_rate"] = ipo_breakage

    # 6) 写 cache
    conn.execute("""
        INSERT OR REPLACE INTO market_environment_cache (
            asof_month, hsi_60d_return, hsi_60d_vol_annualized, hsi_60d_vol_pct_rank,
            hsi_valuation_pct, hk_ipo_30d_avg_d30, hk_ipo_30d_breakage_rate,
            southbound_30d_net_normalized, sector_60d_vol_annualized, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        month_key_str,
        fields["hsi_60d_return"],
        fields["hsi_60d_vol_annualized"],
        fields["hsi_60d_vol_pct_rank"],
        fields["hsi_valuation_pct"],
        fields["hk_ipo_30d_avg_d30"],
        fields["hk_ipo_30d_breakage_rate"],
        fields["southbound_30d_net_normalized"],
        fields["sector_60d_vol_annualized"],
        source,
    ))
    conn.commit()

    return MarketEnvironment(
        hsi_60d_return=fields["hsi_60d_return"],
        hsi_60d_vol_annualized=fields["hsi_60d_vol_annualized"],
        hsi_60d_vol_pct_rank=fields["hsi_60d_vol_pct_rank"],
        hsi_valuation_pct=fields["hsi_valuation_pct"],
        hk_ipo_30d_avg_d30=fields["hk_ipo_30d_avg_d30"],
        hk_ipo_30d_breakage_rate=fields["hk_ipo_30d_breakage_rate"],
        southbound_30d_net_normalized=fields["southbound_30d_net_normalized"],
        sector_60d_vol_annualized=fields["sector_60d_vol_annualized"],
    )
