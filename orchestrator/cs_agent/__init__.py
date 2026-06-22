from flask import Blueprint

cs_bp = Blueprint('cs_agent', __name__)

from . import routes  # noqa: E402,F401
