import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Bot,
  CheckCircle2,
  Database,
  Info,
  Send,
  ShieldCheck,
  Sparkles,
  UserRound,
  Wand2,
} from 'lucide-react'
import {
  Badge,
  Button,
  ErrorPanel,
  PageHeader,
  PageSkeleton,
  SectionHeader,
  cn,
} from '../components/ui'
import { useApi } from '../hooks/useApi'
import { api, getErrorMessage } from '../services/api'
import { formatDateTime } from '../lib/utils'

const STARTERS = [
  'Give me the current operational overview.',
  'Which host is at highest risk and why?',
  'How is network latency trending in the last 24 hours?',
  'What security findings need attention?',
  'Which monitoring agents have not reported recently?',
]

function Message({ message }) {
  const assistant = message.role === 'assistant'
  return (
    <div className={cn('flex gap-3', assistant ? 'justify-start' : 'justify-end')}>
      {assistant && (
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-accent/30 bg-accent/[0.08] text-accent">
          <Bot className="h-4 w-4" />
        </span>
      )}
      <div className={cn('max-w-[86%] rounded-lg border px-4 py-3', assistant ? 'border-app bg-[var(--panel-raised)]' : 'border-[rgb(var(--lime-rgb)/0.25)] bg-[var(--lime-soft)]')}>
        {message.pending ? (
          <div className="flex items-center gap-2 text-[11px] text-muted"><Wand2 className="h-3.5 w-3.5 animate-pulse text-accent" /> Reasoning over stored telemetry…</div>
        ) : (
          <>
            <div className={cn('whitespace-pre-wrap text-[11.5px] leading-relaxed', assistant ? 'text-app' : 'text-app')}>{message.content}</div>
            {assistant && message.result && (
              <div className="mt-3 border-t border-app pt-2.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  <Badge tone="success" dot>Grounded</Badge>
                  <Badge tone="info">confidence {Math.round((message.result.confidence || 0) * 100)}%</Badge>
                  {message.result.llm_configured ? <Badge tone="plum">LLM configured</Badge> : <Badge tone="neutral">deterministic responder</Badge>}
                </div>
                {Array.isArray(message.result.sources) && message.result.sources.length > 0 && (
                  <div className="mt-2 flex flex-wrap items-center gap-1">
                    <span className="font-mono text-[8px] uppercase tracking-[0.1em] text-subtle">Sources</span>
                    {message.result.sources.map((source) => <span key={source} className="code-chip">{source}</span>)}
                  </div>
                )}
                {Array.isArray(message.result.suggestions) && message.result.suggestions.length > 0 && (
                  <div className="mt-2 flex flex-wrap gap-1.5">
                    {message.result.suggestions.map((suggestion) => (
                      <button
                        key={suggestion}
                        onClick={() => message.onSuggest?.(suggestion)}
                        className="rounded border border-app px-2 py-1 text-left text-[8.5px] text-muted transition hover:border-accent/30 hover:text-accent"
                      >
                        {suggestion}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            {assistant && message.result?.data && (
              <details className="mt-2">
                <summary className="cursor-pointer font-mono text-[8px] uppercase tracking-[0.1em] text-subtle hover:text-accent">View structured evidence</summary>
                <pre className="terminal mt-1.5 max-h-56 overflow-auto p-2.5 text-[8.5px] leading-relaxed text-muted">{JSON.stringify(message.result.data, null, 2)}</pre>
              </details>
            )}
          </>
        )}
      </div>
      {!assistant && (
        <span className="mt-0.5 flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-app bg-app text-muted">
          <UserRound className="h-4 w-4" />
        </span>
      )}
    </div>
  )
}

export default function Copilot() {
  const [params] = useSearchParams()
  const suggestions = useApi('/copilot/suggestions')
  const [messages, setMessages] = useState([
    {
      role: 'assistant',
      content: 'I am the NetPulse network copilot. I answer from telemetry stored by this deployment: devices, probes, host metrics, incidents, security findings, predictions and agents. Ask a question or choose a grounded prompt below.\n\nI do not invent measurements, and I do not change device configuration.',
    },
  ])
  const [input, setInput] = useState('')
  const [sending, setSending] = useState(false)
  const endRef = useRef(null)
  const inputRef = useRef(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, sending])

  useEffect(() => {
    const initial = params.get('q')
    if (initial) ask(initial)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function ask(question, options = {}) {
    const text = String(question || '').trim()
    if (!text || sending) return
    setInput('')
    setSending(true)
    const assistantId = `assistant-${Date.now()}`
    setMessages((current) => [
      ...current,
      { role: 'user', content: text },
      { id: assistantId, role: 'assistant', pending: true },
    ])
    try {
      const response = await api.post('/copilot/ask', { question: text })
      setMessages((current) => current.map((message) => (
        message.id === assistantId
          ? { ...message, pending: false, content: response.data.answer, result: response.data, onSuggest: (next) => ask(next) }
          : message
      )))
    } catch (err) {
      setMessages((current) => current.map((message) => (
        message.id === assistantId
          ? { ...message, pending: false, content: `I could not answer that request. ${getErrorMessage(err)}` }
          : message
      )))
    } finally {
      setSending(false)
      if (!options.keepFocus) inputRef.current?.focus()
    }
  }

  const promptList = (suggestions.data?.suggestions?.length ? suggestions.data.suggestions : STARTERS).slice(0, 8)
  const selectedModel = 'Deterministic database responder v1'

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="AI network copilot"
        title="Ask your network"
        description="A database-grounded assistant for operations, host risk, connectivity, incidents and defensive findings. Every answer cites its sources and confidence; no external model is called unless an operator configures one."
        actions={<Badge tone="success" dot>telemetry grounded</Badge>}
      />

      <div className="grid gap-4 xl:grid-cols-[1.45fr_.55fr]">
        <section className="panel flex min-h-[620px] flex-col overflow-hidden">
          <div className="flex items-center gap-3 border-b border-app p-4">
            <span className="relative flex h-9 w-9 items-center justify-center rounded-md border border-accent/30 bg-accent/[0.08] text-accent">
              <Bot className="h-4 w-4" />
              <span className="absolute -right-0.5 -top-0.5 h-2 w-2 rounded-full bg-signal ring-2 ring-[var(--panel-solid)]" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-[12px] font-semibold text-app">NetPulse Copilot</p>
              <p className="font-mono text-[8px] uppercase tracking-[0.1em] text-subtle">{selectedModel} · read-only</p>
            </div>
            <Badge tone="info">{suggestions.data?.suggestions?.length ?? 0} prompts</Badge>
          </div>

          <div className="flex-1 space-y-4 overflow-y-auto p-4" aria-live="polite">
            {messages.map((message, index) => (
              <Message key={message.id || `message-${index}`} message={message} />
            ))}
            <div ref={endRef} />
          </div>

          <div className="border-t border-app p-3">
            <form
              onSubmit={(event) => {
                event.preventDefault()
                ask(input)
              }}
              className="flex items-end gap-2"
            >
              <textarea
                ref={inputRef}
                value={input}
                onChange={(event) => setInput(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === 'Enter' && !event.shiftKey) {
                    event.preventDefault()
                    ask(input)
                  }
                }}
                rows={2}
                maxLength={500}
                placeholder="Ask about latency, host risk, incidents, security findings or agents…"
                className="input min-h-[52px] flex-1 resize-none"
                aria-label="Ask the network copilot"
              />
              <Button type="submit" variant="primary" icon={Send} loading={sending} disabled={!input.trim()}>Send</Button>
            </form>
            <p className="mt-1.5 flex items-center gap-1.5 text-[8px] text-subtle">
              <Info className="h-2.5 w-2.5" /> Answers use stored measurements only. Press Enter to send, Shift+Enter for a new line.
            </p>
          </div>
        </section>

        <div className="space-y-4">
          <section className="panel p-4">
            <SectionHeader icon={Sparkles} title="Grounded prompts" subtitle="Each prompt maps to a deterministic data query" />
            <div className="mt-3 space-y-1.5">
              {promptList.map((prompt) => (
                <button
                  key={prompt}
                  onClick={() => ask(prompt)}
                  disabled={sending}
                  className="w-full rounded-md border border-app px-3 py-2 text-left text-[9.5px] leading-relaxed text-muted transition hover:border-accent/30 hover:bg-accent/[0.03] hover:text-app disabled:opacity-50"
                >
                  {prompt}
                </button>
              ))}
              {suggestions.loading && <div className="skeleton h-28 w-full" />}
              {suggestions.error && <p className="text-[9px] text-danger">{suggestions.error}</p>}
            </div>
          </section>

          <section className="panel p-4">
            <SectionHeader icon={Database} title="How answers are built" subtitle={suggestions.data?.method || 'Deterministic, database-grounded responder'} />
            <ol className="mt-3 space-y-2">
              {[
                ['Intent routing', 'The question is matched to a grounded query family: overview, host, network, security, incidents, agents, root cause or optimisation.'],
                ['Evidence retrieval', 'Only rows stored by this deployment are read: measurements, host metrics, tickets, findings, predictions and registrations.'],
                ['Answer composition', 'The responder writes a plain-language summary, cites source tables and reports confidence plus limitations.'],
              ].map(([title, body], index) => (
                <li key={title} className="flex items-start gap-2">
                  <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-accent/30 bg-accent/[0.06] font-mono text-[8px] text-accent">{index + 1}</span>
                  <span className="text-[9.5px] leading-relaxed text-muted"><span className="font-semibold text-app">{title}.</span> {body}</span>
                </li>
              ))}
            </ol>
            {(suggestions.data?.limitations || []).length > 0 && (
              <div className="mt-3 border-t border-app pt-2">
                <p className="hud-label mb-1">Limitations</p>
                {suggestions.data.limitations.map((limit) => (
                  <p key={limit} className="mt-1 flex items-start gap-1.5 text-[8.5px] leading-relaxed text-subtle"><Info className="mt-0.5 h-2.5 w-2.5 shrink-0" />{limit}</p>
                ))}
              </div>
            )}
          </section>

          <section className="panel p-4">
            <SectionHeader icon={ShieldCheck} title="Safety contract" subtitle="Guardrails enforced by the backend" />
            <ul className="mt-3 space-y-2">
              {[
                'Read-only: the copilot cannot change device, router or firewall configuration.',
                'No hidden ingestion: answers cannot include traffic the deployment did not store.',
                'Analyst review remains mandatory for disruptive remediation.',
                'Rate limiting and RBAC apply to every request.',
              ].map((item) => (
                <li key={item} className="flex items-start gap-2 text-[9px] leading-relaxed text-muted"><CheckCircle2 className="mt-0.5 h-2.5 w-2.5 shrink-0 text-signal" />{item}</li>
              ))}
            </ul>
            <p className="mt-3 font-mono text-[8px] uppercase tracking-[0.08em] text-subtle">Session generated {formatDateTime(new Date())}</p>
          </section>
        </div>
      </div>
    </div>
  )
}
