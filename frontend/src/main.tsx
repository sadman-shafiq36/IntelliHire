import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import '@fontsource-variable/schibsted-grotesk'

import { ApiError } from '@/api/client'

import { App } from '@/App'
import { AuthProvider } from '@/auth/AuthContext'
import '@/index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // one reader at a time, refetch on focus is just noise
      refetchOnWindowFocus: false,
      // a 4xx (not found, forbidden) won't change on retry, only retry network blips and 5xx
      retry: (failures, error) =>
        failures < 1 && (!(error instanceof ApiError) || error.status === 0 || error.status >= 500),
      staleTime: 30_000,
    },
  },
})

const container = document.getElementById('root')
if (!container) throw new Error('Root element #root is missing from index.html')

createRoot(container).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <App />
        </AuthProvider>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
)
