import os, psycopg2
from dotenv import load_dotenv
load_dotenv('backend/.env')
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cursor = conn.cursor()
cursor.execute("DELETE FROM base_guias WHERE guia = '70138883'")
conn.commit()
print("Guia 70138883 deletada com sucesso.")
