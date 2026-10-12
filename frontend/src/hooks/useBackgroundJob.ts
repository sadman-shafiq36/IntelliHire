// polls a background job until it finishes or fails

import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { JobProgress } from '@/api/types'

export function useBackgroundJob(jobId: string | null) {
  return useQuery({
    queryKey: ['background-job', jobId],
    queryFn: () => api.get<JobProgress>(`/api/v1/background-jobs/${jobId}`),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const state = query.state.data?.state
      return state === 'finished' || state === 'failed' ? false : 1000
    },
  })
}

export function isDone(progress: JobProgress | undefined): boolean {
  return progress?.state === 'finished' || progress?.state === 'failed'
}
