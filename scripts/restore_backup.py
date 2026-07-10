"""
Restore a SorinFlow backup (sorinflow-backup-*.json.gz) into the database
pointed to by DATABASE_URL — e.g. on a brand-new server.

Usage (inside the backend container / with app deps installed):
    python scripts/restore_backup.py data/backups/sorinflow-backup-20260710-0000.json.gz

Notes
- Creates all tables first (same as app startup), then inserts rows in
  FK-safe order. Intended for an EMPTY database; existing rows with the
  same primary keys will make inserts fail.
- After inserting, Postgres id sequences are bumped so new rows don't
  collide with restored ones.
"""
import asyncio
import gzip
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import DateTime, text  # noqa: E402

# Import every model module so Base.metadata knows all tables
from app.database import Base, engine, async_session_maker  # noqa: E402
import app.models.property  # noqa: F401,E402
import app.models.scraping_job  # noqa: F401,E402
import app.models.cookie  # noqa: F401,E402
import app.models.proxy  # noqa: F401,E402
import app.models.user  # noqa: F401,E402
import app.models.lead  # noqa: F401,E402
import app.models.crm_models  # noqa: F401,E402


def _parse_row(table, row: dict) -> dict:
    out = {}
    for col in table.columns:
        if col.name not in row:
            continue
        v = row[col.name]
        if v is not None and isinstance(col.type, DateTime) and isinstance(v, str):
            try:
                v = datetime.fromisoformat(v)
            except ValueError:
                v = None
        out[col.name] = v
    return out


async def restore(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        payload = json.load(f)

    tables_data = payload.get("tables", {})
    print(f"backup from {payload.get('created_at')} — {len(tables_data)} tables")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_maker() as db:
        for table in Base.metadata.sorted_tables:
            rows = tables_data.get(table.name) or []
            if not rows:
                continue
            await db.execute(table.insert(), [_parse_row(table, r) for r in rows])
            print(f"  {table.name}: {len(rows)} rows")
        await db.commit()

        # bump Postgres sequences past restored ids
        for table in Base.metadata.sorted_tables:
            if "id" in table.columns and tables_data.get(table.name):
                try:
                    await db.execute(text(
                        f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                        f"(SELECT COALESCE(MAX(id), 1) FROM {table.name}))"
                    ))
                except Exception as e:
                    print(f"  (sequence skip for {table.name}: {e})")
        await db.commit()

    print("restore complete ✓")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    backup_path = Path(sys.argv[1])
    if not backup_path.exists():
        print(f"file not found: {backup_path}")
        sys.exit(1)
    asyncio.run(restore(backup_path))
