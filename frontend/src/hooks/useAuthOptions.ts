// whether this deployment lets people create their own accounts

import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { AuthOptions } from '@/api/types'

export function useAuthOptions() {
  return useQuery({
    queryKey: ['auth-options'],
    queryFn: () => api.get<AuthOptions>('/api/v1/auth/options'),
    staleTime: Infinity,
  })
}
