import datetime

from sqlalchemy import Column, Integer, ForeignKey, DateTime, LargeBinary
from sqlalchemy.orm import relationship

from fpdf import FPDF

from scoring_engine.models.base import Base
from scoring_engine.models.setting import Setting
from scoring_engine.db import db
from scoring_engine.scorecard import get_scorecard_data

def _clear_scorecards():
    db.session.query(Scorecard).delete()

def _flag_as_published():
    published_setting = Setting.get_setting("scorecards_published")
    published_setting.value = True
    db.session.add(published_setting)
    db.session.commit()
    Setting.clear_cache("scorecards_published")

class Scorecard(Base):
    __tablename__ = "scorecards"
    id = Column(Integer, primary_key=True)
    file = Column(LargeBinary)
    created = Column(DateTime, default=datetime.datetime.utcnow)

    team_id = Column(Integer, ForeignKey("teams.id"))

    @staticmethod
    def generate_scorecards():
        is_paused = Setting.get_bool("engine_paused", default=False)
        if not is_paused:
            return

        _clear_scorecards()

        scorecard_data = get_scorecard_data()
        team_names = scorecard_data["team_names"]
        service_scores = scorecard_data["service_scores"]
        inject_scores = scorecard_data["inject_scores"]
        total_scores = scorecard_data["total_scores"]
        service_ranks = scorecard_data["service_ranks"]
        inject_ranks = scorecard_data["inject_ranks"]
        overall_ranks = scorecard_data["overall_ranks"]

        # generate a pdf for each team
        for team_id in team_names:
            team_name = team_names[team_id]
            service_score = service_scores[team_id]
            inject_score = inject_scores[team_id]
            total_score = total_scores[team_id]
            service_rank = service_ranks[team_id]
            inject_rank = inject_ranks[team_id]
            overall_rank = overall_ranks[team_id]

            pdf = FPDF()
            pdf.add_page()

            pdf.set_font("helvetica", style="B", size=32)
            pdf.cell(text=f"{team_name} (#{team_id})")
            pdf.ln()

            pdf.set_font("helvetica", style="B", size=24)
            pdf.cell(text=f"Service score: ")
            pdf.set_font("helvetica", size=24)
            pdf.cell(text=f"{service_score} (rank {service_rank})")
            pdf.ln()

            pdf.set_font("helvetica", style="B", size=24)
            pdf.cell(text=f"Inject score: ")
            pdf.set_font("helvetica", size=24)
            pdf.cell(text=f"{inject_score} (rank {inject_rank})")
            pdf.ln()

            pdf.set_font("helvetica", style="B", size=24)
            pdf.cell(text=f"Total score: ")
            pdf.set_font("helvetica", size=24)
            pdf.cell(text=f"{total_score} (rank {overall_rank})")

            scorecard = Scorecard(file=pdf.output(), team_id=team_id)
            db.session.add(scorecard)

        _flag_as_published()

