"""Copy your existing Mshibe data from the local SQLite file into Supabase Postgres.

Run this BEFORE pointing the live app at Supabase (otherwise the app seeds sample menu
items into the empty database first and this script will refuse to overwrite them
unless you pass --wipe).

    PowerShell:
      $env:DATABASE_URL = "postgresql://postgres.<ref>:<password>@<pooler-host>:5432/postgres"
      python supabase/migrate_sqlite_to_supabase.py instance/mshibe.db

    macOS/Linux:
      DATABASE_URL="postgresql://..." python supabase/migrate_sqlite_to_supabase.py instance/mshibe.db

Options:  --wipe   empty the Supabase tables first, then copy
Requires: pip install sqlalchemy psycopg2-binary
"""
import os
import sys

from sqlalchemy import Boolean, MetaData, create_engine, func, select, text

# parents before children
TABLES = ["site_settings", "menu_categories", "services", "menu_items", "orders",
          "order_items", "appointments", "contact_messages"]


def pg_url():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        sys.exit("Set DATABASE_URL to your Supabase connection string first (see README).")
    for prefix in ("postgresql+psycopg2://", "postgresql://", "postgres://"):
        if url.startswith(prefix):
            # newer SQLAlchemy defaults "postgresql://" to psycopg v3; pin the driver we install
            return "postgresql+psycopg2://" + url[len(prefix):]
    sys.exit("DATABASE_URL must be a Postgres URL (postgresql://...), not SQLite.")


def q(name):
    return '"' + name.replace('"', '""') + '"'


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    wipe = "--wipe" in sys.argv
    sqlite_path = args[0] if args else os.path.join("instance", "mshibe.db")
    if not os.path.isfile(sqlite_path):
        sys.exit(f"SQLite file not found: {sqlite_path}\n"
                 "Flask usually keeps it at instance/mshibe.db (or mshibe.db in the project folder).")

    src = create_engine(f"sqlite:///{os.path.abspath(sqlite_path)}")
    dst = create_engine(pg_url(), pool_pre_ping=True)
    smeta, dmeta = MetaData(), MetaData()
    smeta.reflect(src)
    dmeta.reflect(dst, schema="public")
    dtables = {t.name: t for t in dmeta.tables.values()}

    missing = [t for t in TABLES if t not in dtables]
    if missing:
        sys.exit(f"These tables don't exist in Supabase yet: {', '.join(missing)}\n"
                 "Run supabase/01_schema.sql in the Supabase SQL editor first.")

    with dst.begin() as conn:
        counts = {t: conn.execute(select(func.count()).select_from(dtables[t])).scalar() for t in TABLES}
        if any(counts.values()):
            if not wipe:
                sys.exit("Supabase tables already contain data: "
                         + ", ".join(f"{t}={n}" for t, n in counts.items() if n)
                         + "\nRe-run with --wipe to replace it with the SQLite data.")
            conn.execute(text("truncate " + ", ".join(f"public.{q(t)}" for t in TABLES)
                              + " restart identity cascade"))
            print("Emptied existing Supabase tables.")

    total = 0
    with dst.begin() as conn:
        for name in TABLES:
            if name not in smeta.tables:
                print(f"  {name:16} not in SQLite file, skipped")
                continue
            s, d = smeta.tables[name], dtables[name]
            cols = [c.name for c in d.columns if c.name in s.columns]
            with src.connect() as sc:
                rows = [dict(r._mapping) for r in sc.execute(select(*[s.c[c] for c in cols]))]
            bools = [c.name for c in d.columns if isinstance(c.type, Boolean) and c.name in cols]
            for r in rows:
                for b in bools:
                    if r[b] is not None:
                        r[b] = bool(r[b])
            if rows:
                conn.execute(d.insert(), rows)
            # keep auto-numbering ahead of the ids we just copied
            conn.execute(text(
                f"select setval(pg_get_serial_sequence('public.{q(name)}', 'id'), "
                f"coalesce((select max(id) from public.{q(name)}), 1), "
                f"(select max(id) is not null from public.{q(name)}))"))
            print(f"  {name:16} {len(rows)} rows")
            total += len(rows)
    print(f"Done: {total} rows copied.")


if __name__ == "__main__":
    main()
