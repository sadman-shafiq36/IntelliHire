// shows /health/ready per service, quickest way to see what's down after setup

import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'

import { api } from '@/api/client'
import type { ReadinessResponse, UsageGroup, UsageSummary } from '@/api/types'
import { Alert, Card, Spinner, StatusDot } from '@/components/ui'
import { cn } from '@/lib/cn'

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

      <UsageCard />
    </div>
  )
}

const PERIODS = [
  { days: 1, label: 'Last 24 hours' },
  { days: 7, label: '7 days' },
  { days: 30, label: '30 days' },
]

const formatNumber = (n: number | null | undefined) => (n ?? 0).toLocaleString()

// graph names are snake_case ids; calls with no graph are the co-pilot chat and post text
function sourceLabel(name: string | null) {
  if (!name) return 'Outside agent runs (co-pilot chat, LinkedIn post text)'
  const words = name.replace(/_/g, ' ').replace(/^cv\b/, 'CV')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

function UsageCard() {
  const [days, setDays] = useState(1)
  const { data, isLoading, error } = useQuery({
    queryKey: ['usage', days],
    queryFn: () => api.get<UsageSummary>(`/api/v1/usage?days=${days}`),
    refetchInterval: 30_000,
  })

  const totals: [string, number][] = data
    ? [
        ['Total tokens', data.total_tokens],
        ['Prompt', data.prompt_tokens],
        ['Completion', data.completion_tokens],
        ['AI calls', data.calls],
      ]
    : []

  return (
    <Card
      title="AI usage"
      description="Tokens counted from each AI answer. Completion includes the model's hidden reasoning, which counts toward the provider's limits."
      actions={
        <div role="group" aria-label="Period" className="flex rounded-lg border border-slate-200 p-0.5">
          {PERIODS.map((p) => (
            <button
              key={p.days}
              type="button"
              aria-pressed={days === p.days}
              onClick={() => setDays(p.days)}
              className={cn(
                'rounded-md px-3 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                days === p.days ? 'bg-slate-900 text-white' : 'text-slate-600 hover:bg-slate-50',
              )}
            >
              {p.label}
            </button>
          ))}
        </div>
      }
    >
      {isLoading ? (
        <Spinner label="Adding up tokens" />
      ) : error || !data ? (
        <Alert tone="error">The usage figures could not be loaded.</Alert>
      ) : (
        <div className="space-y-6">
          <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            {totals.map(([label, value]) => (
              <div key={label} className="rounded-lg border border-slate-200 px-4 py-3">
                <dt className="text-xs text-slate-500">{label}</dt>
                <dd className="mt-1 text-xl font-semibold tabular-nums text-slate-900">{formatNumber(value)}</dd>
              </div>
            ))}
          </dl>

          {data.calls === 0 ? (
            <p className="text-sm text-slate-600">No AI calls in this period.</p>
          ) : (
            <div className="grid gap-6 lg:grid-cols-2">
              <UsageTable title="By model" rows={data.by_model} label={(n) => n ?? 'Unknown'} total={data.total_tokens} />
              <UsageTable title="By agent" rows={data.by_source} label={sourceLabel} total={data.total_tokens} />
            </div>
          )}

          <div>
            <h3 className="text-sm font-semibold text-slate-900">Recent agent runs</h3>
            {data.recent_runs.length === 0 ? (
              <p className="mt-2 text-sm text-slate-600">No agent runs yet.</p>
            ) : (
              <div className="mt-2 overflow-x-auto">
                <table className="w-full text-left text-sm">
                  <thead className="text-xs text-slate-500">
                    <tr>
                      <th className="py-2 pr-4 font-medium">Started</th>
                      <th className="py-2 pr-4 font-medium">Agent</th>
                      <th className="py-2 pr-4 font-medium">Status</th>
                      <th className="py-2 pr-4 text-right font-medium">Time</th>
                      <th className="py-2 pr-4 text-right font-medium">Calls</th>
                      <th className="py-2 text-right font-medium">Tokens</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-100">
                    {data.recent_runs.map((r) => (
                      <tr key={r.id}>
                        <td className="whitespace-nowrap py-2 pr-4 text-slate-600">
                          {r.started_at ? new Date(r.started_at).toLocaleString() : '—'}
                        </td>
                        <td className="py-2 pr-4 text-slate-900">{sourceLabel(r.graph_name)}</td>
                        <td className={cn('py-2 pr-4', r.status === 'failed' ? 'text-red-700' : 'text-slate-600')}>
                          {r.status}
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums text-slate-600">
                          {r.duration_ms != null ? `${(r.duration_ms / 1000).toFixed(1)} s` : '—'}
                        </td>
                        <td className="py-2 pr-4 text-right tabular-nums text-slate-600">{r.calls ?? '—'}</td>
                        <td className="py-2 text-right tabular-nums text-slate-900">
                          {r.total_tokens != null ? formatNumber(r.total_tokens) : '—'}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                <p className="mt-2 text-xs text-slate-500">
                  Runs from before token counting was added show a dash.
                </p>
              </div>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}

function UsageTable({
  title,
  rows,
  label,
  total,
}: {
  title: string
  rows: UsageGroup[]
  label: (name: string | null) => string
  total: number
}) {
  return (
    <div className="min-w-0">
      <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
      <ul className="mt-2 space-y-2">
        {rows.map((r) => (
          <li key={r.name ?? '-'} className="text-sm">
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-slate-900" title={label(r.name)}>
                {label(r.name)}
              </span>
              <span className="shrink-0 tabular-nums text-slate-600">
                {formatNumber(r.total_tokens)} · {r.calls} call{r.calls === 1 ? '' : 's'}
              </span>
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-slate-100" aria-hidden="true">
              <div
                className="h-1.5 rounded-full bg-brand-600"
                style={{ width: `${total ? Math.max(2, (r.total_tokens / total) * 100) : 0}%` }}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
