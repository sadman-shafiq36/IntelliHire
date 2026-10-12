// nav is built from the user's role, candidates never see screening links

import { LogOut } from 'lucide-react'
import { NavLink, Outlet } from 'react-router-dom'

import { useAuth } from '@/auth/AuthContext'
import { Button } from '@/components/ui'
import { cn } from '@/lib/cn'

interface NavItem {
  to: string
  label: string
}

const INTERVIEWER_NAV: NavItem[] = [
  { to: '/', label: 'Dashboard' },
  { to: '/jobs', label: 'Jobs' },
  { to: '/policies', label: 'Policies' },
  { to: '/settings/integrations', label: 'Integrations' },
  { to: '/system', label: 'System status' },
]

const CANDIDATE_NAV: NavItem[] = [{ to: '/portal', label: 'My interviews' }]

export function Layout() {
  const { user, signOut } = useAuth()
  const navItems = user?.role === 'candidate' ? CANDIDATE_NAV : INTERVIEWER_NAV

  return (
    <div className="min-h-screen bg-slate-50">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex h-14 max-w-6xl items-center gap-6 px-4">
          <span className="text-base font-semibold tracking-tight text-slate-900">
            IntelliHire
          </span>

          <nav aria-label="Main" className="flex items-center gap-1">
            {navItems.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.to === '/'}
                className={({ isActive }) =>
                  cn(
                    'rounded-md px-3 py-1.5 text-sm transition-colors',
                    isActive
                      ? 'bg-brand-50 font-medium text-brand-700'
                      : 'text-slate-600 hover:bg-slate-100',
                  )
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>

          {user && (
            <div className="ml-auto flex items-center gap-3">
              <span className="text-sm text-slate-600">
                {user.full_name}
                <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs capitalize text-slate-600">
                  {user.role}
                </span>
              </span>
              <Button variant="ghost" size="sm" onClick={signOut}>
                <LogOut aria-hidden="true" className="h-4 w-4" />
                Sign out
              </Button>
            </div>
          )}
        </div>
      </header>

      <main className="mx-auto max-w-6xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  )
}
