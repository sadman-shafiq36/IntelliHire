// add cvs to a job by hand (pdf or word, many at once), next to the ones from the form

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { FileUp, ScanText } from 'lucide-react'
import { type DragEvent, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { CvDocumentItem, UploadResult } from '@/api/types'
import { Alert, Badge, Button, buttonClass, Card } from '@/components/ui'
import { cn } from '@/lib/cn'

const ACCEPT = '.pdf,.docx'
const MAX_MB = 10

const STATUS: Record<CvDocumentItem['status'], { label: string; tone: 'neutral' | 'info' | 'success' | 'warning' | 'error' }> = {
  pending: { label: 'waiting to be screened', tone: 'warning' },
  extracting: { label: 'screening', tone: 'info' },
  extracted: { label: 'screened', tone: 'success' },
  unreadable: { label: 'could not be read', tone: 'error' },
  failed: { label: 'screening failed', tone: 'error' },
}

export function CvUploadCard({ jobId }: { jobId: string }) {
  const queryClient = useQueryClient()
  const input = useRef<HTMLInputElement>(null)
  const [files, setFiles] = useState<File[]>([])
  const [dragging, setDragging] = useState(false)

  const docs = useQuery({
    queryKey: ['job-cvs', jobId],
    queryFn: () => api.get<CvDocumentItem[]>(`/api/v1/jobs/${jobId}/cvs`),
  })

  const upload = useMutation({
    mutationFn: () => {
      const form = new FormData()
      files.forEach((f) => form.append('files', f))
      return api.upload<UploadResult>(`/api/v1/jobs/${jobId}/cvs`, form)
    },
    onSuccess: () => {
      setFiles([])
      if (input.current) input.current.value = ''
      void queryClient.invalidateQueries({ queryKey: ['job-cvs', jobId] })
      void queryClient.invalidateQueries({ queryKey: ['screening', jobId] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
    },
  })

  function add(list: FileList | null) {
    if (!list) return
    setFiles((current) => [...current, ...Array.from(list)])
  }

  function onDrop(event: DragEvent) {
    event.preventDefault()
    setDragging(false)
    add(event.dataTransfer.files)
  }

  const tooBig = files.filter((f) => f.size > MAX_MB * 1024 * 1024)
  const pending = docs.data?.filter((d) => d.status === 'pending').length ?? 0
  const attention = docs.data?.filter((d) => d.status !== 'extracted') ?? []
  const screened = (docs.data?.length ?? 0) - attention.length

  return (
    <Card
      title="CVs"
      description="CVs from the application form arrive here when you sync. You can also add CVs yourself, for example from a recruiter or a referral."
    >
      <div
        onDragOver={(e) => {
          e.preventDefault()
          setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          'flex flex-col items-center justify-center gap-2 rounded-lg border-2 border-dashed px-6 py-8 text-center transition-colors',
          dragging ? 'border-brand-500 bg-brand-50' : 'border-slate-300 bg-slate-50',
        )}
      >
        <FileUp aria-hidden="true" className="h-7 w-7 text-slate-400" />
        <p className="text-sm text-slate-700">
          Drop PDF or Word CVs here, or{' '}
          <button
            type="button"
            onClick={() => input.current?.click()}
            className="font-medium text-brand-700 underline underline-offset-2"
          >
            choose files
          </button>
        </p>
        <p className="text-xs text-slate-500">Up to 50 at a time, {MAX_MB} MB each.</p>
        <input
          ref={input}
          type="file"
          accept={ACCEPT}
          multiple
          className="sr-only"
          aria-label="Choose CV files"
          onChange={(e) => add(e.target.files)}
        />
      </div>

      {files.length > 0 && (
        <div className="mt-4 space-y-3">
          <ul className="max-h-40 space-y-1 overflow-y-auto text-sm">
            {files.map((f, i) => (
              <li key={`${f.name}-${i}`} className="flex items-center justify-between gap-3">
                <span className="truncate text-slate-800">{f.name}</span>
                <span className="tabular shrink-0 text-xs text-slate-500">
                  {(f.size / 1024).toFixed(0)} KB
                </span>
              </li>
            ))}
          </ul>
          {tooBig.length > 0 && (
            <Alert tone="warning">
              {tooBig.length} file{tooBig.length === 1 ? ' is' : 's are'} over {MAX_MB} MB and
              will be refused.
            </Alert>
          )}
          <div className="flex gap-2">
            <Button onClick={() => upload.mutate()} disabled={upload.isPending}>
              {upload.isPending
                ? 'Uploading'
                : `Upload ${files.length} CV${files.length === 1 ? '' : 's'}`}
            </Button>
            <Button variant="ghost" onClick={() => setFiles([])} disabled={upload.isPending}>
              Clear
            </Button>
          </div>
        </div>
      )}

      {upload.error && (
        <div className="mt-4">
          <Alert tone="error">
            {upload.error instanceof ApiError ? upload.error.message : 'The upload failed.'}
          </Alert>
        </div>
      )}
      {upload.data && (
        <div className="mt-4">
          <Alert tone={upload.data.rejected.length ? 'warning' : 'success'}>
            {upload.data.accepted.length} CV{upload.data.accepted.length === 1 ? '' : 's'} added.
            {upload.data.rejected.length > 0 && (
              <ul className="mt-1 list-disc pl-5">
                {upload.data.rejected.map((r) => (
                  <li key={r.filename}>
                    {r.filename}: {r.reason}
                  </li>
                ))}
              </ul>
            )}
          </Alert>
        </div>
      )}

      {pending > 0 && (
        <div className="mt-5 flex flex-wrap items-center justify-between gap-3 rounded-lg bg-marker/40 px-4 py-3">
          <p className="text-sm text-slate-900">
            {pending} CV{pending === 1 ? ' is' : 's are'} waiting to be screened.
          </p>
          <Link
            to={`/jobs/${jobId}?tab=screening`}
            className={buttonClass({ size: 'sm' })}
          >
            <ScanText aria-hidden="true" className="h-4 w-4" />
            Go to screening
          </Link>
        </div>
      )}

      {screened > 0 && (
        <p className="mt-5 text-sm text-slate-600">
          {screened} CV{screened === 1 ? ' has' : 's have'} been screened; the applicants are in
          the list above and in the Screening step.
        </p>
      )}

      {attention.length > 0 && (
        <ul className="mt-4 divide-y divide-slate-100 border-t border-slate-100">
          {attention.map((d) => (
            <li key={d.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm text-slate-900">
                  {d.candidate_name ?? d.filename}
                </span>
                {d.candidate_name && (
                  <span className="block truncate text-xs text-slate-500">{d.filename}</span>
                )}
                {d.failure_reason && (
                  <span className="block text-xs text-red-700">{d.failure_reason}</span>
                )}
              </span>
              <span className="text-xs text-slate-500">
                {d.source === 'upload' ? 'Uploaded' : 'From the form'}
                {d.used_ocr && ', read from a scan'}
              </span>
              <Badge tone={STATUS[d.status].tone}>{STATUS[d.status].label}</Badge>
            </li>
          ))}
        </ul>
      )}
    </Card>
  )
}
