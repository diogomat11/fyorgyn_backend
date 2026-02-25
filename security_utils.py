import os
try:
    from cryptography.fernet import Fernet
except ImportError:
    print("!!! ERROR: cryptography not found")
    Fernet = None

def get_fernet():
    key = os.environ.get("FERNET_SECRET")
    if not key:
        raise ValueError("FERNET_SECRET not set in environment")
    
    if Fernet is None:
        raise ImportError("Fernet class is None, check cryptography installation")
    
    f = Fernet(key.strip().encode())
    return f

def encrypt_password(password: str) -> str:
    f = get_fernet()
    if f is None:
        raise ValueError("Fernet instance is None")
    
    token = f.encrypt(password.encode())
    return token.decode()

def decrypt_password(encrypted_password: str) -> str:
    f = get_fernet()
    return f.decrypt(encrypted_password.encode()).decode()

def generate_key():
    return Fernet.generate_key().decode()
