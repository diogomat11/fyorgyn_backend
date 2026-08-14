import os, psycopg2
from dotenv import load_dotenv
load_dotenv('backend/.env')
conn = psycopg2.connect(os.getenv('DATABASE_URL'))
cursor = conn.cursor()
cursor.execute("SELECT id, guia, carteirinha_id, codigo_beneficiario, id_convenio, status_guia, data_autorizacao, nome_terapia, user_id, cod_prestador FROM base_guias WHERE guia = '70138883'")
rows = cursor.fetchall()
if rows:
    for r in rows: print(r)
else:
    print('No results found.')
