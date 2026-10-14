// company policies the jd agent retrieves from. upload, watch indexing, try a search

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { RefreshCw, Search, Trash2, Upload } from 'lucide-react'
import { type FormEvent, useEffect, useRef, useState } from 'react'

import { api, ApiError } from '@/api/client'
import type { Policy, PolicySearchHit, PolicyUploadResponse } from '@/api/types'
import { Alert, Badge, Button, Card, Field, PageHeader, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'

const ACCEPTED = '.pdf,.docx,.txt,.md'

const STATUS_TONE: Record<Policy['status'], 'neutral' | 'info' | 'success' | 'error'> = {
  uploaded: 'neutral',
  indexing: 'info',
  indexed: 'success',
  failed: 'error',
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function PoliciesPage() {
  const queryClient = useQueryClient()
  const policies = useQuery({
    queryKey: ['policies'],
    queryFn: () => api.get<Policy[]>('/api/v1/policies'),
  })

  const [jobId, setJobId] = useState<string | null>(null)
  const job = useBackgroundJob(jobId)

  useEffect(() => {
    if (isDone(job.data)) void queryClient.invalidateQueries({ queryKey: ['policies'] })
  }, [job.data, queryClient])

  const remove = useMutation({
    mutationFn: (id: number) => api.delete<void>(`/api/v1/policies/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['policies'] }),
  })

  const reindex = useMutation({
    mutationFn: (id: number) => api.post<PolicyUploadResponse>(`/api/v1/policies/${id}/reindex`),
    onSuccess: (data) => {
      setJobId(data.background_job_id)
      void queryClient.invalidateQueries({ queryKey: ['policies'] })
    },
  })

  return (
    <div className="space-y-6">
      <PageHeader
        title="Company policies"
        description="Handbooks and hiring rules the job description agent quotes from. Benefits and company facts in a description only come from these documents."
      />

      <UploadCard onUploaded={(id) => setJobId(id)} />

      {job.data && !isDone(job.data) && (
        <Alert tone="info">
          <Spinner label={job.data.message || 'Indexing'} />
        </Alert>
      )}
      {job.data?.state === 'failed' && <Alert tone="error">{job.data.message}</Alert>}
      {job.data?.state === 'finished' && <Alert tone="success">Policy indexed.</Alert>}

      <Card title="Indexed documents">
        {policies.isLoading && <Spinner />}
        {policies.error && <Alert tone="error">{(policies.error as Error).message}</Alert>}
        {policies.data?.length === 0 && (
          <p className="text-sm text-slate-600">No policies yet. Upload one above.</p>
        )}
        {policies.data && policies.data.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {policies.data.map((policy) => (
              <li key={policy.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-900">{policy.title}</p>
                  <p className="text-xs text-slate-500">
                    {policy.original_filename} · {formatSize(policy.size_bytes)}
                    {policy.status === 'indexed' && ` · ${policy.chunk_count} passages`}
                  </p>
                  {policy.failure_reason && (
                    <p className="mt-1 text-xs text-red-600">{policy.failure_reason}</p>
                  )}
                </div>
                <Badge tone={STATUS_TONE[policy.status]}>{policy.status}</Badge>
                {policy.status === 'failed' && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => reindex.mutate(policy.id)}
                    disabled={reindex.isPending}
                  >
                    <RefreshCw aria-hidden="true" className="h-4 w-4" />
                    Retry
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Delete ${policy.title}`}
                  onClick={() => {
                    if (window.confirm(`Delete "${policy.title}"? Its passages are removed too.`))
                      remove.mutate(policy.id)
                  }}
                  disabled={remove.isPending}
                >
                  <Trash2 aria-hidden="true" className="h-4 w-4" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      <SearchCard />
    </div>
  )
}

function UploadCard({ onUploaded }: { onUploaded: (jobId: string) => void }) {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)

  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.append('file', file as File)
      form.append('title', title)
      return api.upload<PolicyUploadResponse>('/api/v1/policies', form)
    },
    onSuccess: (data) => {
      onUploaded(data.background_job_id)
      setTitle('')
      setFile(null)
      if (fileInput.current) fileInput.current.value = ''
      void queryClient.invalidateQueries({ queryKey: ['policies'] })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (file) upload.mutate()
  }

  return (
    <Card title="Upload a policy" description="PDF, Word, text or Markdown, up to 10 MB.">
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-[1fr_1fr_auto] sm:items-end">
        <Field
          label="Title"
          name="title"
          placeholder="Defaults to the file name"
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          maxLength={300}
        />
        <div className="space-y-1.5">
          <label htmlFor="policy-file" className="block text-sm font-medium text-slate-700">
            File
          </label>
          <input
            ref={fileInput}
            id="policy-file"
            type="file"
            accept={ACCEPTED}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full text-sm text-slate-700 file:mr-3 file:rounded-md file:border-0 file:bg-slate-100 file:px-3 file:py-2 file:text-sm file:font-medium hover:file:bg-slate-200"
          />
        </div>
        <Button type="submit" disabled={!file || upload.isPending}>
          <Upload aria-hidden="true" className="h-4 w-4" />
          {upload.isPending ? 'Uploading' : 'Upload'}
        </Button>
      </form>
      {upload.error && (
        <div className="mt-4">
          <Alert tone="error">
            {upload.error instanceof ApiError ? upload.error.message : 'Upload failed.'}
          </Alert>
        </div>
      )}
    </Card>
  )
}

function SearchCard() {
  const [query, setQuery] = useState('')
  const search = useMutation({
    mutationFn: (q: string) =>
      api.get<PolicySearchHit[]>(`/api/v1/policies/search?q=${encodeURIComponent(q)}`),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (query.trim().length >= 2) search.mutate(query.trim())
  }

  return (
    <Card
      title="Try a search"
      description="The same retrieval the job description agent uses. Only passages above the relevance threshold are shown."
    >
      <form onSubmit={submit} className="flex gap-2">
        <input
          aria-label="Search policies"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="e.g. remote work, leave, benefits"
          className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
        />
        <Button type="submit" variant="outline" disabled={search.isPending}>
          <Search aria-hidden="true" className="h-4 w-4" />
          Search
        </Button>
      </form>

      {search.error && (
        <div className="mt-4">
          <Alert tone="error">{(search.error as Error).message}</Alert>
        </div>
      )}
      {search.data?.length === 0 && (
        <p className="mt-4 text-sm text-slate-600">No passage is relevant enough.</p>
      )}
      {search.data && search.data.length > 0 && (
        <ol className="mt-4 space-y-3">
          {search.data.map((hit) => (
            <li key={hit.chunk_id} className="rounded-md border border-slate-200 p-3">
              <p className="text-xs text-slate-500">
                {hit.policy_title} · passage {hit.chunk_index + 1} · relevance{' '}
                {hit.score.toFixed(2)}
              </p>
              <p className="mt-1 whitespace-pre-line text-sm text-slate-800">{hit.text}</p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}
