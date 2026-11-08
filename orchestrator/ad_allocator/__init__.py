from flask import Blueprint

aa_bp = Blueprint('ad_allocator', __name__)

from . import routes  # noqa: E402,F401
