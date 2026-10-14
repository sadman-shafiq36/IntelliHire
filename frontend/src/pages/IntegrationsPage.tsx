// connect the linkedin and google accounts used for posting jobs and collecting cvs

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, Link2, Unplug } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'

import { api, ApiError } from '@/api/client'
import type { IntegrationStatus } from '@/api/types'
import { Alert, Badge, Button, Card, Spinner } from '@/components/ui'

const PURPOSE: Record<IntegrationStatus['provider'], string> = {
  linkedin: 'Publishes each job as a post on the connected member’s LinkedIn feed.',
  google:
    'Copies your template Google Form for each job, reads the responses and downloads the CVs.',
}

export function IntegrationsPage() {
  const queryClient = useQueryClient()
  const [params, setParams] = useSearchParams()
  const connected = params.get('connected')
  const error = params.get('error')

  const status = useQuery({
    queryKey: ['integrations'],
    queryFn: () => api.get<IntegrationStatus[]>('/api/v1/integrations'),
  })

  const connect = useMutation({
    mutationFn: (provider: string) =>
      api.post<{ authorize_url: string }>(`/api/v1/integrations/${provider}/connect`),
    // the provider's consent screen, which sends the browser back here afterwards
    onSuccess: (data) => window.location.assign(data.authorize_url),
  })

  const disconnect = useMutation({
    mutationFn: (provider: string) => api.delete<void>(`/api/v1/integrations/${provider}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['integrations'] }),
  })

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-lg font-semibold text-slate-900">Integrations</h2>
        <p className="mt-1 text-sm text-slate-600">
          Accounts IntelliHire uses to advertise jobs and collect applications. Setup steps are
          in <code className="text-xs">PROJECT/integrations-setup.md</code>.
        </p>
      </div>

      {connected && (
        <Alert tone="success">
          {connected === 'linkedin' ? 'LinkedIn' : 'Google'} is connected.{' '}
          <button className="underline" onClick={() => setParams({})}>
            Dismiss
          </button>
        </Alert>
      )}
      {error && (
        <Alert tone="error">
          {error === 'declined' ? 'The connection was cancelled.' : error}{' '}
          <button className="underline" onClick={() => setParams({})}>
            Dismiss
          </button>
        </Alert>
      )}
      {connect.error && (
        <Alert tone="error">
          {connect.error instanceof ApiError ? connect.error.message : 'Could not connect.'}
        </Alert>
      )}

      {status.isLoading && <Spinner />}
      <div className="grid gap-4 md:grid-cols-2">
        {status.data?.map((item) => (
          <Card key={item.provider}>
            <div className="flex items-start gap-3">
              <div className="flex-1">
                <h2 className="text-lg font-semibold text-slate-900">{item.label}</h2>
                <p className="mt-1 text-sm text-slate-600">{PURPOSE[item.provider]}</p>
              </div>
              {item.connected ? (
                <Badge tone="success">connected</Badge>
              ) : item.expired ? (
                <Badge tone="warning">expired</Badge>
              ) : (
                <Badge>not connected</Badge>
              )}
            </div>

            <div className="mt-4 space-y-2 text-sm">
              {item.connected && item.account_name && (
                <p className="flex items-center gap-2 text-slate-700">
                  <CheckCircle2 aria-hidden="true" className="h-4 w-4 text-green-600" />
                  {item.account_name}
                  {item.expires_at && item.provider === 'linkedin' && (
                    <span className="text-xs text-slate-500">
                      · until {new Date(item.expires_at).toLocaleDateString()}
                    </span>
                  )}
                </p>
              )}
              {!item.configured && (
                <Alert tone="warning">
                  Not set up: add the {item.label} client ID and secret to <code>.env</code> and
                  restart the backend.
                </Alert>
              )}
              {item.provider === 'google' && item.template_configured === false && (
                <Alert tone="warning">
                  No template form yet: set <code>GOOGLE_FORM_TEMPLATE_ID</code> in{' '}
                  <code>.env</code>.
                </Alert>
              )}
            </div>

            <div className="mt-4 flex gap-2">
              <Button
                size="sm"
                variant={item.connected ? 'outline' : 'primary'}
                disabled={!item.configured || connect.isPending}
                onClick={() => connect.mutate(item.provider)}
              >
                <Link2 aria-hidden="true" className="h-4 w-4" />
                {item.connected ? 'Reconnect' : 'Connect'}
              </Button>
              {(item.connected || item.expired) && (
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={disconnect.isPending}
                  onClick={() => {
                    if (window.confirm(`Disconnect ${item.label}?`)) disconnect.mutate(item.provider)
                  }}
                >
                  <Unplug aria-hidden="true" className="h-4 w-4" />
                  Disconnect
                </Button>
              )}
            </div>
          </Card>
        ))}
      </div>
    </div>
  )
}
