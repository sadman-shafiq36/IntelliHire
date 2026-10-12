import { useQuery } from '@tanstack/react-query'
import { FileText, Plus } from 'lucide-react'
import { Link } from 'react-router-dom'

import { api } from '@/api/client'
import type { JobListItem } from '@/api/types'
import { Alert, Badge, Card, Spinner } from '@/components/ui'

export function JobsPage() {
  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.get<JobListItem[]>('/api/v1/jobs'),
  })

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Jobs</h1>
          <p className="mt-1 text-sm text-slate-600">
            Requirement profiles, and the job descriptions drafted from them.
          </p>
        </div>
        <Link
          to="/jobs/new"
          className="inline-flex h-10 items-center gap-2 rounded-md bg-brand-600 px-4 text-sm font-medium text-white hover:bg-brand-700"
        >
          <Plus aria-hidden="true" className="h-4 w-4" />
          New job
        </Link>
      </div>

      <Card>
        {jobs.isLoading && <Spinner />}
        {jobs.error && <Alert tone="error">{(jobs.error as Error).message}</Alert>}
        {jobs.data?.length === 0 && (
          <p className="text-sm text-slate-600">
            No jobs yet. Create one to define its requirements.
          </p>
        )}
        {jobs.data && jobs.data.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {jobs.data.map((job) => (
              <li key={job.id}>
                <Link
                  to={`/jobs/${job.id}`}
                  className="flex flex-wrap items-center gap-3 rounded-md px-2 py-3 hover:bg-slate-50"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-slate-900">{job.title}</p>
                    <p className="text-xs text-slate-500">
                      {[job.department, job.location].filter(Boolean).join(' · ') || '—'} ·{' '}
                      {job.requirement_count} requirements ({job.mandatory_count} must have)
                    </p>
                  </div>
                  {job.has_description ? (
                    <Badge tone="success">
                      <FileText aria-hidden="true" className="mr-1 h-3 w-3" />
                      description
                    </Badge>
                  ) : (
                    <Badge tone="warning">no description</Badge>
                  )}
                  <Badge>{job.status}</Badge>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  )
}
