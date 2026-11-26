import { useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  Globe2,
  Info,
  Network,
  Router,
  Server,
  ShieldAlert,
  Wifi,
} from 'lucide-react'
import {
  Badge,
  Button,
  EmptyState,
  ErrorPanel,
  InfoNote,
  PageHeader,
  PageSkeleton,
  RefreshStamp,
  SectionHeader,
  StatCard,
  StatusBadge,
  cn,
} from '../components/ui'
import { useApi } from '../hooks/useApi'
import { formatDateTime, relativeTime } from '../lib/utils'

const KIND_META = {
  internet: { label: 'Internet', color: '#38bdf8', icon: Globe2 },
  gateway: { label: 'Gateway', color: '#22d3ee', icon: Router },
  host: { label: 'Monitoring host', color: '#c6ff4a', icon: Server },
  agent: { label: 'Remote agent', color: '#a78bfa', icon: Activity },
  access_point: { label: 'Access point', color: '#ffb020', icon: Wifi },
  device: { label: 'Device', color: '#7dd3fc', icon: Network },
}

const KINDS = Object.keys(KIND_META)

function statusColor(status) {
  if (['online', 'approved'].includes(status)) return '#34e0a1'
  if (['offline', 'unrecognized'].includes(status)) return '#ff5d72'
  if (status === 'disabled') return '#66768c'
  return '#7c8ca3'
}

function buildLayout(nodes) {
  const positions = {}
  const byKind = (kind) => nodes.filter((node) => node.kind === kind)
  const width = 940
  const centerX = width / 2

  positions.internet = { x: centerX, y: 42 }
  positions.gateway = { x: centerX, y: 118 }
  positions.host = { x: centerX, y: 202 }

  const agents = byKind('agent')
  agents.forEach((node, index) => {
    const spread = Math.min(560, Math.max(180, agents.length * 120))
    positions[node.id] = { x: centerX - spread / 2 + (spread / Math.max(1, agents.length - 1 || 1)) * index, y: 112 }
  })

  const accessPoints = byKind('access_point')
  const apColumns = Math.min(7, Math.max(1, accessPoints.length))
  accessPoints.forEach((node, index) => {
    const row = Math.floor(index / apColumns)
    const inRow = Math.min(apColumns, accessPoints.length - row * apColumns)
    const spread = 760
    const step = inRow > 1 ? spread / (inRow - 1) : 0
    const start = centerX - (inRow > 1 ? spread / 2 : 0)
    positions[node.id] = { x: start + step * (index % apColumns), y: 300 + row * 54 }
  })

  const devices = byKind('device')
  const deviceColumns = Math.min(10, Math.max(1, devices.length))
  devices.forEach((node, index) => {
    const row = Math.floor(index / deviceColumns)
    const inRow = Math.min(deviceColumns, devices.length - row * deviceColumns)
    const spread = 840
    const step = inRow > 1 ? spread / (inRow - 1) : 0
    const start = centerX - (inRow > 1 ? spread / 2 : 0)
    positions[node.id] = { x: start + step * (index % deviceColumns), y: 420 + row * 50 - (devices.length > deviceColumns ? 4 : 0) }
  })

  return positions
}

export default function NetworkTopology() {
  const { data, loading, error, reload, refreshing, lastUpdated } = useApi('/topology', { pollMs: 60000 })
  const [selectedId, setSelectedId] = useState(null)
  const [visibleKinds, setVisibleKinds] = useState(() => new Set(KINDS))
  const [showInferred, setShowInferred] = useState(true)

  const allNodes = data?.nodes || []
  const allEdges = data?.edges || []
  const nodes = useMemo(
    () => allNodes.filter((node) => visibleKinds.has(node.kind)).slice(0, 90),
    [allNodes, visibleKinds],
  )
  const positions = useMemo(() => buildLayout(nodes), [nodes])
  const nodeIds = useMemo(() => new Set(nodes.map((node) => node.id)), [nodes])
  const edges = useMemo(
    () => allEdges.filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target) && (showInferred || !edge.inferred)),
    [allEdges, nodeIds, showInferred],
  )
  const selected = allNodes.find((node) => node.id === selectedId) || null
  const selectedEdges = selected
    ? allEdges.filter((edge) => edge.source === selected.id || edge.target === selected.id)
    : []
  const height = Math.max(500, 420 + Math.ceil(nodes.filter((node) => node.kind === 'device').length / 10) * 50 + 60)

  function toggleKind(kind) {
    setVisibleKinds((current) => {
      const next = new Set(current)
      if (next.has(kind)) next.delete(kind)
      else next.add(kind)
      return next
    })
  }

  if (loading && !data) return <PageSkeleton cards={4} />
  if (error && !data) return <ErrorPanel message={error} onRetry={reload} />

  const summary = data?.summary || {}
  const counts = summary.by_kind || {}

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Inferred topology map"
        title="Network topology"
        description="A topology correlated from subnet membership, the operating-system routing table, ARP neighbours, access-point inventory and agent registration. NetPulse does not perform LLDP, SNMP or packet capture."
        actions={<RefreshStamp lastUpdated={lastUpdated} refreshing={refreshing} onRefresh={reload} />}
      >
        <div className="relative mt-5 flex flex-wrap items-center gap-2">
          {KINDS.map((kind) => {
            const meta = KIND_META[kind]
            const active = visibleKinds.has(kind)
            return (
              <button
                key={kind}
                onClick={() => toggleKind(kind)}
                className={cn('inline-flex items-center gap-1.5 rounded border px-2.5 py-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.08em] transition', active ? 'border-app bg-app/40 text-app' : 'border-app text-subtle opacity-50')}
              >
                <span className="h-2 w-2 rounded-full" style={{ background: meta.color }} />
                {meta.label}
                <span className="text-subtle">{counts[kind] ?? 0}</span>
              </button>
            )
          })}
          <button
            onClick={() => setShowInferred((value) => !value)}
            className={cn('ml-auto rounded border px-2.5 py-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.08em] transition', showInferred ? 'border-signal/35 bg-[var(--lime-soft)] text-signal' : 'border-app text-subtle')}
          >
            {showInferred ? 'Inferred edges shown' : 'Confirmed edges only'}
          </button>
        </div>
      </PageHeader>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <StatCard label="Graph nodes" value={summary.nodes ?? 0} hint={`${summary.edges ?? 0} correlated edges`} icon={Network} accent="wine" />
        <StatCard label="Gateway" value={summary.gateway || '—'} hint="Read from the OS routing table" icon={Router} accent="sage" />
        <StatCard label="Internet reachability" value={summary.internet_online ? 'Online' : 'Unknown'} hint="Latest real ICMP probe" icon={Globe2} accent={summary.internet_online ? 'sage' : 'ochre'} />
        <StatCard label="Approved devices" value={summary.approved_devices ?? 0} hint="Observed in the local ARP cache" icon={CheckCircle2} accent="plum" />
        <StatCard label="Unrecognized" value={summary.unrecognized_devices ?? 0} hint="Not yet approved by an operator" icon={ShieldAlert} accent="rust" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.55fr_.75fr]">
        <section className="panel overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-app p-4">
            <SectionHeader icon={Network} title="Correlated graph" subtitle="Click a node to inspect its evidence and adjacent links" />
            <Badge tone="info">inferred</Badge>
          </div>
          <div className="overflow-x-auto p-2">
            {nodes.length ? (
              <svg viewBox={`0 0 940 ${height}`} className="h-auto w-full min-w-[720px]" role="img" aria-label="Inferred network topology graph">
                <defs>
                  <pattern id="topoGrid" width="40" height="40" patternUnits="userSpaceOnUse">
                    <path d="M 40 0 L 0 0 0 40" fill="none" stroke="var(--grid)" strokeWidth="1" />
                  </pattern>
                  <filter id="nodeGlow" x="-50%" y="-50%" width="200%" height="200%">
                    <feGaussianBlur stdDeviation="3" result="blur" />
                    <feMerge><feMergeNode in="blur" /><feMergeNode in="SourceGraphic" /></feMerge>
                  </filter>
                </defs>
                <rect width="940" height={height} fill="url(#topoGrid)" />
                {edges.map((edge, index) => {
                  const from = positions[edge.source]
                  const to = positions[edge.target]
                  if (!from || !to) return null
                  const active = selected && (edge.source === selected.id || edge.target === selected.id)
                  return (
                    <g key={`${edge.source}-${edge.target}-${index}`}>
                      <line
                        x1={from.x}
                        y1={from.y}
                        x2={to.x}
                        y2={to.y}
                        stroke={active ? 'var(--accent)' : edge.inferred ? 'rgba(86,140,198,0.28)' : 'rgba(198,255,74,0.36)'}
                        strokeWidth={active ? 2 : 1}
                        strokeDasharray={edge.inferred ? '4 4' : undefined}
                      />
                      <circle r="2" fill={edge.inferred ? '#38bdf8' : '#c6ff4a'} opacity={active ? 0.95 : 0.35}>
                        <animateMotion dur={`${6 + (index % 5)}s`} repeatCount="indefinite" path={`M ${from.x} ${from.y} L ${to.x} ${to.y}`} />
                      </circle>
                    </g>
                  )
                })}
                {nodes.map((node) => {
                  const point = positions[node.id]
                  if (!point) return null
                  const meta = KIND_META[node.kind] || KIND_META.device
                  const active = selectedId === node.id
                  const radius = node.kind === 'internet' ? 26 : node.kind === 'gateway' ? 22 : node.kind === 'host' ? 24 : node.kind === 'device' ? 11 : 16
                  return (
                    <g key={node.id} transform={`translate(${point.x},${point.y})`} onClick={() => setSelectedId(node.id)} className="cursor-pointer">
                      <circle r={radius + 8} fill={meta.color} opacity={active ? 0.24 : 0.08} />
                      <circle r={radius} fill="var(--panel-solid)" stroke={meta.color} strokeWidth={active ? 3 : 1.6} filter={active ? 'url(#nodeGlow)' : undefined} />
                      <circle r={Math.max(3, radius / 3.2)} fill={statusColor(node.status)} />
                      {(node.kind !== 'device' || nodes.filter((item) => item.kind === 'device').length <= 14) && (
                        <text y={radius + 14} textAnchor="middle" fill="var(--text)" fontSize={node.kind === 'device' ? 9 : 10} fontFamily="Space Mono, monospace">
                          {node.label.length > 30 ? `${node.label.slice(0, 28)}…` : node.label}
                        </text>
                      )}
                    </g>
                  )
                })}
              </svg>
            ) : (
              <EmptyState icon={Network} title="No nodes match the filters" description="Enable a node kind above or wait for discovery to collect observations." />
            )}
          </div>
          <div className="flex flex-wrap items-center gap-4 border-t border-app px-4 py-2.5 font-mono text-[8px] uppercase tracking-[0.08em] text-subtle">
            <span className="flex items-center gap-1.5"><span className="h-px w-6 border-t border-dashed border-accent" /> inferred edge</span>
            <span className="flex items-center gap-1.5"><span className="h-px w-6 bg-signal/60" /> confirmed registration</span>
            <span className="ml-auto">Method · {data?.method}</span>
          </div>
        </section>

        <div className="space-y-4">
          <section className="panel p-4">
            <SectionHeader icon={Info} title="Node inspector" subtitle={selected ? 'Evidence behind the selected node' : 'Select a node to inspect details'} />
            {selected ? (
              <div className="mt-3 space-y-2">
                <div className="flex items-start gap-2">
                  <span className="mt-1 h-2.5 w-2.5 rounded-full" style={{ background: (KIND_META[selected.kind] || KIND_META.device).color }} />
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-[11px] font-semibold text-app">{selected.label}</p>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      <Badge tone="info">{(KIND_META[selected.kind] || KIND_META.device).label}</Badge>
                      <StatusBadge status={selected.status} />
                    </div>
                  </div>
                </div>
                <div className="space-y-1.5 border-t border-app pt-2">
                  {Object.entries(selected.detail || {}).map(([key, value]) => (
                    <div key={key} className="flex items-start justify-between gap-3 text-[9px]">
                      <span className="font-mono uppercase tracking-[0.08em] text-subtle">{key.replaceAll('_', ' ')}</span>
                      <span className="max-w-[65%] break-words text-right font-mono text-app">
                        {value === null || value === undefined ? 'not measured' : typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value)}
                      </span>
                    </div>
                  ))}
                </div>
                <div className="border-t border-app pt-2">
                  <p className="hud-label mb-1.5">Adjacent links</p>
                  {selectedEdges.length ? selectedEdges.map((edge, index) => (
                    <div key={index} className="mb-1.5 rounded border border-app px-2 py-1.5">
                      <p className="font-mono text-[8.5px] text-accent">{edge.source} → {edge.target} · {edge.label}</p>
                      <p className="mt-0.5 text-[8.5px] leading-relaxed text-subtle">{edge.reason}</p>
                    </div>
                  )) : <p className="text-[9px] text-subtle">No correlated links for this node.</p>}
                </div>
              </div>
            ) : (
              <div className="mt-4 rounded-md border border-app bg-[var(--panel-raised)] p-4 text-center">
                <Network className="mx-auto h-6 w-6 text-subtle" />
                <p className="mt-2 text-[10px] text-muted">Select any node on the graph to inspect its IP address, timestamps, approval state and link evidence.</p>
              </div>
            )}
          </section>

          <section className="panel p-4">
            <SectionHeader icon={AlertTriangle} title="Topology honesty" subtitle="What this map is and is not" />
            <ul className="mt-3 space-y-2">
              {(data?.notes || []).map((note) => (
                <li key={note} className="flex items-start gap-2 text-[9.5px] leading-relaxed text-muted">
                  <Info className="mt-0.5 h-3 w-3 shrink-0 text-accent" />{note}
                </li>
              ))}
            </ul>
            <InfoNote className="mt-3">Dashed edges are inferred from address and registration correlation. Solid edges come from a verified agent registration.</InfoNote>
          </section>

          <section className="panel overflow-hidden">
            <div className="border-b border-app p-4">
              <SectionHeader icon={Network} title="Node inventory" subtitle={`${allNodes.length} correlated node(s)`} />
            </div>
            <div className="max-h-[300px] overflow-y-auto">
              {allNodes.map((node) => (
                <button
                  key={node.id}
                  onClick={() => setSelectedId(node.id)}
                  className={cn('flex w-full items-center gap-2 border-b border-app px-4 py-2 text-left transition last:border-0 hover:bg-accent/[0.04]', selectedId === node.id && 'bg-accent/[0.06]')}
                >
                  <span className="h-2 w-2 shrink-0 rounded-full" style={{ background: (KIND_META[node.kind] || KIND_META.device).color }} />
                  <span className="min-w-0 flex-1 truncate text-[9.5px] text-app">{node.label}</span>
                  <span className="font-mono text-[8px] uppercase text-subtle">{(KIND_META[node.kind] || KIND_META.device).label}</span>
                </button>
              ))}
            </div>
          </section>
        </div>
      </div>

      <div className="flex flex-col justify-between gap-2 border-t border-app pt-3 font-mono text-[8px] uppercase tracking-[0.08em] text-subtle sm:flex-row">
        <span>Most links are inferred · no LLDP/SNMP/packet capture</span>
        <span>Generated {data?.generated_at ? formatDateTime(data.generated_at) : '—'}</span>
      </div>
    </div>
  )
}
