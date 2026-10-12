# stored as varchar + check constraint instead of native pg enums,
# adding a value to a native enum needs its own migration


from enum import StrEnum


class UserRole(StrEnum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"


class JobStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"


class RequirementCategory(StrEnum):
    # job-related categories only, nothing protected can be scored

    TECHNICAL_SKILL = "technical_skill"
    EXPERIENCE = "experience"
    QUALIFICATION = "qualification"
    RESPONSIBILITY = "responsibility"
    OTHER = "other"


class PolicyStatus(StrEnum):
    UPLOADED = "uploaded"
    INDEXING = "indexing"
    INDEXED = "indexed"
    FAILED = "failed"


class ExtractionStatus(StrEnum):
    # UNREADABLE is a normal outcome, the batch skips it and carries on

    PENDING = "pending"
    EXTRACTING = "extracting"
    EXTRACTED = "extracted"
    UNREADABLE = "unreadable"
    FAILED = "failed"


class RequirementMatchStatus(StrEnum):
    MET = "met"
    PARTIALLY_MET = "partially_met"
    UNMET = "unmet"


class ScreeningStatus(StrEnum):
    PENDING = "pending"
    SCREENED = "screened"
    EXCLUDED = "excluded"  # failed a mandatory requirement
    FAILED = "failed"


class ScoreDimensionName(StrEnum):
    TECHNICAL_SKILLS = "technical_skills"
    RELEVANT_EXPERIENCE = "relevant_experience"
    QUALIFICATIONS = "qualifications"
    JD_MATCH = "jd_match"


class InterviewStatus(StrEnum):
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class SpeakerRole(StrEnum):
    INTERVIEWER = "interviewer"
    CANDIDATE = "candidate"


class CoverageStatus(StrEnum):
    COVERED = "covered"
    PARTIALLY_COVERED = "partially_covered"
    NOT_EXPLORED = "not_explored"


class FindingKind(StrEnum):
    STRENGTH = "strength"
    WEAKNESS = "weakness"
    EVIDENCE_GAP = "evidence_gap"
    AREA_TO_VERIFY = "area_to_verify"


class EvidenceType(StrEnum):
    # every finding points at its source row instead of free text

    CV = "cv"
    TRANSCRIPT = "transcript"
    SCORE = "score"


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
