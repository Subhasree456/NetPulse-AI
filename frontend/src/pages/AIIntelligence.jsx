import { useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BrainCircuit,
  Cpu,
  GitBranch,
  Gauge,
  Info,
  Lightbulb,
  Play,
  ShieldAlert,
  Sparkles,
  TrendingUp,
  Wifi,
} from 'lucide-react'
import { Link, useNavigate } from 'react-router-dom'
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
import { useAuth } from '../context/AuthContext'
import { useToast } from '../context/ToastContext'
import { useApi } from '../hooks/useApi'
import { formatDateTime, formatNumber, formatPercent, relativeTime } from '../lib/utils'
import { api, getErrorMessage } from '../services/api'

function scoreTone(score) {
  if (score == null) return 'neutral'
  if (score >= 70) return 'danger'
  if (score >= 40) return 'warning'
  return 'success'
}

function RiskBar({ value }) {
  return <ProgressBar value={value || 0} tone={value >= 70 ? 'rust' : value >= 40 ? 'ochre' : 'sage'} />
}

function HostRiskCard({ agent, onAsk }) {
  const metrics = agent.metrics || {}
  const actions = agent.recommended_actions || []
  return (
    <article className="panel p-4">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <p className="truncate text-[12px] font-semibold text-app">{agent.agent_name}</p>
            <Badge tone={agent.kind === 'local' ? 'info' : 'plum'}>{agent.kind}</Badge>
            <StatusBadge status={agent.status} />
          </div>
          <p className="mt-1 text-[9.5px] text-subtle">{agent.hostname || 'hostname not reported'} · {agent.samples} live sample(s) · confidence {formatNumber((agent.confidence || 0) * 100, 0)}%</p>
        </div>
        <div className="text-right">
          <p className={cn('font-display text-[26px] leading-none', agent.risk_score == null ? 'text-subtle' : agent.severity === 'Critical' ? 'text-danger' : agent.severity === 'High' ? 'text-warning' : 'text-signal')}>
            {agent.risk_score == null ? '—' : formatNumber(agent.risk_score, 0)}
          </p>
          <p className="font-mono text-[8px] uppercase tracking-[0.1em] text-subtle">{agent.prediction_status === 'estimated' ? 'risk / 100' : 'insufficient data'}</p>
        </div>
      </div>
      <div className="mt-2.5"><RiskBar value={agent.risk_score} /></div>
      <p className="mt-2 text-[10px] leading-relaxed text-muted">{agent.message}</p>
      <div className="mt-2 grid grid-cols-4 gap-1.5">
        {[
          ['CPU', metrics.cpu_pct, '%'],
          ['Memory', metrics.memory_pct, '%'],
          ['Disk', metrics.disk_pct, '%'],
          ['Latency', metrics.latency_ms, 'ms'],
        ].map(([label, value, unit]) => (
          <div key={label} className="rounded border border-app bg-[var(--panel-raised)] px-2 py-1.5">
            <p className="font-mono text-[7.5px] uppercase tracking-[0.1em] text-subtle">{label}</p>
            <p className="mt-0.5 font-mono text-[10px] text-app">{value != null ? `${formatNumber(value, 1)}${unit}` : '—'}</p>
          </div>
        ))}
      </div>
      {agent.predicted_issue && (
        <div className="mt-2 flex items-start gap-2 rounded border border-warning/20 bg-warning/[0.04] px-2.5 py-2">
          <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-warning" />
          <p className="text-[9.5px] leading-relaxed text-muted">{agent.predicted_issue}</p>
        </div>
      )}
      {actions.length > 0 && (
        <ul className="mt-2 space-y-1.5">
          {actions.slice(0, 3).map((action) => (
            <li key={action.title || action} className="flex items-start gap-2 text-[9.5px] leading-relaxed">
              {action.title ? (
                <>
                  <Badge tone={action.priority === 'High' ? 'warning' : 'neutral'} className="mt-0.5 shrink-0">{action.priority}</Badge>
                  <span className="text-muted"><span className="font-semibold text-app">{action.title}.</span> {action.detail}</span>
                </>
              ) : (
                <span className="text-muted">{action}</span>
              )}
            </li>
          ))}
        </ul>
      )}
      <div className="mt-2 flex items-center gap-3">
        <button onClick={() => onAsk(`Explain the risk for ${agent.agent_name}`)} className="inline-flex items-center gap-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">
          Ask Copilot <ArrowRight className="h-2.5 w-2.5" />
        </button>
        <span className="ml-auto font-mono text-[8px] text-subtle">{metrics.recorded_at ? relativeTime(metrics.recorded_at) : 'never'}</span>
      </div>
    </article>
  )
}

export default function AIIntelligence() {
  const { hasRole } = useAuth()
  const { toast } = useToast()
  const navigate = useNavigate()
  const [busy, setBusy] = useState('')
  const host = useApi('/intelligence/host', { pollMs: 60000 })
  const predictions = useApi('/intelligence/predictions', { pollMs: 90000 })
  const rca = useApi('/intelligence/root-cause', { pollMs: 90000 })
  const anomalies = useApi('/anomalies', { params: { limit: 40 }, pollMs: 60000 })

  const analysis = host.data || {}
  const summary = analysis.summary || {}
  const agents = analysis.agents || []
  const networkRisk = analysis.network || {}
  const predictionData = predictions.data || {}
  const predictionSummary = predictionData.summary || {}
  const investigations = rca.data?.investigations || []
  const anomalyList = anomalies.data?.anomalies || []
  const analyst = hasRole('analyst')

  async function run(action) {
    setBusy(action)
    try {
      if (action === 'predictions') {
        const response = await api.post('/intelligence/predictions/run')
        toast(response.data.message, response.data.status === 'insufficient_data' ? 'warning' : 'success', 'Predictive model')
        predictions.reload()
      } else if (action === 'rca') {
        const response = await api.post('/intelligence/root-cause/run', {})
        toast(response.data.message || 'Investigation completed', response.data.status === 'ok' ? 'success' : 'warning', 'Root-cause engine')
        rca.reload()
      } else {
        const response = await api.post('/anomalies/detect')
        toast(response.data.message, response.data.anomalies_found ? 'warning' : 'success', 'Anomaly detection')
        anomalies.reload()
        host.reload()
      }
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Analysis failed')
    } finally {
      setBusy('')
    }
  }

  function askCopilot(question) {
    navigate(`/copilot?q=${encodeURIComponent(question)}`)
  }

  if (host.loading && !host.data) return <PageSkeleton cards={6} />
  if (host.error && !host.data) return <ErrorPanel message={host.error} onRetry={host.reload} />

  const scored = agents.filter((agent) => agent.risk_score != null)
  const atRisk = scored.filter((agent) => agent.risk_score >= 50)

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Predictive intelligence engine"
        title="AI intelligence"
        description="Host risk, anomalies, failure predictions and root-cause hypotheses computed from stored live telemetry. Every estimate states its sample count, confidence and limitations."
        actions={
          <>
            <RefreshStamp lastUpdated={host.lastUpdated} refreshing={host.refreshing} onRefresh={() => { host.reload(); predictions.reload(); rca.reload(); anomalies.reload() }} />
            {analyst && <Button size="sm" icon={BrainCircuit} loading={busy === 'anomalies'} onClick={() => run('anomalies')}>Detect anomalies</Button>}
            {analyst && <Button size="sm" icon={TrendingUp} loading={busy === 'predictions'} onClick={() => run('predictions')}>Run risk model</Button>}
            {analyst && <Button size="sm" variant="primary" icon={GitBranch} loading={busy === 'rca'} onClick={() => run('rca')}>Investigate root cause</Button>}
          </>
        }
      />

      {analysis.status === 'insufficient_data' && (
        <Alert tone="warning" title="Not enough live telemetry for a stable estimate">
          {analysis.message} The engine needs at least {analysis.minimum_samples || 8} real observations per host before it produces a risk score.
          <Link to="/monitoring" className="ml-2 inline-flex items-center gap-1 font-semibold underline">Open Live Monitoring <ArrowRight className="h-3 w-3" /></Link>
        </Alert>
      )}

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatCard label="Hosts assessed" value={`${summary.scored ?? 0}/${summary.agents ?? 0}`} hint={`${summary.online ?? 0} online · ${summary.offline ?? 0} offline`} icon={Cpu} accent="wine" />
        <StatCard label="At risk" value={summary.at_risk ?? 0} hint="Risk score ≥ 50 / 100" icon={AlertTriangle} accent={summary.at_risk ? 'rust' : 'sage'} />
        <StatCard label="Highest host risk" value={summary.highest_risk != null ? formatNumber(summary.highest_risk, 0) : null} unit={summary.highest_risk != null ? '/ 100' : ''} hint={summary.highest_agent || 'No stable estimate yet'} icon={Gauge} accent="ochre" />
        <StatCard label="Network risk" value={networkRisk.risk_score != null ? formatNumber(networkRisk.risk_score, 0) : null} unit={networkRisk.risk_score != null ? '/ 100' : ''} hint={networkRisk.samples ? `${networkRisk.samples} probes · ${formatPercent(networkRisk.online_ratio_pct, 1)} online` : 'Awaiting live probes'} icon={Wifi} accent="plum" />
        <StatCard label="Host anomalies" value={analysis.summary?.anomalies ?? 0} hint="Baseline z-score and absolute thresholds" icon={Activity} accent="ochre" />
        <StatCard label="Recommendations" value={summary.recommendations ?? 0} hint="Evidence-backed operational actions" icon={Lightbulb} accent="sage" />
      </div>

      <div className="grid gap-4 xl:grid-cols-[1.35fr_.65fr]">
        <section className="panel overflow-hidden">
          <div className="flex flex-col justify-between gap-3 border-b border-app p-4 sm:flex-row sm:items-center">
            <SectionHeader icon={Cpu} title="Host risk register" subtitle="Per-host weighted risk from utilisation, availability, loss and anomalies" />
            <Badge tone="info">{scored.length} scored · {atRisk.length} elevated</Badge>
          </div>
          <div className="grid gap-3 p-4 lg:grid-cols-2">
            {agents.map((agent) => <HostRiskCard key={agent.agent_id} agent={agent} onAsk={askCopilot} />)}
            {!agents.length && (
              <div className="lg:col-span-2">
                <EmptyState icon={Cpu} title="No monitoring agents" description="Start the local collector or register a remote agent in Settings. No host risk is invented without telemetry." action={<Link to="/settings" className="btn btn-secondary btn-sm">Manage agents</Link>} />
              </div>
            )}
          </div>
        </section>

        <div className="space-y-4">
          <section className="panel purple-band p-4">
            <SectionHeader icon={Wifi} title="Network instability risk" subtitle="Computed from real ICMP probes" />
            <div className="mt-3 flex items-end justify-between">
              <p className="font-display text-[40px] leading-none text-app">{networkRisk.risk_score != null ? formatNumber(networkRisk.risk_score, 0) : '—'}</p>
              <div className="text-right">
                <Badge tone={networkRisk.severity ? scoreTone(networkRisk.risk_score) : 'neutral'}>{networkRisk.severity || networkRisk.prediction_status || 'awaiting'}</Badge>
                <p className="mt-1 font-mono text-[8.5px] text-subtle">confidence {formatNumber((networkRisk.confidence || 0) * 100, 0)}%</p>
              </div>
            </div>
            <div className="mt-2"><RiskBar value={networkRisk.risk_score} /></div>
            <p className="mt-2 text-[10px] leading-relaxed text-muted">{networkRisk.message || 'Run live probes to establish network risk.'}</p>
            {(networkRisk.warning_signs || []).length > 0 && (
              <ul className="mt-2 space-y-1">
                {networkRisk.warning_signs.map((warning) => (
                  <li key={warning} className="flex items-start gap-1.5 text-[9px] leading-relaxed text-warning"><AlertTriangle className="mt-0.5 h-2.5 w-2.5 shrink-0" />{warning}</li>
                ))}
              </ul>
            )}
            <p className="mt-2 font-mono text-[8px] text-subtle">{networkRisk.samples || 0} probe(s) · latency trend {networkRisk.latency_trend_pct != null ? `${formatNumber(networkRisk.latency_trend_pct, 1)}%` : '—'}</p>
          </section>

          <section className="panel p-4">
            <SectionHeader icon={Sparkles} title="Method & limits" subtitle={analysis.method} />
            <ul className="mt-3 space-y-2">
              {(analysis.limitations || []).map((limit) => (
                <li key={limit} className="flex items-start gap-2 text-[9.5px] leading-relaxed text-muted"><Info className="mt-0.5 h-3 w-3 shrink-0 text-accent" />{limit}</li>
              ))}
            </ul>
            <div className="mt-3 space-y-1 border-t border-app pt-2 font-mono text-[8px] uppercase tracking-[0.08em] text-subtle">
              <p className="flex justify-between"><span>Model</span><span className="text-app">{analysis.model_version || '—'}</span></p>
              <p className="flex justify-between"><span>Window</span><span className="text-app">{analysis.window_hours || 24} h</span></p>
              <p className="flex justify-between"><span>Minimum samples</span><span className="text-app">{analysis.minimum_samples || 8}</span></p>
              <p className="flex justify-between"><span>Generated</span><span className="text-app">{analysis.generated_at ? relativeTime(analysis.generated_at) : '—'}</span></p>
            </div>
          </section>
        </div>
      </div>

      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-app p-4">
            <SectionHeader icon={BrainCircuit} title="Failure-risk predictions" subtitle={predictionData.message || 'Weighted statistical estimate over the next 24 hours'} />
            <Link to="/predictive" className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">Full model</Link>
          </div>
          <div className="p-4">
            {(predictionData.predictions || []).length ? (
              <div className="space-y-2">
                {predictionData.predictions.slice(0, 6).map((prediction) => (
                  <div key={prediction.id} className="rounded-md border border-app p-2.5">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-[10px] font-medium text-app">{prediction.scope === 'network' ? 'Network-wide' : prediction.access_point_name || `Access point #${prediction.access_point_id}`}</span>
                      <Badge tone={prediction.severity === 'Critical' ? 'danger' : prediction.severity === 'High' ? 'warning' : 'info'} className="ml-auto">{prediction.severity}</Badge>
                    </div>
                    <div className="mt-1.5 flex items-center gap-2">
                      <RiskBar value={prediction.risk_score} />
                      <span className="w-14 text-right font-mono text-[9px] text-app">{formatNumber(prediction.risk_score, 0)}/100</span>
                    </div>
                    <p className="mt-1 font-mono text-[8px] text-subtle">{prediction.data_points} points · confidence {formatNumber((prediction.confidence || 0) * 100, 0)}% · {relativeTime(prediction.predicted_at)}</p>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState compact icon={TrendingUp} title="No predictions generated" description={predictionData.message || 'Collect more live measurements and run the risk model.'} action={analyst ? <Button size="sm" icon={Play} loading={busy === 'predictions'} onClick={() => run('predictions')}>Run risk model</Button> : null} />
            )}
          </div>
        </section>

        <section className="panel overflow-hidden">
          <div className="flex items-center justify-between gap-3 border-b border-app p-4">
            <SectionHeader icon={GitBranch} title="Root-cause investigations" subtitle={rca.data?.message || 'Rule-based hypotheses with cited evidence'} />
            <Link to="/root-cause" className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">Full queue</Link>
          </div>
          <div className="p-4">
            {investigations.length ? (
              <div className="space-y-2">
                {investigations.slice(0, 5).map((item) => (
                  <div key={item.id} className="rounded-md border border-app p-2.5">
                    <div className="flex items-center gap-2">
                      <Badge tone={item.severity === 'Critical' ? 'danger' : item.severity === 'High' ? 'warning' : 'info'}>{item.severity}</Badge>
                      <StatusBadge status={item.status} />
                      <span className="ml-auto font-mono text-[8px] text-subtle">{relativeTime(item.created_at)}</span>
                    </div>
                    <p className="mt-1 text-[10px] font-semibold text-app">{item.problem}</p>
                    <p className="mt-0.5 text-[9px] leading-relaxed text-muted">{item.probable_cause}</p>
                    <div className="mt-1 flex items-center gap-2">
                      <span className="font-mono text-[8px] text-subtle">confidence {formatNumber((item.confidence || 0) * 100, 0)}%</span>
                      <div className="w-20"><ProgressBar value={(item.confidence || 0) * 100} tone="sage" /></div>
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState compact icon={GitBranch} title="No investigations yet" description={rca.data?.message || 'Run an investigation to correlate measurements, anomalies and tickets.'} action={analyst ? <Button size="sm" icon={Play} loading={busy === 'rca'} onClick={() => run('rca')}>Investigate now</Button> : null} />
            )}
          </div>
        </section>
      </div>

      <section className="panel overflow-hidden">
        <div className="flex items-center justify-between gap-3 border-b border-app p-4">
          <SectionHeader icon={Activity} title="Detected anomalies" subtitle="Isolation Forest plus host baseline deviations" />
          <Link to="/anomalies" className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">Anomaly workspace</Link>
        </div>
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Detected</th><th>Severity</th><th>Score</th><th>Explanation</th><th>Baseline</th></tr></thead>
            <tbody>
              {anomalyList.slice(0, 10).map((anomaly) => (
                <tr key={`${anomaly.id}-${anomaly.detected_at}`}>
                  <td className="whitespace-nowrap font-mono text-[9px]">{formatDateTime(anomaly.detected_at)}</td>
                  <td><Badge tone={anomaly.severity === 'Critical' ? 'danger' : anomaly.severity === 'High' ? 'warning' : 'info'}>{anomaly.severity}</Badge></td>
                  <td className="font-mono text-[9px]">{formatNumber(anomaly.anomaly_score, 3)}</td>
                  <td className="max-w-[520px] text-[10px] leading-relaxed">{anomaly.explanation}</td>
                  <td><Badge tone={anomaly.used_sample_baseline ? 'plum' : 'success'}>{anomaly.used_sample_baseline ? 'Labelled baseline' : 'Live model'}</Badge></td>
                </tr>
              ))}
            </tbody>
          </table>
          {!anomalyList.length && <EmptyState compact icon={ShieldAlert} title="No anomalies stored" description="Detection runs against real latency, loss and jitter observations. It will not invent outliers." />}
        </div>
      </section>

      <div className="flex flex-col justify-between gap-2 border-t border-app pt-3 text-[8.5px] leading-relaxed text-subtle lg:flex-row">
        <span className="max-w-4xl">{analysis.disclaimer}</span>
        <span className="shrink-0 font-mono uppercase tracking-[0.08em]">Analysed {analysis.generated_at ? formatDateTime(analysis.generated_at) : '—'}</span>
      </div>
    </div>
  )
}
