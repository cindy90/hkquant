"""
generate_sentiment_report.py — 港股情绪日报生成器 (Kimi K2.6)

读取 daily/{TODAY}/ 下的 4 个数据文件 + watchlist.json + 过去 7 天历史报告,
调用 Kimi (Moonshot 兼容 OpenAI SDK) 生成 Markdown 情绪日报, 写入 reports/{TODAY}.md.

用法:
    python scripts/generate_sentiment_report.py
    python scripts/generate_sentiment_report.py --date 2026-05-08
    python scripts/generate_sentiment_report.py --no-llm           # 跳过 Kimi, 只用硬指标
    python scripts/generate_sentiment_report.py --skip-curation    # 跳过新闻策展 (默认会做)
    python scripts/generate_sentiment_report.py --min-priority 8   # 报告只用 priority>=8 的策展新闻

主流程:
    1) 加载 daily/{TODAY}/ 下 4 个 JSON + watchlist + 过去 7 天历史报告
    2) compute_snapshot 算硬指标快照
    3) (默认) curate_news_with_kimi: 把今日新闻喂 Kimi, 去重 + 1-10 打分
       → daily/{TODAY}/news_curated.json (按 priority 降序)
       → 报告生成时只保留 priority>=N (默认 6) 的条目, 节省 token, 信号更纯
    4) call_kimi 生成 Markdown 报告 → reports/{TODAY}.md

环境变量 (从 .env 读, 项目根 / src/data_sources/ifind/.env 都搜):
    KIMI_API_KEY   (必填)
    KIMI_BASE_URL  (默认 https://api.moonshot.cn/v1)
    KIMI_MODEL     (默认 kimi-k2.6)
"""
from __future__ import annotations

import sys
import os
import json
import argparse
import re
import statistics
from pathlib import Path
from datetime import datetime, date, timedelta
from typing import Any

# ---------- Windows 控制台 UTF-8 ----------
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]


# ============================================================================
# .env 加载: 依次尝试 项目根/.env、src/data_sources/ifind/.env
# ============================================================================
def _load_env() -> None:
    candidates = [
        PROJECT_ROOT / ".env",
        PROJECT_ROOT / "src" / "data_sources" / "ifind" / ".env",
    ]
    for env_path in candidates:
        if not env_path.exists():
            continue
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value


# ============================================================================
# 数据加载
# ============================================================================
def _load_json(path: Path) -> Any:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"  ! 读取 {path.name} 失败: {exc}", file=sys.stderr)
        return None


def load_today_inputs(today: str) -> dict[str, Any]:
    daily_dir = PROJECT_ROOT / "daily" / today
    return {
        "market_data": _load_json(daily_dir / "market_data.json"),
        "news_today": _load_json(daily_dir / "news_today.json"),
        "themes": _load_json(daily_dir / "themes.json"),
        "ipo_recent": _load_json(daily_dir / "ipo_recent.json"),
    }


def load_watchlist() -> Any:
    return _load_json(PROJECT_ROOT / "watchlist.json")


def load_recent_reports(today: str, n_days: int = 7) -> list[dict[str, str]]:
    """读取过去 n 天的报告 (不含今天)."""
    reports_dir = PROJECT_ROOT / "reports"
    if not reports_dir.exists():
        return []
    today_dt = datetime.strptime(today, "%Y-%m-%d").date()
    out: list[dict[str, str]] = []
    for i in range(1, n_days + 1):
        d = today_dt - timedelta(days=i)
        f = reports_dir / f"{d.isoformat()}.md"
        if f.exists():
            try:
                out.append({"date": d.isoformat(), "content": f.read_text(encoding="utf-8")})
            except Exception:
                pass
    return out


# ============================================================================
# 硬指标快照计算 (LLM 失败时的回退路径)
# ============================================================================
def compute_snapshot(today_inputs: dict[str, Any],
                     recent_reports: list[dict[str, str]]) -> dict[str, Any]:
    """从原始 JSON 提取 5 个核心硬指标 + 主题排行 + IPO 30d 平均首日涨幅."""
    md = today_inputs.get("market_data") or {}
    themes = (today_inputs.get("themes") or {}).get("themes") or {}
    ipos = (today_inputs.get("ipo_recent") or {}).get("ipos") or []

    # IPO 30d 平均首日涨幅
    d1_returns = [x.get("d1_return") for x in ipos if isinstance(x.get("d1_return"), (int, float))]
    ipo_d1_avg = statistics.mean(d1_returns) if d1_returns else None

    # 南向单日净买入 (亿港元, p04277_f004); 30d 累计另读 southbound_30d
    sb_today_yi = (md.get("southbound_today") or {}).get("net_inflow_hkd_yi")
    sb_30d_yi = (md.get("southbound_30d") or {}).get("cumulative_net_inflow_hkd_yi")

    snapshot = {
        "as_of": md.get("as_of"),
        "hsi_60d_return": md.get("hsi_60d_return"),
        "hsi_60d_vol_pct_rank": md.get("hsi_60d_vol_pct_rank"),
        "southbound_today_hkd_yi": sb_today_yi,
        "southbound_30d_hkd_yi": sb_30d_yi,
        "ipo_30d_avg_d1_return": ipo_d1_avg,
        "regime_score": md.get("regime_score"),
    }

    # 主题排行 (按 5d 涨幅)
    theme_rows = []
    for k, v in themes.items():
        theme_rows.append({
            "key": k,
            "label": v.get("label", k),
            "ret_1d": v.get("ret_1d"),
            "ret_5d": v.get("ret_5d"),
            "ret_20d": v.get("ret_20d"),
        })
    theme_rows.sort(key=lambda r: (r.get("ret_5d") if r.get("ret_5d") is not None else -1e9), reverse=True)

    # 7 天历史 regime / 情绪分 (用正则从 markdown 抓; 失败则空)
    history = []
    for r in recent_reports:
        body = r["content"]
        m_regime = re.search(r"regime[_\s]*score[^\n\-]*[:：]?\s*(-?\d+\.?\d*)", body, flags=re.I)
        m_score = re.search(r"情绪评分[^\n]*?(\d+(?:\.\d+)?)\s*/\s*10", body)
        history.append({
            "date": r["date"],
            "regime_score": float(m_regime.group(1)) if m_regime else None,
            "sentiment_score": float(m_score.group(1)) if m_score else None,
        })

    return {
        "snapshot": snapshot,
        "themes_ranked": theme_rows,
        "history": history,
        "ipo_count_30d": len(ipos),
    }


# ============================================================================
# 简化报告 (LLM 失败时回退)
# ============================================================================
def _fmt_pct(x: Any) -> str:
    if x is None: return "n/a"
    try: return f"{float(x) * 100:+.2f}%"
    except Exception: return "n/a"


def _fmt_num(x: Any, fmt: str = ".4f") -> str:
    if x is None: return "n/a"
    try: return format(float(x), fmt)
    except Exception: return "n/a"


def build_fallback_report(today: str,
                          inputs: dict[str, Any],
                          digest: dict[str, Any],
                          watchlist: Any,
                          err: str | None = None) -> str:
    s = digest["snapshot"]
    regime = s.get("regime_score")
    # 情绪评分粗算: 0-10, 以 regime_score 为主轴, [-0.3, +0.3] -> [0, 10]
    if regime is None:
        sent = 5.0
    else:
        sent = max(0.0, min(10.0, (float(regime) + 0.3) / 0.6 * 10))

    lines: list[str] = []
    lines.append(f"# 港股情绪日报 {today}\n")
    if err:
        lines.append(f"> ⚠️ Kimi API 调用失败 ({err}), 本报告为硬指标回退版.\n")

    lines.append("## 1. 整体情绪评分 (0-10)\n")
    lines.append(f"**{sent:.1f} / 10** — 基于 regime_score={_fmt_num(regime)} 线性映射, 仅供参考.\n")

    lines.append("## 2. 4 个硬指标快照\n")
    lines.append("| 指标 | 今日值 | 评估 |")
    lines.append("|---|---|---|")
    r60 = s.get("hsi_60d_return")
    lines.append(f"| HSI 60d return | {_fmt_pct(r60)} | "
                 f"{'好' if (r60 or 0) > 0.05 else '差' if (r60 or 0) < -0.05 else '中'} |")
    vp = s.get("hsi_60d_vol_pct_rank")
    lines.append(f"| HSI 60d vol pct rank | {_fmt_num(vp, '.2%') if vp is not None else 'n/a'} | "
                 f"{'差' if (vp or 0) > 0.7 else '好' if (vp or 0) < 0.3 else '中'} |")
    sb = s.get("southbound_today_hkd_yi")
    sb_30 = s.get("southbound_30d_hkd_yi")
    lines.append(f"| 南向资金 当日净买入 (亿港元) | {_fmt_num(sb, ',.2f')} | "
                 f"{'好' if (sb or 0) > 0 else '差'} |")
    if sb_30 is not None:
        lines.append(f"| 南向资金 30d 累计 (亿港元) | {_fmt_num(sb_30, ',.2f')} | "
                     f"{'好' if (sb_30 or 0) > 0 else '差'} |")
    ipo_d1 = s.get("ipo_30d_avg_d1_return")
    lines.append(f"| IPO 30d 平均首日涨幅 | {_fmt_pct(ipo_d1)} | "
                 f"{'好' if (ipo_d1 or 0) > 0.05 else '差' if (ipo_d1 or 0) < 0 else '中'} |")
    lines.append(f"| **regime_score** | **{_fmt_num(regime)}** | "
                 f"**{'好' if (regime or 0) > 0 else '差'}** |\n")

    lines.append("## 3. 主题热度排行 (按 5d 涨幅)\n")
    lines.append("| 主题 | 1d | 5d | 20d |")
    lines.append("|---|---|---|---|")
    for t in digest["themes_ranked"]:
        lines.append(f"| {t['label']} | {_fmt_pct(t['ret_1d'])} | "
                     f"{_fmt_pct(t['ret_5d'])} | {_fmt_pct(t['ret_20d'])} |")
    lines.append("")

    lines.append("## 4. 关注公司情绪\n")
    if watchlist:
        lines.append(f"watchlist 共 {len(watchlist) if isinstance(watchlist, list) else '?'} 项, "
                     f"LLM 不可用, 略.\n")
    else:
        lines.append("未配置 watchlist.json, 跳过.\n")

    lines.append("## 5. 异常信号\n")
    lines.append("LLM 不可用, 略.\n")

    lines.append("## 6. 今日决策建议\n")
    if regime is not None and float(regime) < 0:
        lines.append(f"- regime_score={regime:.3f} < 0, **建议暂停所有基石活动**.\n")
    else:
        lines.append("- regime_score ≥ 0, 可正常评估基石机会.\n")
    lines.append("- LLM 不可用, 仅基于硬指标; 建议人工复核主题与公司层面.\n")

    return "\n".join(lines)


# ============================================================================
# Kimi 调用
# ============================================================================
SYSTEM_PROMPT = """你是港股 IPO 基石投资策略师, 服务于一家专注港股新经济基石轮的投资机构.
你的任务: 基于今日硬指标 (HSI 60d return / vol pct rank / 南向资金 / IPO 30d 首日表现 / regime_score)
+ 主题板块涨跌 + 关注公司清单 + 过去 7 天历史报告, 输出一份结构化的 Markdown 情绪日报.

写作要求:
1. 中文, 专业, 简洁, 不堆砌形容词, 不要使用 "本报告认为" "综上所述" 等空话.
2. 所有结论必须挂钩到具体数字; 禁止纯情绪化叙述.
3. 决策建议必须基于硬指标 — 尤其是 regime_score:
   - regime_score < 0  → 倾向 "暂停 / 推迟基石活动"
   - regime_score 在 [0, 0.1] → 谨慎选择, 仅做高确信度大票
   - regime_score > 0.1 → 可正常推进
4. 通过对比过去 7 天, 指出"趋势变化" (例如 regime 连续上行 / 主题热度从科技切到医药), 不要孤立看今天.
5. 若历史报告缺失字段, 跳过对比即可, 不要编造.
6. 严格按照用户给定的 6 节结构输出, 不要增减小节, 不要前后客套.
7. 异常信号节: 任何指标超过历史 ±2σ 或与硬指标背离都要点出, 没有就写"无显著异常".
8. 报告首行必须是 `# 港股情绪日报 YYYY-MM-DD` (一级标题, 必须带 `# ` 井号), 不要省略.
9. 第 1 节"整体情绪评分"必须以独立一行 `**X.X / 10**` 的粗体格式开头 (X 是 0-10 之间一位小数), 然后换行写解释; 不要把数字写在列表序号里 (如 "4. ..."), 不要省略 "/ 10".
"""


NEWS_CURATION_SYSTEM_PROMPT = """你是港股 IPO 基石策略助手, 帮我筛掉无关噪音, 留下真正重要的信息.

噪音例子: 个股技术分析、广告软文、与 watchlist 无关的小盘股动态、纯财经导读.
信号例子: 监管政策、宏观流动性 (Fed/PBoC/汇率)、watchlist 公司动态、主题板块趋势、地缘政治.

任务:
1. 去重: 同一事件多源报道合并为一条, 用最完整的标题做代表.
2. 重要性打分 1-10, 基于 "对港股 IPO 基石策略的影响":
   - watchlist 提及的公司动态                       → priority = 10
   - 宏观重大事件 (Fed 决议 / 地缘政治 / 中国货币政策) → priority = 9-10
   - 港股监管 / 流动性 / 主题板块趋势                 → priority = 6-8
   - 行业一般性资讯 (相关但不直接影响基石窗口)        → priority = 3-5
   - 个股技术分析 / 广告 / 与策略无关的小盘股噪音    → priority = 1-2

输出: 严格的 JSON 数组 (不要任何前后说明文字, 不要包代码块标记), 按 priority 降序排列.
每条记录字段:
  priority      (int 1-10)
  headline      (str, 合并后的代表性标题, ≤ 80 字)
  rationale     (str, 1 句话说明为何给这个分数, ≤ 60 字)
  source_count  (int, 同事件来源数, 单条独立的填 1)
  category      (str, 取值: "watchlist" / "macro" / "regulatory" / "theme" / "general" / "noise")
  related_codes (list[str], 涉及的港股代码 4-5 位 + .HK, 没有就空数组)
  sample_url    (str, 任一来源原始链接, 没有就空串)
"""


USER_PROMPT_TEMPLATE = """以下是今日 ({today}) 的全部输入数据 (JSON), 以及过去 7 天的历史报告 (Markdown).
请输出今日情绪日报.

# 报告必须严格按以下结构 (照抄标题, 不要改名)
# 港股情绪日报 {today}

## 1. 整体情绪评分 (0-10)
[一个数字 + 1-2 句解释, 必须显式挂钩 regime_score]

## 2. 4 个硬指标快照
[表格: 指标 | 今日值 | 7日变化 | 评估 (好/中/差)]
必须包含且只包含以下 5 行: HSI 60d return / HSI 60d vol pct rank / 南向资金 30d 累计 / 港股 IPO 30d 平均首日涨幅 / **regime_score** (加粗).
"7日变化" 字段从 history 推算, 没有就写 n/a.

## 3. 主题热度排行
[表格: 主题 | 今日 (1d) | 5日 | 20日 | 评估]
按 5日涨幅降序.

## 4. 关注公司情绪 (基于 watchlist)
对 watchlist 中每个 active_deal:
- 它对应的主要主题当前热度 (引用第 3 节主题数据)
- 估值环境是否友好 (主题 5d 为正 + IPO 30d d1 > 0 → 友好)
- 是否建议推迟 / 加快进场 (推迟 / 持平 / 加快)
若 watchlist 为空或 null, 该节写 "未配置关注公司, 跳过.".

## 5. 异常信号
- 单日大幅异常 (任何指标超过历史 ±2σ)
- 主题情绪与硬指标背离 (例: 主题大涨但 regime < 0)
若无异常, 明确写 "无显著异常.".

## 6. 今日决策建议 (≤ 3 句话, 数字驱动)
- 是否暂停所有基石活动 (规则: regime_score < 0 → 暂停)
- 是否对特定主题加仓/减仓
- 关键时点提示 (基于 watchlist 中即将定价的公司, 没有则省略此条)


===== 数据 =====
[market_data.json]
{market_data}

[themes.json]
{themes}

[ipo_recent.json]
{ipo_recent}

[news_today.json]
{news_today}

[watchlist.json]
{watchlist}

[recent_reports]  (过去 7 天, 由近到远)
{recent_reports}

===== 预算 =====
你的输出 ≤ 5000 tokens. 直接输出 Markdown, 不要包 ```markdown``` 代码块.
"""


def _truncate_for_budget(s: str, max_chars: int) -> str:
    if len(s) <= max_chars:
        return s
    return s[:max_chars] + f"\n...[truncated, original {len(s)} chars]"


def call_kimi(today: str,
              inputs: dict[str, Any],
              watchlist: Any,
              recent_reports: list[dict[str, str]]) -> str:
    """
    调用 Kimi 生成报告 Markdown. 失败抛异常 (由 caller 捕获回退).
    输入预算: 总文本 ≤ ~600k 字符 (≈ 300k tokens), 历史报告先截.
    """
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(f"openai SDK 未安装: pip install openai") from exc

    api_key = os.environ.get("KIMI_API_KEY")
    if not api_key:
        raise RuntimeError("KIMI_API_KEY 未在 .env 或环境变量中设置")
    base_url = (os.environ.get("KIMI_BASE_URL")
                or os.environ.get("KIMI_URL")
                or "https://api.moonshot.cn/v1")
    model = os.environ.get("KIMI_MODEL", "kimi-k2.6")

    # 序列化 + 预算控制 (~600k chars 上限, 留余地给 system + 模板)
    def dump(obj: Any, limit: int) -> str:
        text = json.dumps(obj, ensure_ascii=False, indent=2) if obj is not None else "null"
        return _truncate_for_budget(text, limit)

    md_text = dump(inputs.get("market_data"), 80_000)
    th_text = dump(inputs.get("themes"), 30_000)
    ipo_text = dump(inputs.get("ipo_recent"), 60_000)
    news_text = dump(inputs.get("news_today"), 200_000)
    wl_text = dump(watchlist, 30_000)
    # 历史报告: 每篇限 20k 字符
    hist_pieces = []
    for r in recent_reports:
        hist_pieces.append(f"--- {r['date']} ---\n{_truncate_for_budget(r['content'], 20_000)}")
    hist_text = _truncate_for_budget("\n\n".join(hist_pieces) if hist_pieces else "无历史报告.",
                                     150_000)

    user_msg = USER_PROMPT_TEMPLATE.format(
        today=today,
        market_data=md_text,
        themes=th_text,
        ipo_recent=ipo_text,
        news_today=news_text,
        watchlist=wl_text,
        recent_reports=hist_text,
    )

    total_chars = len(SYSTEM_PROMPT) + len(user_msg)
    print(f"  → Kimi 输入 ≈ {total_chars:,} 字符 (~{total_chars // 2:,} tokens)")
    if total_chars > 600_000:
        raise RuntimeError(f"输入超预算: {total_chars} > 600k chars")

    client = OpenAI(api_key=api_key, base_url=base_url)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.3,
        max_tokens=5000,
    )
    content = (resp.choices[0].message.content or "").strip()
    if not content:
        raise RuntimeError("Kimi 返回空内容")
    # 容错: 去掉可能的代码块包裹
    if content.startswith("```"):
        content = re.sub(r"^```[a-zA-Z]*\n?", "", content)
        content = re.sub(r"\n?```\s*$", "", content)
    return content


# ============================================================================
# 新闻策展 (Kimi 去重 + 重要性打分)
# ============================================================================
def _slim_news_items(news_items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """剔除 content 全文 (太长), 只保留打分需要的字段, 节省 Kimi 输入 token."""
    slim: list[dict[str, Any]] = []
    for it in news_items:
        if not isinstance(it, dict):
            continue
        slim.append({
            "stock_code": it.get("stock_code") or "",
            "company_keyword": it.get("company_keyword") or "",
            "headline": it.get("headline") or "",
            "published_at": it.get("published_at") or "",
            "source_name": it.get("source_name") or "",
            "source_url": it.get("source_url") or "",
        })
    return slim


def _watchlist_keywords(watchlist: Any) -> list[str]:
    """从 watchlist 抽出公司名/代码用于提示词参考 (容错: 列表/字典/null)."""
    keys: list[str] = []
    if isinstance(watchlist, list):
        for entry in watchlist:
            if not isinstance(entry, dict):
                continue
            for k in ("name", "company", "company_keyword", "stock_code", "code"):
                v = entry.get(k)
                if v:
                    keys.append(str(v))
    elif isinstance(watchlist, dict):
        for k in ("active_deals", "companies", "items"):
            sub = watchlist.get(k)
            if isinstance(sub, list):
                keys.extend(_watchlist_keywords(sub))
    # 去重保序
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def curate_news_with_kimi(news_today: dict[str, Any] | None,
                          watchlist: Any,
                          today: str,
                          out_path: Path) -> tuple[list[dict[str, Any]], str | None]:
    """
    把 news_today.items 喂给 Kimi 做去重 + 重要性打分.

    返回: (curated_list, error_msg)
        成功 → (list, None) 并落盘 out_path
        失败 → ([], err_str)  caller 可决定回退到原始未策展新闻
    """
    if not news_today or not isinstance(news_today, dict):
        return [], "news_today 为空"
    items = news_today.get("items") or []
    if not items:
        return [], "news_today.items 为空"

    try:
        from openai import OpenAI
    except ImportError as exc:
        return [], f"openai SDK 未安装: {exc}"

    api_key = os.environ.get("KIMI_API_KEY")
    if not api_key:
        return [], "KIMI_API_KEY 未设置"
    base_url = (os.environ.get("KIMI_BASE_URL")
                or os.environ.get("KIMI_URL")
                or "https://api.moonshot.cn/v1")
    model = os.environ.get("KIMI_MODEL", "kimi-k2.6")

    slim = _slim_news_items(items)
    wl_keys = _watchlist_keywords(watchlist)

    user_msg = (
        f"今日日期: {today}\n"
        f"watchlist 关键词 (任一被提及即 priority=10): {json.dumps(wl_keys, ensure_ascii=False)}\n\n"
        f"原始新闻列表 ({len(slim)} 条, JSON):\n"
        f"{json.dumps(slim, ensure_ascii=False, indent=2)}\n\n"
        f"请按 system 指令输出策展后的 JSON 数组."
    )
    total_chars = len(NEWS_CURATION_SYSTEM_PROMPT) + len(user_msg)
    print(f"  → 新闻策展输入 ≈ {total_chars:,} 字符 ({len(slim)} 条新闻)")

    try:
        client = OpenAI(api_key=api_key, base_url=base_url)
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": NEWS_CURATION_SYSTEM_PROMPT},
                {"role": "user", "content": user_msg},
            ],
            temperature=0.2,
            max_tokens=6000,
        )
    except Exception as exc:
        return [], f"{type(exc).__name__}: {exc}"

    raw = (resp.choices[0].message.content or "").strip()
    if not raw:
        return [], "Kimi 返回空内容"
    # 容错: 去掉可能的 ```json``` 包裹
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-zA-Z]*\n?", "", raw)
        raw = re.sub(r"\n?```\s*$", "", raw)

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        # 二次救援: 截取首个 '[' 到末尾 ']' 之间
        m = re.search(r"\[.*\]", raw, flags=re.S)
        if m:
            try:
                parsed = json.loads(m.group(0))
            except Exception:
                return [], f"JSON 解析失败: {exc}; 原文 head=\n{raw[:300]}"
        else:
            return [], f"JSON 解析失败: {exc}; 原文 head=\n{raw[:300]}"

    if not isinstance(parsed, list):
        return [], f"Kimi 输出非 JSON 数组, 类型={type(parsed).__name__}"

    # 规范化字段 + 排序
    curated: list[dict[str, Any]] = []
    for x in parsed:
        if not isinstance(x, dict):
            continue
        try:
            pri = int(x.get("priority", 0))
        except Exception:
            pri = 0
        curated.append({
            "priority": max(1, min(10, pri)),
            "headline": str(x.get("headline") or "")[:200],
            "rationale": str(x.get("rationale") or "")[:200],
            "source_count": int(x.get("source_count") or 1),
            "category": str(x.get("category") or "general"),
            "related_codes": [str(c) for c in (x.get("related_codes") or []) if c],
            "sample_url": str(x.get("sample_url") or ""),
        })
    curated.sort(key=lambda r: r["priority"], reverse=True)

    # 落盘
    out_payload = {
        "as_of": today,
        "raw_count": len(slim),
        "curated_count": len(curated),
        "watchlist_keys": wl_keys,
        "items": curated,
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out_payload, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    bucket = {"10": 0, "9": 0, "8-6": 0, "5-3": 0, "2-1": 0}
    for c in curated:
        p = c["priority"]
        if p == 10: bucket["10"] += 1
        elif p == 9: bucket["9"] += 1
        elif p >= 6: bucket["8-6"] += 1
        elif p >= 3: bucket["5-3"] += 1
        else: bucket["2-1"] += 1
    print(f"  ✓ 策展完成: {len(slim)} 原始 → {len(curated)} 条 "
          f"(p10={bucket['10']}, p9={bucket['9']}, p8-6={bucket['8-6']}, "
          f"p5-3={bucket['5-3']}, p2-1={bucket['2-1']})")
    print(f"  ✓ 写入 {out_path.relative_to(PROJECT_ROOT)}")

    return curated, None


def filter_news_for_report(curated: list[dict[str, Any]],
                           min_priority: int = 6) -> dict[str, Any]:
    """把 curated 重新打包成 news_today 兼容的结构, 仅保留 priority >= min_priority."""
    kept = [c for c in curated if c.get("priority", 0) >= min_priority]
    items = []
    for c in kept:
        items.append({
            "headline": c.get("headline", ""),
            "stock_code": (c.get("related_codes") or [""])[0],
            "source_url": c.get("sample_url", ""),
            "_priority": c.get("priority"),
            "_category": c.get("category"),
            "_rationale": c.get("rationale"),
            "_source_count": c.get("source_count"),
        })
    return {"items": items, "_curated": True, "_min_priority": min_priority}


# ============================================================================
# LLM 输出后处理: 补 # 标题, 补 X/10 评分
# ============================================================================
def _fallback_sentiment_from_regime(regime: Any) -> float:
    """regime_score [-0.3, +0.3] 线性映射到 [0, 10]."""
    if not isinstance(regime, (int, float)):
        return 5.0
    return max(0.0, min(10.0, (float(regime) + 0.3) / 0.6 * 10))


def postprocess_report(report_md: str, today: str, snapshot: dict[str, Any]) -> str:
    """补强 LLM 输出: 首行 H1 + 第 1 节 X/10 数字."""
    text = report_md.lstrip("\n")
    lines = text.splitlines()

    # 1) 强制首行 H1
    if not lines:
        lines = [f"# 港股情绪日报 {today}"]
    else:
        first = lines[0].strip()
        if first.startswith("#"):
            # 已经是标题: 标准化空格
            lines[0] = re.sub(r"^#+\s*", "# ", first)
        else:
            # 不是标题: 如果首行已经写了 "港股情绪日报 ...", 直接前置 #
            if "港股情绪日报" in first:
                lines[0] = f"# {first}"
            else:
                lines.insert(0, f"# 港股情绪日报 {today}")

    # 2) 第 1 节如果没有 X/10 格式数字, 补一个
    body = "\n".join(lines)
    # 提取第 1 节正文 (从 ## 1 到 ## 2)
    m_sec1 = re.search(r"(##\s*1\..*?)(\n##\s*2\.)", body, flags=re.S)
    if m_sec1:
        sec1_body = m_sec1.group(1)
        if not re.search(r"\d+(?:\.\d+)?\s*/\s*10", sec1_body):
            score = _fallback_sentiment_from_regime(snapshot.get("regime_score"))
            # 找到 "## 1. ..." 标题后插入 **X.X / 10**
            sec1_fixed = re.sub(
                r"(##\s*1\.[^\n]*\n)",
                rf"\1\n**{score:.1f} / 10** (脚本回填; LLM 未输出标准格式)\n\n",
                sec1_body, count=1
            )
            body = body.replace(sec1_body, sec1_fixed, 1)

    if not body.endswith("\n"):
        body += "\n"
    return body


# ============================================================================
# 摘要解析 (一行控制台总结)
# ============================================================================
def parse_one_line_summary(report_md: str, snapshot: dict[str, Any]) -> str:
    sentiment = None
    # 优先匹配 **X.X / 10** 这种粗体显式格式
    m = re.search(r"\*\*\s*(\d+(?:\.\d+)?)\s*/\s*10\s*\*\*", report_md)
    if not m:
        m = re.search(r"(\d+(?:\.\d+)?)\s*/\s*10", report_md)
    if m:
        sentiment = m.group(1)
    regime = snapshot.get("regime_score")
    regime_str = f"{regime:.3f}" if isinstance(regime, (int, float)) else "n/a"

    # 抓决策建议第 1 条
    advice = ""
    m2 = re.search(r"##\s*6\..*?\n(.+?)(?:\n##|\Z)", report_md, flags=re.S)
    if m2:
        first = m2.group(1).strip().splitlines()
        for ln in first:
            ln = ln.strip().lstrip("-").strip()
            if ln:
                advice = ln[:80]
                break
    return f"情绪 {sentiment or '?'}/10, regime {regime_str}, 主要建议: {advice or 'n/a'}"


# ============================================================================
# Main
# ============================================================================
def main() -> int:
    parser = argparse.ArgumentParser(description="港股情绪日报生成器")
    parser.add_argument("--date", default=date.today().isoformat(),
                        help="日期 YYYY-MM-DD (默认今日)")
    parser.add_argument("--no-llm", action="store_true",
                        help="跳过 Kimi, 仅用硬指标生成简化报告")
    parser.add_argument("--skip-curation", action="store_true",
                        help="跳过新闻策展 (Kimi 去重 + 打分), 直接用全部原始新闻喂给报告生成")
    parser.add_argument("--min-priority", type=int, default=6,
                        help="报告只使用 priority >= N 的策展新闻 (默认 6)")
    args = parser.parse_args()
    today = args.date

    _load_env()
    print(f"[{datetime.now().isoformat(timespec='seconds')}] 生成 {today} 情绪日报")

    inputs = load_today_inputs(today)
    if all(v is None for v in inputs.values()):
        print(f"  ✗ daily/{today}/ 下找不到任何输入文件, 终止.", file=sys.stderr)
        return 1

    watchlist = load_watchlist()
    recent = load_recent_reports(today, n_days=7)
    print(f"  - 历史报告: {len(recent)} 篇; watchlist: "
          f"{'已加载' if watchlist is not None else '未配置'}")

    digest = compute_snapshot(inputs, recent)

    # ---------- 新闻策展: Kimi 去重 + 重要性打分 (在主报告生成之前) ----------
    if not args.skip_curation and not args.no_llm:
        curated_path = PROJECT_ROOT / "daily" / today / "news_curated.json"
        curated, cur_err = curate_news_with_kimi(
            inputs.get("news_today"), watchlist, today, curated_path
        )
        if cur_err:
            print(f"  ✗ 新闻策展失败 → 沿用原始 news_today: {cur_err}", file=sys.stderr)
        elif curated:
            filtered = filter_news_for_report(curated, min_priority=args.min_priority)
            kept = len(filtered["items"])
            print(f"  → 报告将使用 priority>={args.min_priority} 的 {kept} 条策展新闻 "
                  f"(原始 {len(curated)} 条)")
            inputs["news_today"] = filtered
    elif args.skip_curation:
        print("  - --skip-curation 指定, 跳过 Kimi 新闻策展, 用原始新闻")

    report_md: str
    fallback_err: str | None = None
    if args.no_llm:
        print("  - --no-llm 指定, 走硬指标回退路径")
        report_md = build_fallback_report(today, inputs, digest, watchlist)
    else:
        try:
            report_md = call_kimi(today, inputs, watchlist, recent)
            print("  ✓ Kimi 返回成功")
        except Exception as exc:
            fallback_err = f"{type(exc).__name__}: {exc}"
            print(f"  ✗ Kimi 调用失败 → 回退到硬指标版本: {fallback_err}", file=sys.stderr)
            report_md = build_fallback_report(today, inputs, digest, watchlist, err=fallback_err)

    # 后处理: 补强 # 标题 + X/10 评分 (Kimi 偶发漏掉, fallback 报告本身合规无影响)
    report_md = postprocess_report(report_md, today, digest["snapshot"])

    reports_dir = PROJECT_ROOT / "reports"
    reports_dir.mkdir(exist_ok=True)
    out_path = reports_dir / f"{today}.md"
    out_path.write_text(report_md, encoding="utf-8")
    print(f"  ✓ 写入 {out_path.relative_to(PROJECT_ROOT)} ({len(report_md):,} chars)")

    summary = parse_one_line_summary(report_md, digest["snapshot"])
    print(f"\n>>> {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
