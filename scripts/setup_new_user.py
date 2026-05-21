"""
Script de Setup: Criar novo usuário com credenciais IPASGO para convênio 6.
Execute: python setup_new_user.py (dentro de c:\\dev\\Agenda_hub_MultiConv\\backend)
"""
import os
import sys
import secrets

# Ajuste de path
backend_path = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, backend_path)

# Carregar .env de ambos os diretórios (backend e worker têm configs diferentes)
project_root = os.path.dirname(backend_path)
env_candidates = [
    os.path.join(backend_path, ".env"),
    os.path.join(project_root, "Local_worker", ".env"),
]
for env_file in env_candidates:
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, val = line.partition("=")
                    os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))

from database import SessionLocal
from models import User, UserConvenio
from security_utils import encrypt_password

# ─── Dados do novo usuário ───
NOVO_USERNAME   = "brincando_aprendendo"
NOVO_LOGIN      = "15213080"
NOVO_SENHA      = "Brinca2025"
COD_PRESTADOR   = "01889-2"
ID_CONVENIO     = 6


def main():
    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == NOVO_USERNAME).first()
        if existing:
            print(f"[WARN] Usuário '{NOVO_USERNAME}' já existe (ID: {existing.id}). Atualizando credenciais...")
            user = existing
        else:
            api_key = secrets.token_urlsafe(32)
            user = User(
                username=NOVO_USERNAME,
                api_key=api_key,
                status="Ativo",
                is_admin=False,
                permitir_protocolo=False,
                id_convenio=ID_CONVENIO
            )
            db.add(user)
            db.flush()
            print(f"[OK] Usuário '{NOVO_USERNAME}' criado (ID: {user.id})")
            print(f"\n     *** API KEY (guarde esta chave!) ***")
            print(f"     {api_key}\n")

        senha_enc = encrypt_password(NOVO_SENHA)

        existing_uc = db.query(UserConvenio).filter(
            UserConvenio.user_id == user.id,
            UserConvenio.id_convenio == ID_CONVENIO
        ).first()

        if existing_uc:
            existing_uc.login = NOVO_LOGIN
            existing_uc.senha_criptografada = senha_enc
            existing_uc.cod_prestador = COD_PRESTADOR
            print(f"[OK] Credenciais IPASGO atualizadas.")
        else:
            uc = UserConvenio(
                user_id=user.id,
                id_convenio=ID_CONVENIO,
                login=NOVO_LOGIN,
                senha_criptografada=senha_enc,
                cod_prestador=COD_PRESTADOR
            )
            db.add(uc)
            print(f"[OK] Credenciais IPASGO vinculadas:")

        print(f"     Login     : {NOVO_LOGIN}")
        print(f"     Prestador : {COD_PRESTADOR}")
        print(f"     Convênio  : {ID_CONVENIO} (IPASGO)")

        db.commit()
        print("\n✅ Setup concluído com sucesso!")

    except Exception as e:
        db.rollback()
        print(f"[ERROR] {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
