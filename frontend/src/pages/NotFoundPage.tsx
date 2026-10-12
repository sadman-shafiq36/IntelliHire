import { Link } from 'react-router-dom'

import { Card } from '@/components/ui'

export function NotFoundPage() {
  return (
    <div className="mx-auto mt-16 max-w-md">
      <Card title="Page not found">
        <p className="text-sm text-slate-600">
          That page does not exist. It may have moved, or the link may be incomplete.
        </p>
        <Link to="/" className="mt-4 inline-block text-sm font-medium text-brand-600 hover:underline">
          Back to the dashboard
        </Link>
      </Card>
    </div>
  )
}
