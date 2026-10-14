import { type FormEvent, useState } from 'react'
import { Link, Navigate, useNavigate } from 'react-router-dom'

import { ApiError } from '@/api/client'
import type { UserRole } from '@/api/types'
import { useAuth } from '@/auth/AuthContext'
import { AuthShell } from '@/components/AuthShell'
import { Alert, Button, Field } from '@/components/ui'
import { cn } from '@/lib/cn'

const ROLES: { value: UserRole; label: string; note: string }[] = [
  { value: 'interviewer', label: 'Interviewer', note: 'Create jobs, screen CVs, run interviews' },
  { value: 'candidate', label: 'Candidate', note: 'Join the interviews you are invited to' },
]

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
    <AuthShell
      title="Create an account"
      subtitle="Interviewers run the hiring; candidates only see their own interviews."
    >
      <div>
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

          <fieldset>
            <legend className="mb-1.5 block text-sm font-medium text-slate-800">I am</legend>
            <div className="grid grid-cols-2 gap-2">
              {ROLES.map((option) => (
                <label
                  key={option.value}
                  className={cn(
                    'cursor-pointer rounded-lg border bg-white px-3 py-2.5 transition-colors',
                    'focus-within:ring-2 focus-within:ring-brand-500',
                    role === option.value
                      ? 'border-slate-900 ring-1 ring-slate-900'
                      : 'border-slate-300 hover:border-slate-400',
                  )}
                >
                  <input
                    type="radio"
                    name="role"
                    value={option.value}
                    checked={role === option.value}
                    onChange={() => setRole(option.value)}
                    className="sr-only"
                  />
                  <span className="block text-sm font-medium text-slate-900">{option.label}</span>
                  <span className="mt-0.5 block text-xs leading-snug text-slate-600">
                    {option.note}
                  </span>
                </label>
              ))}
            </div>
          </fieldset>

          <Button type="submit" size="lg" className="w-full" disabled={isSubmitting}>
            {isSubmitting ? 'Creating account…' : 'Create account'}
          </Button>
        </form>

        <p className="mt-6 text-sm text-slate-600">
          Already registered?{' '}
          <Link to="/sign-in" className="font-medium text-brand-700 underline-offset-2 hover:underline">
            Sign in
          </Link>
        </p>
      </div>
    </AuthShell>
  )
}
