// the interviewer's overview: where each role stands and what needs doing next.
// only stages that exist in the product are shown

import { useQuery } from '@tanstack/react-query'
import {
  AlertTriangle,
  Calculator,
  CalendarPlus,
  ChevronRight,
  ExternalLink,
  FilePenLine,
  FileWarning,
  Link2,
  Megaphone,
  Plus,
  RefreshCw,
  ScanText,
  Send,
} from 'lucide-react'
import type { ComponentType } from 'react'
import { Link } from 'react-router-dom'

import { api } from '@/api/client'
import type { OverviewData } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { Alert, Badge, buttonClass, EmptyState, PageHeader, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

type Attention = OverviewData['attention'][number]

const ATTENTION: Record<
  Attention['kind'],
  { icon: ComponentType<{ className?: string }>; action: string; tab?: string }
> = {
  integration: { icon: Link2, action: 'Connect' },
  description: { icon: FilePenLine, action: 'Write description', tab: 'description' },
  post: { icon: Megaphone, action: 'Prepare post', tab: 'posting' },
  publish: { icon: Send, action: 'Review and publish', tab: 'posting' },
  failed: { icon: AlertTriangle, action: 'See what went wrong', tab: 'posting' },
  sync: { icon: RefreshCw, action: 'Sync applications', tab: 'applications' },
  policy: { icon: FileWarning, action: 'Open policies' },
  screen: { icon: ScanText, action: 'Screen CVs', tab: 'screening' },
  score: { icon: Calculator, action: 'Score candidates', tab: 'scoring' },
  interview: { icon: CalendarPlus, action: 'Schedule interviews', tab: 'interviews' },
}

function attentionLink(item: Attention): string {
  if (item.kind === 'integration') return '/settings'
  if (item.kind === 'policy') return '/policies'
  const tab = ATTENTION[item.kind].tab
  return `/jobs/${item.job_id}${tab ? `?tab=${tab}` : ''}`
}

function greeting(): string {
  const hour = new Date().getHours()
  if (hour < 12) return 'Good morning'
  if (hour < 18) return 'Good afternoon'
  return 'Good evening'
}

export function DashboardPage() {
  const { user } = useAuth()
  const overview = useQuery({
    queryKey: ['overview'],
    queryFn: () => api.get<OverviewData>('/api/v1/overview'),
  })

  const firstName = user?.full_name.split(' ')[0]

  return (
    <div>
      <PageHeader
        title={`${greeting()}${firstName ? `, ${firstName}` : ''}`}
        description="Where every role stands, and what needs you next."
        actions={
          <Link to="/jobs/new" className={buttonClass({})}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            New job
          </Link>
        }
      />

      {overview.isLoading && <Spinner label="Loading your overview" />}
      {overview.error && (
        <Alert tone="error">The overview could not be loaded. Check that the backend is running.</Alert>
      )}

      {overview.data && (
        <div className="grid gap-10 xl:grid-cols-[minmax(0,1fr)_21.25rem]">
          <div className="min-w-0 space-y-10">
            <section aria-labelledby="attention-heading">
              <h2 id="attention-heading" className="text-lg font-semibold text-slate-900">
                Needs your attention
              </h2>
              <div className="mt-4">
                {overview.data.attention.length === 0 ? (
                  <EmptyState
                    title="Nothing is waiting on you"
                    action={
                      <Link to="/jobs/new" className={buttonClass({ variant: 'outline' })}>
                        <Plus className="h-4 w-4" aria-hidden="true" />
                        Create a job
                      </Link>
                    }
                  >
                    Every role is described, posted and synced. Create another job when you have
                    one to fill.
                  </EmptyState>
                ) : (
                  <ul className="divide-y divide-slate-200 rounded-xl border border-slate-200 bg-white">
                    {overview.data.attention.map((item, i) => {
                      const meta = ATTENTION[item.kind]
                      return (
                        <li key={`${item.kind}-${item.job_id ?? i}`}>
                          <Link
                            to={attentionLink(item)}
                            className="group flex items-start gap-4 px-5 py-4 transition-colors hover:bg-slate-50 focus-visible:bg-slate-50 focus-visible:outline-none"
                          >
                            <span
                              className={cn(
                                'mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
                                item.kind === 'failed' || item.kind === 'policy'
                                  ? 'bg-red-50 text-red-700'
                                  : 'bg-brand-50 text-brand-700',
                              )}
                            >
                              <meta.icon className="h-[1.125rem] w-[1.125rem]" aria-hidden="true" />
                            </span>
                            <span className="min-w-0 flex-1">
                              <span className="block font-medium text-slate-900">{item.title}</span>
                              <span className="mt-0.5 block text-sm text-slate-600">
                                {item.detail}
                              </span>
                            </span>
                            <span className="mt-1 hidden shrink-0 items-center gap-1 text-sm font-medium text-brand-700 group-hover:underline sm:flex">
                              {meta.action}
                              <ChevronRight className="h-4 w-4" aria-hidden="true" />
                            </span>
                          </Link>
                        </li>
                      )
                    })}
                  </ul>
                )}
              </div>
            </section>

            <Pipeline data={overview.data} />
          </div>

          <div className="space-y-10">
            <LiveRoles jobs={overview.data.live_jobs} />
            <LatestApplications items={overview.data.recent_applications} />
          </div>
        </div>
      )}
    </div>
  )
}

function Pipeline({ data }: { data: OverviewData }) {
  const { pipeline } = data
  // every stage counts candidates, so the arrows and bars compare like with like
  const stages: { count: number; label: string; note?: string; empty?: string }[] = [
    {
      count: pipeline.applications,
      label: 'Applied',
      note: pipeline.new_this_week ? `+${pipeline.new_this_week} this week` : undefined,
      empty: 'Waiting for CVs',
    },
    { count: pipeline.screened, label: 'Screened', empty: 'Screen CVs to start' },
    { count: pipeline.scored, label: 'Scored', empty: 'Score screened CVs' },
    {
      count: pipeline.shortlisted,
      label: 'Shortlisted',
      note: pipeline.selected ? `${pipeline.selected} to interview` : undefined,
      empty: 'Shortlist from scoring',
    },
    {
      count: pipeline.interviewed,
      label: 'Interviewed',
      note: pipeline.interviews_live
        ? `${pipeline.interviews_live} live now`
        : pipeline.interviews_upcoming
          ? `${pipeline.interviews_upcoming} booked`
          : undefined,
      empty: 'None held yet',
    },
    { count: pipeline.assessed, label: 'Assessed', empty: 'Follows an interview' },
  ]
  const base = Math.max(pipeline.applications, 1)

  return (
    <section
      aria-labelledby="pipeline-heading"
      className="overflow-hidden rounded-2xl bg-slate-900 text-white"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 border-b border-white/10 px-6 py-4">
        <h2 id="pipeline-heading" className="font-semibold">
          Candidate pipeline
        </h2>
        <Link
          to="/jobs"
          className="rounded text-sm text-slate-400 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-marker"
        >
          {pipeline.roles} role{pipeline.roles === 1 ? '' : 's'} · {pipeline.live} live on LinkedIn
        </Link>
      </div>
      <ol className="grid grid-cols-2 sm:grid-cols-3 min-[1050px]:grid-cols-6">
        {stages.map((stage, i) => (
          <li
            key={stage.label}
            className={cn(
              'relative border-white/10 px-4 py-5',
              // two columns on phones, three on tablets, one row on wide screens
              i % 2 === 1 && 'border-l',
              i > 1 && 'border-t',
              i % 3 === 0 ? 'sm:border-l-0' : 'sm:border-l',
              i < 3 ? 'sm:border-t-0' : 'sm:border-t',
              i > 0 && 'min-[1050px]:border-l',
              'min-[1050px]:border-t-0',
            )}
          >
            <span
              className={cn(
                'tabular block text-3xl font-semibold leading-none tracking-[-0.03em]',
                stage.count === 0 && 'text-slate-500',
              )}
            >
              {stage.count}
            </span>
            <span className="mt-2 block text-sm font-medium">{stage.label}</span>
            <span
              className="mt-3 block h-1 overflow-hidden rounded-full bg-white/10"
              aria-hidden="true"
            >
              <span
                className="block h-full rounded-full bg-brand-500"
                style={{ width: `${Math.min(100, (stage.count / base) * 100)}%` }}
              />
            </span>
            <span
              className={cn(
                'mt-2 block truncate text-xs',
                i === 0 && stage.note ? 'font-medium text-marker' : 'text-slate-400',
              )}
            >
              {stage.count === 0 ? stage.empty : (stage.note ?? ' ')}
            </span>
            {i < stages.length - 1 && (
              <ChevronRight
                aria-hidden="true"
                className="absolute right-[-0.625rem] top-6 z-10 hidden h-5 w-5 text-slate-500 min-[1050px]:block"
              />
            )}
          </li>
        ))}
      </ol>
    </section>
  )
}

function LiveRoles({ jobs }: { jobs: OverviewData['live_jobs'] }) {
  return (
    <section aria-labelledby="live-heading">
      <h2 id="live-heading" className="text-lg font-semibold text-slate-900">
        Live roles
      </h2>
      {jobs.length === 0 ? (
        <p className="mt-3 text-sm text-slate-600">
          No role is posted yet. Publish one from its Posting step.
        </p>
      ) : (
        <ul className="mt-3 space-y-2">
          {jobs.map((job) => (
            <li
              key={job.id}
              className="flex items-center gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3"
            >
              <Link
                to={`/jobs/${job.id}?tab=applications`}
                className="min-w-0 flex-1 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <span className="block truncate font-medium text-slate-900 hover:underline">
                  {job.title}
                </span>
                <span className="block text-sm text-slate-600">
                  <span className="tabular">{job.applications}</span> application
                  {job.applications === 1 ? '' : 's'}
                  {job.location ? ` in ${job.location}` : ''}
                </span>
              </Link>
              {job.post_url && (
                <a
                  href={job.post_url}
                  target="_blank"
                  rel="noreferrer"
                  className="rounded-md p-2 text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                  aria-label={`Open the LinkedIn post for ${job.title}`}
                >
                  <ExternalLink className="h-4 w-4" aria-hidden="true" />
                </a>
              )}
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}

function LatestApplications({ items }: { items: OverviewData['recent_applications'] }) {
  return (
    <section aria-labelledby="latest-heading">
      <h2 id="latest-heading" className="text-lg font-semibold text-slate-900">
        Latest applications
      </h2>
      {items.length === 0 ? (
        <p className="mt-3 text-sm text-slate-600">
          Applications appear here after you sync a live role.
        </p>
      ) : (
        <ul className="mt-3 divide-y divide-slate-200 border-y border-slate-200">
          {items.map((app) => (
            <li key={app.id}>
              <Link
                to={`/jobs/${app.job_id}?tab=applications`}
                className="flex items-baseline justify-between gap-3 py-3 hover:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
              >
                <span className="min-w-0">
                  <span className="block truncate font-medium text-slate-900">{app.full_name}</span>
                  <span className="block truncate text-sm text-slate-600">{app.job_title}</span>
                </span>
                <Badge>{new Date(app.submitted_at).toLocaleDateString()}</Badge>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
