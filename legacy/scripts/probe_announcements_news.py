"""
probe_announcements_news.py — IFIND 公告 (report_query) + akshare 新闻 联通性探针

用法:
    python scripts/probe_announcements_news.py
    python scripts/probe_announcements_news.py --code 00700.HK
    python scripts/probe_announcements_news.py --code 09988.HK --days 30 --keyword 业绩

输出:
    打印到控制台 + 可选 --out-dir 把结果写 JSON
"""
from __future__ import annotations

import sys
import os
import json
import argparse
from pathlib import Path
from datetime import date, timedelta

# Windows UTF-8
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.data_sources.ifind.announcement_fetcher import (
    fetch_announcements,
    AnnouncementRecord,
)
from src.data_sources.ifind.http_client import IFindAuthError, IFindHttpError
from src.data_sources.akshare.news_fetcher import fetch_news, NewsRecord


def probe_announcements(code: str, days: int, keyword: str | None) -> list[AnnouncementRecord]:
    end = date.today()
    start = end - timedelta(days=days)
    print(f"\n[1/2] iFinD report_query  code={code}  [{start} ~ {end}]  keyword={keyword!r}")
    try:
        records = fetch_announcements(code, start_date=start, end_date=end, keyword=keyword)
    except IFindAuthError as e:
        print(f"  ✗ 认证失败: [{e.errorcode}] {e.errmsg}")
        return []
    except IFindHttpError as e:
        print(f"  ✗ 接口错误: [{e.errorcode}] {e.errmsg}")
        return []

    print(f"  ✓ 共 {len(records)} 条公告")
    for r in records[:5]:
        print(f"    - {r.announcement_date} | {r.report_type} | {r.title[:50]}")
        if r.pdf_url:
            print(f"        pdf: {r.pdf_url[:80]}")
    if len(records) > 5:
        print(f"    ... 省略 {len(records) - 5} 条")
    return records


def probe_news(code: str, limit: int) -> list[NewsRecord]:
    print(f"\n[2/2] akshare stock_news_em  code={code}  limit={limit}")
    try:
        items = fetch_news(code, limit=limit)
    except ImportError as e:
        print(f"  ✗ 依赖缺失: {e}")
        return []
    except RuntimeError as e:
        print(f"  ✗ 调用失败: {e}")
        return []

    print(f"  ✓ 共 {len(items)} 条新闻")
    for n in items[:5]:
        print(f"    - {n.published_at} | {n.source_name} | {n.headline[:50]}")
    if len(items) > 5:
        print(f"    ... 省略 {len(items) - 5} 条")
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="iFinD 公告 + akshare 新闻 联通性探针")
    parser.add_argument("--code", default="00700.HK", help="港股代码, 默认 00700.HK")
    parser.add_argument("--days", type=int, default=30, help="公告查询回溯天数")
    parser.add_argument("--keyword", default=None, help="公告标题关键词")
    parser.add_argument("--news-limit", type=int, default=20, help="新闻条数上限")
    parser.add_argument("--out-dir", default=None, help="可选, 写 JSON 到此目录")
    args = parser.parse_args()

    print(f"== 探针: {args.code} ==")
    anns = probe_announcements(args.code, args.days, args.keyword)
    news = probe_news(args.code, args.news_limit)

    if args.out_dir:
        out = Path(args.out_dir)
        out.mkdir(parents=True, exist_ok=True)
        (out / f"{args.code.replace('.', '_')}_announcements.json").write_text(
            json.dumps([a.to_dict() for a in anns], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        (out / f"{args.code.replace('.', '_')}_news.json").write_text(
            json.dumps([n.to_dict() for n in news], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n已写入 {out}/")

    print("\n== 完成 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
