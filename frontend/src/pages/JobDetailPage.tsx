// one job as a workspace: profile -> description -> posting -> applications -> screening -> scoring.
// the step lives in ?tab= so links from the overview land on the right one

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, Pencil, Trash2 } from 'lucide-react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'

import { api } from '@/api/client'
import type { Application, Job, JobPost, ScoringResults, ScreeningResults } from '@/api/types'
import { Alert, Badge, Button, buttonClass, Card, PageHeader, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'
import { ApplicationsPanel } from '@/pages/ApplicationsPage'
import { JobDescriptionPanel } from '@/pages/JobDescriptionPanel'
import { CATEGORY_LABEL } from '@/pages/JobFormPage'
import { JobPostingPanel } from '@/pages/JobPostingPanel'
import { ScoringPanel } from '@/pages/ScoringPanel'
import { ScreeningPanel } from '@/pages/ScreeningPanel'

const STEPS = ['profile', 'description', 'posting', 'applications', 'screening', 'scoring'] as const
type Step = (typeof STEPS)[number]

export function JobDetailPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()

  const job = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.get<Job>(`/api/v1/jobs/${jobId}`),
  })
  const post = useQuery({
    queryKey: ['job-post', Number(jobId)],
    queryFn: () => api.get<JobPost | null>(`/api/v1/jobs/${jobId}/post`),
  })
  const applications = useQuery({
    queryKey: ['applications', jobId],
    queryFn: () => api.get<Application[]>(`/api/v1/jobs/${jobId}/applications`),
  })
  const screening = useQuery({
    queryKey: ['screening', jobId],
    queryFn: () => api.get<ScreeningResults>(`/api/v1/jobs/${jobId}/screening`),
  })
  const scoring = useQuery({
    queryKey: ['scoring', jobId],
    queryFn: () => api.get<ScoringResults>(`/api/v1/jobs/${jobId}/scoring`),
  })

  const remove = useMutation({
    mutationFn: () => api.delete<void>(`/api/v1/jobs/${jobId}`),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      void queryClient.invalidateQueries({ queryKey: ['overview'] })
      navigate('/jobs')
    },
  })

  if (job.isLoading) return <Spinner label="Loading the job" />
  if (job.error || !job.data)
    return <Alert tone="error">{(job.error as Error)?.message ?? 'Job not found.'}</Alert>

  const data = job.data
  const requested = params.get('tab') as Step | null
  const active: Step = requested && STEPS.includes(requested) ? requested : defaultStep(data)
  const postStatus = post.data?.status
  const received = applications.data?.length ?? 0

  const steps: { id: Step; label: string; state: string; done: boolean }[] = [
    {
      id: 'profile',
      label: 'Profile',
      state: `${data.requirements.length} requirement${data.requirements.length === 1 ? '' : 's'}`,
      done: data.requirements.length > 0,
    },
    {
      id: 'description',
      label: 'Description',
      state: data.description_md ? 'Ready' : 'Not written',
      done: !!data.description_md,
    },
    {
      id: 'posting',
      label: 'Posting',
      state:
        postStatus === 'posted'
          ? 'Live on LinkedIn'
          : postStatus === 'draft'
            ? 'Draft ready'
            : postStatus === 'closed'
              ? 'Closed'
              : postStatus === 'failed'
                ? 'Needs attention'
                : 'Not posted',
      done: postStatus === 'posted' || postStatus === 'closed',
    },
    {
      id: 'applications',
      label: 'Applications',
      state: `${received} received`,
      done: received > 0,
    },
    {
      id: 'screening',
      label: 'Screening',
      state: screeningState(screening.data),
      done: !!screening.data && screening.data.ranked.length > 0 && screening.data.pending_count === 0,
    },
    {
      id: 'scoring',
      label: 'Scoring',
      state: scoringState(scoring.data),
      done: !!scoring.data && scoring.data.ranked.length > 0 && scoring.data.waiting_count === 0,
    },
  ]

  const facts = [
    data.department,
    data.location,
    data.employment_type,
    data.min_years_experience > 0 ? `${data.min_years_experience}+ years` : null,
  ].filter(Boolean)

  return (
    <div>
      <PageHeader
        back={
          <Link to="/jobs" className="text-brand-700 hover:underline">
            Jobs
          </Link>
        }
        title={
          <span className="flex flex-wrap items-center gap-3">
            {data.title}
            <Badge tone={data.status === 'open' ? 'success' : 'neutral'}>{data.status}</Badge>
          </span>
        }
        description={facts.length ? facts.join(', ') : undefined}
        actions={
          <>
            <Link
              to={`/jobs/${data.id}/edit`}
              className={buttonClass({ variant: 'outline', size: 'sm' })}
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
          </>
        }
      />

      <nav aria-label="Job steps" className="mb-8">
        <ol role="tablist" className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
          {steps.map((step, i) => {
            const selected = step.id === active
            return (
              <li key={step.id} role="presentation">
                <button
                  type="button"
                  role="tab"
                  aria-selected={selected}
                  aria-controls="job-step-panel"
                  onClick={() => setParams({ tab: step.id }, { replace: true })}
                  className={cn(
                    'flex w-full items-start gap-3 rounded-xl border px-4 py-3 text-left transition-colors',
                    'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                    selected
                      ? 'border-slate-900 bg-white'
                      : 'border-slate-200 bg-white/60 hover:border-slate-300 hover:bg-white',
                  )}
                >
                  <span
                    className={cn(
                      'tabular mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold',
                      step.done
                        ? 'bg-brand-600 text-white'
                        : selected
                          ? 'bg-marker text-slate-900'
                          : 'bg-slate-200 text-slate-700',
                    )}
                  >
                    {step.done ? <Check className="h-3.5 w-3.5" aria-label="done" /> : i + 1}
                  </span>
                  <span className="min-w-0">
                    <span className="block font-medium text-slate-900">{step.label}</span>
                    <span className="block truncate text-sm text-slate-600">{step.state}</span>
                  </span>
                </button>
              </li>
            )
          })}
        </ol>
      </nav>

      <div id="job-step-panel" role="tabpanel" aria-label={active}>
        {active === 'profile' && <ProfileStep job={data} />}
        {active === 'description' && <JobDescriptionPanel job={data} />}
        {active === 'posting' && <JobPostingPanel job={data} />}
        {active === 'applications' && <ApplicationsPanel jobId={String(data.id)} />}
        {active === 'screening' && <ScreeningPanel jobId={String(data.id)} />}
        {active === 'scoring' && <ScoringPanel jobId={String(data.id)} />}
      </div>
    </div>
  )
}

function screeningState(data: ScreeningResults | undefined): string {
  if (!data) return 'Not started'
  if (data.pending_count > 0) return `${data.pending_count} waiting`
  if (data.ranked.length === 0) return 'Not started'
  return `${data.ranked.length} ranked`
}

function scoringState(data: ScoringResults | undefined): string {
  if (!data || (data.ranked.length === 0 && data.waiting_count === 0)) return 'Not started'
  if (data.waiting_count > 0) return `${data.waiting_count} to score`
  const listed = data.ranked.filter((c) => c.is_shortlisted).length
  return listed > 0 ? `${listed} shortlisted` : `${data.ranked.length} scored`
}

// open on the first step that still needs work
function defaultStep(job: Job): Step {
  if (!job.description_md) return 'description'
  return 'posting'
}

function ProfileStep({ job }: { job: Job }) {
  const must = job.requirements.filter((r) => r.is_mandatory)
  const nice = job.requirements.filter((r) => !r.is_mandatory)
  return (
    <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
      <Card title="Requirements" description="Must-have requirements decide who is screened out.">
        <RequirementGroup title="Must have" items={must} />
        <div className="mt-6">
          <RequirementGroup title="Nice to have" items={nice} />
        </div>
      </Card>
      <Card title="Selection criteria">
        {job.summary ? (
          <p className="whitespace-pre-line text-sm leading-relaxed text-slate-700">{job.summary}</p>
        ) : (
          <p className="text-sm text-slate-600">
            None given. Add context about the team and what success looks like in{' '}
            <Link to={`/jobs/${job.id}/edit`} className="font-medium text-brand-700 underline">
              Edit profile
            </Link>
            ; the description agent uses it.
          </p>
        )}
      </Card>
    </div>
  )
}

function RequirementGroup({ title, items }: { title: string; items: Job['requirements'] }) {
  return (
    <div>
      <h3 className="text-sm font-semibold text-slate-900">
        {title} <span className="tabular font-normal text-slate-500">({items.length})</span>
      </h3>
      {items.length === 0 ? (
        <p className="mt-2 text-sm text-slate-500">None.</p>
      ) : (
        <ul className="mt-2 divide-y divide-slate-100">
          {items.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2.5">
              <span className="flex-1 text-[0.9375rem] text-slate-900">{r.text}</span>
              <span className="text-xs text-slate-500">{CATEGORY_LABEL[r.category]}</span>
              {r.weight !== 1 && (
                <span className="tabular text-xs text-slate-500">weight {r.weight}</span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
