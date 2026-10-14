import { type FormEvent, useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'

import { ApiError } from '@/api/client'
import { useAuth } from '@/auth/AuthContext'
import { AuthShell } from '@/components/AuthShell'
import { Alert, Button, Field } from '@/components/ui'

export function SignInPage() {
  const { user, signIn } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  // already signed in, skip the form
  if (user) return <Navigate to={user.role === 'candidate' ? '/portal' : '/'} replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      const signedIn = await signIn({ email, password })
      const intended = (location.state as { from?: string } | null)?.from
      navigate(intended ?? (signedIn.role === 'candidate' ? '/portal' : '/'), { replace: true })
    } catch (cause) {
      // backend messages are user-facing already, show them as-is
      setError(cause instanceof ApiError ? cause.message : 'Could not sign you in.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <AuthShell title="Sign in" subtitle="Welcome back. Pick up where your hiring left off.">
      <div>
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <Alert tone="error">{error}</Alert>}

          <Field
            label="Email address"
            name="email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(event) => setEmail(event.target.value)}
          />
          <Field
            label="Password"
            name="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />

          <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
            {isSubmitting ? 'Signing in…' : 'Sign in'}
          </Button>
        </form>

        <p className="mt-6 text-sm text-slate-600">
          No account yet?{' '}
          <Link to="/sign-up" className="font-medium text-brand-700 underline-offset-2 hover:underline">
            Create one
          </Link>
        </p>
      </div>
    </AuthShell>
  )
}
