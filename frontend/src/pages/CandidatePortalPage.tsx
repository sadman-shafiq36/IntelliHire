// candidates only see their own interviews, never scores or rankings

import { Card } from '@/components/ui'

export function CandidatePortalPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">My interviews</h1>
        <p className="mt-1 text-sm text-slate-600">
          Interviews you have been invited to will appear here.
        </p>
      </div>

      <Card>
        <p className="text-sm text-slate-600">
          You have no scheduled interviews. When one is booked you will receive an email with a
          join link, and it will also show up on this page.
        </p>
      </Card>
    </div>
  )
}
