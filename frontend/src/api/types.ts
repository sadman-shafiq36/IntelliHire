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
