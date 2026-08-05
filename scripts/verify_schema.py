import asyncio
import os
import asyncpg
from dotenv import load_dotenv

load_dotenv()

async def inspect():
    db_url = os.environ.get("DATABASE_URL")
    conn = await asyncpg.connect(db_url)

    print("=== LIVE NEON DB SCHEMA DUMP ===")
    
    # 1. Enums
    enums = await conn.fetch("""
        SELECT t.typname, array_agg(e.enumlabel ORDER BY e.enumsortorder) as values
        FROM pg_type t
        JOIN pg_enum e ON t.oid = e.enumtypid
        JOIN pg_namespace n ON n.oid = t.typnamespace
        WHERE n.nspname = 'public'
        GROUP BY t.typname;
    """)
    print("\n--- ENUMS ---")
    for row in enums:
        print(f"ENUM {row['typname']}: {row['values']}")

    # 2. Tables & Columns
    tables = await conn.fetch("""
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
        ORDER BY table_name;
    """)
    print("\n--- TABLES & COLUMNS ---")
    for t in tables:
        tname = t['table_name']
        cols = await conn.fetch("""
            SELECT column_name, data_type, udt_name, is_nullable, column_default
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = $1
            ORDER BY ordinal_position;
        """, tname)
        print(f"\nTable: {tname}")
        for c in cols:
            dt = c['udt_name'] if c['data_type'] == 'USER-DEFINED' else c['data_type']
            null_str = "NULL" if c['is_nullable'] == 'YES' else "NOT NULL"
            def_str = f" DEFAULT {c['column_default']}" if c['column_default'] else ""
            print(f"  - {c['column_name']} ({dt}) {null_str}{def_str}")

    # 3. Constraints (Primary Key, Foreign Key, Unique)
    constraints = await conn.fetch("""
        SELECT tc.table_name, tc.constraint_name, tc.constraint_type, kcu.column_name
        FROM information_schema.table_constraints tc
        JOIN information_schema.key_column_usage kcu 
          ON tc.constraint_name = kcu.constraint_name AND tc.table_schema = kcu.table_schema
        WHERE tc.table_schema = 'public'
        ORDER BY tc.table_name, tc.constraint_type, tc.constraint_name;
    """)
    print("\n--- CONSTRAINTS ---")
    for c in constraints:
        print(f"Table: {c['table_name']} | Type: {c['constraint_type']} | Name: {c['constraint_name']} | Column: {c['column_name']}")

    # 4. Triggers
    triggers = await conn.fetch("""
        SELECT event_object_table as table_name, trigger_name, action_statement, action_timing, event_manipulation
        FROM information_schema.triggers
        WHERE trigger_schema = 'public'
        ORDER BY event_object_table;
    """)
    print("\n--- TRIGGERS ---")
    for tr in triggers:
        print(f"Table: {tr['table_name']} | Trigger: {tr['trigger_name']} | Event: {tr['action_timing']} {tr['event_manipulation']}")

    await conn.close()

if __name__ == "__main__":
    asyncio.run(inspect())
