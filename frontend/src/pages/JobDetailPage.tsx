import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Pencil, Trash2 } from 'lucide-react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api } from '@/api/client'
import type { Job } from '@/api/types'
import { Alert, Badge, Button, Card, Spinner } from '@/components/ui'
import { JobDescriptionPanel } from '@/pages/JobDescriptionPanel'
import { CATEGORY_LABEL } from '@/pages/JobFormPage'

export function JobDetailPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.get<Job>(`/api/v1/jobs/${jobId}`),
  })

  const remove = useMutation({
    mutationFn: () => api.delete<void>(`/api/v1/jobs/${jobId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      navigate('/jobs')
    },
  })

  if (job.isLoading) return <Spinner />
  if (job.error || !job.data)
    return <Alert tone="error">{(job.error as Error)?.message ?? 'Job not found.'}</Alert>

  const data = job.data

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <Link to="/jobs" className="text-sm text-brand-700 hover:underline">
            ← Jobs
          </Link>
          <h1 className="mt-1 text-2xl font-semibold text-slate-900">{data.title}</h1>
          <p className="mt-1 text-sm text-slate-600">
            {[data.department, data.location, data.employment_type].filter(Boolean).join(' · ')}
            {data.min_years_experience > 0 && ` · ${data.min_years_experience}+ years`}
          </p>
        </div>
        <div className="flex gap-2">
          <Badge>{data.status}</Badge>
          <Link
            to={`/jobs/${data.id}/edit`}
            className="inline-flex h-8 items-center gap-2 rounded-md border border-slate-300 bg-white px-3 text-sm font-medium text-slate-900 hover:bg-slate-50"
          >
            <Pencil aria-hidden="true" className="h-4 w-4" />
            Edit profile
          </Link>
          <Button
            variant="ghost"
            size="sm"
            aria-label="Delete job"
            disabled={remove.isPending}
            onClick={() => {
              if (window.confirm(`Delete "${data.title}"? This cannot be undone.`)) remove.mutate()
            }}
          >
            <Trash2 aria-hidden="true" className="h-4 w-4" />
          </Button>
        </div>
      </div>

      <Card title="Requirement profile">
        {data.summary && (
          <p className="mb-4 whitespace-pre-line text-sm text-slate-700">{data.summary}</p>
        )}
        <ul className="divide-y divide-slate-100">
          {data.requirements.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-3 py-2">
              <span className="flex-1 text-sm text-slate-900">{r.text}</span>
              <span className="text-xs text-slate-500">{CATEGORY_LABEL[r.category]}</span>
              {r.weight !== 1 && <span className="text-xs text-slate-500">weight {r.weight}</span>}
              <Badge tone={r.is_mandatory ? 'warning' : 'neutral'}>
                {r.is_mandatory ? 'must have' : 'nice to have'}
              </Badge>
            </li>
          ))}
        </ul>
      </Card>

      <JobDescriptionPanel job={data} />
    </div>
  )
}
