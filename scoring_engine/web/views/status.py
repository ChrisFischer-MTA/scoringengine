from flask import Blueprint, render_template, url_for, redirect
from flask_login import login_required, current_user
from scoring_engine.models.setting import Setting


mod = Blueprint('status', __name__)


@mod.route('/status')
@login_required
def status():
    if current_user.is_white_team or current_user.is_red_team:
        return render_template("status.html")
    elif current_user.is_blue_team and Setting.get_setting('blue_team_view_status_page').value is True:
        return render_template(
            "status.html",
            current_enabled=Setting.get_setting('blue_team_view_current_status').value,
            historical_enabled=Setting.get_setting('blue_team_view_historical_status').value,
        )
    else:
        return redirect(url_for("auth.unauthorized"))
