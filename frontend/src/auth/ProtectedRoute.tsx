// only hides screens, the real check is in backend/app/deps.py

import type { ReactNode } from 'react'
import { Navigate, useLocation } from 'react-router-dom'

import type { UserRole } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { Spinner } from '@/components/ui'

export function ProtectedRoute({
  children,
  allow,
}: {
  children: ReactNode
  // omit to allow any signed-in user
  allow?: UserRole[]
}) {
  const { user, isLoading } = useAuth()
  const location = useLocation()

  if (isLoading) {
    return (
      <div className="flex min-h-[40vh] items-center justify-center">
        <Spinner label="Checking your session" />
      </div>
    )
  }

  if (!user) {
    // state.from sends them back after login
    return <Navigate to="/sign-in" replace state={{ from: location.pathname }} />
  }

  if (allow && !allow.includes(user.role)) {
    return <Navigate to={user.role === 'candidate' ? '/portal' : '/'} replace />
  }

  return <>{children}</>
}
