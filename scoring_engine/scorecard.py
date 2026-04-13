from collections import defaultdict

from sqlalchemy.sql import func

from scoring_engine.db import db
from scoring_engine.models.check import Check
from scoring_engine.models.inject import Inject
from scoring_engine.models.round import Round
from scoring_engine.models.service import Service
from scoring_engine.models.team import Team
from scoring_engine.sla import (apply_dynamic_scoring_to_round,
                                calculate_team_total_penalties, get_sla_config)


# from web/views/api/overview.py
def _calculate_ranks(score_dict):
    """
    Calculate ranks for a dict of {id: score} with tie handling.
    Returns dict of {id: rank} where ties get the same rank.
    """
    if not score_dict:
        return {}
    sorted_items = sorted(score_dict.items(), key=lambda x: x[1], reverse=True)
    ranks = {}
    current_rank = 1
    prev_score = None
    for i, (item_id, score) in enumerate(sorted_items):
        if prev_score is not None and score < prev_score:
            current_rank = i + 1
        ranks[item_id] = current_rank
        prev_score = score
    return ranks


# from web/views/api/scoreboard.py
def _calculate_team_scores_with_dynamic_scoring(sla_config):
    """
    Calculate team scores with dynamic scoring multipliers applied per-round.

    Returns dict mapping team_id to total score with multipliers applied.
    """
    if not sla_config.dynamic_enabled:
        # No dynamic scoring - use simple sum
        return dict(
            db.session.query(Service.team_id, func.sum(Service.points))
            .join(Check)
            .filter(Check.result.is_(True))
            .group_by(Service.team_id)
            .all()
        )

    # Query scores grouped by team and round
    round_scores = (
        db.session.query(
            Service.team_id,
            Check.round_id,
            func.sum(Service.points).label("round_score"),
        )
        .join(Check)
        .filter(Check.result.is_(True))
        .group_by(Service.team_id, Check.round_id)
        .all()
    )

    # Get round numbers for each round_id
    rounds = {r.id: r.number for r in db.session.query(Round.id, Round.number).all()}

    # Calculate total with multipliers
    team_scores = defaultdict(int)
    for team_id, round_id, round_score in round_scores:
        round_number = rounds.get(round_id, 0)
        adjusted_score = apply_dynamic_scoring_to_round(
            round_number, round_score, sla_config
        )
        team_scores[team_id] += adjusted_score

    return dict(team_scores)


# based on web/views/api/scoreboard.py
def get_scorecard_data():
    # Get SLA configuration first (needed for dynamic scoring)
    sla_config = get_sla_config()

    current_scores = calculate_team_scores_with_dynamic_scoring(sla_config)

    inject_scores = dict(
        db.session.query(Inject.team_id, func.sum(Inject.score))
        .filter(Inject.status == "Graded")
        .group_by(Inject.team_id)
        .all()
    )

    team_data = {}
    team_labels = []
    team_scores = []
    team_inject_scores = []
    team_sla_penalties = []
    team_adjusted_scores = []

    blue_teams = (
        db.session.query(Team).filter(Team.color == "Blue").order_by(Team.id).all()
    )
    for blue_team in blue_teams:
        team_labels.append(blue_team.name)
        service_score = current_scores.get(blue_team.id, 0)
        inject_score = inject_scores.get(blue_team.id, 0)
        team_scores.append(str(service_score))
        team_inject_scores.append(str(inject_score))

        # Calculate SLA penalties if enabled
        # Total base score includes both service and inject scores
        total_base_score = service_score + inject_score
        if sla_config.sla_enabled:
            penalty = calculate_team_total_penalties(blue_team, sla_config)
            team_sla_penalties.append(str(penalty))
            if sla_config.allow_negative:
                adjusted = total_base_score - penalty
            else:
                adjusted = max(0, total_base_score - penalty)
            team_adjusted_scores.append(str(adjusted))
        else:
            team_sla_penalties.append("0")
            team_adjusted_scores.append(str(total_base_score))

    team_data["team_names"] = team_labels
    team_data["service_scores"] = team_scores
    team_data["inject_scores"] = team_inject_scores
    team_data["total_scores"] = team_adjusted_scores
    team_data["service_ranks"] = _calculate_ranks(team_scores)
    team_data["inject_ranks"] = _calculate_ranks(team_inject_scores)
    team_data["overall_ranks"] = _calculate_ranks(team_adjusted_scores)
    return team_data

