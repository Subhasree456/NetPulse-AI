import { useEffect, useState } from 'react'
import {
  Activity,
  Check,
  Copy,
  KeyRound,
  Laptop,
  Plus,
  RefreshCw,
  Server,
  Trash2,
} from 'lucide-react'
import {
  Alert,
  Badge,
  Button,
  ConfirmModal,
  EmptyState,
  Field,
  InfoNote,
  Modal,
  SectionHeader,
  cn,
} from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { useApi } from '../hooks/useApi'
import { formatDateTime, formatNumber, relativeTime } from '../lib/utils'
import { api, getErrorMessage } from '../services/api'

function TokenModal({ open, onClose, registration }) {
  const { toast } = useToast()
  const [copied, setCopied] = useState(false)
  const token = registration?.token || ''
  const agent = registration?.agent || {}

  useEffect(() => {
    if (open) setCopied(false)
  }, [open])

  async function copy() {
    try {
      await navigator.clipboard.writeText(token)
      setCopied(true)
      toast('Token copied to clipboard', 'success')
    } catch {
      toast('Clipboard is unavailable. Select and copy the token manually.', 'warning')
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Agent token" subtitle="Shown once — store it in the agent's secret manager" icon={KeyRound} size="lg">
      <Alert tone="warning" title="This token cannot be recovered">
        {registration?.warning || 'Only a hash is stored. If the token is lost, rotate it to issue a replacement.'}
      </Alert>
      <div className="mt-4">
        <p className="label">Agent</p>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] font-semibold text-app">{agent.name}</span>
          <Badge tone="info">{agent.kind}</Badge>
          <Badge tone="neutral">{agent.token_prefix}…</Badge>
        </div>
      </div>
      <div className="mt-3">
        <p className="label">Bearer token</p>
        <div className="flex items-start gap-2">
          <code className="terminal min-w-0 flex-1 break-all p-3 text-[10px] leading-relaxed text-signal">{token}</code>
          <Button size="sm" icon={copied ? Check : Copy} onClick={copy}>{copied ? 'Copied' : 'Copy'}</Button>
        </div>
      </div>
      <div className="mt-4">
        <p className="label">Run the agent on the monitored host</p>
        <pre className="terminal overflow-x-auto p-3 text-[9px] leading-relaxed text-muted">{`set NETPULSE_API_BASE=https://your-netpulse-host/api
set NETPULSE_AGENT_TOKEN=${token || '<token>'}
python -m app.agent_client`}</pre>
        <p className="mt-1.5 text-[9px] leading-relaxed text-subtle">
          On Linux or macOS use <span className="code-chip">export</span> instead of <span className="code-chip">set</span>. The agent sends real psutil telemetry and a heartbeat on the configured interval.
        </p>
      </div>
      <div className="mt-4 flex justify-end border-t border-app pt-3">
        <Button variant="primary" onClick={onClose}>I have stored the token</Button>
      </div>
    </Modal>
  )
}

function CreateAgentModal({ open, onClose, onCreated }) {
  const { toast } = useToast()
  const [form, setForm] = useState({ name: '', hostname: '', ip_address: '', os_name: '', notes: '' })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (open) {
      setForm({ name: '', hostname: '', ip_address: '', os_name: '', notes: '' })
      setError('')
    }
  }, [open])

  async function submit(event) {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      const response = await api.post('/agents', {
        name: form.name.trim(),
        hostname: form.hostname.trim() || null,
        ip_address: form.ip_address.trim() || null,
        os_name: form.os_name.trim() || null,
        notes: form.notes.trim() || null,
      })
      toast('Agent registered', 'success', form.name)
      onCreated(response.data)
      onClose()
    } catch (err) {
      setError(getErrorMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal open={open} onClose={onClose} title="Register remote agent" subtitle="Create a credential for a host outside this server" icon={Plus} size="md">
      {error && <div className="mb-3"><Alert tone="danger">{error}</Alert></div>}
      <form onSubmit={submit} className="space-y-3.5">
        <Field label="Agent name" required hint="Unique label shown in dashboards.">
          <input className="input" required minLength={2} maxLength={120} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="Branch router collector" />
        </Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Hostname">
            <input className="input" maxLength={255} value={form.hostname} onChange={(event) => setForm({ ...form, hostname: event.target.value })} placeholder="edge-01" />
          </Field>
          <Field label="IP address">
            <input className="input" maxLength={64} value={form.ip_address} onChange={(event) => setForm({ ...form, ip_address: event.target.value })} placeholder="10.0.0.14" />
          </Field>
        </div>
        <Field label="Operating system">
          <input className="input" maxLength={120} value={form.os_name} onChange={(event) => setForm({ ...form, os_name: event.target.value })} placeholder="Ubuntu 24.04" />
        </Field>
        <Field label="Notes">
          <textarea className="input min-h-[68px] resize-y" maxLength={2000} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} placeholder="Owner, site, maintenance window…" />
        </Field>
        <InfoNote>The agent is created disabled for telemetry until its first authenticated heartbeat. Registration does not open any inbound port.</InfoNote>
        <div className="flex justify-end gap-2 border-t border-app pt-3">
          <Button onClick={onClose} disabled={saving}>Cancel</Button>
          <Button type="submit" variant="primary" loading={saving}>Register agent</Button>
        </div>
      </form>
    </Modal>
  )
}

function AgentRow({ agent, admin, busy, onRotate, onToggle, onDelete }) {
  const latest = agent.latest || {}
  const tone = agent.status === 'online' ? 'success' : agent.status === 'disabled' ? 'neutral' : agent.status === 'never' ? 'info' : 'danger'
  return (
    <tr>
      <td>
        <div className="flex items-center gap-2">
          <span className={cn('flex h-7 w-7 shrink-0 items-center justify-center rounded-md border', agent.kind === 'local' ? 'border-accent/25 bg-accent/[0.08] text-accent' : 'border-plum/25 bg-plum/[0.08] text-plum')}>
            {agent.kind === 'local' ? <Laptop className="h-3.5 w-3.5" /> : <Server className="h-3.5 w-3.5" />}
          </span>
          <div className="min-w-0">
            <p className="truncate text-[10.5px] font-semibold text-app">{agent.name}</p>
            <p className="font-mono text-[8px] uppercase tracking-[0.08em] text-subtle">{agent.kind} · {agent.token_prefix || 'managed'}…</p>
          </div>
        </div>
      </td>
      <td>
        <p className="font-mono text-[9.5px] text-app">{agent.hostname || '—'}</p>
        <p className="font-mono text-[8.5px] text-subtle">{agent.ip_address || 'no address'}</p>
      </td>
      <td><Badge tone={tone}>{agent.status}</Badge></td>
      <td className="whitespace-nowrap font-mono text-[9px]">{agent.last_seen_at ? relativeTime(agent.last_seen_at) : 'never'}</td>
      <td className="whitespace-nowrap font-mono text-[9px]">
        {latest.cpu_pct != null ? `${formatNumber(latest.cpu_pct, 1)}%` : '—'} / {latest.memory_pct != null ? `${formatNumber(latest.memory_pct, 1)}%` : '—'}
      </td>
      <td className="whitespace-nowrap font-mono text-[9px]">{agent.created_at ? formatDateTime(agent.created_at) : '—'}</td>
      <td>
        {admin && agent.kind !== 'local' ? (
          <div className="flex flex-wrap items-center gap-1">
            <Button size="sm" variant="ghost" icon={KeyRound} loading={busy === `rotate-${agent.id}`} onClick={() => onRotate(agent)}>Rotate</Button>
            <Button size="sm" variant="ghost" loading={busy === `toggle-${agent.id}`} onClick={() => onToggle(agent)}>{agent.is_active ? 'Disable' : 'Enable'}</Button>
            <Button size="sm" variant="ghost" className="text-danger" icon={Trash2} loading={busy === `delete-${agent.id}`} onClick={() => onDelete(agent)}>Delete</Button>
          </div>
        ) : (
          <span className="font-mono text-[8px] uppercase tracking-[0.08em] text-subtle">{agent.kind === 'local' ? 'auto-managed' : 'read-only'}</span>
        )}
      </td>
    </tr>
  )
}

export default function AgentManager() {
  const { hasRole } = useAuth()
  const { toast } = useToast()
  const agents = useApi('/agents', { pollMs: 30000 })
  const [createOpen, setCreateOpen] = useState(false)
  const [registration, setRegistration] = useState(null)
  const [busy, setBusy] = useState('')
  const [pendingDelete, setPendingDelete] = useState(null)
  const admin = hasRole('admin')

  const items = agents.data?.agents || []
  const summary = agents.data?.summary || {}

  async function rotate(agent) {
    setBusy(`rotate-${agent.id}`)
    try {
      const response = await api.post(`/agents/${agent.id}/rotate-token`)
      setRegistration(response.data)
      agents.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Token rotation failed')
    } finally {
      setBusy('')
    }
  }

  async function toggle(agent) {
    setBusy(`toggle-${agent.id}`)
    try {
      await api.patch(`/agents/${agent.id}`, { is_active: !agent.is_active })
      toast(agent.is_active ? 'Agent disabled' : 'Agent enabled', 'success', agent.name)
      agents.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Could not update agent')
    } finally {
      setBusy('')
    }
  }

  async function remove() {
    if (!pendingDelete) return
    setBusy(`delete-${pendingDelete.id}`)
    try {
      await api.delete(`/agents/${pendingDelete.id}`)
      toast('Agent deleted', 'success', pendingDelete.name)
      setPendingDelete(null)
      agents.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Could not delete agent')
    } finally {
      setBusy('')
    }
  }

  return (
    <>
      <section className="panel overflow-hidden">
        <div className="flex flex-col justify-between gap-3 border-b border-app p-4 sm:flex-row sm:items-center">
          <SectionHeader
            icon={Activity}
            title="Monitoring agents"
            subtitle={`${summary.online ?? 0} online · ${summary.offline ?? 0} offline · ${summary.never_reported ?? 0} never reported`}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" icon={RefreshCw} loading={agents.loading} onClick={agents.reload}>Refresh</Button>
            {admin && <Button size="sm" variant="primary" icon={Plus} onClick={() => setCreateOpen(true)}>Register agent</Button>}
          </div>
        </div>

        {!admin && <div className="border-b border-app px-4 py-2.5"><InfoNote>Only administrators can register, rotate or disable an agent. Read-only agent health is visible to every signed-in role.</InfoNote></div>}

        <div className="table-wrap">
          <table className="data-table min-w-[860px]">
            <thead><tr><th>Agent</th><th>Host</th><th>Status</th><th>Last seen</th><th>CPU / mem</th><th>Registered</th><th>Actions</th></tr></thead>
            <tbody>
              {items.map((agent) => (
                <AgentRow
                  key={agent.id}
                  agent={agent}
                  admin={admin}
                  busy={busy}
                  onRotate={rotate}
                  onToggle={toggle}
                  onDelete={setPendingDelete}
                />
              ))}
            </tbody>
          </table>
          {!items.length && (
            <EmptyState
              compact
              icon={Server}
              title="No monitoring agents"
              description="The local psutil agent appears automatically once host collection runs. Register a remote agent to monitor another host."
              action={admin ? <Button size="sm" icon={Plus} onClick={() => setCreateOpen(true)}>Register remote agent</Button> : null}
            />
          )}
        </div>

        <div className="border-t border-app px-4 py-2.5">
          <InfoNote>{agents.data?.note || 'Remote agents push telemetry with a per-agent hashed token. Offline means no heartbeat inside the stale window.'}</InfoNote>
        </div>
      </section>

      <CreateAgentModal
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={(data) => {
          setRegistration(data)
          agents.reload()
        }}
      />
      <TokenModal open={Boolean(registration)} onClose={() => setRegistration(null)} registration={registration} />
      <ConfirmModal
        open={Boolean(pendingDelete)}
        onClose={() => setPendingDelete(null)}
        onConfirm={remove}
        title="Delete monitoring agent"
        message={pendingDelete ? `Delete ${pendingDelete.name}? Stored telemetry for this agent is removed and its token stops working immediately. This cannot be undone.` : ''}
        confirmLabel="Delete agent"
        loading={busy === `delete-${pendingDelete?.id}`}
        danger
      />
    </>
  )
}
