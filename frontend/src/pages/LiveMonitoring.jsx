import { useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  CheckCircle2,
  Clock3,
  Cpu,
  Database,
  Gauge,
  Globe2,
  HardDrive,
  Info,
  MemoryStick,
  Network,
  Play,
  Radar,
  RefreshCw,
  Server,
  Signal,
  Thermometer,
  Timer,
  Wifi,
  WifiOff,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import ChartTooltip from '../components/ChartTooltip'
import {
  Alert,
  Badge,
  Button,
  EmptyState,
  ErrorPanel,
  InfoNote,
  PageHeader,
  PageSkeleton,
  ProgressBar,
  RefreshStamp,
  SectionHeader,
  StatCard,
  StatusBadge,
  cn,
} from '../components/ui'
import { useLive } from '../context/LiveContext'
import { useToast } from '../context/ToastContext'
import { useApi } from '../hooks/useApi'
import {
  formatBps,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatTime,
  formatUptime,
  relativeTime,
} from '../lib/utils'
import { api, getErrorMessage } from '../services/api'

function gaugeTone(value, threshold = 90) {
  if (value == null) return 'var(--neutral)'
  if (value >= threshold) return 'var(--danger)'
  if (value >= threshold * 0.8) return 'var(--warning)'
  return 'var(--accent)'
}

function RadialGauge({ label, value, unit = '%', threshold = 90, caption }) {
  const safe = value == null ? null : Math.max(0, Math.min(100, Number(value)))
  const radius = 38
  const circumference = Math.PI * radius
  const offset = safe == null ? circumference : circumference - (safe / 100) * circumference
  const color = gaugeTone(value, threshold)
  return (
    <div className="flex flex-col items-center rounded-lg border border-app bg-[var(--panel-raised)] p-3">
      <div className="relative h-[86px] w-[110px]">
        <svg viewBox="0 0 110 58" className="h-full w-full">
          <path d="M 14 50 A 38 38 0 0 1 96 50" fill="none" stroke="var(--border-strong)" strokeWidth="8" strokeLinecap="round" opacity="0.5" />
          <path
            d="M 14 50 A 38 38 0 0 1 96 50"
            fill="none"
            stroke={color}
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={offset}
            style={{ transition: 'stroke-dashoffset 700ms ease, stroke 300ms ease' }}
          />
        </svg>
        <div className="absolute inset-x-0 bottom-0 text-center">
          <p className="font-display text-[21px] leading-none text-app">{safe == null ? '—' : `${formatNumber(safe, 0)}${unit === '%' ? '' : ''}`}</p>
          <p className="font-mono text-[8px] uppercase tracking-[0.1em] text-subtle">{value == null ? 'not measured' : unit}</p>
        </div>
      </div>
      <p className="mt-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.12em] text-muted">{label}</p>
      {caption && <p className="mt-0.5 text-center text-[8.5px] text-subtle">{caption}</p>}
    </div>
  )
}

function ProbeCard({ probe, protocol }) {
  if (!probe) {
    return (
      <div className="rounded-lg border border-app bg-[var(--panel-raised)] p-3">
        <div className="flex items-center gap-2">
          <Globe2 className="h-3.5 w-3.5 text-subtle" />
          <span className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-subtle">{protocol}</span>
          <Badge tone="neutral" className="ml-auto">No sample</Badge>
        </div>
        <p className="mt-2 text-[10px] text-muted">No {protocol} check stored yet. Run probes to collect a real observation.</p>
      </div>
    )
  }
  return (
    <div className={cn('rounded-lg border p-3', probe.success ? 'border-success/25 bg-success/[0.03]' : 'border-danger/30 bg-danger/[0.04]')}>
      <div className="flex items-center gap-2">
        {probe.success ? <CheckCircle2 className="h-3.5 w-3.5 text-success" /> : <AlertTriangle className="h-3.5 w-3.5 text-danger" />}
        <span className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-subtle">{protocol}</span>
        <Badge tone={probe.success ? 'success' : 'danger'} className="ml-auto">{probe.success ? 'reachable' : 'failed'}</Badge>
      </div>
      <p className="mt-2 truncate font-mono text-[10.5px] text-app">{probe.target}{probe.port ? `:${probe.port}` : ''}</p>
      <div className="mt-1.5 flex items-center justify-between font-mono text-[8.5px] text-subtle">
        <span>{probe.latency_ms != null ? `${formatNumber(probe.latency_ms, 1)} ms` : 'no latency'}</span>
        <span>{relativeTime(probe.recorded_at)}</span>
      </div>
      {probe.error_message && <p className="mt-1 line-clamp-2 text-[8.5px] leading-relaxed text-danger">{probe.error_message}</p>}
    </div>
  )
}

const PROCESS_LIMIT = 12
const CONNECTION_LIMIT = 10

export default function LiveMonitoring() {
  const { toast } = useToast()
  const live = useLive()
  const [collecting, setCollecting] = useState(false)
  const [probing, setProbing] = useState(false)
  const [tab, setTab] = useState('resources')
  const host = useApi('/host/overview', { params: { hours: 24 }, pollMs: 15000 })
  const network = useApi('/host/network', { params: { hours: 24 }, pollMs: 30000 })
  const processes = useApi('/host/processes', { enabled: tab === 'processes' })

  const data = host.data || {}
  const latest = data.latest
  const summary = data.summary || {}
  const thresholds = data.thresholds || {}
  const agent = data.agent || {}
  const details = latest?.details || {}
  const system = data.system || {}
  const probes = data.network?.probes || {}
  const connectionSummary = details.connections || {}
  const interfaces = details.interfaces || system.interfaces || []
  const disks = details.disks || []
  const topProcesses = (processes.data?.processes || details.top_processes || []).slice(0, PROCESS_LIMIT)
  const distinctPorts = Object.entries(connectionSummary.distinct_ports_by_host || {}).slice(0, CONNECTION_LIMIT)
  const publicEndpoints = (connectionSummary.public_remote_endpoints || []).slice(0, CONNECTION_LIMIT)

  const series = useMemo(
    () =>
      (data.series || []).map((point) => ({
        ...point,
        label: formatTime(point.timestamp || point.recorded_at),
        time: formatTime(point.timestamp || point.recorded_at),
        inbound_mbps: point.download_bps != null ? point.download_bps / 1e6 : null,
        outbound_mbps: point.upload_bps != null ? point.upload_bps / 1e6 : null,
        latency: point.latency_ms,
        loss: point.packet_loss_pct,
      })),
    [data.series],
  )
  const latencySeries = useMemo(
    () =>
      (data.network?.series || []).map((point) => ({
        ...point,
        label: formatTime(point.timestamp),
      })),
    [data.network?.series],
  )

  async function collectNow() {
    setCollecting(true)
    try {
      const response = await api.post('/host/collect')
      toast(response.data.message, 'success', 'Host telemetry collected')
      host.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Collection failed')
    } finally {
      setCollecting(false)
    }
  }

  async function runProbes() {
    setProbing(true)
    try {
      const response = await api.post('/host/probes/run')
      toast(response.data.message, 'success', `${response.data.successful}/${response.data.total} checks reachable`)
      host.reload()
      network.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Network probes failed')
    } finally {
      setProbing(false)
    }
  }

  const empty = data.empty !== false && !latest
  const stale = latest?.recorded_at ? Date.now() - new Date(latest.recorded_at).getTime() > 3 * (data.monitoring?.interval_seconds || 60) * 1000 : false
  const transport = live.transport

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Real-time telemetry grid"
        title="Live monitoring"
        description="Local psutil host telemetry, interface throughput, ICMP latency and DNS/TCP connectivity checks. Every sample is timestamped and tagged with its source; missing readings remain blank."
        actions={
          <>
            <RefreshStamp lastUpdated={host.lastUpdated} refreshing={host.refreshing} onRefresh={() => { host.reload(); network.reload() }} />
            <Button size="sm" icon={Play} loading={collecting} onClick={collectNow}>Collect now</Button>
            <Button size="sm" variant="primary" icon={Radar} loading={probing} onClick={runProbes}>Run probes</Button>
          </>
        }
      >
        <div className="relative mt-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div className="glass-inset flex items-center gap-2 px-3 py-2">
            <span className={cn('h-1.5 w-1.5 rounded-full', transport === 'offline' ? 'bg-danger' : 'bg-signal animate-pulse')} />
            <span className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle">Socket feed</span>
            <span className="ml-auto font-mono text-[9px] text-app">{transport}</span>
          </div>
          <div className="glass-inset flex items-center gap-2 px-3 py-2">
            <Server className="h-3 w-3 text-accent" />
            <span className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle">Source</span>
            <span className="ml-auto truncate font-mono text-[9px] text-app">{latest?.source || 'psutil'}</span>
          </div>
          <div className={cn('flex items-center gap-2 rounded-md border px-3 py-2', stale ? 'border-warning/30 bg-warning/[0.05]' : 'border-app')}>
            <Clock3 className={cn('h-3 w-3', stale ? 'text-warning' : 'text-accent')} />
            <span className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle">Sample age</span>
            <span className={cn('ml-auto font-mono text-[9px]', stale ? 'text-warning' : 'text-app')}>{latest?.recorded_at ? relativeTime(latest.recorded_at) : 'never'}</span>
          </div>
          <div className="glass-inset flex items-center gap-2 px-3 py-2">
            <Database className="h-3 w-3 text-accent" />
            <span className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle">Origin</span>
            <span className="ml-auto font-mono text-[9px] uppercase text-app">{latest?.data_origin || 'live'}</span>
          </div>
        </div>
      </PageHeader>

      {host.error && !host.data && <ErrorPanel message={host.error} onRetry={host.reload} />}
      {host.loading && !host.data && <PageSkeleton cards={4} />}

      {data.message && !stale && (
        <Alert tone="info" title="Collector guidance">{data.message}</Alert>
      )}
      {stale && latest && (
        <Alert tone="warning" title="Telemetry is stale">
          The newest host sample is {relativeTime(latest.recorded_at)}. Values are historical until the scheduler or a manual collection produces a fresh sample.
        </Alert>
      )}

      {empty ? (
        <section className="panel p-2">
          <EmptyState
            icon={Server}
            title="No host telemetry stored yet"
            description="Start the backend scheduler or press Collect now. NetPulse reads real psutil counters and never substitutes synthetic values."
            action={<Button variant="primary" icon={Play} loading={collecting} onClick={collectNow}>Collect the first sample</Button>}
          />
        </section>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-8">
            <StatCard label="CPU" value={summary.cpu_pct != null ? formatNumber(summary.cpu_pct, 1) : null} unit={summary.cpu_pct != null ? '%' : ''} hint={`Threshold ${formatNumber(thresholds.cpu_pct, 0)}%`} icon={Cpu} accent={summary.cpu_pct >= (thresholds.cpu_pct || 90) ? 'rust' : 'sage'} />
            <StatCard label="Memory" value={summary.memory_pct != null ? formatNumber(summary.memory_pct, 1) : null} unit={summary.memory_pct != null ? '%' : ''} hint={`Threshold ${formatNumber(thresholds.memory_pct, 0)}%`} icon={MemoryStick} accent={summary.memory_pct >= (thresholds.memory_pct || 90) ? 'rust' : 'wine'} />
            <StatCard label="Disk" value={summary.disk_pct != null ? formatNumber(summary.disk_pct, 1) : null} unit={summary.disk_pct != null ? '%' : ''} hint={`Threshold ${formatNumber(thresholds.disk_pct, 0)}%`} icon={HardDrive} accent={summary.disk_pct >= (thresholds.disk_pct || 90) ? 'rust' : 'plum'} />
            <StatCard label="Temperature" value={summary.temperature_c != null ? formatNumber(summary.temperature_c, 1) : null} unit={summary.temperature_c != null ? '°C' : ''} hint={summary.temperature_c != null ? 'OS sensor reading' : 'Not exposed by this OS'} icon={Thermometer} accent="ochre" />
            <StatCard label="Inbound" value={summary.download_bps != null ? formatBps(summary.download_bps) : null} hint="Host NIC receive rate" icon={ArrowDownToLine} accent="sage" />
            <StatCard label="Outbound" value={summary.upload_bps != null ? formatBps(summary.upload_bps) : null} hint="Host NIC transmit rate" icon={ArrowUpFromLine} accent="wine" />
            <StatCard label="Uptime" value={summary.uptime_seconds != null ? formatUptime(summary.uptime_seconds) : null} hint={`${summary.process_count ?? '—'} processes observed`} icon={Timer} accent="plum" />
            <StatCard label="Samples" value={summary.samples ?? 0} hint={`Window 24 h · ${agent.kind || 'local'} agent`} icon={Database} accent="ochre" />
          </div>

          <div className="grid gap-4 xl:grid-cols-[.85fr_1.6fr]">
            <section className="panel p-4">
              <SectionHeader icon={Gauge} title="Resource pressure" subtitle={`Latest psutil sample · ${latest?.recorded_at ? formatDateTime(latest.recorded_at) : '—'}`} />
              <div className="mt-3 grid grid-cols-2 gap-2">
                <RadialGauge label="CPU" value={summary.cpu_pct} threshold={thresholds.cpu_pct || 90} caption={summary.average_cpu_pct != null ? `avg ${formatNumber(summary.average_cpu_pct, 1)}%` : 'no average'} />
                <RadialGauge label="Memory" value={summary.memory_pct} threshold={thresholds.memory_pct || 90} caption={summary.average_memory_pct != null ? `avg ${formatNumber(summary.average_memory_pct, 1)}%` : 'no average'} />
                <RadialGauge label="Disk" value={summary.disk_pct} threshold={thresholds.disk_pct || 90} caption={summary.average_disk_pct != null ? `avg ${formatNumber(summary.average_disk_pct, 1)}%` : 'no average'} />
                <RadialGauge label="Bandwidth" value={summary.net_utilization_pct} threshold={90} caption={latest?.details?.link_speed_mbps ? `${latest.details.link_speed_mbps} Mbps link` : 'link speed unknown'} />
              </div>
              <div className="mt-3 space-y-2">
                <div className="flex items-center justify-between text-[9px] text-subtle"><span>Swap</span><span className="font-mono text-app">{summary.swap_pct != null ? formatPercent(summary.swap_pct, 1) : 'not measured'}</span></div>
                <div className="flex items-center justify-between text-[9px] text-subtle"><span>Load 1 / 5 / 15</span><span className="font-mono text-app">{latest?.load_1 != null ? `${formatNumber(latest.load_1, 2)} / ${formatNumber(latest.load_5, 2)} / ${formatNumber(latest.load_15, 2)}` : 'not measured'}</span></div>
                <div className="flex items-center justify-between text-[9px] text-subtle"><span>Interface speed</span><span className="font-mono text-app">{details.link_speed_mbps ? `${details.link_speed_mbps} Mbps` : 'not measured'}</span></div>
                <div className="flex items-center justify-between text-[9px] text-subtle"><span>Platform</span><span className="max-w-[60%] truncate font-mono text-app">{details.platform || system.platform || '—'}</span></div>
              </div>
            </section>

            <section className="panel overflow-hidden">
              <div className="flex flex-col justify-between gap-3 border-b border-app p-4 sm:flex-row sm:items-center">
                <SectionHeader icon={Activity} title="Live resource & throughput" subtitle="CPU, memory and interface rates from stored real samples" />
                <div className="flex flex-wrap gap-1">
                  {[
                    ['resources', 'Resources', Cpu],
                    ['throughput', 'Throughput', Network],
                    ['latency', 'Latency', Signal],
                  ].map(([key, label, Icon]) => (
                    <button
                      key={key}
                      onClick={() => setTab(key)}
                      className={cn(
                        'inline-flex items-center gap-1.5 rounded border px-2.5 py-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.08em] transition',
                        tab === key ? 'border-accent/40 bg-accent/10 text-accent' : 'border-app text-subtle hover:border-accent/25 hover:text-app',
                      )}
                    >
                      <Icon className="h-3 w-3" /> {label}
                    </button>
                  ))}
                </div>
              </div>
              <div className="p-4">
                {tab === 'resources' && (
                  series.length ? (
                    <div className="h-[280px] w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={series} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="2 5" stroke="var(--chart-grid)" vertical={false} />
                          <XAxis dataKey="label" tick={{ fontSize: 9 }} minTickGap={36} axisLine={false} tickLine={false} />
                          <YAxis domain={[0, 100]} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} width={38} unit="%" />
                          <Tooltip content={<ChartTooltip unit="%" />} />
                          <Line type="monotone" dataKey="cpu_pct" name="CPU" stroke="#38bdf8" strokeWidth={1.8} dot={false} />
                          <Line type="monotone" dataKey="memory_pct" name="Memory" stroke="#c6ff4a" strokeWidth={1.6} dot={false} />
                          <Line type="monotone" dataKey="disk_pct" name="Disk" stroke="#a78bfa" strokeWidth={1.3} strokeDasharray="4 3" dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : <EmptyState compact icon={Activity} title="No resource history" description="Collect at least two real samples to draw a trend." />
                )}
                {tab === 'throughput' && (
                  series.length ? (
                    <div className="h-[280px] w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={series} margin={{ top: 8, right: 8, left: -10, bottom: 0 }}>
                          <defs>
                            <linearGradient id="liveInbound" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.3} />
                              <stop offset="100%" stopColor="#38bdf8" stopOpacity={0.01} />
                            </linearGradient>
                            <linearGradient id="liveOutbound" x1="0" y1="0" x2="0" y2="1">
                              <stop offset="0%" stopColor="#c6ff4a" stopOpacity={0.2} />
                              <stop offset="100%" stopColor="#c6ff4a" stopOpacity={0.01} />
                            </linearGradient>
                          </defs>
                          <CartesianGrid strokeDasharray="2 5" stroke="var(--chart-grid)" vertical={false} />
                          <XAxis dataKey="label" tick={{ fontSize: 9 }} minTickGap={36} axisLine={false} tickLine={false} />
                          <YAxis tick={{ fontSize: 9 }} axisLine={false} tickLine={false} width={46} unit="M" />
                          <Tooltip content={<ChartTooltip unit=" Mbps" />} />
                          <Area type="monotone" dataKey="inbound_mbps" name="Inbound" stroke="#38bdf8" strokeWidth={1.8} fill="url(#liveInbound)" connectNulls />
                          <Area type="monotone" dataKey="outbound_mbps" name="Outbound" stroke="#c6ff4a" strokeWidth={1.6} fill="url(#liveOutbound)" connectNulls />
                        </AreaChart>
                      </ResponsiveContainer>
                    </div>
                  ) : <EmptyState compact icon={Network} title="No throughput history" description="Rates need two counters at least 15 seconds apart." />
                )}
                {tab === 'latency' && (
                  latencySeries.length ? (
                    <div className="h-[280px] w-full">
                      <ResponsiveContainer width="100%" height="100%">
                        <LineChart data={latencySeries} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                          <CartesianGrid strokeDasharray="2 5" stroke="var(--chart-grid)" vertical={false} />
                          <XAxis dataKey="label" tick={{ fontSize: 9 }} minTickGap={36} axisLine={false} tickLine={false} />
                          <YAxis tick={{ fontSize: 9 }} axisLine={false} tickLine={false} width={42} unit="ms" />
                          <Tooltip content={<ChartTooltip unit=" ms" decimals={2} />} />
                          <Line type="monotone" dataKey="latency_ms" name="Latency" stroke="#38bdf8" strokeWidth={1.8} dot={false} connectNulls />
                          <Line type="monotone" dataKey="packet_loss_pct" name="Loss" stroke="#ff5d72" strokeWidth={1.3} strokeDasharray="4 3" dot={false} connectNulls />
                        </LineChart>
                      </ResponsiveContainer>
                    </div>
                  ) : <EmptyState compact icon={WifiOff} title="No latency history" description="Run an ICMP probe or wait for the scheduler." />
                )}
              </div>
            </section>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.1fr_.9fr]">
            <section className="panel overflow-hidden">
              <div className="flex items-center justify-between gap-3 border-b border-app p-4">
                <SectionHeader icon={Globe2} title="Connectivity checks" subtitle={`DNS and TCP observations · gateway ${network.data?.gateway || 'unknown'}`} />
                <Button size="sm" icon={RefreshCw} loading={probing} onClick={runProbes}>Run now</Button>
              </div>
              <div className="grid gap-3 p-4 sm:grid-cols-2">
                <ProbeCard protocol="DNS" probe={(probes.dns || [])[0]} />
                <ProbeCard protocol="TCP" probe={(probes.tcp || [])[0]} />
                {(probes.tcp || []).slice(1).map((probe) => <ProbeCard key={`tcp-${probe.id}`} protocol="TCP" probe={probe} />)}
              </div>
              <div className="border-t border-app px-4 py-3">
                <div className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-[8.5px] uppercase tracking-[0.08em] text-subtle">
                  <span>Targets · DNS {network.data?.configured_targets?.dns || '—'}</span>
                  <span>TCP {network.data?.configured_targets?.tcp || '—'}</span>
                  <span>ICMP {network.data?.configured_targets?.icmp || '—'}</span>
                </div>
              </div>
            </section>

            <section className="panel overflow-hidden">
              <div className="border-b border-app p-4">
                <SectionHeader icon={Network} title="Interfaces & disks" subtitle="Operating-system inventory from the latest sample" />
              </div>
              <div className="max-h-[340px] overflow-y-auto">
                <div className="space-y-2 p-4">
                  {interfaces.slice(0, 6).map((iface) => (
                    <div key={iface.name} className="flex items-center gap-3 rounded-md border border-app bg-[var(--panel-raised)] px-3 py-2">
                      {iface.is_up !== false ? <Wifi className="h-3.5 w-3.5 text-success" /> : <WifiOff className="h-3.5 w-3.5 text-danger" />}
                      <div className="min-w-0 flex-1">
                        <p className="truncate font-mono text-[10px] text-app">{iface.name}</p>
                        <p className="truncate font-mono text-[8.5px] text-subtle">{(iface.addresses || []).join(' · ') || 'no address'}</p>
                      </div>
                      <Badge tone={iface.is_up !== false ? 'success' : 'danger'}>{iface.speed_mbps ? `${iface.speed_mbps}M` : iface.is_up !== false ? 'up' : 'down'}</Badge>
                    </div>
                  ))}
                  {!interfaces.length && <p className="py-4 text-center text-[10px] text-subtle">No interface inventory available.</p>}
                </div>
                {disks.length > 0 && (
                  <div className="border-t border-app p-4">
                    <p className="hud-label mb-2">Disk volumes</p>
                    <div className="space-y-2.5">
                      {disks.slice(0, 5).map((disk) => (
                        <div key={disk.mount}>
                          <div className="flex items-center justify-between text-[9px]">
                            <span className="truncate font-mono text-app">{disk.mount} <span className="text-subtle">({disk.filesystem})</span></span>
                            <span className="font-mono text-muted">{formatNumber(disk.used_gb, 1)} / {formatNumber(disk.total_gb, 1)} GB</span>
                          </div>
                          <ProgressBar value={disk.used_pct} tone={disk.used_pct >= (thresholds.disk_pct || 90) ? 'rust' : disk.used_pct >= 70 ? 'ochre' : 'sage'} className="mt-1" />
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </section>
          </div>

          <div className="grid gap-4 xl:grid-cols-[1.1fr_.9fr]">
            <section className="panel overflow-hidden">
              <div className="flex items-center justify-between gap-3 border-b border-app p-4">
                <SectionHeader icon={Cpu} title="Top processes" subtitle="Read-only psutil snapshot ordered by memory" />
                <Button size="sm" icon={RefreshCw} loading={processes.loading} onClick={processes.reload}>Refresh</Button>
              </div>
              <div className="table-wrap">
                <table className="data-table min-w-[440px]">
                  <thead><tr><th>PID</th><th>Process</th><th>CPU</th><th>Memory</th></tr></thead>
                  <tbody>
                    {topProcesses.map((process) => (
                      <tr key={`${process.pid}-${process.name}`}>
                        <td className="font-mono text-[9px]">{process.pid}</td>
                        <td className="max-w-[240px] truncate font-mono text-[10px] text-app">{process.name}</td>
                        <td className="font-mono text-[9px]">{process.cpu_pct != null ? formatPercent(process.cpu_pct, 1) : '—'}</td>
                        <td className="font-mono text-[9px]">{process.memory_pct != null ? formatPercent(process.memory_pct, 2) : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {!topProcesses.length && <EmptyState compact icon={Cpu} title="No process snapshot" description={processes.error || 'The operating system did not expose process details.'} />}
              </div>
            </section>

            <section className="panel overflow-hidden">
              <div className="border-b border-app p-4">
                <SectionHeader icon={Signal} title="Socket observations" subtitle="This host's connection table only — not packet inspection" />
              </div>
              <div className="p-4">
                <div className="grid grid-cols-3 gap-2">
                  {[
                    ['Total', connectionSummary.total],
                    ['Established', connectionSummary.established],
                    ['Listening', connectionSummary.listen],
                  ].map(([label, value]) => (
                    <div key={label} className="rounded-md border border-app bg-[var(--panel-raised)] p-2.5 text-center">
                      <p className="font-mono text-[8px] uppercase tracking-[0.1em] text-subtle">{label}</p>
                      <p className="mt-1 font-display text-[18px] text-app">{value ?? '—'}</p>
                    </div>
                  ))}
                </div>
                <div className="mt-3">
                  <p className="hud-label mb-1.5">Distinct destination ports</p>
                  {distinctPorts.length ? distinctPorts.map(([host, count]) => (
                    <div key={host} className="flex items-center justify-between rounded border border-app px-2.5 py-1.5 text-[9px]">
                      <span className="font-mono text-muted">{host}</span>
                      <Badge tone={count >= 12 ? 'warning' : 'neutral'}>{count} ports</Badge>
                    </div>
                  )) : <p className="text-[9.5px] text-subtle">No public remote endpoints observed.</p>}
                </div>
                <InfoNote className="mt-3">{connectionSummary.collection_status === 'ok' ? 'Socket counts describe this host only. Heuristics are documented in Cybersecurity.' : `Socket collection ${connectionSummary.collection_status}.`}</InfoNote>
              </div>
            </section>
          </div>
        </>
      )}

      <div className="flex flex-col justify-between gap-2 border-t border-app pt-3 text-[8.5px] leading-relaxed text-subtle lg:flex-row">
        <span className="max-w-4xl">{data.disclaimer || 'Host metrics are real psutil measurements from this machine.'}</span>
        <span className="shrink-0 font-mono uppercase tracking-[0.08em]">Collected {latest?.recorded_at ? formatDateTime(latest.recorded_at) : '—'}</span>
      </div>
    </div>
  )
}
