// everything except sign-in / sign-up and a candidate's interview link goes through
// ProtectedRoute

import { Navigate, Route, Routes } from 'react-router-dom'

import { ProtectedRoute } from '@/auth/ProtectedRoute'
import { Layout } from '@/components/Layout'
import { CandidatePortalPage } from '@/pages/CandidatePortalPage'
import { ApplicationsPage } from '@/pages/ApplicationsPage'
import { DashboardPage } from '@/pages/DashboardPage'
import { IntegrationsPage } from '@/pages/IntegrationsPage'
import { AssessmentPage } from '@/pages/AssessmentPage'
import { InterviewAnalysisPage } from '@/pages/InterviewAnalysisPage'
import { InterviewJoinPage } from '@/pages/InterviewJoinPage'
import { InterviewRoomPage } from '@/pages/InterviewRoomPage'
import { JobDetailPage } from '@/pages/JobDetailPage'
import { JobFormPage } from '@/pages/JobFormPage'
import { JobsPage } from '@/pages/JobsPage'
import { NotFoundPage } from '@/pages/NotFoundPage'
import { PoliciesPage, ReferencesPage } from '@/pages/DocumentLibrary'
import { SettingsPage } from '@/pages/SettingsPage'
import { SignInPage } from '@/pages/SignInPage'
import { SignUpPage } from '@/pages/SignUpPage'
import { SystemStatusPage } from '@/pages/SystemStatusPage'
import { TranscriptPage } from '@/pages/TranscriptPage'

export function App() {
  return (
    <Routes>
      {/* public */}
      <Route path="/sign-in" element={<SignInPage />} />
      <Route path="/sign-up" element={<SignUpPage />} />
      {/* the secure link in the invitation; the token is the only credential */}
      <Route path="/interview/:token" element={<InterviewJoinPage />} />
      {/* the room is full screen, outside the app layout */}
      <Route
        path="/interviews/:interviewId/room"
        element={
          <ProtectedRoute allow={['interviewer']}>
            <InterviewRoomPage />
          </ProtectedRoute>
        }
      />

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
          path="jobs/:jobId/applications"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <ApplicationsPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="interviews/:interviewId/transcript"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <TranscriptPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="interviews/:interviewId/analysis"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <InterviewAnalysisPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="interviews/:interviewId/assessment"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <AssessmentPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="settings"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <SettingsPage />
            </ProtectedRoute>
          }
        >
          <Route index element={<Navigate to="integrations" replace />} />
          <Route path="integrations" element={<IntegrationsPage />} />
          <Route path="system" element={<SystemStatusPage />} />
        </Route>
        <Route
          path="policies"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <PoliciesPage />
            </ProtectedRoute>
          }
        />
        <Route
          path="references"
          element={
            <ProtectedRoute allow={['interviewer']}>
              <ReferencesPage />
            </ProtectedRoute>
          }
        />
        <Route path="system" element={<Navigate to="/settings/system" replace />} />
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
