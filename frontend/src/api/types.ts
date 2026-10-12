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
