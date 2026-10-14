import { Link } from 'react-router-dom'

import { buttonClass, EmptyState } from '@/components/ui'

export function NotFoundPage() {
  return (
    <div className="mx-auto mt-8 max-w-lg">
      <EmptyState
        title="This page does not exist"
        action={
          <Link to="/" className={buttonClass({ variant: 'outline' })}>
            Go to the overview
          </Link>
        }
      >
        It may have moved, or the link may be incomplete.
      </EmptyState>
    </div>
  )
}
