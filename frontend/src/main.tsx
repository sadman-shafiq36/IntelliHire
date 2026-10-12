import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'

import { App } from '@/App'
import { AuthProvider } from '@/auth/AuthContext'
import '@/index.css'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // one reader at a time, refetch on focus is just noise
      refetchOnWindowFocus: false,
      // 401/403 won't succeed on retry, only retry network blips
      retry: 1,
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
