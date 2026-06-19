import psycopg2

conn = psycopg2.connect("postgresql://suggestify:suggestify_secret@localhost:5433/suggestify")
cur = conn.cursor()
cur.execute("SELECT genres FROM items LIMIT 1")
print(cur.fetchone())

cur.execute("""
    SELECT column_name, data_type
    FROM information_schema.columns
    WHERE table_name = 'items'
    ORDER BY ordinal_position
""")
print("\nitems columns:")
for row in cur.fetchall():
    print(row)
