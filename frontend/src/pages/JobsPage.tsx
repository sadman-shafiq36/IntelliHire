// every role with how far it has got: profile, description, posting, applications, screening,
// scoring, interviews, assessment

import { useQuery } from '@tanstack/react-query'
import { Briefcase, ChevronRight, Plus, Search } from 'lucide-react'
import { useMemo, useState } from 'react'
import { Link } from 'react-router-dom'

import { api } from '@/api/client'
import type { JobListItem } from '@/api/types'
import { Alert, Badge, buttonClass, EmptyState, PageHeader, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

function nextStep(job: JobListItem): { label: string; tab?: string } {
  const closed = job.status === 'closed'
  // a closed role isn't posted again, only its remaining candidates are worked through
  if (!closed) {
    if (!job.has_description) return { label: 'Write the description', tab: 'description' }
    if (!job.post_status || job.post_status === 'failed')
      return { label: 'Prepare the post', tab: 'posting' }
    if (job.post_status === 'draft') return { label: 'Publish the post', tab: 'posting' }
  }
  if (job.pending_cv_count > 0)
    return { label: `Screen ${job.pending_cv_count} CV${job.pending_cv_count === 1 ? '' : 's'}`, tab: 'screening' }
  if (job.to_schedule_count > 0)
    return {
      label: `Schedule ${job.to_schedule_count} interview${job.to_schedule_count === 1 ? '' : 's'}`,
      tab: 'interviews',
    }
  if (job.unscored_count > 0)
    return { label: `Score ${job.unscored_count} candidate${job.unscored_count === 1 ? '' : 's'}`, tab: 'scoring' }
  if (closed) return { label: 'View the job' }
  if (job.assessed_count > 0) return { label: 'Review the assessments', tab: 'assessment' }
  if (job.scored_count > 0) return { label: 'Review the shortlist', tab: 'scoring' }
  if (job.screened_count > 0) return { label: 'Review the ranking', tab: 'screening' }
  if (job.application_count > 0) return { label: 'Review applications', tab: 'applications' }
  return { label: 'Waiting for applications', tab: 'applications' }
}

function plural(n: number, word: string) {
  return `${n} ${word}${n === 1 ? '' : 's'}`
}

export function JobsPage() {
  const [query, setQuery] = useState('')
  const jobs = useQuery({
    queryKey: ['jobs'],
    queryFn: () => api.get<JobListItem[]>('/api/v1/jobs'),
  })

  const visible = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return jobs.data ?? []
    return (jobs.data ?? []).filter((j) =>
      [j.title, j.department, j.location].some((v) => v?.toLowerCase().includes(q)),
    )
  }, [jobs.data, query])

  return (
    <div>
      <PageHeader
        title="Jobs"
        description="Each role moves through eight steps: profile, description, posting, applications, screening, scoring, interviews and assessment."
        actions={
          <Link to="/jobs/new" className={buttonClass({})}>
            <Plus aria-hidden="true" className="h-4 w-4" />
            New job
          </Link>
        }
      />

      {jobs.isLoading && <Spinner label="Loading jobs" />}
      {jobs.error && <Alert tone="error">{(jobs.error as Error).message}</Alert>}

      {jobs.data?.length === 0 && (
        <EmptyState
          icon={<Briefcase className="h-8 w-8" aria-hidden="true" />}
          title="No jobs yet"
          action={
            <Link to="/jobs/new" className={buttonClass({})}>
              <Plus aria-hidden="true" className="h-4 w-4" />
              Create your first job
            </Link>
          }
        >
          Start with the role and its requirements. The description, the LinkedIn post and the
          application form are built from them.
        </EmptyState>
      )}

      {jobs.data && jobs.data.length > 0 && (
        <>
          <div className="relative mb-4 max-w-sm">
            <Search
              aria-hidden="true"
              className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
            />
            <label htmlFor="job-search" className="sr-only">
              Search jobs
            </label>
            <input
              id="job-search"
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search by title, team or location"
              className="block w-full rounded-md border border-slate-300 bg-white py-2 pl-9 pr-3 text-sm placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-2 focus:ring-brand-500/25"
            />
          </div>

          {visible.length === 0 ? (
            <p className="text-sm text-slate-600">No job matches “{query}”.</p>
          ) : (
            <ul className="divide-y divide-slate-200 overflow-hidden rounded-xl border border-slate-200 bg-white">
              {visible.map((job) => {
                const next = nextStep(job)
                return (
                  <li key={job.id}>
                    <Link
                      to={next.tab ? `/jobs/${job.id}?tab=${next.tab}` : `/jobs/${job.id}`}
                      className="group grid gap-3 px-5 py-4 transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none sm:grid-cols-[minmax(0,1fr)_auto_12rem] sm:items-center sm:gap-4 xl:gap-6"
                    >
                      <span className="min-w-0">
                        <span className="flex items-center gap-2">
                          <span className="truncate font-medium text-slate-900">{job.title}</span>
                          {job.status === 'open' && <Badge tone="success">open</Badge>}
                          {job.status === 'closed' && <Badge tone="neutral">closed</Badge>}
                        </span>
                        {/* the place is worth more than the counts, so the counts are cut first */}
                        <span className="mt-0.5 flex min-w-0 text-sm text-slate-600">
                          <span className="max-w-full shrink-0 truncate">
                            {[job.department, job.location].filter(Boolean).join(', ') ||
                              'No team or location'}
                          </span>
                          <span className="min-w-0 truncate">
                            &nbsp;· {plural(job.requirement_count, 'requirement')}
                            {job.mandatory_count > 0 && ` (${job.mandatory_count} must have)`}
                          </span>
                        </span>
                      </span>
                      <Progress job={job} />
                      <span className="flex items-center gap-1 text-sm font-medium text-brand-700 sm:justify-end">
                        {next.label}
                        <ChevronRight
                          className="h-4 w-4 transition-transform group-hover:translate-x-0.5"
                          aria-hidden="true"
                        />
                      </span>
                    </Link>
                  </li>
                )
              })}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

function Progress({ job }: { job: JobListItem }) {
  const live = job.post_status === 'posted' || job.post_status === 'closed'
  // cvs can be uploaded without a live post; that still counts as collecting applications.
  // every step counts as reached once it has started; the action says what is left
  const steps = [
    { label: 'Profile', done: job.requirement_count > 0 },
    { label: 'Description', done: job.has_description },
    {
      label: live ? 'Posted' : job.application_count > 0 ? 'CVs added without a post' : 'Not posted',
      done: live || job.application_count > 0,
    },
    { label: plural(job.application_count, 'application'), done: job.application_count > 0 },
    { label: `${job.screened_count} screened`, done: job.screened_count > 0 },
    { label: `${job.scored_count} scored`, done: job.scored_count > 0 },
    { label: plural(job.interview_count, 'interview'), done: job.interview_count > 0 },
    { label: `${job.assessed_count} assessed`, done: job.assessed_count > 0 },
  ]
  const done = steps.filter((s) => s.done).length
  return (
    <span
      className="flex items-center gap-1 xl:gap-1.5"
      aria-label={`${done} of ${steps.length} steps done: ${steps.map((s) => `${s.label} ${s.done ? 'done' : 'to do'}`).join(', ')}`}
    >
      {steps.map((s) => (
        <span
          key={s.label}
          title={s.label}
          className={cn('h-1.5 w-4 rounded-full xl:w-6', s.done ? 'bg-brand-600' : 'bg-slate-200')}
        />
      ))}
      {/* fixed width so the bars line up from row to row */}
      <span className="tabular ml-2 w-16 text-right text-xs text-slate-600">
        {job.application_count} applied
      </span>
    </span>
  )
}
