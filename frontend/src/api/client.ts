// the only place that calls fetch

import type { ApiErrorBody } from './types'

const BASE_URL = (import.meta.env.VITE_API_BASE_URL as string) ?? 'http://localhost:8000'

const TOKEN_STORAGE_KEY = 'intellihire.token'

// message is written for the user and can go straight on screen
export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details?: Record<string, unknown>

  constructor(status: number, code: string, message: string, details?: Record<string, unknown>) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

export function getStoredToken(): string | null {
  try {
    return window.localStorage.getItem(TOKEN_STORAGE_KEY)
  } catch {
    // private browsing can make localStorage throw
    return null
  }
}

export function storeToken(token: string | null): void {
  try {
    if (token === null) window.localStorage.removeItem(TOKEN_STORAGE_KEY)
    else window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
  } catch {
    // not fatal, the session just won't survive a reload
  }
}

interface RequestOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE'
  // FormData is sent as multipart, anything else as json
  body?: unknown
  // false for endpoints called while signed out, e.g. login
  authenticated?: boolean
  signal?: AbortSignal
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, authenticated = true, signal } = options

  const isForm = body instanceof FormData
  const headers: Record<string, string> = {}
  // the browser sets the multipart boundary itself
  if (body !== undefined && !isForm) headers['Content-Type'] = 'application/json'

  if (authenticated) {
    const token = getStoredToken()
    if (token) headers['Authorization'] = `Bearer ${token}`
  }

  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? body : JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    // network failure has no status, e.g. the api container is down
    throw new ApiError(0, 'network_error', 'Could not reach the server. Is it running?', {
      cause: String(cause),
    })
  }

  if (response.status === 204) return undefined as T

  const payload = await response.json().catch(() => null)

  if (!response.ok) {
    const envelope = payload as ApiErrorBody | null
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? 'unknown_error',
      envelope?.error?.message ?? 'Something went wrong.',
      envelope?.error?.details,
    )
  }

  return payload as T
}

// files come back as a blob, everything else as json
async function download(path: string): Promise<Blob> {
  const token = getStoredToken()
  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: token ? { Authorization: `Bearer ${token}` } : {},
    })
  } catch (cause) {
    throw new ApiError(0, 'network_error', 'Could not reach the server. Is it running?', {
      cause: String(cause),
    })
  }
  if (!response.ok) {
    const envelope = (await response.json().catch(() => null)) as ApiErrorBody | null
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? 'unknown_error',
      envelope?.error?.message ?? 'The file could not be downloaded.',
    )
  }
  return response.blob()
}

export interface StreamEvent {
  event: string
  data: unknown
}

// a POST answered with server-sent events; onEvent runs for each event as it arrives.
// errors before the stream starts come back in the usual envelope
async function stream(
  path: string,
  body: unknown,
  onEvent: (e: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const token = getStoredToken()
  let response: Response
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: JSON.stringify(body),
      signal,
    })
  } catch (cause) {
    throw new ApiError(0, 'network_error', 'Could not reach the server. Is it running?', {
      cause: String(cause),
    })
  }
  if (!response.ok || !response.body) {
    const envelope = (await response.json().catch(() => null)) as ApiErrorBody | null
    throw new ApiError(
      response.status,
      envelope?.error?.code ?? 'unknown_error',
      envelope?.error?.message ?? 'Something went wrong.',
      envelope?.error?.details,
    )
  }

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += value
    let end: number
    while ((end = buffer.indexOf('\n\n')) >= 0) {
      const block = buffer.slice(0, end)
      buffer = buffer.slice(end + 2)
      let event = 'message'
      let data = ''
      for (const line of block.split('\n')) {
        if (line.startsWith('event: ')) event = line.slice(7)
        else if (line.startsWith('data: ')) data += line.slice(6)
      }
      if (data) onEvent({ event, data: JSON.parse(data) })
    }
  }
}

export const api = {
  download,
  stream,

  get: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'GET' }),

  post: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'POST', body }),

  put: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PUT', body }),

  upload: <T>(path: string, form: FormData, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'POST', body: form }),

  patch: <T>(path: string, body?: unknown, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'PATCH', body }),

  delete: <T>(path: string, options?: Omit<RequestOptions, 'method' | 'body'>) =>
    request<T>(path, { ...options, method: 'DELETE' }),
}

export { BASE_URL }
