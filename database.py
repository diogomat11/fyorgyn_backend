import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

# Handle Supabase Pooler usually requiring transaction mode or specific port
# The .env has keys like SUPABASE_DB_HOST, etc. 
# Or we can construct from standard postgres connection string.
# Using the .env values if available, or falling back to a constructed string.

DB_USER = os.getenv("SUPABASE_DB_USER", "postgres")
DB_PASSWORD = os.getenv("SUPABASE_PASSWORD", "")
DB_HOST = os.getenv("SUPABASE_DB_HOST", "db.ourddwzetcbdjnbvamgj.supabase.co")
DB_PORT = os.getenv("SUPABASE_DB_PORT", "5432")
DB_NAME = os.getenv("SUPABASE_DB_NAME", "postgres")

# Prefer DATABASE_URL when explicitly set: enables the Supabase POOLER (IPv4),
# needed in clouds like Render where the direct host db.*.supabase.co is
# IPv6-only ("Network is unreachable"). Force port 5432 (session mode), safer
# for SQLAlchemy ORM. connect_args already handles pooler compatibility
# (prepare_threshold=None). Falls back to direct SUPABASE_DB_* construction.
_DATABASE_URL_ENV = os.getenv("DATABASE_URL", "").strip()
if _DATABASE_URL_ENV:
    SQLALCHEMY_DATABASE_URL = _DATABASE_URL_ENV.replace(":6543", ":5432")
elif DB_HOST and "supabase.co" in DB_HOST and DB_PASSWORD:
    SQLALCHEMY_DATABASE_URL = f"postgresql://postgres:{DB_PASSWORD}@{DB_HOST}:5432/{DB_NAME}"
else:
    SQLALCHEMY_DATABASE_URL = f"postgresql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(
    SQLALCHEMY_DATABASE_URL, 
    use_native_hstore=False,
    pool_size=10,
    max_overflow=20,
    pool_timeout=30,
    pool_recycle=300,
    pool_pre_ping=True,
    connect_args={
        "prepare_threshold": None,
        "keepalives": 1,
        "keepalives_idle": 30,
        "keepalives_interval": 10,
        "keepalives_count": 5
    } 
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
