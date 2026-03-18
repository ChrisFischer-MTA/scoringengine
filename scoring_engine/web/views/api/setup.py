import re

from flask import jsonify, request

from scoring_engine.cache import cache
from scoring_engine.config import config as app_config
from scoring_engine.db import db
from scoring_engine.engine.engine import Engine
from scoring_engine.models.account import Account
from scoring_engine.models.environment import Environment
from scoring_engine.models.property import Property
from scoring_engine.models.service import Service
from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team
from scoring_engine.models.user import User

from . import mod


# ---------------------------------------------------------------------------
# Shared default content (also imported by bin/setup)
# ---------------------------------------------------------------------------

DEFAULT_WELCOME_CONTENT = """
<div class="row">
    <h1 class="text-center">Diamond Sponsors</h1>
</div>
<div class="row">
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
</div>
<div class="row">
    <h1 class="text-center">Platinum Sponsors</h1>
</div>
<div class="row">
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
</div>
<div class="row">
    <h1 class="text-center">Gold Sponsors</h1>
</div>
<div class="row">
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
    <div class="col-xs-12 col-md-4">
        <div class="card">
            <img class='center-block' src="static/images/logo-placeholder.jpg" alt="sponsor image placeholder">
        </div>
    </div>
</div>
"""


# ---------------------------------------------------------------------------
# Bracket-notation form parser
# ---------------------------------------------------------------------------

def _parse_key(key):
    """'services[0][accounts][1][username]' -> ['services', '0', 'accounts', '1', 'username']"""
    parts = re.split(r"\[|\]", key)
    return [p for p in parts if p != ""]


def _set_nested(obj, keys, value):
    for key in keys[:-1]:
        if key not in obj:
            obj[key] = {}
        obj = obj[key]
    last = keys[-1]
    if last in obj:
        existing = obj[last]
        if isinstance(existing, list):
            existing.append(value)
        else:
            obj[last] = [existing, value]
    else:
        obj[last] = value


def _dicts_to_lists(obj):
    """Recursively convert dicts with all-integer keys to sorted lists."""
    if isinstance(obj, dict):
        converted = {k: _dicts_to_lists(v) for k, v in obj.items()}
        if converted and all(k.isdigit() for k in converted.keys()):
            return [converted[k] for k in sorted(converted.keys(), key=int)]
        return converted
    elif isinstance(obj, list):
        return [_dicts_to_lists(v) for v in obj]
    return obj


def _parse_bracket_form(form):
    result = {}
    for key, value in form.items(multi=True):
        _set_nested(result, _parse_key(key), value)
    return _dicts_to_lists(result)


# ---------------------------------------------------------------------------
# Config builder
# ---------------------------------------------------------------------------

def _to_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_teams(raw):
    return [
        {"name": t.get("name", ""), "username": t.get("username", ""), "password": t.get("password", "")}
        for t in (raw if isinstance(raw, list) else [])
        if t.get("name")
    ]


def _parse_services(raw):
    services = []
    for svc in (raw if isinstance(raw, list) else []):
        props = [
            {"name": p.get("name", ""), "value": p.get("value", "")}
            for p in (svc.get("properties", []) if isinstance(svc.get("properties"), list) else [])
            if p.get("name") and p.get("value") is not None
        ]
        environments = [{"matching_content": svc.get("matching_content", ""), "properties": props}]

        team_hosts_raw = svc.get("team_hosts", {})
        if not isinstance(team_hosts_raw, dict):
            team_hosts_raw = {}

        accounts_raw = svc.get("accounts", [])
        accounts = [
            {"username": a.get("username", ""), "password": a.get("password", "")}
            for a in (accounts_raw if isinstance(accounts_raw, list) else [])
            if a.get("username")
        ]

        services.append({
            "name": svc.get("name", ""),
            "check_name": svc.get("check_name", ""),
            "port": _to_int(svc.get("port", 0)),
            "points": _to_int(svc.get("points", 100)),
            "worker_queue": "main",
            "team_hosts": team_hosts_raw,
            "accounts": accounts,
            "environments": environments,
        })
    return services


def _build_config(parsed):
    return {
        "admin": {
            "admin_username": parsed.get("admin_username", ""),
            "admin_password": parsed.get("admin_password", ""),
        },
        "red_team": {
            "username": parsed.get("red_team_username", ""),
            "password": parsed.get("red_team_password", ""),
        },
        "competition": {
            "competition_name": parsed.get("competition_name", ""),
            "scoring_interval": parsed.get("scoring_interval", "300"),
        },
        "teams": _parse_teams(parsed.get("teams")),
        "services": _parse_services(parsed.get("services")),
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _get_known_check_names():
    return {c.__name__ for c in Engine.load_check_files(app_config.checks_location)}


def _validate_config(config):
    errors = []

    if not config["competition"]["competition_name"]:
        errors.append("Competition name is required.")

    if not config["admin"]["admin_username"]:
        errors.append("Admin username is required.")
    if not config["admin"]["admin_password"]:
        errors.append("Admin password is required.")

    if not config["red_team"]["username"]:
        errors.append("Red team username is required.")
    if not config["red_team"]["password"]:
        errors.append("Red team password is required.")

    if not config["teams"]:
        errors.append("At least one blue team is required.")
    for team in config["teams"]:
        if not team["username"]:
            errors.append(f"Team '{team['name']}' is missing a username.")
        if not team["password"]:
            errors.append(f"Team '{team['name']}' is missing a password.")

    all_usernames = (
        [config["admin"]["admin_username"], config["red_team"]["username"]]
        + [t["username"] for t in config["teams"]]
    )
    seen = set()
    for u in all_usernames:
        if u in seen:
            errors.append(f"Duplicate username: '{u}'.")
        seen.add(u)

    if not config["services"]:
        errors.append("At least one service is required.")

    known_checks = _get_known_check_names()
    team_names = {t["name"] for t in config["teams"]}
    for svc in config["services"]:
        if not svc["name"]:
            errors.append("Every service must have a name.")
        if svc["check_name"] not in known_checks:
            errors.append(f"Unknown check type: '{svc['check_name']}'.")
        if not (0 <= svc["port"] <= 65535):
            errors.append(f"Service '{svc['name']}' has an invalid port.")
        if svc["points"] <= 0:
            errors.append(f"Service '{svc['name']}' must have points > 0.")
        missing_hosts = team_names - set(svc["team_hosts"].keys())
        if missing_hosts:
            errors.append(
                f"Service '{svc['name']}' is missing hosts for: {', '.join(missing_hosts)}."
            )

    return errors


# ---------------------------------------------------------------------------
# DB writer
# ---------------------------------------------------------------------------

def _write_to_db(config):
    if Team.query.filter_by(color="White").first():
        raise RuntimeError("Competition is already configured.")

    white_team = Team(name="White Team", color="White")
    db.session.add(white_team)
    db.session.add(User(
        username=config["admin"]["admin_username"],
        password=config["admin"]["admin_password"],
        team=white_team,
    ))

    red_team = Team(name="Red Team", color="Red")
    db.session.add(red_team)
    db.session.add(User(
        username=config["red_team"]["username"],
        password=config["red_team"]["password"],
        team=red_team,
    ))

    blue_teams = {}
    for t in config["teams"]:
        team_obj = Team(name=t["name"], color="Blue")
        db.session.add(team_obj)
        db.session.add(User(username=t["username"], password=t["password"], team=team_obj))
        blue_teams[t["name"]] = team_obj

    db.session.flush()

    for svc in config["services"]:
        for team_name, team_obj in blue_teams.items():
            host = svc["team_hosts"].get(team_name, "")
            svc_obj = Service(
                name=svc["name"],
                team=team_obj,
                check_name=svc["check_name"],
                host=host,
                port=svc["port"],
                points=svc["points"],
            )
            svc_obj.worker_queue = svc.get("worker_queue", "main")
            db.session.add(svc_obj)
            for acc in svc.get("accounts", []):
                db.session.add(Account(
                    username=acc["username"],
                    password=acc["password"],
                    service=svc_obj,
                ))
            env = svc["environments"][0]
            env_obj = Environment(
                service=svc_obj,
                matching_content=env.get("matching_content", ""),
            )
            db.session.add(env_obj)
            for prop in env.get("properties", []):
                db.session.add(Property(
                    environment=env_obj,
                    name=prop["name"],
                    value=prop["value"],
                ))

    db.session.commit()

    scoring_interval = _to_int(config["competition"]["scoring_interval"], 300)
    for s in [
        Setting(name="about_page_content", value=""),
        Setting(name="welcome_page_content", value=DEFAULT_WELCOME_CONTENT),
        Setting(name="target_round_time", value=scoring_interval),
        Setting(name="worker_refresh_time", value=app_config.worker_refresh_time),
        Setting(name="engine_paused", value=app_config.engine_paused),
        Setting(name="pause_duration", value=app_config.pause_duration),
        Setting(name="blue_team_update_hostname", value=app_config.blue_team_update_hostname),
        Setting(name="blue_team_update_port", value=app_config.blue_team_update_port),
        Setting(name="blue_team_update_account_usernames", value=app_config.blue_team_update_account_usernames),
        Setting(name="blue_team_update_account_passwords", value=app_config.blue_team_update_account_passwords),
        Setting(name="blue_team_view_check_output", value=app_config.blue_team_view_check_output),
        Setting(name="blue_team_view_status_page", value=app_config.blue_team_view_status_page),
        Setting(name="blue_team_view_current_status", value=app_config.blue_team_view_current_status),
        Setting(name="blue_team_view_historical_status", value=app_config.blue_team_view_historical_status),
        Setting(name="agent_checkin_interval_sec", value=app_config.target_round_time // 5),
        Setting(name="agent_show_flag_early_mins", value=app_config.agent_show_flag_early_mins),
        Setting(name="agent_psk", value=app_config.agent_psk),
    ]:
        db.session.add(s)
    db.session.commit()
    Setting.clear_cache()


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@mod.route("/api/setup/checks", methods=["GET"])
def api_setup_checks():
    """Return the list of available check types for the setup wizard dropdown."""
    checks = sorted(c.__name__ for c in Engine.load_check_files(app_config.checks_location))
    return jsonify({"checks": checks})


@mod.route("/api/setup", methods=["POST"])
def api_setup():
    # Rate limit: max 10 attempts per minute per IP to prevent abuse before
    # setup completes (once setup is done every request returns 403 anyway).
    ip = request.environ.get("HTTP_X_REAL_IP") or request.remote_addr or "unknown"
    rate_key = f"setup_rate:{ip}"
    attempts = cache.get(rate_key) or 0
    if attempts >= 10:
        return jsonify({"status": "error", "message": "Too many requests. Please wait a moment."}), 429
    cache.set(rate_key, attempts + 1, timeout=60)

    if Team.get_all_blue_teams():
        return jsonify({"status": "error", "message": "Already configured."}), 403

    parsed = _parse_bracket_form(request.form)
    cfg = _build_config(parsed)

    errors = _validate_config(cfg)
    if errors:
        return jsonify({"status": "error", "message": errors[0]}), 400

    try:
        _write_to_db(cfg)
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": f"Database error: {e}"}), 500

    return jsonify({"status": "ok", "message": "Setup complete."})
