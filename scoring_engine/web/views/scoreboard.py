from flask import Blueprint, render_template
from flask_login import current_user

mod = Blueprint('scoreboard', __name__)


@mod.route('/scoreboard')
def home():
    is_blue_team = current_user.is_blue_team
    is_paused = Setting.get_bool("engine_paused", default=False)
    are_scorecards_published = Setting.get_bool("scorecards_published", default=False)
    can_download = is_blue_team and is_paused and are_scorecards_published
    return render_template('scoreboard.html', can_download_scorecard=can_download)
