// account-level settings: the connected accounts and the health of the services

import { NavLink, Outlet } from 'react-router-dom'

import { PageHeader } from '@/components/ui'
import { cn } from '@/lib/cn'

const SECTIONS = [
  { to: '/settings/integrations', label: 'Integrations' },
  { to: '/settings/system', label: 'System status' },
]

export function SettingsPage() {
  return (
    <div>
      <PageHeader
        title="Settings"
        description="The accounts IntelliHire uses on your behalf, and whether its services are running."
      />
      <div className="grid grid-cols-1 gap-8 md:grid-cols-[12.5rem_minmax(0,1fr)]">
        <nav aria-label="Settings sections">
          <ul className="flex gap-1 md:flex-col">
            {SECTIONS.map((section) => (
              <li key={section.to}>
                <NavLink
                  to={section.to}
                  className={({ isActive }) =>
                    cn(
                      'block rounded-md px-3 py-2 text-sm transition-colors',
                      'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
                      isActive
                        ? 'bg-white font-medium text-slate-900 ring-1 ring-slate-200'
                        : 'text-slate-600 hover:bg-white hover:text-slate-900',
                    )
                  }
                >
                  {section.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        <div className="min-w-0">
          <Outlet />
        </div>
      </div>
    </div>
  )
}
