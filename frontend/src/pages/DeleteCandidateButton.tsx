// removes a candidate and everything held about them, after a typed confirmation

import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Trash2 } from 'lucide-react'

import { api, ApiError } from '@/api/client'
import { Alert, Button } from '@/components/ui'

export function DeleteCandidateButton({
  candidateId,
  name,
  onDeleted,
}: {
  candidateId: number
  name: string
  onDeleted: () => void
}) {
  const queryClient = useQueryClient()
  const remove = useMutation({
    mutationFn: () => api.delete<void>(`/api/v1/candidates/${candidateId}`),
    onSuccess: () => {
      for (const key of ['screening', 'scoring', 'applications', 'job-cvs', 'overview', 'jobs'])
        void queryClient.invalidateQueries({ queryKey: [key] })
      onDeleted()
    },
  })

  return (
    <div className="space-y-2">
      <Button
        variant="ghost"
        size="sm"
        className="text-red-700 hover:bg-red-50 hover:text-red-800"
        disabled={remove.isPending}
        onClick={() => {
          if (
            window.confirm(
              `Delete ${name} and all their data? Their CVs, screening, scores and the AI's working copies are removed for every job. This cannot be undone.`,
            )
          )
            remove.mutate()
        }}
      >
        <Trash2 aria-hidden="true" className="h-4 w-4" />
        {remove.isPending ? 'Deleting' : 'Delete candidate data'}
      </Button>
      {remove.error && (
        <Alert tone="error">
          {remove.error instanceof ApiError ? remove.error.message : 'The candidate was not deleted.'}
        </Alert>
      )}
    </div>
  )
}
