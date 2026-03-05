from collections import defaultdict
from flask import jsonify
from flask_login import current_user, login_required
from sqlalchemy import desc, func
from sqlalchemy.orm import subqueryload

from scoring_engine.cache import cache
from scoring_engine.db import db
from scoring_engine.models.check import Check
from scoring_engine.models.round import Round
from scoring_engine.models.service import Service
from scoring_engine.models.team import Team
from scoring_engine.sla import get_sla_config
from scoring_engine.models.flag import Flag, Solve
from scoring_engine.models.machines import Machine
from scoring_engine.models.setting import Setting
from . import make_cache_key, mod


def calculate_ranks(score_dict):
    """
    Calculate ranks for a dict of {id: score} with tie handling.

    Returns dict of {id: rank} where ties get the same rank.
    E.g., scores [100, 90, 90, 80] -> ranks [1, 2, 2, 4]
    """
    if not score_dict:
        return {}

    # Sort by score descending
    sorted_items = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)

    ranks = {}
    current_rank = 1
    prev_score = None

    for i, (item_id, score) in enumerate(sorted_items):
        if prev_score is not None and score < prev_score:
            current_rank = i + 1  # Skip ranks for ties
        ranks[item_id] = current_rank
        prev_score = score

    return ranks


@mod.route("/api/team/<team_id>/stats")
@login_required
@cache.cached(make_cache_key=make_cache_key)
def services_get_team_data(team_id):
    team = db.session.get(Team, team_id)
    if team is None or not current_user.team == team or not current_user.is_blue_team:
        return {"status": "Unauthorized"}, 403

    data = {"place": str(team.place), "current_score": str(team.current_score)}
    return jsonify(data)


@mod.route("/api/team/<team_id>/services")
@login_required
@cache.cached(make_cache_key=make_cache_key)
def api_services(team_id):
    team = db.session.get(Team, team_id)
    if team is None or not current_user.team == team or not current_user.is_blue_team:
        return {"status": "Unauthorized"}, 403

    data = []

    # Get SLA config for dynamic scoring
    sla_config = get_sla_config()
    dynamic_enabled = sla_config.dynamic_enabled

    if dynamic_enabled:
        # Pre-fetch config values for performance
        early_rounds = sla_config.early_rounds
        early_multiplier = sla_config.early_multiplier
        late_start = sla_config.late_start_round
        late_multiplier = sla_config.late_multiplier

        # Query for dynamic scoring: get service scores with round numbers via JOIN
        # This is more efficient than querying all rounds separately
        service_check_data = (
            db.session.query(
                Service.team_id,
                Service.name,
                Service.points,
                Round.number,
                Check.result,
            )
            .select_from(Check)
            .join(Service, Check.service_id == Service.id)
            .join(Round, Check.round_id == Round.id)
            .all()
        )

        # Calculate dynamic scores per service per team
        service_dict = defaultdict(lambda: defaultdict(int))
        service_max_dict = defaultdict(lambda: defaultdict(int))

        for svc_team_id, name, points, round_number, result in service_check_data:
            # Inline multiplier calculation for performance
            if round_number <= early_rounds:
                multiplier = early_multiplier
            elif round_number >= late_start:
                multiplier = late_multiplier
            else:
                multiplier = 1.0

            dynamic_points = int(points * multiplier)
            service_max_dict[name][svc_team_id] += dynamic_points
            if result:
                service_dict[name][svc_team_id] += dynamic_points
    else:
        # Original non-dynamic scoring logic
        service_scores = (
            db.session.query(Service.team_id, Service.name, func.sum(Service.points).label("score"))
            .join(Check)
            .filter(Check.result.is_(True))
            .group_by(Service.team_id, Service.name)
            .order_by(Service.name, desc("score"))
            .all()
        )

        service_dict = defaultdict(lambda: defaultdict(int))
        for svc_team_id, name, points in service_scores:
            service_dict[name][svc_team_id] = points

        service_max_dict = None  # Will calculate per-service below

    # Calculate ranks based on scores
    service_ranks = defaultdict(lambda: defaultdict(int))
    for service_name in service_dict.keys():
        service_ranks[service_name] = calculate_ranks(service_dict[service_name])

    services = (
        db.session.query(Service)
        .options(subqueryload(Service.checks))
        .options(subqueryload(Service.team))
        .filter(Service.team_id == team.id)
        .order_by(Service.id)
        .all()
    )

    for service in services:
        score_earned = service_dict[service.name].get(service.team_id, 0)

        if dynamic_enabled and service_max_dict:
            max_score = service_max_dict[service.name].get(service.team_id, 0)
        else:
            max_score = len(service.checks) * service.points

        percent_earned = "{:.1%}".format(score_earned / max_score if max_score != 0 else 0)

        if not service.checks:
            check = "Undetermined"
        else:
            if service.last_check_result():
                check = "UP"
            else:
                check = "DOWN"
        data.append(
            dict(
                service_id=str(service.id),
                service_name=str(service.name),
                host=str(service.host),
                port=str(service.port),
                check=str(check),
                rank=str(service_ranks[service.name].get(service.team_id, 1)),
                score_earned=str(score_earned),
                max_score=str(max_score),
                percent_earned=percent_earned,
                pts_per_check=str(service.points),
                last_ten_checks=[check.result for check in service.last_ten_checks[::-1]],
            )
        )
    return jsonify(data=data)


@mod.route("/api/team/<team_id>/services/status")
@login_required
@cache.cached(make_cache_key=make_cache_key)
def team_services_status(team_id):
    team = db.session.get(Team, team_id)
    if team is None or not current_user.team == team or not current_user.is_blue_team:
        return {"status": "Unauthorized"}, 403

    data = {}

    round_obj = db.session.query(Round.id).order_by(Round.number.desc()).first()

    # We have no round data, the first round probably hasn't started yet
    if not round_obj:
        return data

    round_id = round_obj[0]

    checks = (
        db.session.query(
            Service.name,
            Check.service_id,
            Check.result,
        )
        .select_from(Check)
        .join(Service)
        .filter(Service.team_id == team_id)
        .filter(Check.round_id == round_id)
        .order_by(Service.name)
        .all()
    )

    for service_name, service_id, check_result in checks:
        data[service_name] = {
            "id": str(service_id),
            "result": str(check_result),
        }
    return jsonify(data)

@mod.route("/api/team/<team_id>/hosts")
@login_required
@cache.cached(make_cache_key=make_cache_key)
def team_hosts(team_id):
    team = db.session.get(Team, team_id)
    if team is None or not current_user.is_blue_team or current_user.team != team:
        return {"status": "Unauthorized"}, 403

    hosts = (
        db.session.query(Service.host)
        .filter(Service.team_id == team.id)
        .distinct()
        .order_by(Service.host)
        .all()
    )
    data = [{"host": host} for (host,) in hosts]
    return jsonify(data=data)


def _iso_or_none(value):
    return value.isoformat() if value is not None else None

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

def _can_access_team_history(team):
    if team is None:
        return False
    if current_user.is_white_team or current_user.is_red_team:
        return True
    if current_user.is_blue_team and current_user.team == team:
      if not _setting_bool("blue_team_view_status_page", default=True):
          return False
      if not _setting_bool("blue_team_view_historical_status", default=True):
          return False
      return True
    return False


@mod.route("/api/team/<team_id>/machine-history")
@login_required
def team_machine_history(team_id):
    """
    Returns per-machine compromise history by flag rotation window.

    Response shape:
    {
      "data": {
        "team_id": 3,
        "columns": [
          {"index": 0, "start_time": "...", "end_time": "...", "label": "..."},
          ... newest first ...
        ],
        "rows": [
          {"machine_id": 10, "host": "webserver.team3.local", "history": [true, false, ...]},
          ...
        ]
      }
    }
    """
    team = db.session.get(Team, team_id)
    if not _can_access_team_history(team):
        return {"status": "Unauthorized"}, 403

    # 1) Distinct rotation windows from flags, newest first, excluding dummy flags.
    windows = (
        db.session.query(Flag.start_time, Flag.end_time)
        .filter(Flag.dummy.is_(False))
        .distinct()
        .order_by(Flag.start_time.desc(), Flag.end_time.desc())
        .all()
    )

    # 2) Team machines (host names are in Machine.name).
    machines = (
        db.session.query(Machine)
        .filter(Machine.team_id == team.id)
        .order_by(Machine.name)
        .all()
    )

    # 3) Solve hits keyed by (host, rotation window).
    solve_hits = (
        db.session.query(Solve.host, Flag.start_time, Flag.end_time)
        .join(Flag, Solve.flag_id == Flag.id)
        .filter(Solve.team_id == team.id)
        .filter(Flag.dummy.is_(False))
        .distinct()
        .all()
    )

    compromised_keys = {
        ((host or "").strip().lower(), start_time, end_time)
        for host, start_time, end_time in solve_hits
    }

    columns = []
    for idx, (start_time, end_time) in enumerate(windows):
        columns.append(
            {
                "index": idx,
                "start_time": _iso_or_none(start_time),
                "end_time": _iso_or_none(end_time),
                "label": f"{_iso_or_none(start_time)} -> {_iso_or_none(end_time)}",
            }
        )

    rows = []
    for machine in machines:
        host_norm = (machine.name or "").strip().lower()
        history = [
            (host_norm, start_time, end_time) in compromised_keys
            for start_time, end_time in windows
        ]

        rows.append(
            {
                "machine_id": machine.id,
                "host": machine.name,
                "history": history,
            }
        )

    return jsonify(
        data={
            "team_id": team.id,
            "columns": columns,
            "rows": rows,
        }
    )