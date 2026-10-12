// the job description with the agent chat beside it: generate, refine by chatting,
// edit by hand, see the policy sources, and go back to any earlier version

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { BookOpen, History, MessageSquare, Pencil, Sparkles } from 'lucide-react'
import { useEffect, useState } from 'react'

import { api, ApiError } from '@/api/client'
import type { DescriptionVersion, Job, PolicyCitation } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { Alert, Badge, Button, Card, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { cn } from '@/lib/cn'
import { DescriptionChat } from '@/pages/DescriptionChat'
import { VersionHistory } from '@/pages/VersionHistory'

type SideTab = 'chat' | 'sources' | 'history'

interface ActiveJob {
  id: string
  kind: 'generate' | 'chat'
}

export function JobDescriptionPanel({ job }: { job: Job }) {
  const queryClient = useQueryClient()
  const [active, setActive] = useState<ActiveJob | null>(null)
  const progress = useBackgroundJob(active?.id ?? null)
  const [mode, setMode] = useState<'preview' | 'edit'>('preview')
  const [side, setSide] = useState<SideTab>('chat')
  const [draft, setDraft] = useState(job.description_md ?? '')

  const versions = useQuery({
    queryKey: ['job-versions', job.id],
    queryFn: () => api.get<DescriptionVersion[]>(`/api/v1/jobs/${job.id}/description/versions`),
  })

  useEffect(() => {
    setDraft(job.description_md ?? '')
  }, [job.description_md])

  function refreshAll() {
    void queryClient.invalidateQueries({ queryKey: ['job', String(job.id)] })
    void queryClient.invalidateQueries({ queryKey: ['job-chat', job.id] })
    void queryClient.invalidateQueries({ queryKey: ['job-versions', job.id] })
    void queryClient.invalidateQueries({ queryKey: ['jobs'] })
  }

  useEffect(() => {
    if (isDone(progress.data)) {
      refreshAll()
      setMode('preview')
    }
    // refreshAll only depends on job.id and the query client
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.data?.state])

  const generate = useMutation({
    mutationFn: () =>
      api.post<{ background_job_id: string }>(`/api/v1/jobs/${job.id}/description/generate`),
    onSuccess: (data) => {
      setActive({ id: data.background_job_id, kind: 'generate' })
      setSide('chat')
    },
  })

  const save = useMutation({
    mutationFn: () => api.put<Job>(`/api/v1/jobs/${job.id}/description`, { description_md: draft }),
    onSuccess: (updated) => {
      queryClient.setQueryData(['job', String(job.id)], updated)
      void queryClient.invalidateQueries({ queryKey: ['job-versions', job.id] })
      setMode('preview')
    },
  })

  const restore = useMutation({
    mutationFn: (versionId: number) =>
      api.post<Job>(`/api/v1/jobs/${job.id}/description/versions/${versionId}/restore`),
    onSuccess: (updated) => {
      queryClient.setQueryData(['job', String(job.id)], updated)
      void queryClient.invalidateQueries({ queryKey: ['job-versions', job.id] })
      setMode('preview')
    },
  })

  const running = active !== null && !isDone(progress.data)
  const generating = running && active?.kind === 'generate'
  const dirty = draft.trim() !== (job.description_md ?? '').trim()

  function startGeneration() {
    if (
      job.description_md &&
      !window.confirm(
        'Generate a new draft from scratch? The current description stays in History.',
      )
    )
      return
    generate.mutate()
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_400px]">
      <Card>
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <h2 className="mr-auto text-lg font-semibold text-slate-900">Job description</h2>
          {job.description_md && (
            <Tabs
              label="Description view"
              value={mode}
              onChange={setMode}
              tabs={[
                { value: 'preview', label: 'Preview' },
                { value: 'edit', label: 'Edit' },
              ]}
            />
          )}
          <Button
            variant={job.description_md ? 'outline' : 'primary'}
            size="sm"
            onClick={startGeneration}
            disabled={running || generate.isPending}
          >
            <Sparkles aria-hidden="true" className="h-4 w-4" />
            {job.description_md ? 'Regenerate' : 'Generate with AI'}
          </Button>
        </div>

        {generate.error && (
          <Alert tone="error">
            {generate.error instanceof ApiError ? generate.error.message : 'Could not start.'}
          </Alert>
        )}

        {generating && progress.data && (
          <div className="mb-4 space-y-2" aria-live="polite">
            <Spinner label={progress.data.message || 'Working'} />
            <div className="h-1.5 overflow-hidden rounded bg-slate-100">
              <div
                className="h-full bg-brand-600 transition-all"
                style={{ width: `${Math.max(progress.data.percent, 5)}%` }}
              />
            </div>
          </div>
        )}
        {active?.kind === 'generate' && progress.data?.state === 'failed' && (
          <div className="mb-4">
            <Alert tone="error">{progress.data.message}</Alert>
          </div>
        )}
        {restore.error && (
          <div className="mb-4">
            <Alert tone="error">{(restore.error as Error).message}</Alert>
          </div>
        )}

        {!job.description_md && !generating && (
          <p className="text-sm text-slate-600">
            No description yet. Press Generate with AI, or tell the agent in the chat what you
            want. It drafts from the requirement profile and the indexed company policies, checks
            the draft, and shows which policy passages it used.
          </p>
        )}

        {job.description_md && mode === 'preview' && <Markdown source={job.description_md} />}

        {job.description_md && mode === 'edit' && (
          <div className="space-y-3">
            <label htmlFor="jd-editor" className="sr-only">
              Edit job description
            </label>
            <textarea
              id="jd-editor"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              rows={24}
              maxLength={20000}
              className="block w-full rounded-md border border-slate-300 px-3 py-2 font-mono text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
            />
            <p className="text-xs text-slate-500">
              Markdown: # heading, ## section, - bullet. Blank line between paragraphs. Saving
              adds a new version.
            </p>
            {save.error && (
              <Alert tone="error">
                {save.error instanceof ApiError ? save.error.message : 'Could not save.'}
              </Alert>
            )}
            <div className="flex gap-2">
              <Button onClick={() => save.mutate()} disabled={!dirty || save.isPending}>
                <Pencil aria-hidden="true" className="h-4 w-4" />
                {save.isPending ? 'Saving' : 'Save changes'}
              </Button>
              <Button
                variant="ghost"
                disabled={!dirty}
                onClick={() => setDraft(job.description_md ?? '')}
              >
                Discard
              </Button>
            </div>
          </div>
        )}
      </Card>

      <Card className="p-4">
        <Tabs
          label="Agent panel"
          value={side}
          onChange={setSide}
          tabs={[
            { value: 'chat', label: 'Chat', icon: MessageSquare },
            {
              value: 'sources',
              label: `Sources${job.policy_citations?.length ? ` (${job.policy_citations.length})` : ''}`,
              icon: BookOpen,
            },
            { value: 'history', label: 'History', icon: History },
          ]}
        />
        <div className="mt-4">
          {side === 'chat' && (
            <DescriptionChat
              jobId={job.id}
              hasDescription={!!job.description_md}
              versions={versions.data ?? []}
              running={running}
              thinking={running && active?.kind === 'chat'}
              progress={progress.data}
              onSent={(id) => setActive({ id, kind: 'chat' })}
              onUndo={(versionId) => restore.mutate(versionId)}
              undoPending={restore.isPending}
            />
          )}
          {side === 'sources' && (
            <SourcesList
              citations={job.policy_citations ?? []}
              hasDescription={!!job.description_md}
            />
          )}
          {side === 'history' && (
            <VersionHistory
              versions={versions.data ?? []}
              onRestore={(versionId) => restore.mutate(versionId)}
              disabled={running || restore.isPending}
            />
          )}
        </div>
      </Card>
    </div>
  )
}

function Tabs<T extends string>({
  label,
  value,
  onChange,
  tabs,
}: {
  label: string
  value: T
  onChange: (value: T) => void
  tabs: { value: T; label: string; icon?: typeof BookOpen }[]
}) {
  return (
    <div role="tablist" aria-label={label} className="flex rounded-md bg-slate-100 p-0.5">
      {tabs.map((tab) => (
        <button
          key={tab.value}
          role="tab"
          aria-selected={value === tab.value}
          onClick={() => onChange(tab.value)}
          className={cn(
            'inline-flex flex-1 items-center justify-center gap-1.5 rounded px-3 py-1 text-sm',
            value === tab.value ? 'bg-white font-medium shadow-sm' : 'text-slate-600',
          )}
        >
          {tab.icon && <tab.icon aria-hidden="true" className="h-3.5 w-3.5" />}
          {tab.label}
        </button>
      ))}
    </div>
  )
}

function SourcesList({
  citations,
  hasDescription,
}: {
  citations: PolicyCitation[]
  hasDescription: boolean
}) {
  if (!hasDescription) {
    return <p className="text-sm text-slate-600">Shown after a description is generated.</p>
  }
  if (citations.length === 0) {
    return (
      <p className="text-sm text-slate-600">
        No company policy was cited. Upload policies on the Policies page so benefits and the
        equal opportunity statement come from your own documents.
      </p>
    )
  }
  return (
    <ul className="max-h-[560px] space-y-3 overflow-y-auto pr-1">
      {citations.map((c) => (
        <li key={c.chunk_id} className="rounded-md border border-slate-200 p-3">
          <p className="text-xs font-medium text-slate-900">{c.policy_title}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {c.used_for.map((section) => (
              <Badge key={section} tone="info">
                {section}
              </Badge>
            ))}
          </div>
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-brand-700">Show passage</summary>
            <p className="mt-2 whitespace-pre-line text-xs leading-5 text-slate-700">
              {c.excerpt}
            </p>
          </details>
        </li>
      ))}
    </ul>
  )
}
