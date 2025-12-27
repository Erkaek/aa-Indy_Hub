import sqlite3
import os
DB_PATH = os.path.join(os.getcwd(), 'alliance_auth.sqlite3')
print('DB:', DB_PATH)
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

print('\nMaterialExchangeConfig:')
cur.execute("SELECT id, is_active, corporation_id, structure_id, hangar_division, last_stock_sync, last_price_sync FROM indy_hub_materialexchangeconfig")
rows = cur.fetchall()
for r in rows:
    print(dict(r))

print('\nStock summary:')
cur.execute("SELECT COUNT(*) AS cnt, COALESCE(SUM(quantity),0) AS qty FROM indy_hub_materialexchangestock WHERE quantity>0")
print(dict(cur.fetchone()))
cur.execute("SELECT COUNT(*) AS cnt, COALESCE(SUM(quantity),0) AS qty FROM indy_hub_materialexchangestock WHERE quantity>0 AND jita_buy_price>0")
print(dict(cur.fetchone()))

print('\nCorptools assets (CorpSAG4):')
try:
    cur.execute("SELECT COUNT(*) AS cnt FROM corptools_corpasset WHERE location_flag='CorpSAG4'")
    print('Total CorpSAG4 rows:', dict(cur.fetchone())['cnt'])
    cur.execute("SELECT corporation_id, location_id, type_id, quantity FROM corptools_corpasset WHERE location_flag='CorpSAG4' LIMIT 10")
    print([dict(x) for x in cur.fetchall()])
except sqlite3.OperationalError as e:
    print('Corptools table not found:', e)

print('\nCorptools assets for active config + CorpSAG4:')
try:
    cur.execute("""
    SELECT COUNT(*) AS cnt
    FROM corptools_corpasset
    WHERE location_flag='CorpSAG4'
      AND location_id=(SELECT structure_id FROM indy_hub_materialexchangeconfig WHERE is_active=1 LIMIT 1)
      AND corporation_id=(SELECT corporation_id FROM indy_hub_materialexchangeconfig WHERE is_active=1 LIMIT 1)
    """)
    print('Matching rows:', dict(cur.fetchone())['cnt'])
    cur.execute("""
    SELECT type_id, SUM(quantity) AS total_qty
    FROM corptools_corpasset
    WHERE location_flag='CorpSAG4'
      AND location_id=(SELECT structure_id FROM indy_hub_materialexchangeconfig WHERE is_active=1 LIMIT 1)
      AND corporation_id=(SELECT corporation_id FROM indy_hub_materialexchangeconfig WHERE is_active=1 LIMIT 1)
    GROUP BY type_id
    ORDER BY total_qty DESC
    LIMIT 20
    """)
    print([dict(x) for x in cur.fetchall()])
except sqlite3.OperationalError as e:
    print('Corptools table not found:', e)

conn.close()
