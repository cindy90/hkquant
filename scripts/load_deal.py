"""
load_deal.py — 把 data/deals/<stock_code>.yaml 灌进 ipo_master + ipo_cornerstone_link

用法:
    # 单个 deal
    python scripts/load_deal.py --file data/deals/1187.HK.yaml

    # 批量
    python scripts/load_deal.py --dir data/deals/

    # dry-run (只 lint + 打印, 不写库)
    python scripts/load_deal.py --file data/deals/1187.HK.yaml --dry-run
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from data.dao import db_connect
from data.deal_loader import load_deal_file, lint_deal


def _load_yaml(p: Path) -> dict:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as e:
        raise SystemExit("pyyaml 未安装: pip install pyyaml") from e
    return yaml.safe_load(p.read_text(encoding="utf-8")) or {}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--file", help="单个 deal YAML 路径")
    g.add_argument("--dir", help="deal YAML 目录, 批量加载")
    ap.add_argument("--db", default=str(_ROOT / "data" / "nacs_real.db"))
    ap.add_argument("--dry-run", action="store_true",
                    help="只 lint + 打印, 不写库")
    args = ap.parse_args()

    files = []
    if args.file:
        files = [Path(args.file)]
    else:
        files = sorted(Path(args.dir).glob("*.yaml")) + \
                sorted(Path(args.dir).glob("*.yml"))
        files = [f for f in files if f.name != "TEMPLATE.yaml"]
        if not files:
            print(f"no yaml files in {args.dir}", file=sys.stderr)
            return 1

    if args.dry_run:
        n_pass, n_fail = 0, 0
        for f in files:
            data = _load_yaml(f)
            errs = lint_deal(data)
            if errs:
                n_fail += 1
                print(f"\n✗ {f.name}")
                for e in errs:
                    print(f"    {e}")
            else:
                n_pass += 1
                cs_count = len(data.get("cornerstones") or [])
                print(f"✓ {f.name}  stock={data.get('stock_code')}  "
                      f"chapter={data.get('listing_chapter')}  "
                      f"expected={data.get('expected_listing_date')}  "
                      f"cs={cs_count}")
        print(f"\n[DRY-RUN] {n_pass} pass, {n_fail} fail")
        return 1 if n_fail else 0

    with db_connect(args.db) as conn:
        for f in files:
            stats = load_deal_file(conn, f)
            print(f"✓ {f.name}: ipo_master={stats.ipo_master_action}, "
                  f"cornerstones={stats.cornerstones_inserted}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
