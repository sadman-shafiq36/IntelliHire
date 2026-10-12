// advertise the job: prepare (form copy + drafted post), edit, publish to linkedin, close

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { ExternalLink, FileText, Lock, Send, Users } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { IntegrationStatus, Job, JobPost } from '@/api/types'
import { Alert, Badge, Button, Card, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'

const STATUS_TONE = {
  preparing: 'info',
  draft: 'warning',
  posted: 'success',
  closed: 'neutral',
  failed: 'error',
} as const

function message(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.message : fallback
}

export function JobPostingPanel({ job }: { job: Job }) {
  const queryClient = useQueryClient()
  const [jobRunId, setJobRunId] = useState<string | null>(null)
  const progress = useBackgroundJob(jobRunId)
  const [text, setText] = useState('')

  const post = useQuery({
    queryKey: ['job-post', job.id],
    queryFn: () => api.get<JobPost | null>(`/api/v1/jobs/${job.id}/post`),
  })
  const integrations = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api.get<IntegrationStatus[]>('/api/v1/integrations'),
  })

  useEffect(() => {
    setText(post.data?.text ?? '')
  }, [post.data?.text])

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ['job-post', job.id] })
    void queryClient.invalidateQueries({ queryKey: ['job', String(job.id)] })
    void queryClient.invalidateQueries({ queryKey: ['jobs'] })
  }

  useEffect(() => {
    if (isDone(progress.data)) refresh()
    // refresh only depends on the job id and the query client
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [progress.data?.state])

  const prepare = useMutation({
    mutationFn: () => api.post<{ background_job_id: string }>(`/api/v1/jobs/${job.id}/post`),
    onSuccess: (data) => {
      setJobRunId(data.background_job_id)
      refresh()
    },
  })
  const save = useMutation({
    mutationFn: () => api.put<JobPost>(`/api/v1/jobs/${job.id}/post/text`, { text }),
    onSuccess: (data) => queryClient.setQueryData(['job-post', job.id], data),
  })
  const publish = useMutation({
    mutationFn: () => api.post<JobPost>(`/api/v1/jobs/${job.id}/post/publish`),
    onSuccess: refresh,
  })
  const close = useMutation({
    mutationFn: () => api.post<JobPost>(`/api/v1/jobs/${job.id}/post/close`),
    onSuccess: refresh,
  })

  const linkedin = integrations.data?.find((i) => i.provider === 'linkedin')
  const google = integrations.data?.find((i) => i.provider === 'google')
  const ready = !!linkedin?.connected && !!google?.connected && !!google?.template_configured
  const current = post.data
  const running = jobRunId !== null && !isDone(progress.data)
  const dirty = current?.status === 'draft' && text.trim() !== (current.text ?? '').trim()
  const canPrepare = !current || current.status === 'failed' || current.status === 'closed'
  const error = prepare.error ?? save.error ?? publish.error ?? close.error

  return (
    <Card>
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="mr-auto text-lg font-semibold text-slate-900">Posting and applications</h2>
        {current && <Badge tone={STATUS_TONE[current.status]}>{current.status}</Badge>}
        <Link
          to={`/jobs/${job.id}/applications`}
          className="inline-flex h-8 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-900 hover:bg-slate-50"
        >
          <Users aria-hidden="true" className="h-4 w-4" />
          Applications
        </Link>
      </div>
      <p className="mt-1 text-sm text-slate-600">
        The job is announced as a post on LinkedIn that links to its own Google Form, where
        applicants leave their details and upload a CV.
      </p>

      <div className="mt-4 space-y-4">
        {error && <Alert tone="error">{message(error, 'Something went wrong.')}</Alert>}

        {!job.description_md && (
          <Alert tone="info">Write the job description first; the post is built from it.</Alert>
        )}

        {job.description_md && integrations.data && !ready && canPrepare && (
          <Alert tone="warning">
            Connect {[!linkedin?.connected && 'LinkedIn', !google?.connected && 'Google']
              .filter(Boolean)
              .join(' and ') || 'the template form'}{' '}
            first.{' '}
            <Link to="/settings/integrations" className="font-medium underline">
              Open Integrations
            </Link>
          </Alert>
        )}

        {current?.status === 'failed' && current.failure_reason && (
          <Alert tone="error">{current.failure_reason}</Alert>
        )}

        {(running || current?.status === 'preparing') && (
          <Spinner label={progress.data?.message || 'Preparing the post'} />
        )}

        {canPrepare && job.description_md && !running && (
          <Button onClick={() => prepare.mutate()} disabled={!ready || prepare.isPending}>
            <FileText aria-hidden="true" className="h-4 w-4" />
            {current ? 'Prepare a new post' : 'Prepare LinkedIn post'}
          </Button>
        )}

        {current?.status === 'draft' && (
          <div className="space-y-3">
            <FormLinks post={current} />
            <div className="space-y-1.5">
              <label htmlFor="post-text" className="block text-sm font-medium text-slate-700">
                Post text
              </label>
              <textarea
                id="post-text"
                rows={10}
                maxLength={2800}
                value={text}
                onChange={(e) => setText(e.target.value)}
                className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
              <p className="text-xs text-slate-500">
                {text.length} characters. The application form is attached as a link card
                {current.hashtags?.length
                  ? `, followed by ${current.hashtags.map((h) => `#${h}`).join(' ')}`
                  : ''}
                .
              </p>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                disabled={!dirty || save.isPending}
                onClick={() => save.mutate()}
              >
                Save text
              </Button>
              <Button
                disabled={dirty || publish.isPending}
                title={dirty ? 'Save the text first' : undefined}
                onClick={() => {
                  if (
                    window.confirm(
                      `Publish this post publicly on ${linkedin?.account_name ?? 'the connected'} LinkedIn feed?`,
                    )
                  )
                    publish.mutate()
                }}
              >
                <Send aria-hidden="true" className="h-4 w-4" />
                {publish.isPending ? 'Publishing' : 'Publish on LinkedIn'}
              </Button>
            </div>
          </div>
        )}

        {current?.status === 'posted' && (
          <div className="space-y-3">
            <p className="text-sm text-slate-700">
              Posted {current.posted_at && new Date(current.posted_at).toLocaleString()}.
            </p>
            <div className="flex flex-wrap gap-3 text-sm">
              {current.post_url && (
                <a
                  href={current.post_url}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1 font-medium text-brand-700 hover:underline"
                >
                  <ExternalLink aria-hidden="true" className="h-4 w-4" />
                  View on LinkedIn
                </a>
              )}
            </div>
            <FormLinks post={current} />
            <Button
              variant="outline"
              disabled={close.isPending}
              onClick={() => {
                if (window.confirm('Stop accepting applications? The form will be closed.'))
                  close.mutate()
              }}
            >
              <Lock aria-hidden="true" className="h-4 w-4" />
              Close applications
            </Button>
          </div>
        )}

        {current?.status === 'closed' && (
          <p className="text-sm text-slate-600">
            Applications closed {current.closed_at && new Date(current.closed_at).toLocaleString()}.
            The LinkedIn post stays up; the form no longer accepts responses.
          </p>
        )}
      </div>
    </Card>
  )
}

function FormLinks({ post }: { post: JobPost }) {
  return (
    <div className="flex flex-wrap gap-4 text-sm">
      {post.form_url && (
        <a
          href={post.form_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 font-medium text-brand-700 hover:underline"
        >
          <ExternalLink aria-hidden="true" className="h-4 w-4" />
          Application form
        </a>
      )}
      {post.form_edit_url && (
        <a
          href={post.form_edit_url}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-slate-600 hover:underline"
        >
          <ExternalLink aria-hidden="true" className="h-4 w-4" />
          Edit the form in Google
        </a>
      )}
    </div>
  )
}
