// shows /health/ready per service, quickest way to see what's down after setup

import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { ReadinessResponse } from '@/api/types'
import { Alert, Card, Spinner, StatusDot } from '@/components/ui'

const DEPENDENCY_PURPOSE: Record<string, string> = {
  postgres: 'Jobs, candidates, scores, transcripts and assessments',
  redis: 'Background job queue and progress tracking',
  chroma: 'Vector store for company policy retrieval',
  whisper: 'Local speech-to-text for live transcription',
}

export function SystemStatusPage() {
  const { data, isLoading, error, dataUpdatedAt } = useQuery({
    queryKey: ['readiness'],
    queryFn: () => api.get<ReadinessResponse>('/health/ready'),
    // services start at different speeds, keep polling
    refetchInterval: 10_000,
  })

  if (isLoading) return <Spinner label="Checking services" />

  if (error || !data) {
    return (
      <Alert tone="error">
        Could not reach the API. Check that the backend container is running.
      </Alert>
    )
  }

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">System status</h2>
        <p className="mt-1 text-sm text-slate-600">
          Last checked {new Date(dataUpdatedAt).toLocaleTimeString()}. Refreshes automatically.
        </p>
      </div>

      {data.status === 'degraded' && (
        <Alert tone="warning">
          One or more services are unavailable. Features that depend on them will report a clear
          error rather than failing silently.
        </Alert>
      )}

      <Card title="Services">
        <ul className="divide-y divide-slate-100">
          {Object.entries(data.dependencies).map(([name, dependency]) => (
            <li key={name} className="flex items-center gap-3 py-2.5">
              <StatusDot ok={dependency.status === 'ok'} />
              <span className="w-24 text-sm font-medium capitalize text-slate-900">{name}</span>
              <span className="flex-1 text-sm text-slate-600">
                {DEPENDENCY_PURPOSE[name] ?? ''}
                {dependency.model ? ` (${dependency.model})` : ''}
              </span>
              <span className="text-sm text-slate-500">{dependency.status}</span>
            </li>
          ))}
        </ul>
      </Card>

      <Card
        title="Configuration"
        description="Values supplied through environment variables. Only their presence is reported, never the value itself."
      >
        <ul className="divide-y divide-slate-100">
          <li className="flex items-center gap-3 py-2.5">
            <StatusDot ok={data.configuration.gemini_api_key_present} />
            <span className="flex-1 text-sm text-slate-900">GOOGLE_API_KEY</span>
            <span className="text-sm text-slate-500">
              {data.configuration.gemini_api_key_present ? 'set' : 'missing'}
            </span>
          </li>
          <li className="flex items-center gap-3 py-2.5">
            <StatusDot ok={data.configuration.jwt_secret_configured} />
            <span className="flex-1 text-sm text-slate-900">JWT_SECRET_KEY</span>
            <span className="text-sm text-slate-500">
              {data.configuration.jwt_secret_configured ? 'set' : 'missing'}
            </span>
          </li>
        </ul>
      </Card>
    </div>
  )
}
