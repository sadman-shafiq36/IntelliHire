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
