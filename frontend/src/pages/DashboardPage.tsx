// each stage card is enabled once its module is built

import { Link } from 'react-router-dom'

import { useAuth } from '@/auth/AuthContext'
import { Card } from '@/components/ui'

interface Stage {
  module: string
  title: string
  description: string
  status: 'available' | 'planned'
  to?: string
}

const WORKFLOW: Stage[] = [
  {
    module: 'Module 3',
    title: 'Job description',
    description: 'Draft a job description from the requirement profile and company policy.',
    status: 'available',
    to: '/jobs',
  },
  {
    module: 'Module 4',
    title: 'Job posting & applications',
    description: 'Post the role on LinkedIn and collect CVs through a Google Form.',
    status: 'available',
    to: '/jobs',
  },
  {
    module: 'Module 5',
    title: 'CV screening & ranking',
    description: 'Match every CV against the requirements and rank by fulfilment.',
    status: 'planned',
  },
  {
    module: 'Module 6',
    title: 'Candidate scoring',
    description: 'Weighted scores per dimension, each with a written justification.',
    status: 'planned',
  },
  {
    module: 'Module 7',
    title: 'Interview scheduling',
    description: 'Send the invitation and the unique join link.',
    status: 'planned',
  },
  {
    module: 'Module 8',
    title: 'Interview & transcript',
    description: 'Run the session and capture a speaker-attributed transcript.',
    status: 'planned',
  },
  {
    module: 'Module 9',
    title: 'Interviewer co-pilot',
    description: 'Suggested questions and reference answers during the interview.',
    status: 'planned',
  },
  {
    module: 'Module 10',
    title: 'Q&A analysis',
    description: 'Pair questions with answers and map them to job requirements.',
    status: 'planned',
  },
  {
    module: 'Module 11',
    title: 'Consolidated assessment',
    description: 'One evidence-backed report, and candidates side by side.',
    status: 'planned',
  },
]

export function DashboardPage() {
  const { user } = useAuth()

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">
          Welcome{user ? `, ${user.full_name.split(' ')[0]}` : ''}
        </h1>
        <p className="mt-1 text-sm text-slate-600">
          The hiring workflow, end to end. Stages switch on as each module is delivered.
        </p>
      </div>

      <ol className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {WORKFLOW.map((stage) => (
          <li key={stage.module}>
            <Card className="h-full">
              <div className="flex items-start justify-between gap-2">
                <h2 className="text-sm font-semibold text-slate-900">{stage.title}</h2>
                <span className="shrink-0 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
                  {stage.module}
                </span>
              </div>
              <p className="mt-2 text-sm text-slate-600">{stage.description}</p>
              {stage.to ? (
                <Link
                  to={stage.to}
                  className="mt-3 inline-block text-xs font-medium uppercase tracking-wide text-green-700 hover:underline"
                >
                  Open
                </Link>
              ) : (
                <p className="mt-3 text-xs font-medium uppercase tracking-wide text-amber-600">
                  Not built yet
                </p>
              )}
            </Card>
          </li>
        ))}
      </ol>
    </div>
  )
}
