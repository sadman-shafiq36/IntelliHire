// candidates only see their own interviews, never scores or rankings

import { CalendarClock } from 'lucide-react'

import { EmptyState, PageHeader } from '@/components/ui'

export function CandidatePortalPage() {
  return (
    <div>
      <PageHeader
        title="My interviews"
        description="Interviews you are invited to appear here, with the link to join."
      />
      <EmptyState
        icon={<CalendarClock className="h-8 w-8" aria-hidden="true" />}
        title="No interviews scheduled"
      >
        When an interview is booked you will get an email with a join link, and it will show here
        too.
      </EmptyState>
    </div>
  )
}
