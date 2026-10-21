// transcript turns with speaker, time into the interview and clock time. lines arrive
// in the order the speech service answers, so they are always sorted by when they
// were said

import type { TranscriptTurn } from '@/api/types'
import { cn } from '@/lib/cn'

export function elapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = String(total % 60).padStart(2, '0')
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`
}

export function clockTime(iso: string | null, timeZone?: string): string {
  if (!iso) return ''
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  }).format(new Date(iso))
}

export function mergeTurns(...lists: TranscriptTurn[][]): TranscriptTurn[] {
  const bySequence = new Map<number, TranscriptTurn>()
  for (const list of lists) for (const t of list) bySequence.set(t.sequence, t)
  return [...bySequence.values()].sort(
    (a, b) => a.start_offset_ms - b.start_offset_ms || a.sequence - b.sequence,
  )
}

export function TranscriptLines({
  turns,
  candidateName,
  timeZone,
  dark,
}: {
  turns: TranscriptTurn[]
  candidateName: string
  // the interview's zone, so clock times match the session record
  timeZone?: string
  dark?: boolean
}) {
  return (
    <ol className="space-y-3">
      {turns.map((t) => {
        const interviewer = t.speaker_role === 'interviewer'
        const gap = t.stt_provider === 'none'
        return (
          <li key={t.sequence} className="text-sm">
            <p className="flex flex-wrap items-baseline gap-x-2">
              <span
                className={cn(
                  'font-semibold',
                  interviewer
                    ? dark ? 'text-brand-200' : 'text-brand-700'
                    : dark ? 'text-white' : 'text-slate-900',
                )}
              >
                {interviewer ? 'Interviewer' : candidateName || 'Candidate'}
              </span>
              <span className={cn('tabular text-xs', dark ? 'text-slate-400' : 'text-slate-500')}>
                <time dateTime={t.spoken_at ?? undefined} title="Time into the interview">
                  {elapsed(t.start_offset_ms)}
                </time>
                <span aria-hidden="true"> · </span>
                {clockTime(t.spoken_at, timeZone)}
              </span>
              {t.is_uncertain && !gap && (
                <span
                  className={cn('text-xs', dark ? 'text-amber-200' : 'text-amber-800')}
                  title="The speech service wasn't sure of these words"
                >
                  unclear
                </span>
              )}
            </p>
            <p
              className={cn(
                'mt-0.5 leading-relaxed',
                gap
                  ? cn('italic', dark ? 'text-slate-500' : 'text-slate-500')
                  : dark ? 'text-slate-200' : 'text-slate-800',
              )}
            >
              {gap ? 'Not transcribed: the speech service was unavailable.' : t.text}
            </p>
          </li>
        )
      })}
    </ol>
  )
}
