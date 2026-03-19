from flask import Blueprint, redirect, render_template, url_for

from scoring_engine.models.setting import Setting
from scoring_engine.models.team import Team

mod = Blueprint('welcome', __name__)


@mod.route('/')
@mod.route("/index")
def home():
    if not Team.get_all_blue_teams():
        return redirect(url_for('setup.setup'))
    welcome_content = Setting.get_setting('welcome_page_content').value
    return render_template('welcome.html', welcome_content=welcome_content)
