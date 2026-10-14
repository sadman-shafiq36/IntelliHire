// create and edit a job requirement profile. /jobs/new and /jobs/:id/edit

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus, Trash2 } from 'lucide-react'
import { type FormEvent, useEffect, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { Job, JobInput, RequirementCategory, RequirementInput } from '@/api/types'
import { Alert, Button, Card, Field, PageHeader, Spinner, Textarea } from '@/components/ui'

export const CATEGORY_LABEL: Record<RequirementCategory, string> = {
  technical_skill: 'Technical skill',
  experience: 'Experience',
  qualification: 'Qualification',
  responsibility: 'Responsibility',
  other: 'Other',
}

const EMPTY_REQUIREMENT: RequirementInput = {
  text: '',
  category: 'technical_skill',
  is_mandatory: false,
  weight: 1,
}

const EMPTY_JOB: JobInput = {
  title: '',
  department: null,
  location: null,
  employment_type: 'Full-time',
  min_years_experience: 0,
  summary: null,
  requirements: [{ ...EMPTY_REQUIREMENT, is_mandatory: true }],
}

function toInput(job: Job): JobInput {
  return {
    title: job.title,
    department: job.department,
    location: job.location,
    employment_type: job.employment_type,
    min_years_experience: job.min_years_experience,
    summary: job.summary,
    requirements: job.requirements.map(({ id, text, category, is_mandatory, weight }) => ({
      id,
      text,
      category,
      is_mandatory,
      weight,
    })),
  }
}

export function JobFormPage() {
  const { jobId } = useParams()
  const isEdit = jobId !== undefined
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const existing = useQuery({
    queryKey: ['job', jobId],
    queryFn: () => api.get<Job>(`/api/v1/jobs/${jobId}`),
    enabled: isEdit,
  })

  const [form, setForm] = useState<JobInput>(EMPTY_JOB)
  useEffect(() => {
    if (existing.data) setForm(toInput(existing.data))
  }, [existing.data])

  const save = useMutation({
    mutationFn: (payload: JobInput) =>
      isEdit
        ? api.put<Job>(`/api/v1/jobs/${jobId}`, payload)
        : api.post<Job>('/api/v1/jobs', payload),
    onSuccess: (job) => {
      void queryClient.invalidateQueries({ queryKey: ['jobs'] })
      queryClient.setQueryData(['job', String(job.id)], job)
      navigate(`/jobs/${job.id}`)
    },
  })

  function update<K extends keyof JobInput>(key: K, value: JobInput[K]) {
    setForm((current) => ({ ...current, [key]: value }))
  }

  function updateRequirement(index: number, patch: Partial<RequirementInput>) {
    setForm((current) => ({
      ...current,
      requirements: current.requirements.map((r, i) => (i === index ? { ...r, ...patch } : r)),
    }))
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    save.mutate({
      ...form,
      requirements: form.requirements.filter((r) => r.text.trim().length > 0),
    })
  }

  if (isEdit && existing.isLoading) return <Spinner />
  if (isEdit && existing.error)
    return <Alert tone="error">{(existing.error as Error).message}</Alert>

  const problems = save.error instanceof ApiError ? fieldProblems(save.error) : []

  return (
    <form onSubmit={submit} className="space-y-6">
      <PageHeader
        back={
          <Link to={isEdit ? `/jobs/${jobId}` : '/jobs'} className="text-brand-700 hover:underline">
            {isEdit ? 'Back to the job' : 'Jobs'}
          </Link>
        }
        title={isEdit ? 'Edit job profile' : 'New job'}
        description="Start with the role and what it needs. The description, the LinkedIn post and, later, CV screening are all built from these requirements."
      />

      <Card title="Role">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            label="Job title"
            name="title"
            required
            minLength={2}
            maxLength={200}
            value={form.title}
            onChange={(e) => update('title', e.target.value)}
          />
          <Field
            label="Department"
            name="department"
            maxLength={120}
            value={form.department ?? ''}
            onChange={(e) => update('department', e.target.value || null)}
          />
          <Field
            label="Location"
            name="location"
            placeholder="e.g. Dhaka (hybrid)"
            maxLength={160}
            value={form.location ?? ''}
            onChange={(e) => update('location', e.target.value || null)}
          />
          <Field
            label="Employment type"
            name="employment_type"
            placeholder="e.g. Full-time"
            maxLength={60}
            value={form.employment_type ?? ''}
            onChange={(e) => update('employment_type', e.target.value || null)}
          />
          <Field
            label="Minimum years of experience"
            name="min_years_experience"
            type="number"
            min={0}
            max={50}
            value={form.min_years_experience}
            onChange={(e) => update('min_years_experience', Number(e.target.value) || 0)}
          />
        </div>
        <div className="mt-4">
          <Textarea
            label="Selection criteria and context"
            name="summary"
            rows={4}
            maxLength={4000}
            hint="Anything else the agent should know: team, product, what success looks like."
            value={form.summary ?? ''}
            onChange={(e) => update('summary', e.target.value || null)}
          />
        </div>
      </Card>

      <Card
        title="Requirements"
        description="Must-have requirements filter candidates out at screening. The rest are nice to have. Weight sets importance within a category."
      >
        <ul className="space-y-3">
          {form.requirements.map((requirement, index) => (
            <li
              key={requirement.id ?? `new-${index}`}
              className="grid gap-2 rounded-md border border-slate-200 p-3 sm:grid-cols-[1fr_170px_auto_90px_auto] sm:items-center"
            >
              <input
                aria-label={`Requirement ${index + 1}`}
                value={requirement.text}
                maxLength={500}
                placeholder="e.g. Python web development"
                onChange={(e) => updateRequirement(index, { text: e.target.value })}
                className="block w-full rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
              <select
                aria-label={`Category of requirement ${index + 1}`}
                value={requirement.category}
                onChange={(e) =>
                  updateRequirement(index, { category: e.target.value as RequirementCategory })
                }
                className="rounded-md border border-slate-300 px-2 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              >
                {Object.entries(CATEGORY_LABEL).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={requirement.is_mandatory}
                  onChange={(e) => updateRequirement(index, { is_mandatory: e.target.checked })}
                  className="h-4 w-4 rounded border-slate-300"
                />
                Must have
              </label>
              <input
                aria-label={`Weight of requirement ${index + 1}`}
                type="number"
                min={0.5}
                max={10}
                step={0.5}
                value={requirement.weight}
                onChange={(e) => updateRequirement(index, { weight: Number(e.target.value) || 1 })}
                className="rounded-md border border-slate-300 px-2 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500"
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={`Remove requirement ${index + 1}`}
                disabled={form.requirements.length === 1}
                onClick={() =>
                  update(
                    'requirements',
                    form.requirements.filter((_, i) => i !== index),
                  )
                }
              >
                <Trash2 aria-hidden="true" className="h-4 w-4" />
              </Button>
            </li>
          ))}
        </ul>
        <Button
          type="button"
          variant="outline"
          size="sm"
          className="mt-3"
          disabled={form.requirements.length >= 50}
          onClick={() => update('requirements', [...form.requirements, { ...EMPTY_REQUIREMENT }])}
        >
          <Plus aria-hidden="true" className="h-4 w-4" />
          Add requirement
        </Button>
      </Card>

      {save.error && (
        <Alert tone="error">
          {save.error instanceof ApiError ? save.error.message : 'Could not save the job.'}
          {problems.length > 0 && (
            <ul className="mt-1 list-disc pl-5">
              {problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
        </Alert>
      )}

      <div className="flex gap-3">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? 'Saving' : isEdit ? 'Save changes' : 'Create job'}
        </Button>
        <Link
          to={isEdit ? `/jobs/${jobId}` : '/jobs'}
          className="inline-flex h-10 items-center rounded-md px-4 text-sm text-slate-700 hover:bg-slate-100"
        >
          Cancel
        </Link>
      </div>
    </form>
  )
}

function fieldProblems(error: ApiError): string[] {
  const problems = error.details?.problems
  if (!Array.isArray(problems)) return []
  return problems.map((p: { field?: string; reason?: string }) =>
    p.field ? `${p.field}: ${p.reason}` : String(p.reason),
  )
}
