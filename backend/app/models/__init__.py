# import every model here or alembic autogenerate silently misses it

from app.db.base import Base
from app.models.analysis import (
    AgentRun,
    Assessment,
    AssessmentFinding,
    CopilotMessage,
    CopilotSuggestion,
    InterviewAnalysis,
    QaPair,
    QaRequirementLink,
)
from app.models.candidate import Application, Candidate, CvDocument, CvExtraction
from app.models.integration import IntegrationAccount
from app.models.interview import Interview, InterviewParticipant, TranscriptTurn
from app.models.job import (
    CompanyPolicy,
    Job,
    JobDescriptionMessage,
    JobDescriptionVersion,
    JobPost,
    JobRequirement,
)
from app.models.screening import (
    CandidateScore,
    ScoreDimension,
    Screening,
    ScreeningRequirementResult,
)
from app.models.user import User

__all__ = [
    "Base",
    # users
    "User",
    # policy and jobs
    "CompanyPolicy",
    "Job",
    "JobRequirement",
    "JobPost",
    "JobDescriptionVersion",
    "JobDescriptionMessage",
    # candidates and cvs
    "Candidate",
    "Application",
    "CvDocument",
    "CvExtraction",
    # screening and scoring
    "Screening",
    "ScreeningRequirementResult",
    "CandidateScore",
    "ScoreDimension",
    # interviews
    "Interview",
    "InterviewParticipant",
    "TranscriptTurn",
    # analysis and assessment
    "QaPair",
    "QaRequirementLink",
    "Assessment",
    "AssessmentFinding",
    "CopilotMessage",
    "CopilotSuggestion",
    "InterviewAnalysis",
    "AgentRun",
    # integrations
    "IntegrationAccount",
]
