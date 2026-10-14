// applications collected for one job, pulled in from its google form

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { Link, Navigate, useParams } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { Application, JobPost } from '@/api/types'
import { Alert, Badge, Button, Card, Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { CvUploadCard } from '@/pages/CvUploadCard'

const CV_STATUS: Record<string, { label: string; tone: 'neutral' | 'info' | 'success' | 'warning' | 'error' }> = {
  pending: { label: 'waiting to be screened', tone: 'warning' },
  extracting: { label: 'screening', tone: 'info' },
  extracted: { label: 'screened', tone: 'success' },
  unreadable: { label: 'could not be read', tone: 'error' },
  failed: { label: 'screening failed', tone: 'error' },
}

// old /jobs/:id/applications links land on the job's applications step
export function ApplicationsPage() {
  const { jobId } = useParams()
  return <Navigate to={`/jobs/${jobId}?tab=applications`} replace />
}

export function ApplicationsPanel({ jobId }: { jobId: string }) {
  const queryClient = useQueryClient()
  const [runId, setRunId] = useState<string | null>(null)
  const progress = useBackgroundJob(runId)

  const post = useQuery({
    queryKey: ['job-post', Number(jobId)],
    queryFn: () => api.get<JobPost | null>(`/api/v1/jobs/${jobId}/post`),
  })
  const applications = useQuery({
    queryKey: ['applications', jobId],
    queryFn: () => api.get<Application[]>(`/api/v1/jobs/${jobId}/applications`),
  })

  useEffect(() => {
    if (isDone(progress.data)) {
      void queryClient.invalidateQueries({ queryKey: ['applications', jobId] })
      void queryClient.invalidateQueries({ queryKey: ['job-post', Number(jobId)] })
    }
  }, [progress.data, jobId, queryClient])

  const sync = useMutation({
    mutationFn: () =>
      api.post<{ background_job_id: string }>(`/api/v1/jobs/${jobId}/applications/sync`),
    onSuccess: (data) => setRunId(data.background_job_id),
  })

  const download = useMutation({
    mutationFn: async (app: Application) => {
      const blob = await api.download(`/api/v1/applications/${app.id}/cv`)
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = app.cv_filename ?? 'cv'
      link.click()
      URL.revokeObjectURL(url)
    },
  })

  const running = runId !== null && !isDone(progress.data)
  const result = progress.data?.state === 'finished' ? progress.data.result_ref : null
  const failedItems =
    (progress.data?.result_ref?.failed_items as { item_ref: string; reason: string }[]) ??
    progress.data?.failed_items ??
    []

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-4">
        <p className="text-sm text-slate-600">
          {post.data?.last_synced_at
            ? `Last synced ${new Date(post.data.last_synced_at).toLocaleString()}.`
            : 'Not synced yet.'}{' '}
          New responses to the job’s Google Form come in when you sync.
        </p>
        <Button onClick={() => sync.mutate()} disabled={running || !post.data?.form_url}>
          <RefreshCw aria-hidden="true" className={running ? 'h-4 w-4 animate-spin' : 'h-4 w-4'} />
          {running ? 'Syncing' : 'Sync applications'}
        </Button>
      </div>

      {!post.data?.form_url && post.isFetched && (applications.data?.length ?? 0) === 0 && (
        <Alert tone="info">
          This job has no application form yet. Prepare and publish its post in the{' '}
          <Link to={`/jobs/${jobId}?tab=posting`} className="font-medium underline">
            Posting
          </Link>{' '}
          step.
        </Alert>
      )}
      {sync.error && (
        <Alert tone="error">
          {sync.error instanceof ApiError ? sync.error.message : 'Could not start the sync.'}
        </Alert>
      )}
      {running && <Spinner label={progress.data?.message || 'Syncing'} />}
      {progress.data?.state === 'failed' && <Alert tone="error">{progress.data.message}</Alert>}
      {result && (
        <Alert tone={failedItems.length ? 'warning' : 'success'}>
          {String(result.imported)} new application{result.imported === 1 ? '' : 's'} imported
          {Number(result.already_imported) > 0 && `, ${String(result.already_imported)} already here`}.
          {failedItems.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {failedItems.map((item) => (
                <li key={item.item_ref}>
                  One response was skipped: {item.reason}
                </li>
              ))}
            </ul>
          )}
        </Alert>
      )}
      {download.error && (
        <Alert tone="error">
          {download.error instanceof ApiError ? download.error.message : 'Download failed.'}
        </Alert>
      )}

      <Card>
        {applications.isLoading && <Spinner />}
        {applications.data?.length === 0 && (
          <p className="text-sm text-slate-600">
            No applications yet. Once people apply through the form, press Sync applications.
          </p>
        )}
        {applications.data && applications.data.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="border-b border-slate-200 text-sm text-slate-600">
                <tr>
                  <th className="py-2 pr-4 font-medium">Applicant</th>
                  <th className="py-2 pr-4 font-medium">Contact</th>
                  <th className="py-2 pr-4 font-medium">Experience</th>
                  <th className="py-2 pr-4 font-medium">Submitted</th>
                  <th className="py-2 pr-4 font-medium">CV</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {applications.data.map((app) => (
                  <tr key={app.id} className="align-top">
                    <td className="py-3 pr-4">
                      <p className="font-medium text-slate-900">{app.full_name}</p>
                      {app.location && <p className="text-xs text-slate-500">{app.location}</p>}
                      {app.cover_note && (
                        <details className="mt-1">
                          <summary className="cursor-pointer text-xs text-brand-700">
                            Cover note
                          </summary>
                          <p className="mt-1 max-w-md whitespace-pre-line text-xs text-slate-700">
                            {app.cover_note}
                          </p>
                        </details>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-slate-700">
                      <p>{app.email}</p>
                      {app.phone && <p className="text-xs text-slate-500">{app.phone}</p>}
                      {app.linkedin_url && (
                        <a
                          href={app.linkedin_url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-xs text-brand-700 hover:underline"
                        >
                          LinkedIn profile
                        </a>
                      )}
                    </td>
                    <td className="py-3 pr-4 text-slate-700">
                      {app.years_experience ? `${app.years_experience} years` : '—'}
                    </td>
                    <td className="py-3 pr-4 text-slate-700">
                      {app.submitted_at ? new Date(app.submitted_at).toLocaleDateString() : '—'}
                    </td>
                    <td className="py-3 pr-4">
                      {app.cv_filename ? (
                        <div className="flex items-center gap-2">
                          <Button
                            variant="ghost"
                            size="sm"
                            className="h-7 px-2"
                            onClick={() => download.mutate(app)}
                            disabled={download.isPending}
                          >
                            <Download aria-hidden="true" className="h-4 w-4" />
                            <span className="max-w-[140px] truncate">{app.cv_filename}</span>
                          </Button>
                          {app.cv_status && (
                            <Badge tone={CV_STATUS[app.cv_status]?.tone ?? 'neutral'}>
                              {CV_STATUS[app.cv_status]?.label ?? app.cv_status}
                            </Badge>
                          )}
                        </div>
                      ) : (
                        '—'
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      <CvUploadCard jobId={jobId} />
    </div>
  )
}
