// chat with the jd agent. each reply that changes the description says which
// version it made, and the latest change can be undone from here

import { useMutation, useQuery } from '@tanstack/react-query'
import { Send, Undo2 } from 'lucide-react'
import { type FormEvent, type KeyboardEvent, useEffect, useRef, useState } from 'react'

import { api, ApiError } from '@/api/client'
import type { ChatMessage, ChatResponse, DescriptionVersion, JobProgress } from '@/api/types'
import { Alert, Button, Spinner } from '@/components/ui'
import { cn } from '@/lib/cn'

const SUGGESTIONS = [
  'Make the summary shorter',
  'Use a more formal tone',
  'Add a short "How to apply" section',
  'Which policy did the benefits come from?',
]

interface Props {
  jobId: number
  hasDescription: boolean
  versions: DescriptionVersion[]
  // any background job on this description, the input waits for it
  running: boolean
  // a chat request in particular, shown as the agent typing
  thinking: boolean
  progress: JobProgress | undefined
  onSent: (backgroundJobId: string) => void
  onUndo: (versionId: number) => void
  undoPending: boolean
}

export function DescriptionChat({
  jobId,
  hasDescription,
  versions,
  running,
  thinking,
  progress,
  onSent,
  onUndo,
  undoPending,
}: Props) {
  const [text, setText] = useState('')
  const listEnd = useRef<HTMLDivElement>(null)

  const chat = useQuery({
    queryKey: ['job-chat', jobId],
    queryFn: () => api.get<ChatMessage[]>(`/api/v1/jobs/${jobId}/description/chat`),
  })

  const send = useMutation({
    mutationFn: (message: string) =>
      api.post<ChatResponse>(`/api/v1/jobs/${jobId}/description/chat`, { message }),
    onSuccess: (data) => {
      setText('')
      void chat.refetch()
      onSent(data.background_job_id)
    },
  })

  useEffect(() => {
    listEnd.current?.scrollIntoView({ block: 'end' })
  }, [chat.data?.length, running])

  const busy = running || send.isPending
  const current = versions[0]

  function submit(event?: FormEvent) {
    event?.preventDefault()
    const message = text.trim()
    if (message.length >= 2 && !busy) send.mutate(message)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    // enter sends, shift+enter is a new line
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submit()
    }
  }

  // undo means going back to the version before the current one
  function undoTarget(message: ChatMessage): DescriptionVersion | undefined {
    if (!current || message.version !== current.version) return undefined
    return versions.find((v) => v.version === current.version - 1)
  }

  const messages = chat.data ?? []

  return (
    <div className="flex h-[35rem] flex-col">
      <div className="flex-1 space-y-3 overflow-y-auto pr-1" aria-live="polite">
        {chat.isLoading && <Spinner />}
        {messages.length === 0 && !chat.isLoading && (
          <p className="text-sm text-slate-600">
            {hasDescription
              ? 'Ask for any change to the description, or ask where something came from.'
              : 'Describe the description you want, or press Generate with AI.'}
          </p>
        )}

        {messages.map((m) => {
          const target = undoTarget(m)
          return (
            <div
              key={m.id}
              className={cn('flex', m.role === 'user' ? 'justify-end' : 'justify-start')}
            >
              <div
                className={cn(
                  'max-w-[85%] rounded-lg px-3 py-2 text-sm',
                  m.role === 'user'
                    ? 'bg-brand-600 text-white'
                    : m.status === 'failed'
                      ? 'border border-red-200 bg-red-50 text-red-800'
                      : 'bg-slate-100 text-slate-800',
                  m.role === 'user' && m.status === 'failed' && 'opacity-60',
                )}
              >
                <p className="whitespace-pre-line">{m.content}</p>
                {m.warnings.length > 0 && (
                  <ul className="mt-2 list-disc space-y-0.5 pl-4 text-xs text-amber-700">
                    {m.warnings.map((w) => (
                      <li key={w}>{w}</li>
                    ))}
                  </ul>
                )}
                {m.version !== null && (
                  <div className="mt-2 flex items-center gap-2 text-xs text-slate-500">
                    <span>Version {m.version}</span>
                    {target && (
                      <button
                        type="button"
                        onClick={() => onUndo(target.id)}
                        disabled={busy || undoPending}
                        className="inline-flex items-center gap-1 font-medium text-brand-700 hover:underline disabled:opacity-50"
                      >
                        <Undo2 aria-hidden="true" className="h-3 w-3" />
                        Undo
                      </button>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}

        {thinking && (
          <div className="flex justify-start">
            <div className="rounded-lg bg-slate-100 px-3 py-2">
              <Spinner label={progress?.message || 'Thinking'} />
            </div>
          </div>
        )}
        <div ref={listEnd} />
      </div>

      {send.error && (
        <div className="mt-2">
          <Alert tone="error">
            {send.error instanceof ApiError ? send.error.message : 'Could not send.'}
          </Alert>
        </div>
      )}

      {messages.length <= 1 && hasDescription && !busy && (
        <div className="mt-3 flex flex-wrap gap-1.5">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              onClick={() => setText(s)}
              className="rounded-full border border-slate-200 px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-50"
            >
              {s}
            </button>
          ))}
        </div>
      )}

      <form onSubmit={submit} className="mt-3 flex items-end gap-2">
        <label htmlFor="jd-chat-input" className="sr-only">
          Message the job description agent
        </label>
        <textarea
          id="jd-chat-input"
          rows={2}
          maxLength={2000}
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          disabled={busy}
          placeholder={busy ? 'The agent is working…' : 'Ask for a change…'}
          className="block w-full resize-none rounded-md border border-slate-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-brand-500 disabled:bg-slate-50"
        />
        <Button type="submit" size="sm" aria-label="Send" disabled={busy || text.trim().length < 2}>
          <Send aria-hidden="true" className="h-4 w-4" />
        </Button>
      </form>
    </div>
  )
}
