from flask import Blueprint
from functools import wraps
from flask import session, redirect, url_for, request, jsonify

sc_bp = Blueprint('supply_chain', __name__)


def sc_login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('authenticated'):
            if request.path.startswith('/api/'):
                return jsonify({'error': 'Unauthorized'}), 401
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated


from . import routes  # noqa
