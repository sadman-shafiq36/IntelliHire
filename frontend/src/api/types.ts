// hand-written, keep in step with backend/app/schemas/

export type UserRole = 'interviewer' | 'candidate'

export interface User {
  id: number
  email: string
  full_name: string
  role: UserRole
  is_active: boolean
}

export interface TokenResponse {
  access_token: string
  token_type: string
  expires_in_minutes: number
  user: User
}

export interface AuthOptions {
  registration_open: boolean
}

export interface RegisterPayload {
  email: string
  password: string
  full_name: string
  role: UserRole
}

export interface LoginPayload {
  email: string
  password: string
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    details?: Record<string, unknown>
  }
}

export interface JobProgress {
  job_id: string
  kind: string
  state: 'queued' | 'running' | 'finished' | 'failed'
  message: string
  total: number
  completed: number
  percent: number
  failed_items: Array<Record<string, unknown>>
  result_ref: Record<string, unknown> | null
  updated_at: string
}

export interface DependencyStatus {
  status: 'ok' | 'unavailable'
  model?: string
}

export interface ReadinessResponse {
  status: 'ok' | 'degraded'
  dependencies: Record<string, DependencyStatus>
  configuration: {
    gemini_api_key_present: boolean
    jwt_secret_configured: boolean
  }
}

export type PolicyStatus = 'uploaded' | 'indexing' | 'indexed' | 'failed'

export interface Policy {
  id: number
  title: string
  original_filename: string
  content_type: string
  size_bytes: number
  status: PolicyStatus
  chunk_count: number
  failure_reason: string | null
  created_at: string
}

export interface PolicyUploadResponse {
  policy: Policy
  background_job_id: string
}

export interface PolicySearchHit {
  chunk_id: string
  policy_id: number
  policy_title: string
  chunk_index: number
  text: string
  score: number
}

export type JobStatus = 'draft' | 'open' | 'closed'

export type RequirementCategory =
  | 'technical_skill'
  | 'experience'
  | 'qualification'
  | 'responsibility'
  | 'other'

export interface RequirementInput {
  id?: number
  text: string
  category: RequirementCategory
  is_mandatory: boolean
  weight: number
}

export interface Requirement extends RequirementInput {
  id: number
}

export interface JobInput {
  title: string
  department: string | null
  location: string | null
  employment_type: string | null
  min_years_experience: number
  summary: string | null
  requirements: RequirementInput[]
}

export interface PolicyCitation {
  chunk_id: string
  policy_id: number
  policy_title: string
  excerpt: string
  used_for: string[]
}

export interface Job extends JobInput {
  id: number
  status: JobStatus
  description_md: string | null
  policy_citations: PolicyCitation[] | null
  requirements: Requirement[]
  created_at: string
  updated_at: string
}

export interface JobListItem {
  id: number
  title: string
  department: string | null
  location: string | null
  status: JobStatus
  requirement_count: number
  mandatory_count: number
  has_description: boolean
  post_status: PostStatus | null
  application_count: number
  pending_cv_count: number
  screened_count: number
  scored_count: number
  unscored_count: number
  shortlisted_count: number
  to_schedule_count: number
  interview_count: number
  updated_at: string
}

export type DescriptionSource = 'generated' | 'chat' | 'manual' | 'restored'

export interface DescriptionVersion {
  id: number
  version: number
  source: DescriptionSource
  restored_from: number | null
  description_md: string
  created_at: string
}

export interface ChatMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  status: 'pending' | 'done' | 'failed'
  version: number | null
  warnings: string[]
  created_at: string
}

export interface ChatResponse {
  background_job_id: string
  message: ChatMessage
}

export interface IntegrationStatus {
  provider: 'linkedin' | 'google'
  label: string
  configured: boolean
  connected: boolean
  expired: boolean
  account_name: string | null
  expires_at: string | null
  template_configured: boolean | null
  scheduling_ready: boolean | null
}

export type PostStatus = 'preparing' | 'draft' | 'posted' | 'closed' | 'failed'

export interface JobPost {
  id: number
  job_id: number
  channel: string
  status: PostStatus
  text: string | null
  hashtags: string[] | null
  form_url: string | null
  form_edit_url: string | null
  post_url: string | null
  failure_reason: string | null
  posted_at: string | null
  closed_at: string | null
  last_synced_at: string | null
}

export interface Application {
  id: number
  candidate_id: number
  full_name: string
  email: string
  phone: string | null
  location: string | null
  source: string
  submitted_at: string | null
  years_experience: string | null
  linkedin_url: string | null
  cover_note: string | null
  cv_filename: string | null
  cv_status: string | null
}

export interface OverviewData {
  pipeline: {
    roles: number
    described: number
    live: number
    applications: number
    new_this_week: number
    screened: number
    shortlist_ready: number
    scored: number
    shortlisted: number
    selected: number
    interviews_upcoming: number
    interviewed: number
    interviews_live: number
  }
  policies_indexed: number
  attention: {
    kind:
      | 'integration'
      | 'description'
      | 'post'
      | 'publish'
      | 'failed'
      | 'sync'
      | 'policy'
      | 'screen'
      | 'score'
      | 'interview'
    title: string
    detail: string
    job_id: number | null
  }[]
  recent_applications: {
    id: number
    job_id: number
    job_title: string
    full_name: string
    submitted_at: string
  }[]
  live_jobs: {
    id: number
    title: string
    location: string | null
    applications: number
    post_url: string | null
  }[]
}

export interface CvDocumentItem {
  id: number
  filename: string
  source: 'google_form' | 'upload'
  status: 'pending' | 'extracting' | 'extracted' | 'unreadable' | 'failed'
  failure_reason: string | null
  used_ocr: boolean
  candidate_name: string | null
  uploaded_at: string
}

export interface UploadResult {
  accepted: { id: number; filename: string }[]
  rejected: { filename: string; reason: string }[]
}

export interface RankedCandidate {
  candidate_id: number
  full_name: string
  status: 'screened' | 'excluded'
  rank: number | null
  fulfilment_percent: number
  met: number
  partially_met: number
  unmet: number
  must_have_met: number
  must_have_total: number
  summary: string | null
  exclusion_reason: string | null
  years_experience: number | null
  headline: string | null
  screened_at: string | null
}

export interface ScreeningResults {
  ranked: RankedCandidate[]
  excluded: RankedCandidate[]
  not_screened: {
    cv_document_id: number
    filename: string
    reason: string | null
    candidate_name: string | null
  }[]
  pending_count: number
  last_screened_at: string | null
}

export type MatchStatus = 'met' | 'partially_met' | 'unmet'

export interface RequirementEvidence {
  requirement_id: number
  text: string
  category: RequirementCategory
  is_mandatory: boolean
  weight: number
  status: MatchStatus
  evidence_quote: string | null
  evidence_verified: boolean
  reasoning: string | null
  confidence: number | null
}

export interface CandidateProfile {
  headline?: string
  summary?: string
  total_years_experience?: number
  skills?: string[]
  education?: { qualification: string; institution: string; year: string }[]
  work_history?: {
    title: string
    organisation: string
    start: string
    end: string
    highlights: string[]
  }[]
  certifications?: string[]
  projects?: { name: string; description: string }[]
}

export interface CandidateScreening {
  candidate: {
    id: number
    full_name: string
    email: string | null
    phone: string | null
    location: string | null
  }
  status: 'screened' | 'excluded' | 'failed'
  rank: number | null
  fulfilment_percent: number
  summary: string | null
  exclusion_reason: string | null
  screened_at: string | null
  model_name: string | null
  cv: { document_id: number; filename: string; used_ocr: boolean; page_count: number | null } | null
  profile: CandidateProfile
  requirements: RequirementEvidence[]
}

export type ScoreDimensionName =
  | 'technical_skills'
  | 'relevant_experience'
  | 'qualifications'
  | 'jd_match'

export interface ScoreProfile {
  weights: Record<ScoreDimensionName, number>
  source: 'ai' | 'edited' | 'default'
  suggested: {
    weights: Record<ScoreDimensionName, number>
    reasons: Partial<Record<ScoreDimensionName, string>>
    model_name: string | null
    prompt_version: string | null
  } | null
  keywords: { term: string; aliases: string[] }[]
  applicable: ScoreDimensionName[]
}

export interface ScoredCandidate {
  candidate_id: number
  full_name: string
  rank: number
  aggregate_score: number
  dimensions: Partial<Record<ScoreDimensionName, number>>
  is_shortlisted: boolean
  selected_at: string | null
  stale: boolean
  fulfilment_percent: number
  headline: string | null
  years_experience: number | null
  computed_at: string | null
  warning_count: number
}

export interface ScoringResults {
  profile: ScoreProfile | null
  ranked: ScoredCandidate[]
  waiting_count: number
  excluded_count: number
  last_scored_at: string | null
}

export interface DimensionScore {
  dimension: ScoreDimensionName
  score: number
  base_score: number | null
  adjustment: number
  weight: number
  contribution: number
  justification: string | null
  details: {
    basis?: string
    quotes?: string[]
    requirements?: { requirement_id: number | null; text: string; status: MatchStatus; evidence_quote: string }[]
    found?: string[]
    missing?: string[]
    coverage?: number | null
    similarity?: number
    semantic?: number
  }
}

export interface ScoreBreakdown {
  candidate: { id: number; full_name: string }
  rank: number | null
  aggregate_score: number
  is_shortlisted: boolean
  selected_at: string | null
  stale: boolean
  model_name: string
  prompt_version: string
  computed_at: string | null
  warnings: string[]
  dimensions: DimensionScore[]
  not_scored: ScoreDimensionName[]
}

export type InterviewStatus =
  | 'requested'
  | 'scheduled'
  | 'failed'
  | 'in_progress'
  | 'completed'
  | 'cancelled'

export interface InterviewRow {
  id: number
  candidate_id: number
  full_name: string
  status: InterviewStatus
  scheduled_start: string | null
  scheduled_end: string | null
  timezone_name: string
  duration_minutes: number
  has_calendar_event: boolean
  invitation_channel: 'gmail' | 'smtp' | null
  invitation_sent_at: string | null
  pending_action: 'schedule' | 'reschedule' | 'cancel' | null
  last_outcome: string | null
  last_error: string | null
  cancellation_reason: string | null
  started_at: string | null
  ended_at: string | null
}

export interface JobInterviews {
  channel: 'google' | 'smtp'
  account: string | null
  needs_reconnect: boolean
  to_schedule: {
    candidate_id: number
    full_name: string
    has_email: boolean
    rank: number | null
    aggregate_score: number
  }[]
  interviews: InterviewRow[]
}

export interface ScheduleOptions {
  duration_minutes: number
  time_zone: string
  start?: string | null
  preference?: string | null
  allow_conflict?: boolean
  note?: string | null
}

export interface JoinDetails {
  job_title: string
  status: InterviewStatus
  scheduled_start: string | null
  scheduled_end: string | null
  timezone_name: string
  interviewer_name: string | null
}

// --- interview room -------------------------------------------------------------

export interface CallConnection {
  url: string
  token: string
  room: string
  identity: string
}

export interface TranscriptionState {
  // running, degraded (groq down, local whisper), stopped, off, finished, unknown
  state: string
  message: string
  provider?: 'groq' | 'whisper' | null
  backlog?: number | null
}

export interface SessionDetails {
  id: number
  job_id: number
  job_title: string
  candidate_id: number
  candidate_name: string
  status: InterviewStatus
  scheduled_start: string | null
  scheduled_end: string | null
  timezone_name: string
  duration_minutes: number
  started_at: string | null
  ended_at: string | null
  attendance: { role: 'interviewer' | 'candidate'; joined_at: string | null; left_at: string | null }[]
  turn_count: number
  transcription: TranscriptionState
}

export interface TranscriptTurn {
  id: number
  sequence: number
  speaker_role: 'interviewer' | 'candidate'
  speaker_label: string
  text: string
  start_offset_ms: number
  end_offset_ms: number
  spoken_at: string | null
  is_uncertain: boolean
  // none: neither speech service could transcribe it
  stt_provider: 'groq' | 'whisper' | 'none' | null
}

export interface Transcript {
  interview_id: number
  status: InterviewStatus
  started_at: string | null
  ended_at: string | null
  turns: TranscriptTurn[]
}
