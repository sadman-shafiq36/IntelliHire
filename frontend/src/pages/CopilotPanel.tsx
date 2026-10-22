// the interviewer's co-pilot beside the call: suggested questions on request, and a chat
// for reference answers. only interviewers reach this page; the candidate never sees it

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Check, ChevronDown, Send, Sparkles } from 'lucide-react'
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from 'react'

import { api, ApiError } from '@/api/client'
import type {
  CopilotMessage,
  CopilotSource,
  CopilotSuggestion,
  SessionDetails,
} from '@/api/types'
import { Spinner } from '@/components/ui'
import { isDone, useBackgroundJob } from '@/hooks/useBackgroundJob'
import { cn } from '@/lib/cn'

const SCREENING_LABEL = {
  unmet: 'Not found in the CV',
  partially_met: 'Partly shown in the CV',
  met: 'Shown in the CV',
} as const

function message(e: unknown, fallback: string): string {
  return e instanceof ApiError ? e.message : fallback
}

function canUse(session: SessionDetails): boolean {
  return session.status === 'scheduled' || session.status === 'in_progress'
}

const panelInput =
  'block w-full rounded-md border border-white/15 bg-slate-900/60 px-3 py-2 text-sm text-white placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-brand-500'

// --- suggested questions ---------------------------------------------------------------

export function QuestionsPanel({ session }: { session: SessionDetails }) {
  const queryClient = useQueryClient()
  const key = ['copilot-suggestions', session.id]
  const [focus, setFocus] = useState('')
  const [jobId, setJobId] = useState<string | null>(null)
  const job = useBackgroundJob(jobId)
  const running = jobId !== null && !isDone(job.data)

  const suggestions = useQuery({
    queryKey: key,
    queryFn: () => api.get<CopilotSuggestion[]>(`/api/v1/interviews/${session.id}/copilot/suggestions`),
  })

  const ask = useMutation({
    mutationFn: () =>
      api.post<{ background_job_id: string }>(`/api/v1/interviews/${session.id}/copilot/suggestions`, {
        focus: focus.trim() || null,
      }),
    onSuccess: (data) => setJobId(data.background_job_id),
  })

  useEffect(() => {
    if (isDone(job.data)) void queryClient.invalidateQueries({ queryKey: key })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.data?.state])

  const rows = suggestions.data ?? []
  const latest = rows.length ? rows[0].batch : 0
  const current = rows.filter((s) => s.batch === latest)
  const earlier = rows.filter((s) => s.batch !== latest)

  function submit(e: FormEvent) {
    e.preventDefault()
    if (!running) ask.mutate()
  }

  return (
    <div className="flex h-full flex-col">
      <form onSubmit={submit} className="space-y-2 border-b border-white/10 px-4 py-3">
        <label htmlFor="copilot-focus" className="block text-xs text-slate-300">
          Focus (optional)
        </label>
        <div className="flex gap-2">
          <input
            id="copilot-focus"
            value={focus}
            onChange={(e) => setFocus(e.target.value)}
            maxLength={200}
            placeholder="e.g. system design"
            className={panelInput}
            disabled={!canUse(session)}
          />
          <button
            type="submit"
            disabled={running || ask.isPending || !canUse(session)}
            className="inline-flex shrink-0 items-center gap-1.5 rounded-md bg-brand-500 px-3 py-2 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50"
          >
            <Sparkles aria-hidden="true" className="h-4 w-4" />
            Suggest
          </button>
        </div>
        <p aria-live="polite" className="min-h-[1rem] text-xs text-slate-300">
          {running ? (
            <Spinner label={job.data?.message || 'Starting'} />
          ) : job.data?.state === 'failed' ? (
            <span className="text-red-200">{job.data.message}</span>
          ) : ask.error ? (
            <span className="text-red-200">{message(ask.error, 'The co-pilot could not start.')}</span>
          ) : !canUse(session) ? (
            'The co-pilot works before and during the interview.'
          ) : (
            'Only you can see this panel.'
          )}
        </p>
      </form>

      <div className="flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {suggestions.isLoading && <Spinner />}
        {suggestions.error && (
          <p className="text-sm text-red-200">{message(suggestions.error, 'Suggestions could not be loaded.')}</p>
        )}
        {!suggestions.isLoading && rows.length === 0 && (
          <p className="text-sm text-slate-400">
            Press Suggest when you want ideas. The co-pilot looks at the job's requirements, what screening
            found in the CV, the scores and what has been said so far, and suggests questions for the gaps.
          </p>
        )}
        <ol className="space-y-3">
          {current.map((s) => (
            <SuggestionCard key={s.id} s={s} interviewId={session.id} />
          ))}
        </ol>
        {earlier.length > 0 && (
          <details className="group">
            <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-slate-300 hover:text-white">
              <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 transition group-open:rotate-180" />
              Earlier suggestions ({earlier.length})
            </summary>
            <ol className="mt-3 space-y-3">
              {earlier.map((s) => (
                <SuggestionCard key={s.id} s={s} interviewId={session.id} />
              ))}
            </ol>
          </details>
        )}
      </div>
    </div>
  )
}

function Chip({ children, tone = 'plain' }: { children: React.ReactNode; tone?: 'plain' | 'gap' }) {
  return (
    <span
      className={cn(
        'rounded px-1.5 py-0.5 text-2xs',
        tone === 'gap' ? 'bg-amber-400/15 text-amber-100' : 'bg-white/10 text-slate-200',
      )}
    >
      {children}
    </span>
  )
}

function SuggestionCard({ s, interviewId }: { s: CopilotSuggestion; interviewId: number }) {
  const queryClient = useQueryClient()
  const used = useMutation({
    mutationFn: (was_used: boolean) =>
      api.patch(`/api/v1/interviews/${interviewId}/copilot/suggestions/${s.id}`, { was_used }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['copilot-suggestions', interviewId] }),
  })
  const titles = new Map(s.sources.map((src) => [src.chunk_id, src.title]))
  const b = s.basis

  return (
    <li className={cn('rounded-xl bg-white/5 p-3', s.was_used && 'opacity-60')}>
      {s.requirement && <p className="text-xs text-slate-400">{s.requirement.text}</p>}
      <div className="mt-1 flex flex-wrap gap-1">
        {b.must_have && <Chip>Must-have</Chip>}
        {b.screening && <Chip tone={b.screening === 'met' ? 'plain' : 'gap'}>{SCREENING_LABEL[b.screening]}</Chip>}
        {b.discussed === false && <Chip tone="gap">Not discussed yet</Chip>}
        {b.discussed === true && <Chip>Touched on already</Chip>}
      </div>
      <p className="mt-2 text-sm font-medium leading-snug text-white">{s.question}</p>
      {s.why && <p className="mt-1 text-xs text-slate-300">{s.why}</p>}

      {s.evidence.length > 0 && (
        <ul className="mt-2 space-y-1">
          {s.evidence.map((e, i) => (
            <li key={i} className="text-xs text-slate-300">
              <span className="mr-1 text-slate-400">{e.source === 'cv' ? 'CV:' : 'They said:'}</span>
              <mark className="rounded-sm bg-marker px-0.5 text-slate-900">{e.quote}</mark>
            </li>
          ))}
        </ul>
      )}

      {s.answer_points.length > 0 ? (
        <details className="group mt-2">
          <summary className="flex cursor-pointer list-none items-center gap-1 text-xs font-medium text-brand-200 hover:text-white">
            <ChevronDown aria-hidden="true" className="h-3.5 w-3.5 transition group-open:rotate-180" />
            What a strong answer covers
          </summary>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-slate-200">
            {s.answer_points.map((p, i) => (
              <li key={i}>
                {p.point}{' '}
                <span className="text-slate-400">({p.passage_ids.map((id) => titles.get(id) ?? id).join(', ')})</span>
              </li>
            ))}
          </ul>
        </details>
      ) : (
        <p className="mt-2 text-2xs text-slate-400">No reference material covers this question.</p>
      )}

      <button
        type="button"
        onClick={() => used.mutate(!s.was_used)}
        disabled={used.isPending}
        className={cn(
          'mt-3 inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-medium',
          s.was_used ? 'bg-brand-500/30 text-white' : 'bg-white/10 text-slate-200 hover:bg-white/20',
        )}
      >
        <Check aria-hidden="true" className="h-3.5 w-3.5" />
        {s.was_used ? 'Asked' : 'Mark as asked'}
      </button>
    </li>
  )
}

// --- chat ------------------------------------------------------------------------------

export function AskPanel({ session }: { session: SessionDetails }) {
  const queryClient = useQueryClient()
  const key = ['copilot-chat', session.id]
  const [draft, setDraft] = useState('')
  const [sending, setSending] = useState<string | null>(null)
  const [streamed, setStreamed] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const box = useRef<HTMLDivElement>(null)
  const abort = useRef<AbortController | null>(null)

  const history = useQuery({
    queryKey: key,
    queryFn: () => api.get<CopilotMessage[]>(`/api/v1/interviews/${session.id}/copilot/chat`),
  })

  useEffect(() => () => abort.current?.abort(), [])

  const count = (history.data?.length ?? 0) + (sending ? 1 : 0)
  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight
  }, [count, streamed])

  async function send(text: string) {
    setSending(text)
    setStreamed('')
    setError(null)
    setNote(null)
    setDraft('')
    abort.current = new AbortController()
    try {
      await api.stream(
        `/api/v1/interviews/${session.id}/copilot/chat`,
        { message: text },
        ({ event, data }) => {
          const d = data as { text?: string; message?: string }
          if (event === 'delta') setStreamed((s) => s + (d.text ?? ''))
          else if (event === 'note') setNote(d.message ?? null)
          else if (event === 'error') setError(d.message ?? 'The co-pilot could not answer.')
        },
        abort.current.signal,
      )
    } catch (e) {
      if (!(e instanceof DOMException && e.name === 'AbortError')) {
        setError(message(e, 'The co-pilot could not answer.'))
        // the question wasn't sent, so give it back
        if (e instanceof ApiError && e.status !== 0) setDraft(text)
      }
    } finally {
      // the saved reply is the checked one, which replaces what was streamed
      await queryClient.invalidateQueries({ queryKey: key })
      setSending(null)
      setStreamed('')
    }
  }

  function submit(e?: FormEvent) {
    e?.preventDefault()
    const text = draft.trim()
    if (text && !sending) void send(text)
  }

  function onKey(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      submit()
    }
  }

  const messages = history.data ?? []
  return (
    <div className="flex h-full flex-col">
      <div ref={box} className="flex-1 space-y-3 overflow-y-auto px-4 py-3" aria-live="polite">
        {messages.length === 0 && !sending && (
          <p className="text-sm text-slate-400">
            Ask for a reference answer ("What should a good answer about indexing cover?"), a follow-up
            question, or what the candidate said about something. Answers cite your reference material,
            the transcript and the CV, and nothing else.
          </p>
        )}
        {messages.map((m) => (
          <ChatBubble key={m.id} m={m} />
        ))}
        {sending && (
          <>
            <ChatBubble m={{ id: -1, role: 'user', content: sending, status: 'done', sources: [], created_at: '' }} />
            <div className="rounded-xl bg-white/5 px-3 py-2 text-sm text-slate-100">
              {streamed ? <p className="whitespace-pre-line">{streamed}</p> : <Spinner label="Thinking" />}
            </div>
          </>
        )}
        {note && <p className="text-xs text-amber-200">{note}</p>}
        {error && (
          <p role="alert" className="text-xs text-red-200">
            {error}
          </p>
        )}
      </div>
      <form onSubmit={submit} className="flex items-end gap-2 border-t border-white/10 px-4 py-3">
        <label htmlFor="copilot-ask" className="sr-only">
          Ask the co-pilot
        </label>
        <textarea
          id="copilot-ask"
          rows={2}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={onKey}
          maxLength={1000}
          placeholder={canUse(session) ? 'Ask the co-pilot' : 'The co-pilot works before and during the interview.'}
          disabled={!canUse(session)}
          className={cn(panelInput, 'resize-none')}
        />
        <button
          type="submit"
          aria-label="Send"
          disabled={!draft.trim() || sending !== null || !canUse(session)}
          className="rounded-md bg-brand-500 p-2.5 text-white hover:bg-brand-600 disabled:opacity-50"
        >
          <Send aria-hidden="true" className="h-4 w-4" />
        </button>
      </form>
    </div>
  )
}

function ChatBubble({ m }: { m: CopilotMessage }) {
  if (m.role === 'user')
    return (
      <div className="ml-8 rounded-xl bg-brand-500/25 px-3 py-2 text-sm text-white">
        <p className="whitespace-pre-line">{m.content}</p>
      </div>
    )
  return (
    <div className={cn('mr-4 rounded-xl bg-white/5 px-3 py-2 text-sm', m.status === 'failed' ? 'text-red-200' : 'text-slate-100')}>
      <p className="whitespace-pre-line">{m.content}</p>
      {m.sources.length > 0 && (
        <ul className="mt-2 space-y-1 border-t border-white/10 pt-2">
          {m.sources.map((s, i) => (
            <SourceLine key={i} s={s} />
          ))}
        </ul>
      )}
    </div>
  )
}

function SourceLine({ s }: { s: CopilotSource }) {
  const label = <span className="mr-1 font-mono text-2xs text-brand-200">[{s.label}]</span>
  if (s.kind === 'passage')
    return (
      <li className="text-xs text-slate-300">
        <details>
          <summary className="cursor-pointer list-none hover:text-white">
            {label}
            {s.title}
          </summary>
          <p className="mt-1 whitespace-pre-line text-2xs text-slate-400">{s.excerpt}</p>
        </details>
      </li>
    )
  if (s.kind === 'turn')
    return (
      <li className="text-xs text-slate-300">
        {label}
        {s.speaker_role === 'candidate' ? 'Candidate' : 'You'}:{' '}
        <mark className="rounded-sm bg-marker px-0.5 text-slate-900">{s.text}</mark>
      </li>
    )
  return (
    <li className="text-xs text-slate-300">
      {label}
      <mark className="rounded-sm bg-marker px-0.5 text-slate-900">{s.quote}</mark>
    </li>
  )
}
