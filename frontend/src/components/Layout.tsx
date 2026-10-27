// nav is built from the user's role, candidates never see screening links.
// settings (integrations, system status) live behind the account block, not in the main nav

import { BookOpen, Briefcase, FileStack, LayoutGrid, LogOut, Menu, Settings, X } from 'lucide-react'
import { type ComponentType, useEffect, useState } from 'react'
import { Link, NavLink, Outlet, useLocation } from 'react-router-dom'

import { useAuth } from '@/auth/AuthContext'
import { cn } from '@/lib/cn'

interface NavItem {
  to: string
  label: string
  icon: ComponentType<{ className?: string }>
}

const INTERVIEWER_NAV: NavItem[] = [
  { to: '/', label: 'Overview', icon: LayoutGrid },
  { to: '/jobs', label: 'Jobs', icon: Briefcase },
  { to: '/policies', label: 'Policies', icon: FileStack },
  { to: '/references', label: 'References', icon: BookOpen },
]

const CANDIDATE_NAV: NavItem[] = [{ to: '/portal', label: 'My interviews', icon: LayoutGrid }]

export function Layout() {
  const { user } = useAuth()
  const [open, setOpen] = useState(false)
  const location = useLocation()
  const nav = user?.role === 'candidate' ? CANDIDATE_NAV : INTERVIEWER_NAV

  // close the mobile drawer whenever the page changes
  useEffect(() => setOpen(false), [location.pathname])

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-md focus:bg-white focus:px-3 focus:py-2 focus:text-sm"
      >
        Skip to content
      </a>

      {/* mobile top bar */}
      <div className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-slate-800 bg-slate-900 px-4 lg:hidden">
        <Wordmark />
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="rounded-md p-2 text-slate-200 hover:bg-white/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-marker"
          aria-label="Open menu"
          aria-expanded={open}
        >
          <Menu className="h-5 w-5" aria-hidden="true" />
        </button>
      </div>

      {open && (
        <div
          className="fixed inset-0 z-40 bg-slate-900/50 lg:hidden"
          onClick={() => setOpen(false)}
          aria-hidden="true"
        />
      )}

      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-50 flex w-64 flex-col bg-slate-900 text-slate-200 transition-transform duration-200 lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
        aria-label="Sidebar"
      >
        <div className="flex h-16 items-center justify-between px-5">
          <Wordmark />
          <button
            type="button"
            onClick={() => setOpen(false)}
            className="rounded-md p-2 text-slate-300 hover:bg-white/10 lg:hidden"
            aria-label="Close menu"
          >
            <X className="h-5 w-5" aria-hidden="true" />
          </button>
        </div>

        <nav aria-label="Main" className="mt-4 flex-1 space-y-1 px-3">
          {nav.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.to === '/'}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-3 rounded-md px-3 py-2.5 text-[0.9375rem] transition-colors',
                  'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-marker',
                  isActive
                    ? 'bg-marker font-semibold text-slate-900'
                    : 'text-slate-300 hover:bg-white/5 hover:text-white',
                )
              }
            >
              <item.icon className="h-[1.125rem] w-[1.125rem]" aria-hidden="true" />
              {item.label}
            </NavLink>
          ))}
        </nav>

        <AccountBlock />
      </aside>

      <main id="main" className="lg:pl-64">
        <div className="mx-auto max-w-6xl px-4 py-8 sm:px-8 lg:py-12">
          <Outlet />
        </div>
      </main>
    </div>
  )
}

function Wordmark() {
  return (
    <Link
      to="/"
      className="rounded text-lg font-bold tracking-[-0.02em] text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-marker"
    >
      Intelli<span className="text-marker">Hire</span>
    </Link>
  )
}

function AccountBlock() {
  const { user, signOut } = useAuth()
  if (!user) return null
  const initials = user.full_name
    .split(/\s+/)
    .map((p) => p[0])
    .slice(0, 2)
    .join('')
    .toUpperCase()

  return (
    <div className="border-t border-white/10 p-3">
      <div className="flex items-center gap-3 px-2 py-2">
        <span
          aria-hidden="true"
          className="flex h-9 w-9 items-center justify-center rounded-full bg-white/10 text-sm font-semibold text-white"
        >
          {initials}
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-white">{user.full_name}</p>
          <p className="truncate text-xs capitalize text-slate-400">{user.role}</p>
        </div>
      </div>
      <div className="mt-1 space-y-0.5">
        {user.role === 'interviewer' && (
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              cn(
                'flex items-center gap-3 rounded-md px-3 py-2 text-sm transition-colors',
                'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-marker',
                isActive ? 'bg-white/10 text-white' : 'text-slate-300 hover:bg-white/5 hover:text-white',
              )
            }
          >
            <Settings className="h-4 w-4" aria-hidden="true" />
            Settings
          </NavLink>
        )}
        <button
          type="button"
          onClick={signOut}
          className="flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-white/5 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-marker"
        >
          <LogOut className="h-4 w-4" aria-hidden="true" />
          Sign out
        </button>
      </div>
    </div>
  )
}
