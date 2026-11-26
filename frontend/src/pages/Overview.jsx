import { useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  ArrowDownToLine,
  ArrowUpFromLine,
  Bot,
  CheckCircle2,
  Cpu,
  Gauge,
  HardDrive,
  Info,
  MemoryStick,
  Network,
  Radar,
  RefreshCw,
  Router,
  ShieldAlert,
  ShieldCheck,
  Ticket,
  Timer,
  Wifi,
  WifiOff,
} from 'lucide-react'
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
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
  HealthRing,
  PageHeader,
  PageSkeleton,
  ProgressBar,
  RefreshStamp,
  SectionHeader,
  StatCard,
  StatusBadge,
  cn,
} from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { useLive } from '../context/LiveContext'
import { useToast } from '../context/ToastContext'
import { useApi } from '../hooks/useApi'
import {
  formatBps,
  formatDateTime,
  formatNumber,
  formatPercent,
  formatShortDate,
  formatTime,
  parseApiDate,
  relativeTime,
} from '../lib/utils'
import { api, getErrorMessage } from '../services/api'

const RANGES = [
  { label: '6H', hours: 6 },
  { label: '24H', hours: 24 },
  { label: '72H', hours: 72 },
  { label: '7D', hours: 168 },
]

const DEVICE_COLORS = ['#38bdf8', '#c6ff4a', '#a78bfa', '#ffb020', '#34e0a1', '#ff5d72']

function chartRows(series) {
  return (series || []).map((point) => ({
    ...point,
    label: `${formatShortDate(point.timestamp)} ${formatTime(point.timestamp).slice(0, 5)}`,
    incoming_mbps: point.download_bps != null ? point.download_bps / 1e6 : null,
    outgoing_mbps: point.upload_bps != null ? point.upload_bps / 1e6 : null,
    bytes_mbps: point.bytes_per_sec != null ? (point.bytes_per_sec * 8) / 1e6 : null,
  }))
}

function InsightRow({ tone = 'info', title, body, to, cta }) {
  const palette = {
    info: 'border-accent/25 bg-accent/[0.04] text-accent',
    warning: 'border-warning/25 bg-warning/[0.04] text-warning',
    danger: 'border-danger/25 bg-danger/[0.04] text-danger',
    success: 'border-success/25 bg-success/[0.04] text-success',
  }
  const Icon = tone === 'danger' ? AlertTriangle : tone === 'warning' ? AlertTriangle : tone === 'success' ? CheckCircle2 : Bot
  return (
    <div className={cn('flex items-start gap-2.5 rounded-lg border p-3', palette[tone])}>
      <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <div className="min-w-0 flex-1">
        <p className="text-[10.5px] font-semibold text-app">{title}</p>
        <p className="mt-0.5 text-[9.5px] leading-relaxed text-muted">{body}</p>
        {to && (
          <Link to={to} className="mt-1.5 inline-flex items-center gap-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">
            {cta || 'Open'} <ArrowRight className="h-2.5 w-2.5" />
          </Link>
        )}
      </div>
    </div>
  )
}

export default function Overview() {
  const { hasRole } = useAuth()
  const { toast } = useToast()
  const navigate = useNavigate()
  const live = useLive()
  const [hours, setHours] = useState(24)
  const [running, setRunning] = useState(false)
  const { data, loading, error, reload, refreshing, lastUpdated } = useApi('/dashboard/overview', {
    params: { hours },
    pollMs: 20000,
  })

  async function runLiveCheck() {
    setRunning(true)
    try {
      const response = await api.post('/monitoring/run')
      toast(response.data.message, 'success', 'Live probe complete')
      reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Probe failed')
    } finally {
      setRunning(false)
    }
  }

  const summary = data?.summary || {}
  const latest = data?.latest_measurement
  const host = data?.host?.summary
  const freshness = data?.data_freshness || {}
  const intelligence = data?.intelligence || {}
  const recentIncidents = data?.recent_incidents || []
  const securityTimeline = data?.security?.timeline || []
  const alerts = data?.recent_alerts || []
  const distribution = data?.devices?.distribution || []
  const healthTrend = useMemo(
    () =>
      (data?.health_trend || []).map((point) => ({
        ...point,
        label: `${formatShortDate(point.timestamp)} ${formatTime(point.timestamp).slice(0, 5)}`,
      })),
    [data?.health_trend],
  )
  const trafficRows = useMemo(
    () => chartRows((data?.host?.series || []).length ? data.host.series : data?.traffic?.series || []),
    [data?.host?.series, data?.traffic?.series],
  )

  const stale = freshness.host_stale || freshness.measurement_stale
  const transport = live.transport
  const online = latest?.is_online
  const health = summary.network_health_pct
  const criticalAlerts = alerts.filter((item) => ['Critical', 'High'].includes(item.severity)).slice(0, 4)

  const insights = useMemo(() => {
    const items = []
    if (freshness.host_stale || freshness.measurement_stale) {
      items.push({
        tone: 'warning',
        title: 'Telemetry freshness warning',
        body: 'The newest host or probe sample is older than three collection intervals. Values on this page are historical until collection resumes.',
        to: '/monitoring',
        cta: 'Inspect collector',
      })
    }
    if ((summary.active_incidents || 0) > 0) {
      items.push({
        tone: 'warning',
        title: `${summary.active_incidents} active incident${summary.active_incidents === 1 ? '' : 's'}`,
        body: 'Open and in-progress tickets remain in the queue. Triage is ordered by priority and newest evidence.',
        to: '/incidents',
        cta: 'Open incident queue',
      })
    }
    if ((summary.security_threats || 0) > 0) {
      items.push({
        tone: 'danger',
        title: `${summary.security_threats} open security finding${summary.security_threats === 1 ? '' : 's'}`,
        body: `${summary.critical_threats || 0} critical finding(s) require defensive review. No offensive action is performed.`,
        to: '/security',
        cta: 'Review findings',
      })
    }
    if ((summary.agents_offline || 0) > 0) {
      items.push({
        tone: 'warning',
        title: `${summary.agents_offline} monitoring agent(s) offline`,
        body: 'No heartbeat or telemetry has arrived inside the stale window. Confirm the agent process and token.',
        to: '/settings',
        cta: 'Manage agents',
      })
    }
    if (intelligence.prediction?.highest_risk != null && intelligence.prediction.highest_risk >= 50) {
      items.push({
        tone: 'danger',
        title: `Predicted risk ${formatNumber(intelligence.prediction.highest_risk, 0)}/100`,
        body: `${intelligence.prediction.at_risk} scope(s) at risk. This is a statistical estimate with the confidence shown in AI Intelligence.`,
        to: '/intelligence',
        cta: 'Open AI intelligence',
      })
    }
    if (host && host.cpu_pct != null && host.cpu_pct >= 85) {
      items.push({
        tone: 'warning',
        title: `Host CPU at ${formatPercent(host.cpu_pct)}`,
        body: 'Sustained CPU pressure can increase response times. Review the top processes in Live Monitoring.',
        to: '/monitoring',
        cta: 'Inspect processes',
      })
    }
    if (!items.length) {
      items.push({
        tone: 'success',
        title: 'No threshold breaches in the selected window',
        body: health != null
          ? `Network health is ${formatPercent(health, 1)} across stored live probes. Absence of alerts is not a guarantee of availability.`
          : 'No live measurements are stored yet. Start the scheduler or run a probe to establish a baseline.',
        to: '/monitoring',
        cta: 'Open live telemetry',
      })
    }
    return items.slice(0, 5)
  }, [freshness, summary, intelligence, host, health])

  if (loading && !data) return <PageSkeleton cards={8} />
  if (error && !data) return <ErrorPanel message={error} onRetry={reload} />

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Unified network command"
        title="Operations overview"
        description="Devices, local host telemetry, remote agents, internet probes and defensive intelligence in one operational picture. Every value is measured; missing data stays empty."
        actions={
          <>
            <RefreshStamp lastUpdated={lastUpdated} refreshing={refreshing} onRefresh={reload} />
            {hasRole('analyst') && (
              <Button size="sm" icon={Radar} loading={running} onClick={runLiveCheck}>Run live probe</Button>
            )}
            <Button size="sm" variant="primary" icon={Bot} onClick={() => navigate('/copilot')}>Ask Copilot</Button>
          </>
        }
      >
        <div className="relative mt-5 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div className="glass-inset flex items-center gap-2 px-3 py-2">
            <span className={cn('h-1.5 w-1.5 rounded-full', transport === 'offline' ? 'bg-danger' : transport === 'connecting' ? 'bg-warning' : 'bg-signal animate-pulse')} />
            <span className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle">Feed</span>
            <span className="ml-auto font-mono text-[9px] text-app">{transport}</span>
          </div>
          <div className="glass-inset flex items-center gap-2 px-3 py-2">
            <Timer className="h-3 w-3 text-accent" />
            <span className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle">Last probe</span>
            <span className="ml-auto font-mono text-[9px] text-app">{latest ? relativeTime(latest.recorded_at) : 'never'}</span>
          </div>
          <div className="glass-inset flex items-center gap-2 px-3 py-2">
            <Cpu className="h-3 w-3 text-accent" />
            <span className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle">Host sample</span>
            <span className="ml-auto font-mono text-[9px] text-app">{host?.recorded_at ? relativeTime(host.recorded_at) : 'awaiting'}</span>
          </div>
          <div className={cn('flex items-center gap-2 rounded-md border px-3 py-2', stale ? 'border-warning/30 bg-warning/[0.05]' : 'border-signal/25 bg-[var(--lime-soft)]')}>
            {stale ? <AlertTriangle className="h-3 w-3 text-warning" /> : <CheckCircle2 className="h-3 w-3 text-signal" />}
            <span className="font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle">Data state</span>
            <span className={cn('ml-auto font-mono text-[9px] font-bold', stale ? 'text-warning' : 'text-signal')}>{stale ? 'stale' : 'fresh'}</span>
          </div>
        </div>

        <div className="relative mt-4 flex flex-wrap items-center gap-1.5">
          <span className="font-mono text-[8px] uppercase tracking-[0.14em] text-subtle">Window</span>
          {RANGES.map((range) => (
            <button
              key={range.hours}
              onClick={() => setHours(range.hours)}
              className={cn(
                'rounded border px-2.5 py-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.08em] transition',
                hours === range.hours ? 'border-accent/40 bg-accent/10 text-accent' : 'border-app text-subtle hover:border-accent/25 hover:text-app',
              )}
            >
              {range.label}
            </button>
          ))}
          <span className="ml-auto font-mono text-[8.5px] text-subtle">Generated {data?.generated_at ? formatDateTime(data.generated_at) : '—'}</span>
        </div>
      </PageHeader>

      {latest && !latest.is_online && (
        <Alert tone="danger" title="Latest live probe is offline">
          No ICMP replies from <span className="font-mono">{latest.probe_target || 'the configured target'}</span> at {relativeTime(latest.recorded_at)}. Either that target is unreachable, the path blocks ICMP, or the local collector failed — compare with Live Monitoring before acting.
          <Link to="/monitoring" className="ml-2 inline-flex items-center gap-1 font-semibold underline">Investigate <ArrowRight className="h-3 w-3" /></Link>
        </Alert>
      )}
      {data?.connection?.last_error && (
        <Alert tone="warning" title="Scheduler reported an error">
          {data.connection.last_error}
        </Alert>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Total devices" value={summary.total_devices ?? 0} hint={`${summary.known_devices ?? 0} approved · ${summary.unknown_devices ?? 0} unrecognized`} icon={Router} accent="wine" />
        <StatCard label="Online / offline" value={`${summary.online_devices ?? 0}/${summary.offline_devices ?? 0}`} hint={`Seen within ${data?.devices?.online_window_minutes ?? 15} min`} icon={Wifi} accent="sage" />
        <StatCard label="Network health" value={health != null ? formatNumber(health, 0) : null} unit={health != null ? '/ 100' : ''} hint={summary.uptime_pct != null ? `${formatNumber(summary.uptime_pct, 1)}% probe availability` : 'Awaiting live probes'} icon={Gauge} accent="sage" />
        <StatCard label="Active incidents" value={summary.active_incidents ?? 0} hint={`${data?.ticket_breakdown?.in_progress ?? 0} in progress`} icon={Ticket} accent="ochre" />
        <StatCard label="Security threats" value={summary.security_threats ?? 0} hint={`${summary.critical_threats ?? 0} critical open finding(s)`} icon={ShieldAlert} accent="rust" />
        <StatCard label="Monitoring agents" value={`${summary.agents_online ?? 0}/${summary.agents_total ?? 0}`} hint={`${summary.agents_offline ?? 0} offline · live heartbeats`} icon={Activity} accent="plum" />
        <StatCard label="Inbound traffic" value={summary.incoming_bps != null ? formatBps(summary.incoming_bps) : null} hint={host?.recorded_at ? `Host NIC · ${relativeTime(host.recorded_at)}` : 'Awaiting host sample'} icon={ArrowDownToLine} accent="sage" />
        <StatCard label="Outbound traffic" value={summary.outgoing_bps != null ? formatBps(summary.outgoing_bps) : null} hint="Upload rate from psutil counters" icon={ArrowUpFromLine} accent="wine" />
        <StatCard label="Bandwidth util." value={summary.bandwidth_utilization_pct != null ? formatNumber(summary.bandwidth_utilization_pct, 1) : null} unit="%" hint={data?.traffic?.latest?.link_speed_mbps ? `Link ${data.traffic.latest.link_speed_mbps} Mbps` : 'Measured against link speed'} icon={Network} accent="plum" />
        <StatCard label="Avg latency" value={summary.average_latency_ms != null ? formatNumber(summary.average_latency_ms, 1) : null} unit={summary.average_latency_ms != null ? 'ms' : ''} hint="Mean across all stored live ICMP probes" icon={Timer} accent="ochre" />
        <StatCard label="CPU" value={summary.cpu_pct != null ? formatNumber(summary.cpu_pct, 1) : null} unit={summary.cpu_pct != null ? '%' : ''} hint={host?.process_count != null ? `${host.process_count} processes observed` : 'psutil · local host'} icon={Cpu} accent="rust" />
        <StatCard label="Memory" value={summary.memory_pct != null ? formatNumber(summary.memory_pct, 1) : null} unit={summary.memory_pct != null ? '%' : ''} hint={summary.disk_pct != null ? `Disk ${formatNumber(summary.disk_pct, 1)}%` : 'Local host memory'} icon={MemoryStick} accent="plum" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.55fr_.85fr]">
        <div className="space-y-4">
          <section className="panel overflow-hidden">
            <div className="flex flex-col justify-between gap-3 border-b border-app p-5 sm:flex-row sm:items-start">
              <SectionHeader
                icon={Activity}
                title="Throughput telemetry"
                subtitle="Host interface counters converted to Mbps. Simulated samples are excluded."
              />
              <div className="flex flex-wrap gap-3 font-mono text-[8.5px] uppercase tracking-[0.08em] text-subtle">
                <span className="flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-accent" /> Inbound</span>
                <span className="flex items-center gap-1.5"><span className="h-1.5 w-1.5 rounded-full bg-signal" /> Outbound</span>
              </div>
            </div>
            <div className="p-4">
              {trafficRows.length ? (
                <div className="h-[268px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={trafficRows} margin={{ top: 8, right: 6, left: -12, bottom: 0 }}>
                      <defs>
                        <linearGradient id="inboundFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.32} />
                          <stop offset="100%" stopColor="#38bdf8" stopOpacity={0.01} />
                        </linearGradient>
                        <linearGradient id="outboundFill" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#c6ff4a" stopOpacity={0.22} />
                          <stop offset="100%" stopColor="#c6ff4a" stopOpacity={0.01} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="2 5" stroke="var(--chart-grid)" vertical={false} />
                      <XAxis dataKey="label" tick={{ fontSize: 9 }} minTickGap={30} axisLine={false} tickLine={false} />
                      <YAxis tick={{ fontSize: 9 }} axisLine={false} tickLine={false} width={46} unit="M" />
                      <Tooltip content={<ChartTooltip unit=" Mbps" />} />
                      <Area type="monotone" dataKey="incoming_mbps" name="Inbound" stroke="#38bdf8" strokeWidth={1.8} fill="url(#inboundFill)" connectNulls />
                      <Area type="monotone" dataKey="outgoing_mbps" name="Outbound" stroke="#c6ff4a" strokeWidth={1.6} fill="url(#outboundFill)" connectNulls />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
              ) : (
                <EmptyState
                  compact
                  icon={Network}
                  title="No throughput samples yet"
                  description="Host interface counters are collected by the local psutil agent. Start the collector or open Live Monitoring to collect a real sample."
                  action={<Link to="/monitoring" className="btn btn-secondary btn-sm">Open Live Monitoring</Link>}
                />
              )}
            </div>
            <div className="grid grid-cols-2 gap-px border-t border-app bg-[var(--border)] sm:grid-cols-4">
              {[
                ['Inbound', summary.incoming_bps != null ? formatBps(summary.incoming_bps) : '—'],
                ['Outbound', summary.outgoing_bps != null ? formatBps(summary.outgoing_bps) : '—'],
                ['Utilisation', summary.bandwidth_utilization_pct != null ? formatPercent(summary.bandwidth_utilization_pct, 1) : '—'],
                ['Latest sample', host?.recorded_at ? relativeTime(host.recorded_at) : '—'],
              ].map(([label, value]) => (
                <div key={label} className="bg-[var(--panel-solid)] px-4 py-3">
                  <p className="font-mono text-[8px] uppercase tracking-[0.14em] text-subtle">{label}</p>
                  <p className="mt-1 font-mono text-[13px] font-semibold text-app">{value}</p>
                </div>
              ))}
            </div>
          </section>

          <div className="grid gap-4 lg:grid-cols-2">
            <section className="panel overflow-hidden">
              <div className="border-b border-app p-4">
                <SectionHeader icon={Gauge} title="Health trend" subtitle="Derived from real probe uptime and packet loss" />
              </div>
              <div className="p-4">
                {healthTrend.length ? (
                  <div className="h-[210px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <LineChart data={healthTrend} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="2 5" stroke="var(--chart-grid)" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 8.5 }} minTickGap={40} axisLine={false} tickLine={false} />
                        <YAxis domain={[0, 100]} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} width={38} />
                        <Tooltip content={<ChartTooltip unit="%" />} />
                        <Line type="monotone" dataKey="health_pct" name="Health" stroke="#38bdf8" strokeWidth={2} dot={false} />
                        <Line type="monotone" dataKey="uptime_pct" name="Uptime" stroke="#c6ff4a" strokeWidth={1.4} strokeDasharray="4 3" dot={false} />
                      </LineChart>
                    </ResponsiveContainer>
                  </div>
                ) : <EmptyState compact icon={Gauge} title="No health history" description="Run live probes to build the health trend. No values are fabricated." />}
              </div>
            </section>

            <section className="panel overflow-hidden">
              <div className="border-b border-app p-4">
                <SectionHeader icon={Wifi} title="Availability" subtitle={summary.uptime_pct != null ? `${formatNumber(summary.uptime_pct, 2)}% online across stored probes` : 'Awaiting probe history'} />
              </div>
              <div className="p-4">
                {healthTrend.length ? (
                  <div className="h-[210px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={healthTrend} margin={{ top: 8, right: 8, left: -18, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="2 5" stroke="var(--chart-grid)" vertical={false} />
                        <XAxis dataKey="label" tick={{ fontSize: 8.5 }} minTickGap={40} axisLine={false} tickLine={false} />
                        <YAxis domain={[0, 100]} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} width={38} />
                        <Tooltip content={<ChartTooltip unit="%" />} />
                        <Bar dataKey="uptime_pct" name="Uptime" radius={[2, 2, 0, 0]} fill="#38bdf8" maxBarSize={22} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : <EmptyState compact icon={WifiOff} title="No availability buckets" description="Availability is calculated from stored ICMP probe results only." />}
              </div>
            </section>
          </div>
        </div>

        <div className="space-y-4">
          <section className="panel purple-band relative overflow-hidden p-5">
            <div className="login-grid pointer-events-none absolute inset-0 opacity-35" />
            <div className="relative flex items-start justify-between">
              <div>
                <p className="eyebrow">Live health index</p>
                <h2 className="mt-1 text-[13px] font-semibold text-app">Unified network state</h2>
              </div>
              <StatusBadge status={online === false ? 'offline' : online ? 'online' : '—'}>
                {online === false ? 'Offline' : online ? 'Operational' : 'Awaiting'}
              </StatusBadge>
            </div>
            <div className="relative mt-2 flex justify-center">
              <HealthRing value={health} size={168} />
            </div>
            <div className="relative mt-2 grid grid-cols-2 gap-2">
              <div className="panel-raised p-2.5">
                <p className="text-[8.5px] uppercase tracking-wider text-subtle">Latency</p>
                <p className="mt-0.5 font-mono text-[13px] font-semibold text-app">{latest?.latency_ms != null ? `${formatNumber(latest.latency_ms, 1)} ms` : '—'}</p>
              </div>
              <div className="panel-raised p-2.5">
                <p className="text-[8.5px] uppercase tracking-wider text-subtle">Packet loss</p>
                <p className="mt-0.5 font-mono text-[13px] font-semibold text-app">{latest?.packet_loss_pct != null ? `${formatNumber(latest.packet_loss_pct, 2)}%` : '—'}</p>
              </div>
              <div className="panel-raised p-2.5">
                <p className="text-[8.5px] uppercase tracking-wider text-subtle">Disk</p>
                <p className="mt-0.5 font-mono text-[13px] font-semibold text-app">{summary.disk_pct != null ? formatPercent(summary.disk_pct, 1) : '—'}</p>
              </div>
              <div className="panel-raised p-2.5">
                <p className="text-[8.5px] uppercase tracking-wider text-subtle">Anomalies</p>
                <p className="mt-0.5 font-mono text-[13px] font-semibold text-app">{data?.anomaly_breakdown?.total ?? 0}</p>
              </div>
            </div>
            <div className="relative mt-3 flex items-center justify-between border-t border-app pt-3 text-[9.5px] text-subtle">
              <span className="flex items-center gap-1.5"><Radar className="h-3 w-3 text-accent" /> {latest?.probe_target || 'No target configured'}</span>
              <span>{latest ? relativeTime(latest.recorded_at) : '—'}</span>
            </div>
          </section>

          <section className="panel p-4">
            <SectionHeader icon={Bot} title="AI insights" subtitle="Deterministic observations from stored telemetry" action={<Link to="/copilot" className="btn btn-ghost btn-sm">Ask</Link>} />
            <div className="mt-3 space-y-2">
              {insights.map((insight) => <InsightRow key={insight.title} {...insight} />)}
            </div>
            <p className="mt-3 flex items-start gap-1.5 text-[8.5px] leading-relaxed text-subtle">
              <Info className="mt-0.5 h-3 w-3 shrink-0" /> Statistical observations, not guarantees. Confidence and limitations are shown on the AI Intelligence page.
            </p>
          </section>

          <section className="panel p-4">
            <SectionHeader icon={AlertTriangle} title="Critical alerts" subtitle="Newest high-severity events and unknown devices" />
            <div className="mt-3 space-y-2">
              {criticalAlerts.length ? criticalAlerts.map((alert) => (
                <Link key={`${alert.kind}-${alert.id}`} to={alert.path || '/incidents'} className="block rounded-md border border-app p-2.5 transition hover:border-danger/30 hover:bg-danger/[0.03]">
                  <div className="flex items-center gap-2">
                    <Badge tone={alert.severity === 'Critical' ? 'danger' : alert.severity === 'High' ? 'warning' : 'info'}>{alert.severity}</Badge>
                    <span className="ml-auto font-mono text-[8px] text-subtle">{relativeTime(alert.created_at)}</span>
                  </div>
                  <p className="mt-1.5 text-[10.5px] font-semibold text-app">{alert.title}</p>
                  <p className="mt-0.5 line-clamp-2 text-[9.5px] leading-relaxed text-muted">{alert.message}</p>
                </Link>
              )) : (
                <div className="rounded-md border border-signal/20 bg-[var(--lime-soft)] px-3 py-4 text-center">
                  <ShieldCheck className="mx-auto h-5 w-5 text-signal" />
                  <p className="mt-1.5 text-[10px] font-semibold text-app">No critical alerts</p>
                  <p className="mt-0.5 text-[9px] text-subtle">The current window contains no high-severity ticket, anomaly or unknown device.</p>
                </div>
              )}
            </div>
          </section>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.2fr_1fr_.85fr]">
        <section className="panel overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-app p-4">
            <SectionHeader icon={Ticket} title="Recent incidents" subtitle="Open and in-progress work items" />
            <Link to="/incidents" className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">All incidents</Link>
          </div>
          <div className="table-wrap">
            <table className="data-table min-w-[520px]">
              <thead><tr><th>Ticket</th><th>Priority</th><th>Status</th><th>Opened</th></tr></thead>
              <tbody>
                {recentIncidents.slice(0, 6).map((incident) => (
                  <tr key={incident.id}>
                    <td>
                      <Link to="/incidents" className="block max-w-[250px] truncate font-medium text-app hover:text-accent">{incident.title}</Link>
                      <span className="font-mono text-[8.5px] text-subtle">{incident.ticket_number} · {incident.issue_type}</span>
                    </td>
                    <td><Badge tone={incident.priority === 'Critical' ? 'danger' : incident.priority === 'High' ? 'warning' : 'info'}>{incident.priority}</Badge></td>
                    <td><Badge tone={incident.status === 'Open' ? 'warning' : 'info'}>{incident.status}</Badge></td>
                    <td className="whitespace-nowrap font-mono text-[9px]">{relativeTime(incident.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!recentIncidents.length && <EmptyState compact icon={Ticket} title="No active incidents" description="Tickets are opened by threshold rules or operator action. Nothing is fabricated." />}
          </div>
        </section>

        <section className="panel overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-app p-4">
            <SectionHeader icon={ShieldAlert} title="Security timeline" subtitle="Newest defensive observations" />
            <Link to="/security" className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">Open</Link>
          </div>
          <div className="p-4">
            {securityTimeline.length ? (
              <ol className="relative space-y-3 border-l border-app pl-4">
                {securityTimeline.slice(0, 6).map((event) => (
                  <li key={event.id} className="relative">
                    <span className={cn('absolute -left-[21px] top-1 h-2 w-2 rounded-full ring-4 ring-[var(--panel-solid)]', event.severity === 'Critical' ? 'bg-danger' : event.severity === 'High' ? 'bg-warning' : 'bg-accent')} />
                    <div className="flex items-center gap-2">
                      <Badge tone={event.severity === 'Critical' ? 'danger' : event.severity === 'High' ? 'warning' : 'info'}>{event.severity}</Badge>
                      <span className="ml-auto font-mono text-[8px] text-subtle">{relativeTime(event.detected_at)}</span>
                    </div>
                    <p className="mt-1 text-[10px] font-medium text-app">{event.title}</p>
                    <p className="mt-0.5 line-clamp-2 text-[9px] leading-relaxed text-muted">{event.description}</p>
                  </li>
                ))}
              </ol>
            ) : <EmptyState compact icon={ShieldCheck} title="No security events" description="Run a defensive scan to evaluate locally observable signals." />}
          </div>
        </section>

        <section className="panel overflow-hidden">
          <div className="border-b border-app p-4">
            <SectionHeader icon={HardDrive} title="Device distribution" subtitle="Approved vs unrecognized observations" />
          </div>
          <div className="p-4">
            {distribution.length ? (
              <>
                <div className="h-[150px] w-full">
                  <ResponsiveContainer width="100%" height="100%">
                    <BarChart data={distribution} layout="vertical" margin={{ top: 0, right: 8, left: 4, bottom: 0 }}>
                      <XAxis type="number" hide allowDecimals={false} />
                      <YAxis type="category" dataKey="name" width={88} tick={{ fontSize: 9 }} axisLine={false} tickLine={false} />
                      <Tooltip content={<ChartTooltip />} />
                      <Bar dataKey="value" name="Devices" radius={[0, 3, 3, 0]} maxBarSize={18}>
                        {distribution.map((entry, index) => <Cell key={entry.name} fill={DEVICE_COLORS[index % DEVICE_COLORS.length]} />)}
                      </Bar>
                    </BarChart>
                  </ResponsiveContainer>
                </div>
                <div className="mt-2 space-y-1.5">
                  {distribution.map((entry, index) => (
                    <div key={entry.name} className="flex items-center gap-2 text-[9.5px]">
                      <span className="h-1.5 w-1.5 rounded-full" style={{ background: DEVICE_COLORS[index % DEVICE_COLORS.length] }} />
                      <span className="flex-1 truncate text-muted">{entry.name}</span>
                      <span className="font-mono text-app">{entry.value}</span>
                    </div>
                  ))}
                </div>
              </>
            ) : <EmptyState compact icon={Router} title="No devices discovered" description="The read-only ARP discovery cycle has not stored devices yet." />}
            <div className="mt-3 border-t border-app pt-3">
              <p className="font-mono text-[8px] uppercase tracking-[0.12em] text-subtle">Open issues by state</p>
              <div className="mt-2 space-y-2">
                <div className="flex items-center gap-2"><span className="w-20 text-[9px] text-subtle">Open</span><ProgressBar value={summary.active_incidents ? ((data?.ticket_breakdown?.open || 0) / summary.active_incidents) * 100 : 0} tone="ochre" className="flex-1" /><span className="w-5 text-right font-mono text-[9px] text-app">{data?.ticket_breakdown?.open ?? 0}</span></div>
                <div className="flex items-center gap-2"><span className="w-20 text-[9px] text-subtle">In progress</span><ProgressBar value={summary.active_incidents ? ((data?.ticket_breakdown?.in_progress || 0) / summary.active_incidents) * 100 : 0} tone="sage" className="flex-1" /><span className="w-5 text-right font-mono text-[9px] text-app">{data?.ticket_breakdown?.in_progress ?? 0}</span></div>
              </div>
            </div>
          </div>
        </section>
      </div>

      <div className="flex flex-col justify-between gap-2 border-t border-app pt-3 text-[8.5px] leading-relaxed text-subtle lg:flex-row">
        <span className="max-w-4xl">{data?.disclaimer}</span>
        <span className="shrink-0 font-mono uppercase tracking-[0.08em]">Updated {lastUpdated ? lastUpdated.toLocaleTimeString('en-GB', { hour12: false }) : '—'}</span>
      </div>
    </div>
  )
}
