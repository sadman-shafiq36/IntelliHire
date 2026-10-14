// sign-in and sign-up share this frame: the product idea on the left, the form on the right

import type { ReactNode } from 'react'

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
      <aside className="relative hidden flex-col justify-between bg-slate-900 p-12 text-slate-200 lg:flex">
        <p className="text-xl font-bold tracking-[-0.02em] text-white">
          Intelli<span className="text-marker">Hire</span>
        </p>

        <div className="max-w-md">
          <p className="text-[2.25rem] font-semibold leading-[1.15] tracking-[-0.02em] text-white">
            From a clear requirement to a pile of CVs, in one place.
          </p>
          <p className="mt-4 text-[0.9375rem] leading-relaxed text-slate-400">
            IntelliHire drafts the job description from your requirements and company policy,
            posts it on LinkedIn and collects the applications and CVs.
          </p>
        </div>

        <p className="text-xs text-slate-500">
          Candidates only see their own interviews.
        </p>
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
