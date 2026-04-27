import os
from database import engine
from sqlalchemy import text

with engine.connect() as conn:
    with open(os.path.join("migrations", "0050_create_lotes_convenio.sql"), "r", encoding="utf-8") as f:
        sql = f.read()
    conn.execute(text(sql))
    conn.commit()

print("Migration 0050 applied successfully.")
