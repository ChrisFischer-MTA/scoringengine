from flask import Blueprint, redirect, render_template, url_for

from scoring_engine.models.team import Team

mod = Blueprint("setup", __name__)


@mod.route("/setup")
def setup():
    if Team.get_all_blue_teams():
        return redirect(url_for("auth.login"))
    return render_template("setup.html")
