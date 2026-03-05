from datetime import datetime

from flask import jsonify
from flask_login import current_user, login_required

from scoring_engine.db import db
from scoring_engine.models.machines import Machine
from scoring_engine.models.setting import Setting

from . import mod


def _serialize_machine(machine):
    data = {}
    for column in Machine.__table__.columns:
        value = getattr(machine, column.name)
        if isinstance(value, datetime):
            value = value.isoformat()
        data[column.name] = value
    return data


def _setting_bool(name, default=False):
    setting = Setting.get_setting(name)
    if setting is None:
        return default
    value = setting.value
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _status_permissions_for_current_user():
    if current_user.is_white_team or current_user.is_red_team:
        return {
            "status_full": True,
            "status_w_history": True,
            "current_status_only": True,
            "status_page": True,
        }

    if current_user.is_blue_team:
        page_enabled = _setting_bool("blue_team_view_status_page", default=True)
        current_enabled = _setting_bool("blue_team_view_current_status", default=True)
        history_enabled = _setting_bool("blue_team_view_historical_status", default=True)

        if not page_enabled:
            return {
                "status_full": False,
                "status_w_history": False,
                "current_status_only": False,
                "status_page": False,
            }

        return {
            "status_full": current_enabled and history_enabled,
            "status_w_history": history_enabled,
            "current_status_only": current_enabled,
            "status_page": True,
        }

    return {
        "status_full": False,
        "status_w_history": False,
        "current_status_only": False,
        "status_page": False,
    }


@mod.route("/api/status/permissions")
@login_required
def api_status_permissions():
    return jsonify(data=_status_permissions_for_current_user())


@mod.route("/api/status")
@login_required
def api_status():
    permissions = _status_permissions_for_current_user()
    if not permissions["status_page"]:
        return {"status": "Unauthorized"}, 403

    if current_user.is_white_team or current_user.is_red_team:
        machines = db.session.query(Machine).order_by(Machine.name).all()
    elif current_user.is_blue_team:
        if not permissions["current_status_only"]:
            return {"status": "Unauthorized"}, 403
        machines = (
            db.session.query(Machine)
            .filter(Machine.team_id == current_user.team.id)
            .order_by(Machine.name)
            .all()
        )
    else:
        return {"status": "Unauthorized"}, 403

    return jsonify(data=[_serialize_machine(machine) for machine in machines])
