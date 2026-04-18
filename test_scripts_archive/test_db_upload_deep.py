import asyncio
import io
import traceback
from database import SessionLocal
from models import User
from routes.carteirinhas import upload_carteirinhas
from fastapi import UploadFile
from sqlalchemy import text

db = SessionLocal()

print("--- TESTING UPLOAD ---")
user = db.query(User).first()
if not user:
    print("No user found!")
    exit(1)

# As the user describes: carteirinha, id_paciente, paciente, id_convenio, status, Cod_convenio
csv_content = b'Carteirinha,id_paciente,Paciente,id_convenio,status,Cod_convenio\n123456789,123,Teste,3,ativo,1180507-2\n'
file = UploadFile(filename='test.csv', file=io.BytesIO(csv_content))

try:
    result = asyncio.run(upload_carteirinhas(file=file, overwrite=False, id_convenio=3, db=db, user=user))
    print("Success:", result)
except Exception as e:
    print("FATAL ERROR IN UPLOAD:")
    traceback.print_exc()

db.close()
