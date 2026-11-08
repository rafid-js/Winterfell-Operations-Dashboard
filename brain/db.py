from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()

_url = os.getenv('DATABASE_URL')
if not _url:
    raise RuntimeError("DATABASE_URL not set in .env")

# Force the psycopg2 driver explicitly. SQLAlchemy 2.1+ resolves a bare
# "postgresql://" (or Railway's legacy "postgres://") to psycopg v3, which
# isn't installed — only psycopg2-binary is — so pin the dialect to psycopg2.
if _url.startswith('postgres://'):
    _url = 'postgresql+psycopg2://' + _url[len('postgres://'):]
elif _url.startswith('postgresql://'):
    _url = 'postgresql+psycopg2://' + _url[len('postgresql://'):]

engine = create_engine(_url, pool_pre_ping=True)

def get_connection():
    return engine.connect()
