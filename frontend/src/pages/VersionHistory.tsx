import type { DescriptionVersion } from '@/api/types'
import { Markdown } from '@/components/Markdown'
import { Badge, Button } from '@/components/ui'

const SOURCE_LABEL: Record<DescriptionVersion['source'], string> = {
  generated: 'generated',
  chat: 'chat change',
  manual: 'manual edit',
  restored: 'restored',
}

export function VersionHistory({
  versions,
  onRestore,
  disabled,
}: {
  versions: DescriptionVersion[]
  onRestore: (versionId: number) => void
  disabled: boolean
}) {
  if (versions.length === 0) {
    return <p className="text-sm text-slate-600">No versions yet.</p>
  }

  return (
    <ol className="max-h-[35rem] space-y-2 overflow-y-auto pr-1">
      {versions.map((v, i) => (
        <li key={v.id} className="rounded-md border border-slate-200 p-3">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-900">Version {v.version}</span>
            <Badge>{SOURCE_LABEL[v.source]}</Badge>
            {i === 0 && <Badge tone="success">current</Badge>}
            {i > 0 && (
              <Button
                variant="outline"
                size="sm"
                className="ml-auto h-7 px-2 text-xs"
                disabled={disabled}
                onClick={() => onRestore(v.id)}
              >
                Restore
              </Button>
            )}
          </div>
          <p className="mt-1 text-xs text-slate-500">
            {new Date(v.created_at).toLocaleString()}
            {v.restored_from !== null && ` · copy of version ${v.restored_from}`}
          </p>
          <details className="mt-2">
            <summary className="cursor-pointer text-xs text-brand-700">Show text</summary>
            <div className="mt-2 rounded bg-slate-50 p-2">
              <Markdown source={v.description_md} />
            </div>
          </details>
        </li>
      ))}
    </ol>
  )
}
