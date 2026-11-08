import os

from dotenv import load_dotenv

# Load brain/.env before anything imports db.py, so running the allocator's CLI
# modules (product_sync, etc.) from any directory still finds DATABASE_URL.
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         '..', 'brain', '.env'))

from flask import Blueprint  # noqa: E402

aa_bp = Blueprint('ad_allocator', __name__)

from . import routes  # noqa: E402,F401
