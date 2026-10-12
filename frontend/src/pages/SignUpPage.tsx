import { type FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'

import { ApiError } from '@/api/client'
import type { UserRole } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { Alert, Button, Card, Field } from '@/components/ui'

const MIN_PASSWORD_LENGTH = 8

export function SignUpPage() {
  const { user, signUp } = useAuth()
  const navigate = useNavigate()

  const [fullName, setFullName] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [role, setRole] = useState<UserRole>('interviewer')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  if (user) return <Navigate to={user.role === 'candidate' ? '/portal' : '/'} replace />

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    setIsSubmitting(true)
    try {
      const created = await signUp({ full_name: fullName, email, password, role })
      navigate(created.role === 'candidate' ? '/portal' : '/', { replace: true })
    } catch (cause) {
      setError(cause instanceof ApiError ? cause.message : 'Could not create your account.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="mx-auto mt-16 max-w-md">
      <Card title="Create an account">
        <form onSubmit={handleSubmit} className="space-y-4">
          {error && <Alert tone="error">{error}</Alert>}

          <Field
            label="Full name"
            name="full_name"
            autoComplete="name"
            required
            value={fullName}
            onChange={(event) => setFullName(event.target.value)}
          />
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
            autoComplete="new-password"
            required
            minLength={MIN_PASSWORD_LENGTH}
            hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
            value={password}
            onChange={(event) => setPassword(event.target.value)}
          />

          <fieldset className="space-y-1.5">
            <legend className="block text-sm font-medium text-slate-700">I am a</legend>
            <select
              name="role"
              value={role}
              onChange={(event) => setRole(event.target.value as UserRole)}
              className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm text-slate-900 focus:outline-none focus:ring-2 focus:ring-brand-500"
            >
              <option value="interviewer">Interviewer / recruiter</option>
              <option value="candidate">Candidate</option>
            </select>
            <p className="text-xs text-slate-500">
              Candidates can join their own interviews. Screening, scoring and assessments are
              visible to interviewers only.
            </p>
          </fieldset>

          <Button type="submit" className="w-full" disabled={isSubmitting}>
            {isSubmitting ? 'Creating account…' : 'Create account'}
          </Button>
        </form>

        <p className="mt-4 text-sm text-slate-600">
          Already registered?{' '}
          <Link to="/sign-in" className="font-medium text-brand-600 hover:underline">
            Sign in
          </Link>
          .
        </p>
      </Card>
    </div>
  )
}
