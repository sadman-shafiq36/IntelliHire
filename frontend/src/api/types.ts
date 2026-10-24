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
  kind: 'policy' | 'reference'
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
    assessed: number
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

// --- interview co-pilot ---

export interface CopilotEvidence {
  source: 'cv' | 'transcript'
  quote: string
  turn_id: number | null
}

export interface CopilotSuggestion {
  id: number
  batch: number
  created_at: string
  requirement: { id: number; text: string; must_have: boolean } | null
  question: string
  why: string | null
  // why code picked the requirement
  basis: {
    must_have?: boolean
    screening?: 'met' | 'partially_met' | 'unmet' | null
    discussed?: boolean | null
    discussed_turn_id?: number | null
  }
  evidence: CopilotEvidence[]
  answer_points: { point: string; passage_ids: string[] }[]
  sources: { chunk_id: string; title: string; excerpt: string }[]
  focus: string | null
  warnings: string[]
  was_used: boolean
  model_name: string | null
}

export type CopilotSource =
  | { kind: 'passage'; label: string; chunk_id: string; title: string; excerpt: string }
  | { kind: 'turn'; label: string; turn_id: number; speaker_role: 'interviewer' | 'candidate'; text: string }
  | { kind: 'cv'; label: string; quote: string }

export interface CopilotMessage {
  id: number
  role: 'user' | 'assistant'
  content: string
  status: 'pending' | 'done' | 'failed'
  sources: CopilotSource[]
  created_at: string
}

// --- interview q&a analysis ---

export type QaScoreName = 'relevance' | 'accuracy' | 'completeness' | 'articulation'
export type CoverageStatus = 'covered' | 'partially_covered' | 'not_explored'

export interface QaPair {
  id: number
  sequence: number
  question: string
  answer: string
  question_turn_ids: number[]
  answer_turn_ids: number[]
  // the sequence number of the pair this follows up
  follow_up_to: number | null
  scores: Record<QaScoreName, number | null>
  score: number | null
  accuracy_basis: 'reference' | 'general' | 'not_applicable' | null
  justification: string | null
  sources: { label: string; chunk_id: string; title: string; excerpt: string }[]
  requirements: { requirement_id: number; text: string; coverage: Exclude<CoverageStatus, 'not_explored'>; note: string | null }[]
}

export interface InterviewAnalysis {
  interview: {
    id: number
    job_id: number
    job_title: string
    candidate_id: number
    candidate_name: string
    status: InterviewStatus
    ended_at: string | null
    timezone_name: string
  }
  analysis: {
    status: 'waiting' | 'running' | 'analysed' | 'empty' | 'failed'
    message: string | null
    segmented_by: 'ai' | 'exchanges' | null
    analysed_at: string | null
    model_name: string | null
    warnings: string[]
    stale: boolean
  } | null
  summary: {
    pairs: number
    answer_quality: number | null
    averages: Record<QaScoreName, number | null>
    covered: number
    partially_covered: number
    not_explored: number
  }
  pairs: QaPair[]
  turns: { id: number; speaker_role: 'interviewer' | 'candidate'; text: string; start_offset_ms: number }[]
  coverage: {
    requirement_id: number
    text: string
    must_have: boolean
    status: CoverageStatus
    pairs: { sequence: number; coverage: Exclude<CoverageStatus, 'not_explored'>; note: string | null }[]
  }[]
  follow_ups: { requirement_id: number; requirement_text: string; question: string }[]
}

// --- consolidated assessment ---

export type FitPosition = 'strong_fit' | 'possible_fit' | 'not_a_fit' | 'not_assessed'
export type FindingKind = 'strength' | 'weakness' | 'evidence_gap' | 'area_to_verify'

export interface AssessmentEvidence {
  // score = a named requirement, for gaps
  type: 'cv' | 'transcript' | 'score'
  ref_id: number | null
  quote: string
  speaker_role?: 'interviewer' | 'candidate'
  start_offset_ms?: number
}

export interface AssessmentReport {
  interview: {
    id: number
    job_id: number
    job_title: string
    candidate_id: number
    candidate_name: string
    status: InterviewStatus
    started_at: string | null
    ended_at: string | null
    timezone_name: string
  }
  analysis_status: string | null
  assessment: {
    status: 'waiting' | 'running' | 'assessed' | 'failed'
    message: string | null
    overall_score: number | null
    position: FitPosition | null
    position_label: string
    components: {
      name: 'cv_fit' | 'interview_answers' | 'requirement_coverage'
      label: string
      score: number | null
      weight: number
      effective_weight: number
      contribution: number
    }[]
    requirements: {
      id: number
      text: string
      is_mandatory: boolean
      cv_status: 'met' | 'partially_met' | 'unmet' | null
      interview_status: CoverageStatus
    }[]
    capped_by: string[]
    summary: string | null
    findings: { kind: FindingKind; text: string; evidence: AssessmentEvidence[] }[]
    warnings: string[]
    model_name: string
    generated_at: string | null
    stale: boolean
  } | null
}

export interface AssessmentComparison {
  job_id: number
  job_title: string
  weights: Record<'cv_fit' | 'interview_answers' | 'requirement_coverage', number>
  requirements: { id: number; text: string; must_have: boolean }[]
  candidates: {
    interview_id: number
    candidate_id: number
    full_name: string
    overall_score: number | null
    position: FitPosition | null
    position_label: string
    components: Record<'cv_fit' | 'interview_answers' | 'requirement_coverage', number | null>
    requirements: Record<string, { cv: string | null; interview: CoverageStatus | null }>
    findings: Record<FindingKind, string[]>
    generated_at: string | null
  }[]
}

export type ReviewKind = 'claims' | 'interview_score' | 'quality'
export type ClaimStatus = 'supported' | 'contradiction' | 'unverified' | 'not_discussed' | 'not_checked'

export interface CandidateQuote {
  turn_id: number
  quote: string
}

export interface ClaimFlag {
  claim: string
  cv_quote: string
  topic: string
  status: ClaimStatus
  evidence: CandidateQuote[]
  explanation: string
  follow_up: string
}

export interface InterviewScoreDimension {
  name: string
  label: string
  score: number | null
  base: number | null
  adjustment: number
  reason: string
  evidence: CandidateQuote[]
  weight: number
  effective_weight: number
  basis: string
}

export interface QualityQuestion {
  sequence: number
  question: string
  question_turn_ids: number[]
  signal?: 'high' | 'medium' | 'low'
  style?: 'open' | 'closed' | 'leading' | 'multi_part' | 'unclear'
  reason?: string
  better_question?: string
}

export interface QualityRecommendation {
  issue: string
  title: string
  evidence: string
  text: string
  written_by: 'ai' | 'rule'
}

export interface QualityResult {
  scores: {
    question_effectiveness: number | null
    coverage: number | null
    balance: number | null
    consistency: number | null
    overall: number | null
  }
  metrics: {
    speaking_ms: { interviewer: number; candidate: number }
    candidate_share: number | null
    longest_interviewer_turn_s: number
    overlaps: number
    questions: number
    follow_ups: number
    unanswered: number
    average_answer_words: number
    coverage: number | null
    must_haves: number
    must_haves_explored: number
    peers: number
    consistency: number | null
  }
  questions: QualityQuestion[]
  recommendations: QualityRecommendation[]
  summary: string
}

export interface Review<T> {
  status: 'waiting' | 'running' | 'done' | 'empty' | 'failed'
  message: string | null
  result: T | null
  warnings: string[]
  model_name: string | null
  generated_at: string | null
  stale: boolean
}

export interface InterviewReviews {
  interview: { id: number; job_id: number; job_title: string; candidate_id: number; candidate_name: string; status: InterviewStatus }
  analysis_status: string | null
  cv_score: number | null
  requirements: { id: number; text: string }[]
  turns: { id: number; speaker_role: 'interviewer' | 'candidate'; text: string; start_offset_ms: number }[]
  claims: Review<{ claims: ClaimFlag[] }> | null
  interview_score: Review<{ overall: number | null; dimensions: InterviewScoreDimension[] }> | null
  quality: Review<QualityResult> | null
}

export interface LinkIdentity {
  consent: 'given' | 'declined' | 'withdrawn' | null
  reference: 'pending' | 'ready' | 'unusable' | null
  source: 'form' | 'selfie' | null
  text: string
  version: string
}

export type IdentityStatus =
  | 'pending'
  | 'confirmed'
  | 'low_confidence'
  | 'mismatch'
  | 'not_identified'
  | 'manually_confirmed'
  | 'rejected'

export interface IdentityState {
  status: IdentityStatus
  message: string
  consent: LinkIdentity['consent']
  reference: LinkIdentity['reference']
  profile_available: boolean
  method?: 'face' | 'manual' | null
  reason?: string | null
  score?: number | null
  margin?: number | null
  checked_at?: string | null
}

export interface RoomProfile {
  name: string
  headline: string
  years_experience: number | null
  current_role: { title: string; organisation: string } | null
  skills: string[]
  requirements_met: { text: string; quote: string | null }[]
  score: number | null
  rank: number | null
  probe: { text: string; must_have: boolean; screening: 'met' | 'partially_met' | 'unmet' | null; cv_quote: string | null }[]
  summary: string | null
}
