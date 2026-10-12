// renders the small markdown subset the jd agent produces (headings, bullets,
// paragraphs) as react elements, so nothing is ever injected as html

import type { ReactNode } from 'react'

type Block =
  | { kind: 'h1' | 'h2' | 'h3' | 'p'; text: string }
  | { kind: 'ul'; items: string[] }

function parse(source: string): Block[] {
  const blocks: Block[] = []
  let paragraph: string[] = []
  let list: string[] | null = null

  const flush = () => {
    if (paragraph.length) blocks.push({ kind: 'p', text: paragraph.join(' ') })
    if (list) blocks.push({ kind: 'ul', items: list })
    paragraph = []
    list = null
  }

  for (const raw of source.split('\n')) {
    const line = raw.trim()
    const heading = /^(#{1,3})\s+(.*)$/.exec(line)
    const bullet = /^[-*]\s+(.*)$/.exec(line)
    if (!line) {
      flush()
    } else if (heading) {
      flush()
      blocks.push({ kind: `h${heading[1].length}` as 'h1' | 'h2' | 'h3', text: heading[2] })
    } else if (bullet) {
      if (paragraph.length) {
        blocks.push({ kind: 'p', text: paragraph.join(' ') })
        paragraph = []
      }
      list = [...(list ?? []), bullet[1]]
    } else {
      if (list) {
        blocks.push({ kind: 'ul', items: list })
        list = null
      }
      paragraph.push(line)
    }
  }
  flush()
  return blocks
}

// **bold** only, everything else is plain text
function inline(text: string): ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*)/g).map((part, i) =>
    part.startsWith('**') && part.endsWith('**') ? (
      <strong key={i}>{part.slice(2, -2)}</strong>
    ) : (
      part
    ),
  )
}

export function Markdown({ source }: { source: string }) {
  return (
    <div className="space-y-3 text-sm leading-6 text-slate-800">
      {parse(source).map((block, i) => {
        switch (block.kind) {
          case 'h1':
            return (
              <h2 key={i} className="text-xl font-semibold text-slate-900">
                {inline(block.text)}
              </h2>
            )
          case 'h2':
            return (
              <h3 key={i} className="pt-2 text-base font-semibold text-slate-900">
                {inline(block.text)}
              </h3>
            )
          case 'h3':
            return (
              <h4 key={i} className="font-semibold text-slate-900">
                {inline(block.text)}
              </h4>
            )
          case 'ul':
            return (
              <ul key={i} className="list-disc space-y-1 pl-5">
                {block.items.map((item, j) => (
                  <li key={j}>{inline(item)}</li>
                ))}
              </ul>
            )
          default:
            return <p key={i}>{inline(block.text)}</p>
        }
      })}
    </div>
  )
}
