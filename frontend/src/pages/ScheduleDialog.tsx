// the dialog for scheduling, moving, retrying or cancelling one interview

import { CalendarClock, Sparkles, X } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'

import type { InterviewRow, ScheduleOptions } from '@/api/types'
import { Alert, Button, Field, inputClass, Textarea } from '@/components/ui'
import { cn } from '@/lib/cn'

export type DialogMode =
  | { kind: 'schedule'; candidateId: number; name: string }
  | { kind: 'reschedule'; interview: InterviewRow }
  | { kind: 'cancel'; interview: InterviewRow }

const LENGTHS = [30, 45, 60, 90]

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

function localValue(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`
}

// for <input type=datetime-local>: the interview's current time when moving it, otherwise
// the next weekday at 10:00
function defaultStart(existing: InterviewRow | null): string {
  if (existing?.scheduled_start && existing.status === 'scheduled')
    return localValue(new Date(existing.scheduled_start))
  const d = new Date()
  d.setDate(d.getDate() + 1)
  while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1)
  d.setHours(10, 0, 0, 0)
  return localValue(d)
}

export function ScheduleDialog({
  mode,
  pending,
  error,
  onClose,
  onSubmit,
}: {
  mode: DialogMode
  pending: boolean
  error: string | null
  onClose: () => void
  onSubmit: (options?: ScheduleOptions, reason?: string) => void
}) {
  const first = useRef<HTMLButtonElement>(null)
  const existing = mode.kind === 'schedule' ? null : mode.interview
  const retry = existing?.status === 'failed'
  const [how, setHow] = useState<'exact' | 'agent'>('exact')
  const [start, setStart] = useState(() => defaultStart(existing))
  const [preference, setPreference] = useState('')
  const [duration, setDuration] = useState(existing?.duration_minutes ?? 45)
  const [note, setNote] = useState('')
  const [reason, setReason] = useState('')
  const [allowConflict, setAllowConflict] = useState(false)
  const zone = browserZone()

  useEffect(() => {
    first.current?.focus()
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const name = mode.kind === 'schedule' ? mode.name : mode.interview.full_name
  const title =
    mode.kind === 'schedule'
      ? `Schedule ${name}`
      : mode.kind === 'cancel'
        ? retry
          ? `Dismiss the booking for ${name}`
          : `Cancel ${name}'s interview`
        : retry
          ? `Try again for ${name}`
          : `Move ${name}'s interview`

  function send() {
    if (mode.kind === 'cancel') return onSubmit(undefined, reason.trim() || undefined)
    onSubmit({
      duration_minutes: duration,
      time_zone: zone,
      start: how === 'exact' ? start : null,
      preference: how === 'agent' ? preference.trim() : null,
      allow_conflict: allowConflict,
      note: note.trim() || null,
    })
  }

  const canSend =
    mode.kind === 'cancel' || (how === 'exact' ? !!start : preference.trim().length > 2)

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center sm:items-center" role="dialog" aria-modal="true" aria-labelledby="schedule-title">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} aria-hidden="true" />
      <div className="relative max-h-[92vh] w-full max-w-lg overflow-y-auto rounded-t-2xl bg-white p-6 shadow-2xl sm:rounded-2xl">
        <div className="flex items-start justify-between gap-4">
          <h2 id="schedule-title" className="text-lg font-semibold text-slate-900">
            {title}
          </h2>
          <button
            ref={first}
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="rounded-md p-1.5 text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500"
          >
            <X aria-hidden="true" className="h-5 w-5" />
          </button>
        </div>

        <form
          className="mt-5 space-y-5"
          onSubmit={(e) => {
            e.preventDefault()
            if (canSend) send()
          }}
        >
          {mode.kind === 'cancel' ? (
            retry ? (
              <p className="text-sm text-slate-700">
                Nothing was booked and no email was sent, so nobody needs to be told.
              </p>
            ) : (
              <>
                <p className="text-sm text-slate-700">
                  {name} gets an email saying the interview is cancelled, then the event is
                  removed from your calendar and their link stops working.
                </p>
                <Textarea
                  label="Reason (optional, included in the email)"
                  rows={2}
                  maxLength={500}
                  value={reason}
                  onChange={(e) => setReason(e.target.value)}
                />
              </>
            )
          ) : (
            <>
              <fieldset>
                <legend className="text-sm font-medium text-slate-800">When</legend>
                <div className="mt-2 grid grid-cols-2 gap-2">
                  {(
                    [
                      ['exact', 'Pick a time', CalendarClock],
                      ['agent', 'Let the agent find one', Sparkles],
                    ] as const
                  ).map(([value, label, Icon]) => (
                    <label
                      key={value}
                      className={cn(
                        'flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2.5 text-sm transition-colors',
                        'has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-brand-500',
                        how === value ? 'border-slate-900 bg-slate-50 font-medium text-slate-900' : 'border-slate-200 text-slate-700 hover:border-slate-300',
                      )}
                    >
                      <input
                        type="radio"
                        name="how"
                        value={value}
                        checked={how === value}
                        onChange={() => setHow(value)}
                        className="sr-only"
                      />
                      <Icon aria-hidden="true" className="h-4 w-4 shrink-0" />
                      {label}
                    </label>
                  ))}
                </div>
              </fieldset>

              {how === 'exact' ? (
                <Field
                  label="Start"
                  type="datetime-local"
                  step={900}
                  value={start}
                  onChange={(e) => setStart(e.target.value)}
                  hint={`In ${zone}, your browser's time zone.`}
                  required
                />
              ) : (
                <Field
                  label="Describe when"
                  placeholder="e.g. Tuesday or Wednesday afternoon"
                  maxLength={300}
                  value={preference}
                  onChange={(e) => setPreference(e.target.value)}
                  hint="The agent reads this, checks your calendar and books the first free slot in working hours."
                  required
                />
              )}

              <div className="space-y-1.5">
                <label htmlFor="length" className="block text-sm font-medium text-slate-800">
                  Length
                </label>
                <select
                  id="length"
                  value={duration}
                  onChange={(e) => setDuration(Number(e.target.value))}
                  className={cn(inputClass, 'border-slate-300')}
                >
                  {LENGTHS.map((m) => (
                    <option key={m} value={m}>
                      {m} minutes
                    </option>
                  ))}
                </select>
              </div>

              <Textarea
                label="Note for the email (optional)"
                rows={2}
                maxLength={500}
                placeholder="e.g. The interview includes a short live coding exercise."
                value={note}
                onChange={(e) => setNote(e.target.value)}
              />

              {existing?.last_outcome === 'conflict' && (
                <label className="flex items-start gap-2 text-sm text-slate-700">
                  <input
                    type="checkbox"
                    checked={allowConflict}
                    onChange={(e) => setAllowConflict(e.target.checked)}
                    className="mt-0.5 accent-brand-600"
                  />
                  Schedule it even if my calendar is busy at that time
                </label>
              )}

              <p className="text-xs leading-relaxed text-slate-500">
                {mode.kind === 'schedule' || retry
                  ? `${name} gets one email with the time, their secure IntelliHire link and a calendar file. The AI writes the email; it never sees their name or address, which are filled in afterwards.`
                  : `${name} gets an email with the new time and a new secure link; the old link stops working.`}
              </p>
            </>
          )}

          {error && <Alert tone="error">{error}</Alert>}

          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" onClick={onClose}>
              Back
            </Button>
            <Button type="submit" disabled={pending || !canSend}>
              {mode.kind === 'cancel'
                ? retry
                  ? 'Dismiss'
                  : 'Cancel interview'
                : mode.kind === 'schedule' || retry
                  ? 'Book and send invitation'
                  : 'Move and tell them'}
            </Button>
          </div>
        </form>
      </div>
    </div>
  )
}
