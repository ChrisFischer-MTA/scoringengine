import io
# import pytz

from flask import jsonify, send_file
from flask_login import current_user, login_required

from scoring_engine.config import config
from scoring_engine.db import db
from scoring_engine.models.setting import Setting
from scoring_engine.models.scorecard import Scorecard

from . import mod


def _prepare_scorecard_payload(team_id):
    # disallow retrieving before scorecards are all marked as published
    is_page_enabled = Setting.get_bool("scorecards_published", default=False)
    if not is_page_enabled:
        return jsonify({"error": "unauthorized"}), 403

    scorecard = (
        db.session.query(Scorecard)
        .filter(Scorecard.team_id == team_id)
        .first()
    )

    if scorecard is None:
        return jsonify({"error": "Scorecard not found"}), 404

    prepared_file = io.BytesIO(scorecard.file)
    return send_file(
        prepared_file,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="scorecard.pdf"
    )


@mod.route("/api/scorecard/download")
@login_required
def api_scorecard_download():
    return _prepare_scorecard_payload(current_user.team_id)

@mod.route("/api/scorecard/download/<int:team_id>")
@login_required
def api_scorecard_download_targeted():
    # only allow white team to give a specific team id
    if not current_user.is_white_team:
        return jsonify({"error": "unauthorized"}), 403

    return _prepare_scorecard_payload(team_id)

