import { useMemo, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  ClipboardCheck,
  Cpu,
  FlaskConical,
  GitBranch,
  Info,
  Play,
  RefreshCw,
  Settings2,
  ShieldCheck,
  Sparkles,
  Wrench,
  XCircle,
} from 'lucide-react'
import { Link } from 'react-router-dom'
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
import { formatDateTime, formatNumber, relativeTime } from '../lib/utils'
import { api, getErrorMessage } from '../services/api'

const TABS = [
  { key: 'runbook', label: 'Troubleshooting runbooks', icon: Wrench },
  { key: 'optimization', label: 'Optimisation actions', icon: Settings2 },
  { key: 'tests', label: 'Automated verification', icon: FlaskConical },
]

function severityTone(severity) {
  if (severity === 'Critical') return 'danger'
  if (severity === 'High') return 'warning'
  if (severity === 'Medium') return 'info'
  return 'neutral'
}

function RecommendationCard({ item, analyst, busy, onStatus }) {
  const causes = item.possible_causes || []
  const steps = item.recommended_steps || []
  return (
    <article className="panel p-4">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={severityTone(item.severity)}>{item.severity}</Badge>
            <Badge tone="neutral">{item.issue_type}</Badge>
            {!item.is_active && <Badge tone="neutral">archived</Badge>}
          </div>
          <p className="mt-1.5 text-[11.5px] font-semibold leading-snug text-app">{item.detected_issue}</p>
          <p className="mt-1 font-mono text-[8.5px] uppercase tracking-[0.08em] text-subtle">
            {item.access_point_name || 'Network scope'} · generated {relativeTime(item.generated_at)}
          </p>
        </div>
      </div>

      {causes.length > 0 && (
        <div className="mt-2.5">
          <p className="hud-label mb-1">Possible causes</p>
          <ul className="space-y-1">
            {causes.slice(0, 3).map((cause) => (
              <li key={cause} className="flex items-start gap-1.5 text-[9.5px] leading-relaxed text-muted">
                <AlertTriangle className="mt-0.5 h-2.5 w-2.5 shrink-0 text-warning" />{cause}
              </li>
            ))}
          </ul>
        </div>
      )}

      {steps.length > 0 && (
        <div className="mt-2.5">
          <p className="hud-label mb-1">Approved steps</p>
          <ol className="space-y-1">
            {steps.slice(0, 4).map((step, index) => (
              <li key={step} className="flex items-start gap-1.5 text-[9.5px] leading-relaxed text-muted">
                <span className="mt-0.5 flex h-3.5 w-3.5 shrink-0 items-center justify-center rounded-full border border-accent/25 bg-accent/[0.06] font-mono text-[7.5px] text-accent">{index + 1}</span>
                {step}
              </li>
            ))}
          </ol>
        </div>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-app pt-2">
        {analyst && item.is_active && (
          <Button size="sm" variant="ghost" icon={CheckCircle2} loading={busy === `resolve-${item.id}`} onClick={() => onStatus(item, 'resolved')}>Mark handled</Button>
        )}
        <span className="ml-auto flex items-start gap-1 text-[8.5px] leading-relaxed text-subtle">
          <ShieldCheck className="mt-0.5 h-2.5 w-2.5 shrink-0" />{item.safety_note}
        </span>
      </div>
    </article>
  )
}

function OptimizationCard({ item, analyst, busy, onStatus }) {
  return (
    <article className="panel p-4">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-1.5">
            <Badge tone={severityTone(item.severity)}>{item.severity}</Badge>
            <Badge tone="info">{item.category_label || item.category}</Badge>
            <StatusBadge status={item.status} />
          </div>
          <p className="mt-1.5 text-[11.5px] font-semibold text-app">{item.title}</p>
          <p className="mt-1 text-[9.5px] leading-relaxed text-muted">{item.description}</p>
        </div>
      </div>

      {(item.actions || []).length > 0 && (
        <div className="mt-2.5">
          <p className="hud-label mb-1">Advisory actions</p>
          <ul className="space-y-1">
            {item.actions.slice(0, 4).map((action) => (
              <li key={action} className="flex items-start gap-1.5 text-[9.5px] leading-relaxed text-muted">
                <ArrowRight className="mt-0.5 h-2.5 w-2.5 shrink-0 text-accent" />{action}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[8.5px] text-subtle">
        <span>{item.data_points ?? 0} live point(s)</span>
        <span>confidence {formatNumber((item.confidence || 0) * 100, 0)}%</span>
        {item.expected_impact_pct != null && <span>expected impact {formatNumber(item.expected_impact_pct, 1)}%</span>}
        <span>{relativeTime(item.generated_at)}</span>
      </div>

      {analyst && (
        <div className="mt-2.5 flex flex-wrap gap-1.5 border-t border-app pt-2">
          {['Planned', 'Dismissed'].filter((status) => status !== item.status).map((status) => (
            <Button
              key={status}
              size="sm"
              variant={status === 'Planned' ? 'secondary' : 'ghost'}
              loading={busy === `${status}-${item.id}`}
              onClick={() => onStatus(item, status)}
            >
              {status === 'Planned' ? 'Mark planned' : 'Dismiss'}
            </Button>
          ))}
        </div>
      )}
    </article>
  )
}

function TestRow({ result }) {
  const tone = result.status === 'passed' ? 'success' : result.status === 'failed' ? 'danger' : 'neutral'
  const Icon = result.status === 'passed' ? CheckCircle2 : result.status === 'failed' ? XCircle : Activity
  return (
    <tr>
      <td><span className="flex items-center gap-1.5"><Icon className={cn('h-3 w-3', result.status === 'passed' ? 'text-success' : result.status === 'failed' ? 'text-danger' : 'text-subtle')} /><Badge tone={tone}>{result.status}</Badge></span></td>
      <td className="font-mono text-[9px]">{result.suite}</td>
      <td className="max-w-[420px] truncate text-[10px] text-app">{result.test_name}</td>
      <td className="whitespace-nowrap font-mono text-[9px]">{formatNumber(result.duration_ms, 0)} ms</td>
      <td className="whitespace-nowrap font-mono text-[9px]">{formatDateTime(result.executed_at)}</td>
    </tr>
  )
}

export default function Automation() {
  const { hasRole } = useAuth()
  const { toast } = useToast()
  const [tab, setTab] = useState('runbook')
  const [busy, setBusy] = useState('')
  const runbooks = useApi('/troubleshooting', { params: { limit: 40 }, pollMs: 60000 })
  const optimization = useApi('/intelligence/optimization', { pollMs: 60000 })
  const tests = useApi('/tests', { pollMs: 20000 })
  const analyst = hasRole('analyst')
  const admin = hasRole('admin')

  const recommendations = runbooks.data || []
  const optimizationData = optimization.data || {}
  const optimizationItems = optimizationData.recommendations || []
  const testResults = tests.data?.results || []
  const currentRun = tests.data?.current_run

  const runbookStats = useMemo(() => {
    const active = recommendations.filter((item) => item.is_active)
    return {
      total: recommendations.length,
      active: active.length,
      critical: active.filter((item) => item.severity === 'Critical').length,
      high: active.filter((item) => item.severity === 'High').length,
    }
  }, [recommendations])

  async function runTroubleshooting() {
    setBusy('troubleshoot')
    try {
      const response = await api.post('/troubleshooting/run', { access_point_id: null })
      toast(response.data?.detected_issue || 'Runbook generated', 'success', 'Troubleshooting engine')
      runbooks.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Runbook generation failed')
    } finally {
      setBusy('')
    }
  }

  async function runOptimization() {
    setBusy('optimize')
    try {
      const response = await api.post('/intelligence/optimization/scan')
      toast(response.data?.message || 'Optimisation scan complete', response.data?.status === 'insufficient_data' ? 'warning' : 'success', 'Optimisation engine')
      optimization.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Optimisation scan failed')
    } finally {
      setBusy('')
    }
  }

  async function runTests() {
    setBusy('tests')
    try {
      const response = await api.post('/tests/run')
      toast(response.data.message, 'success', `Run ${response.data.run_id}`)
      tests.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Test run failed to start')
    } finally {
      setBusy('')
    }
  }

  async function updateOptimization(item, status) {
    setBusy(`${status}-${item.id}`)
    try {
      await api.patch(`/intelligence/optimization/${item.id}`, { status })
      toast(`Recommendation marked ${status.toLowerCase()}`, 'success')
      optimization.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Could not update recommendation')
    } finally {
      setBusy('')
    }
  }

  async function resolveRunbook(item) {
    setBusy(`resolve-${item.id}`)
    try {
      await api.patch(`/troubleshooting/${item.id}`, { is_active: false })
      toast('Runbook marked handled', 'success')
      runbooks.reload()
    } catch (err) {
      toast(getErrorMessage(err), 'error', 'Could not update runbook')
    } finally {
      setBusy('')
    }
  }

  if (runbooks.loading && !runbooks.data) return <PageSkeleton cards={4} />

  return (
    <div className="space-y-5">
      <PageHeader
        eyebrow="Guarded operations automation"
        title="Automation"
        description="Evidence-backed runbooks, advisory optimisation actions and the repository test runner. NetPulse never pushes device configuration: every action is an operator-reviewed recommendation or a read-only verification."
        actions={
          <>
            <RefreshStamp
              lastUpdated={runbooks.lastUpdated}
              refreshing={runbooks.refreshing}
              onRefresh={() => { runbooks.reload(); optimization.reload(); tests.reload() }}
            />
            {analyst && tab === 'runbook' && <Button size="sm" icon={Wrench} loading={busy === 'troubleshoot'} onClick={runTroubleshooting}>Generate runbook</Button>}
            {analyst && tab === 'optimization' && <Button size="sm" icon={Sparkles} loading={busy === 'optimize'} onClick={runOptimization}>Run scan</Button>}
            {admin && tab === 'tests' && <Button size="sm" variant="primary" icon={Play} loading={busy === 'tests'} onClick={runTests}>Run test suite</Button>}
          </>
        }
      >
        <div className="relative mt-5 flex flex-wrap gap-1">
          {TABS.map((item) => (
            <button
              key={item.key}
              onClick={() => setTab(item.key)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded border px-3 py-1.5 font-mono text-[9px] font-bold uppercase tracking-[0.08em] transition',
                tab === item.key ? 'border-accent/40 bg-accent/10 text-accent' : 'border-app text-subtle hover:border-accent/25 hover:text-app',
              )}
            >
              <item.icon className="h-3 w-3" /> {item.label}
            </button>
          ))}
        </div>
      </PageHeader>

      {tab === 'runbook' && (
        <>
          {runbooks.error && !runbooks.data && <ErrorPanel message={runbooks.error} onRetry={runbooks.reload} />}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Stored runbooks" value={runbookStats.total} hint="Generated from real probe evidence" icon={Wrench} accent="wine" />
            <StatCard label="Active" value={runbookStats.active} hint="Still applicable to current telemetry" icon={Activity} accent="sage" />
            <StatCard label="Critical" value={runbookStats.critical} hint="Severity from the underlying measurements" icon={AlertTriangle} accent={runbookStats.critical ? 'rust' : 'sage'} />
            <StatCard label="High" value={runbookStats.high} hint="Requires analyst review" icon={GitBranch} accent="ochre" />
          </div>

          <Alert tone="info" title="Human approval is mandatory">
            Every runbook is advisory. Applying a change to a router, access point, firewall or host remains an operator action outside this application.
          </Alert>

          <section className="panel overflow-hidden">
            <div className="flex items-center justify-between gap-3 border-b border-app p-4">
              <SectionHeader icon={Wrench} title="Generated runbooks" subtitle={`${recommendations.length} stored recommendation(s)`} />
              <Link to="/troubleshooting" className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">Open workspace</Link>
            </div>
            <div className="grid gap-3 p-4 lg:grid-cols-2">
              {recommendations.slice(0, 12).map((item) => (
                <RecommendationCard key={item.id} item={item} analyst={analyst} busy={busy} onStatus={resolveRunbook} />
              ))}
              {!recommendations.length && (
                <div className="lg:col-span-2">
                  <EmptyState
                    icon={Wrench}
                    title="No runbooks generated yet"
                    description="Runbooks are derived from stored latency, loss, jitter and availability evidence. Generate one when live telemetry exists."
                    action={analyst ? <Button size="sm" icon={Wrench} loading={busy === 'troubleshoot'} onClick={runTroubleshooting}>Generate runbook</Button> : null}
                  />
                </div>
              )}
            </div>
          </section>
        </>
      )}

      {tab === 'optimization' && (
        <>
          {optimizationData.status === 'insufficient_data' && (
            <Alert tone="warning" title="Not enough live measurements">{optimizationData.message}</Alert>
          )}
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Advisories" value={optimizationData.summary?.total ?? 0} hint={`${optimizationData.window_hours || 24} h window`} icon={Sparkles} accent="wine" />
            <StatCard label="High impact" value={optimizationData.summary?.high ?? 0} hint="Review before planning" icon={AlertTriangle} accent={optimizationData.summary?.high ? 'rust' : 'sage'} />
            <StatCard label="Available points" value={optimizationData.available_points ?? 0} hint={`Minimum ${optimizationData.minimum_required || 10} required`} icon={Activity} accent="plum" />
            <StatCard label="Categories" value={Object.keys(optimizationData.summary?.categories || {}).length} hint="Distinct measured symptom classes" icon={Cpu} accent="ochre" />
          </div>

          <section className="panel overflow-hidden">
            <div className="flex items-center justify-between gap-3 border-b border-app p-4">
              <SectionHeader icon={Settings2} title="Optimisation recommendations" subtitle={optimizationData.message || 'Advisory actions derived from measured symptoms'} />
              <Link to="/optimization" className="font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] text-accent hover:underline">Open workspace</Link>
            </div>
            <div className="grid gap-3 p-4 lg:grid-cols-2">
              {optimizationItems.map((item) => (
                <OptimizationCard key={item.id} item={item} analyst={analyst} busy={busy} onStatus={updateOptimization} />
              ))}
              {!optimizationItems.length && (
                <div className="lg:col-span-2">
                  <EmptyState
                    icon={Settings2}
                    title="No optimisation advisories"
                    description={optimizationData.message || 'Run the scan once enough live measurements are available.'}
                    action={analyst ? <Button size="sm" icon={Sparkles} loading={busy === 'optimize'} onClick={runOptimization}>Run scan</Button> : null}
                  />
                </div>
              )}
            </div>
            <div className="border-t border-app px-4 py-2.5"><InfoNote>{optimizationData.disclaimer || optimizationData.method}</InfoNote></div>
          </section>
        </>
      )}

      {tab === 'tests' && (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <StatCard label="Passed" value={tests.data?.passed ?? 0} hint="Repository suite results" icon={CheckCircle2} accent="sage" />
            <StatCard label="Failed" value={tests.data?.failed ?? 0} hint="Investigate before release" icon={XCircle} accent={tests.data?.failed ? 'rust' : 'sage'} />
            <StatCard label="Skipped" value={tests.data?.skipped ?? 0} hint="Environment-dependent checks" icon={Activity} accent="ochre" />
            <StatCard label="Current run" value={currentRun?.status || (tests.data?.latest_run_id ? 'complete' : null)} hint={tests.data?.latest_run_id || 'No run started'} icon={FlaskConical} accent="plum" />
          </div>

          {currentRun && currentRun.status === 'running' && (
            <Alert tone="info" title="Test run in progress">
              Run {currentRun.run_id} is executing the fixed repository pytest suite. Results refresh automatically every 20 seconds.
            </Alert>
          )}
          {!admin && <Alert tone="info" title="Read-only test history">Only administrators can start the fixed repository test suite.</Alert>}

          <section className="panel overflow-hidden">
            <div className="flex flex-col justify-between gap-3 border-b border-app p-4 sm:flex-row sm:items-center">
              <SectionHeader icon={FlaskConical} title="Automated verification" subtitle={tests.data?.note || 'Fixed repository suite only'} />
              <Button size="sm" icon={RefreshCw} loading={tests.loading} onClick={tests.reload}>Refresh</Button>
            </div>
            <div className="table-wrap">
              <table className="data-table">
                <thead><tr><th>Result</th><th>Suite</th><th>Test</th><th>Duration</th><th>Executed</th></tr></thead>
                <tbody>
                  {testResults.slice(0, 60).map((result) => <TestRow key={result.id} result={result} />)}
                </tbody>
              </table>
              {!testResults.length && (
                <EmptyState
                  compact
                  icon={ClipboardCheck}
                  title="No stored test results"
                  description={tests.data?.note || 'Start the suite to record a real run. Results are stored with their execution timestamps.'}
                  action={admin ? <Button size="sm" icon={Play} loading={busy === 'tests'} onClick={runTests}>Run test suite</Button> : null}
                />
              )}
            </div>
            <div className="border-t border-app px-4 py-2.5">
              <div className="flex flex-wrap items-center gap-2 font-mono text-[8px] uppercase tracking-[0.08em] text-subtle">
                <span>Backend · {tests.data?.suites?.backend || 'pytest'}</span>
                <span className="text-accent/50">/</span>
                <span>Browser · {tests.data?.suites?.browser || 'Playwright'}</span>
              </div>
            </div>
          </section>
        </>
      )}

      <div className="flex flex-col justify-between gap-2 border-t border-app pt-3 text-[8.5px] leading-relaxed text-subtle lg:flex-row">
        <span className="flex items-start gap-1.5"><Info className="mt-0.5 h-3 w-3 shrink-0" /> Automation surfaces recommendations only. No shell command, router change or credential is exposed to the browser.</span>
        <span className="shrink-0 font-mono uppercase tracking-[0.08em]">Updated {formatDateTime(new Date())}</span>
      </div>
    </div>
  )
}
