// everything except sign-in / sign-up goes through ProtectedRoute

import { Navigate, Route, Routes } from 'react-router-dom'

import { ProtectedRoute } from '@/auth/ProtectedRoute'
import { Layout } from '@/components/Layout'
import { CandidatePortalPage } from '@/pages/CandidatePortalPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { SignInPage } from '@/pages/SignInPage'
import { SignUpPage } from '@/pages/SignUpPage'
import { SystemStatusPage } from '@/pages/SystemStatusPage'

export function App() {
  return (
    <Routes>
      {/* public */}
      <Route path="/sign-in" element={<SignInPage />} />
      <Route path="/sign-up" element={<SignUpPage />} />

      {/* signed in */}
      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route
          index
          element={
            <ProtectedRoute allow={['interviewer']}>
              <DashboardPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="system"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <SystemStatusPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="portal"
          element={
            <ProtectedRoute allow={['candidate']}>
              <CandidatePortalPage />
            </ProtectedRoute>
          }
        />
        <Route path="404" element={<NotFoundPage />} />
      </Route>

      <Route path="*" element={<Navigate to="/404" replace />} />
    </Routes>
  )
}
