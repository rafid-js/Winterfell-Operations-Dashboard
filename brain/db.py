from sqlalchemy import create_engine, text
from dotenv import load_dotenv
import os

load_dotenv()

_url = os.getenv('DATABASE_URL')
if not _url:
    raise RuntimeError("DATABASE_URL not set in .env")

engine = create_engine(_url, pool_pre_ping=True)

def get_connection():
    return engine.connect()
