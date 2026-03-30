from sqlalchemy import Column, Integer, ForeignKey, Text, String
from sqlalchemy.orm import relationship

from scoring_engine.models.base import Base


class SSOCredential(Base):
    __tablename__ = "sso_credentials"
    id = Column(Integer, primary_key=True)
    username = Column(String(50), nullable=False)
    password = Column(Text, nullable=False)
    team_id = Column(Integer, ForeignKey('teams.id'))
    team = relationship("Team", back_populates="sso_credentials")
