// a library of indexed documents an agent retrieves from: company policies for the job
// description agent, reference material for the interview co-pilot. upload, watch
// indexing, try a search

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

export interface LibraryConfig {
  // api path under /api/v1 and the react-query key
  path: 'policies' | 'references'
  title: string
  description: string
  // "policy", "reference document"
  noun: string
  emptyText: string
  searchDescription: string
  searchPlaceholder: string
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

export function DocumentLibrary({ config }: { config: LibraryConfig }) {
  const queryClient = useQueryClient()
  const base = `/api/v1/${config.path}`
  const documents = useQuery({
    queryKey: [config.path],
    queryFn: () => api.get<Policy[]>(base),
  })

  const [jobId, setJobId] = useState<string | null>(null)
  const job = useBackgroundJob(jobId)

  useEffect(() => {
    if (isDone(job.data)) void queryClient.invalidateQueries({ queryKey: [config.path] })
  }, [job.data, queryClient, config.path])

  const remove = useMutation({
    mutationFn: (id: number) => api.delete<void>(`${base}/${id}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: [config.path] }),
  })

  const reindex = useMutation({
    mutationFn: (id: number) => api.post<PolicyUploadResponse>(`${base}/${id}/reindex`),
    onSuccess: (data) => {
      setJobId(data.background_job_id)
      void queryClient.invalidateQueries({ queryKey: [config.path] })
    },
  })

  return (
    <div className="space-y-6">
      <PageHeader title={config.title} description={config.description} />

      <UploadCard config={config} onUploaded={(id) => setJobId(id)} />

      {job.data && !isDone(job.data) && (
        <Alert tone="info">
          <Spinner label={job.data.message || 'Indexing'} />
        </Alert>
      )}
      {job.data?.state === 'failed' && <Alert tone="error">{job.data.message}</Alert>}
      {job.data?.state === 'finished' && <Alert tone="success">Document indexed.</Alert>}

      <Card title="Indexed documents">
        {documents.isLoading && <Spinner />}
        {documents.error && <Alert tone="error">{(documents.error as Error).message}</Alert>}
        {documents.data?.length === 0 && <p className="text-sm text-slate-600">{config.emptyText}</p>}
        {documents.data && documents.data.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {documents.data.map((doc) => (
              <li key={doc.id} className="flex flex-wrap items-center gap-3 py-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-slate-900">{doc.title}</p>
                  <p className="text-xs text-slate-500">
                    {doc.original_filename} · {formatSize(doc.size_bytes)}
                    {doc.status === 'indexed' && ` · ${doc.chunk_count} passages`}
                  </p>
                  {doc.failure_reason && <p className="mt-1 text-xs text-red-600">{doc.failure_reason}</p>}
                </div>
                <Badge tone={STATUS_TONE[doc.status]}>{doc.status}</Badge>
                {doc.status === 'failed' && (
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => reindex.mutate(doc.id)}
                    disabled={reindex.isPending}
                  >
                    <RefreshCw aria-hidden="true" className="h-4 w-4" />
                    Retry
                  </Button>
                )}
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label={`Delete ${doc.title}`}
                  onClick={() => {
                    if (window.confirm(`Delete "${doc.title}"? Its passages are removed too.`))
                      remove.mutate(doc.id)
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

      <SearchCard config={config} />
    </div>
  )
}

function UploadCard({ config, onUploaded }: { config: LibraryConfig; onUploaded: (jobId: string) => void }) {
  const queryClient = useQueryClient()
  const fileInput = useRef<HTMLInputElement>(null)
  const [title, setTitle] = useState('')
  const [file, setFile] = useState<File | null>(null)
  const fileId = `${config.path}-file`

  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData()
      form.append('file', file as File)
      form.append('title', title)
      return api.upload<PolicyUploadResponse>(`/api/v1/${config.path}`, form)
    },
    onSuccess: (data) => {
      onUploaded(data.background_job_id)
      setTitle('')
      setFile(null)
      if (fileInput.current) fileInput.current.value = ''
      void queryClient.invalidateQueries({ queryKey: [config.path] })
    },
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (file) upload.mutate()
  }

  return (
    <Card title={`Upload a ${config.noun}`} description="PDF, Word, text or Markdown, up to 10 MB.">
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
          <label htmlFor={fileId} className="block text-sm font-medium text-slate-700">
            File
          </label>
          <input
            ref={fileInput}
            id={fileId}
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

function SearchCard({ config }: { config: LibraryConfig }) {
  const [query, setQuery] = useState('')
  const search = useMutation({
    mutationFn: (q: string) =>
      api.get<PolicySearchHit[]>(`/api/v1/${config.path}/search?q=${encodeURIComponent(q)}`),
  })

  function submit(event: FormEvent) {
    event.preventDefault()
    if (query.trim().length >= 2) search.mutate(query.trim())
  }

  return (
    <Card title="Try a search" description={config.searchDescription}>
      <form onSubmit={submit} className="flex gap-2">
        <input
          aria-label={`Search ${config.title.toLowerCase()}`}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={config.searchPlaceholder}
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
      {search.data?.length === 0 && <p className="mt-4 text-sm text-slate-600">No passage is relevant enough.</p>}
      {search.data && search.data.length > 0 && (
        <ol className="mt-4 space-y-3">
          {search.data.map((hit) => (
            <li key={hit.chunk_id} className="rounded-md border border-slate-200 p-3">
              <p className="text-xs text-slate-500">
                {hit.policy_title} · passage {hit.chunk_index + 1} · relevance {hit.score.toFixed(2)}
              </p>
              <p className="mt-1 whitespace-pre-line text-sm text-slate-800">{hit.text}</p>
            </li>
          ))}
        </ol>
      )}
    </Card>
  )
}

export const POLICIES: LibraryConfig = {
  path: 'policies',
  title: 'Company policies',
  description:
    'Handbooks and hiring rules the job description agent quotes from. Benefits and company facts in a description only come from these documents.',
  noun: 'policy',
  emptyText: 'No policies yet. Upload one above.',
  searchDescription:
    'The same retrieval the job description agent uses. Only passages above the relevance threshold are shown.',
  searchPlaceholder: 'e.g. remote work, leave, benefits',
}

export const REFERENCES: LibraryConfig = {
  path: 'references',
  title: 'Interview references',
  description:
    "Technical guides, answer keys and standards your team trusts. The interview co-pilot's reference answers only cite passages from these documents.",
  noun: 'reference document',
  emptyText: 'No reference material yet. Without it the co-pilot still suggests questions, but gives no reference answers.',
  searchDescription:
    'The same retrieval the co-pilot uses for reference answers. Only passages above the relevance threshold are shown.',
  searchPlaceholder: 'e.g. database indexing, REST design, testing',
}

export function PoliciesPage() {
  return <DocumentLibrary config={POLICIES} />
}

export function ReferencesPage() {
  return <DocumentLibrary config={REFERENCES} />
}
