// sign-in and sign-up share this frame: the product idea on the left, the form on the right

import { ClipboardCheck, FileText, ScanText, Video } from 'lucide-react'
import type { ReactNode } from 'react'

const STEPS = [
  { icon: ScanText, title: 'Screen', note: 'CVs checked against the role' },
  { icon: Video, title: 'Interview', note: 'Live transcript and co-pilot' },
  { icon: ClipboardCheck, title: 'Assess', note: 'A report you can trace' },
]

export function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: ReactNode
}) {
  return (
    <div className="grid min-h-screen lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <aside className="relative hidden overflow-hidden bg-slate-900 text-slate-200 lg:block">
        {/* texture only: a faint grid fading out, and one soft glow */}
        <div
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 bg-[linear-gradient(to_right,rgb(255_255_255/0.035)_1px,transparent_1px),linear-gradient(to_bottom,rgb(255_255_255/0.035)_1px,transparent_1px)] bg-[size:3rem_3rem] [mask-image:radial-gradient(ellipse_at_top_left,black_20%,transparent_75%)]"
        />
        <div
          aria-hidden="true"
          className="pointer-events-none absolute -bottom-40 -right-32 h-[28rem] w-[28rem] rounded-full bg-brand-500/15 blur-3xl"
        />

        <p className="absolute left-12 top-12 text-xl font-bold tracking-[-0.02em] text-white">
          Intelli<span className="text-marker">Hire</span>
        </p>

        {/* centred like the form, so the two halves line up */}
        <div className="relative flex min-h-screen items-center px-12 py-36">
          <div className="max-w-md">
            <p className="text-[2.25rem] font-semibold leading-[1.15] tracking-[-0.02em] text-white">
              Every hiring call, backed by evidence.
            </p>
            <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-400">
              IntelliHire screens CVs, runs interviews and writes the assessment, and every point
              it makes links to the line in the CV or the moment in the interview it came from.
            </p>
            <EvidenceSample />
          </div>
        </div>

        <ol className="absolute inset-x-12 bottom-12 grid grid-cols-3 gap-6 border-t border-white/10 pt-6">
          {STEPS.map((step) => (
            <li key={step.title} className="flex items-start gap-3">
              <step.icon className="mt-0.5 h-4 w-4 shrink-0 text-brand-200" aria-hidden="true" />
              <span>
                <span className="block text-sm font-medium text-white">{step.title}</span>
                <span className="mt-0.5 block text-xs text-slate-400">{step.note}</span>
              </span>
            </li>
          ))}
        </ol>
      </aside>

      <main className="flex items-center justify-center bg-slate-50 px-4 py-12 sm:px-8">
        <div className="w-full max-w-sm">
          <p className="mb-10 text-xl font-bold tracking-[-0.02em] text-slate-900 lg:hidden">
            Intelli<span className="rounded-sm bg-marker px-0.5">Hire</span>
          </p>
          <h1 className="text-2xl font-semibold tracking-[-0.015em] text-slate-900">{title}</h1>
          <p className="mt-2 text-[0.9375rem] text-slate-600">{subtitle}</p>
          <div className="mt-8">{children}</div>
        </div>
      </main>
    </div>
  )
}

// an illustration of a finding, with made-up text; screen readers skip it
function EvidenceSample() {
  return (
    <div
      aria-hidden="true"
      className="mt-10 rounded-xl border border-white/10 bg-white/[0.04] p-5 shadow-2xl shadow-black/20 backdrop-blur-sm"
    >
      <div className="flex items-center justify-between gap-4">
        <span className="text-sm font-medium text-white">Selenium test automation</span>
        <span className="rounded-full bg-brand-500/20 px-2 py-0.5 text-xs font-medium text-brand-200">
          Met
        </span>
      </div>
      <p className="mt-3 text-sm leading-relaxed text-slate-300">
        &ldquo;…built a{' '}
        <mark className="rounded-sm bg-marker px-1 text-slate-900">
          Selenium WebDriver suite of 400 tests
        </mark>{' '}
        that ran nightly in Jenkins…&rdquo;
      </p>
      <p className="mt-3 flex items-center gap-1.5 text-xs text-slate-400">
        <FileText className="h-3.5 w-3.5" aria-hidden="true" />
        CV, line 14
      </p>
    </div>
  )
}
