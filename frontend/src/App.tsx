// everything except sign-in / sign-up goes through ProtectedRoute

import { Navigate, Route, Routes } from 'react-router-dom'

import { ProtectedRoute } from '@/auth/ProtectedRoute'
import { Layout } from '@/components/Layout'
import { CandidatePortalPage } from '@/pages/CandidatePortalPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { JobDetailPage } from '@/pages/JobDetailPage'
import { JobFormPage } from '@/pages/JobFormPage'
import { JobsPage } from '@/pages/JobsPage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { PoliciesPage } from '@/pages/PoliciesPage'
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
          path="jobs"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <JobsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="jobs/new"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <JobFormPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="jobs/:jobId"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <JobDetailPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="jobs/:jobId/edit"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <JobFormPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="policies"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <PoliciesPage />
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
