"""
迁移: 为 ipo_concepts 和 ipo_industries 补加 PRIMARY KEY

背景:
    原 schema 中 ipo_concepts / ipo_industries 无 PK, ETL 重跑可能堆积重复行.
    新 schema 已在 src/data/schema.py 中加了 PK:
        ipo_concepts:   PRIMARY KEY (ipo_id, concept_id)
        ipo_industries: PRIMARY KEY (ipo_id, source)

    SQLite 不支持 ALTER TABLE ADD PRIMARY KEY, 需要重建表.

幂等: 可重复运行, 如果表已有 PK 则跳过.

用法:
    python scripts/migrate_schema_pk_fix.py [db_path]
    默认: data/nacs_real.db
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path


def _table_has_pk(conn: sqlite3.Connection, table: str) -> bool:
    """检查表是否有复合 PK (排除 AUTOINCREMENT 单列 PK)"""
    info = conn.execute(f"PRAGMA table_info({table})").fetchall()
    pk_cols = [r for r in info if r[5] > 0]  # col[5] = pk ordinal
    return len(pk_cols) >= 2


def migrate_ipo_concepts(conn: sqlite3.Connection) -> int:
    """重建 ipo_concepts 表, 加 PK (ipo_id, concept_id), 去重. 返回去重删除行数."""
    if _table_has_pk(conn, "ipo_concepts"):
        print("  ipo_concepts: 已有 PK, 跳过")
        return 0

    before = conn.execute("SELECT COUNT(*) FROM ipo_concepts").fetchone()[0]

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ipo_concepts_new (
            ipo_id          TEXT NOT NULL,
            stock_code      TEXT NOT NULL,
            concept_id      TEXT NOT NULL,
            concept_name    TEXT,
            data_date       TEXT,
            PRIMARY KEY (ipo_id, concept_id)
        );

        INSERT OR IGNORE INTO ipo_concepts_new
            SELECT ipo_id, stock_code, concept_id, concept_name, data_date
            FROM ipo_concepts;

        DROP TABLE ipo_concepts;
        ALTER TABLE ipo_concepts_new RENAME TO ipo_concepts;

        CREATE INDEX IF NOT EXISTS idx_ipo_concepts_stock
            ON ipo_concepts(stock_code);
        CREATE INDEX IF NOT EXISTS idx_ipo_concepts_concept
            ON ipo_concepts(concept_id);
    """)

    after = conn.execute("SELECT COUNT(*) FROM ipo_concepts").fetchone()[0]
    dropped = before - after
    print(f"  ipo_concepts: {before} → {after} 行 (去重 {dropped} 行)")
    return dropped


def migrate_ipo_industries(conn: sqlite3.Connection) -> int:
    """重建 ipo_industries 表, 加 PK (ipo_id, source), 去重. 返回去重删除行数."""
    if _table_has_pk(conn, "ipo_industries"):
        print("  ipo_industries: 已有 PK, 跳过")
        return 0

    before = conn.execute("SELECT COUNT(*) FROM ipo_industries").fetchone()[0]

    conn.executescript("""
        CREATE TABLE IF NOT EXISTS ipo_industries_new (
            ipo_id          TEXT NOT NULL,
            stock_code      TEXT NOT NULL,
            source          TEXT NOT NULL,
            l1_name         TEXT, l2_name TEXT, l3_name TEXT, l4_name TEXT,
            leaf_bid        TEXT,
            leaf_level      INTEGER,
            data_date       TEXT,
            PRIMARY KEY (ipo_id, source)
        );

        INSERT OR IGNORE INTO ipo_industries_new
            SELECT ipo_id, stock_code, source,
                   l1_name, l2_name, l3_name, l4_name,
                   leaf_bid, leaf_level, data_date
            FROM ipo_industries;

        DROP TABLE ipo_industries;
        ALTER TABLE ipo_industries_new RENAME TO ipo_industries;

        CREATE INDEX IF NOT EXISTS idx_ipo_industries_stock
            ON ipo_industries(stock_code);
        CREATE INDEX IF NOT EXISTS idx_ipo_industries_leaf
            ON ipo_industries(leaf_bid);
        CREATE INDEX IF NOT EXISTS idx_ipo_industries_l1
            ON ipo_industries(l1_name);
    """)

    after = conn.execute("SELECT COUNT(*) FROM ipo_industries").fetchone()[0]
    dropped = before - after
    print(f"  ipo_industries: {before} → {after} 行 (去重 {dropped} 行)")
    return dropped


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(
        Path(__file__).resolve().parent.parent / "data" / "nacs_real.db"
    )
    print(f"迁移目标: {db_path}")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = OFF")  # 重建表时需关闭 FK
    try:
        migrate_ipo_concepts(conn)
        migrate_ipo_industries(conn)
        conn.commit()
        print("[OK] 迁移完成")
    except Exception as e:
        conn.rollback()
        print(f"[FAIL] 迁移失败: {e}")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
