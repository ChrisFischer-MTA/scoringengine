from datetime import datetime, timedelta
from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from scoring_engine.models.base import Base


class Machine(Base):
    __tablename__ = "machines"

    STATUS_UNKNOWN = "unknown"
    STATUS_HEALTHY = "healthy"
    STATUS_COMPROMISED = "compromised"
    STATUS_OFFLINE = "offline"

    id = Column(Integer, primary_key=True)
    team_id = Column(Integer, ForeignKey("teams.id"))
    name = Column(String(100), nullable=False)
    status = Column(String(20), nullable=False, default=STATUS_UNKNOWN)
    last_check_in_at = Column(DateTime, nullable=True, index=True)
    first_check_in_at = Column(DateTime, nullable=True)
    last_status_change_at = Column(DateTime, nullable=True, index=True)

    def mark_check_in(self, at=None):
        now = at or datetime.utcnow()
        if self.first_check_in_at is None:
            self.first_check_in_at = now
        self.last_check_in_at = now

    def update_status(self, new_status, at=None):
        if self.status != new_status:
            self.status = new_status
            self.last_status_change_at = at or datetime.utcnow()

