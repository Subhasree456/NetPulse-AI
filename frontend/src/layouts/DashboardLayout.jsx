import { useEffect, useMemo, useRef, useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import {
  Activity,
  AlertTriangle,
  BarChart3,
  Bell,
  Bot,
  ChevronDown,
  CircleDot,
  Command,
  Cpu,
  FlaskConical,
  GitBranch,
  LayoutDashboard,
  LogOut,
  Menu,
  Monitor,
  Moon,
  Network,
  Router,
  Search,
  Settings,
  Radar,
  ShieldAlert,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Sun,
  Ticket,
  TrendingUp,
  UserRound,
  Wrench,
  X,
} from 'lucide-react'
import Brand from '../components/Brand'
import NotificationCenter from '../components/NotificationCenter'
import { Badge, Modal, cn } from '../components/ui'
import { useAuth } from '../context/AuthContext'
import { LiveProvider, useLive } from '../context/LiveContext'
import { useTheme } from '../context/ThemeContext'
import { useApi } from '../hooks/useApi'
import { relativeTime } from '../lib/utils'

const NAV_GROUPS = [
  {
    label: 'Command',
    items: [
      { to: '/', label: 'Overview', icon: LayoutDashboard, end: true },
      { to: '/monitoring', label: 'Live Monitoring', icon: Activity },
    ],
  },
  {
    label: 'Intelligence',
    items: [
      { to: '/intelligence', label: 'AI Intelligence', icon: Sparkles },
      { to: '/copilot', label: 'AI Network Copilot', icon: Bot },
      { to: '/security', label: 'Cybersecurity', icon: ShieldAlert },
    ],
  },
  {
    label: 'Network',
    items: [
      { to: '/devices', label: 'Devices', icon: Router },
      { to: '/topology', label: 'Network Topology', icon: Network },
      { to: '/incidents', label: 'Incidents', icon: Ticket },
    ],
  },
  {
    label: 'Operations',
    items: [
      { to: '/automation', label: 'Automation', icon: FlaskConical },
      { to: '/reports', label: 'Analytics & Reports', icon: BarChart3 },
    ],
  },
  {
    label: 'System',
    items: [{ to: '/settings', label: 'Settings', icon: Settings }],
  },
]

const LEGACY_NAV = [
  { to: '/probes', label: 'Network Probe History', icon: Radar },
  { to: '/predictive', label: 'Predictive Analytics', icon: TrendingUp },
  { to: '/root-cause', label: 'Root Cause Analysis', icon: GitBranch },
  { to: '/anomalies', label: 'AI Anomaly Detection', icon: AlertTriangle },
  { to: '/optimization', label: 'Network Optimization', icon: SlidersHorizontal },
  { to: '/access-points', label: 'Access Point Inventory', icon: Router },
  { to: '/troubleshooting', label: 'Smart Troubleshooting', icon: Wrench },
  { to: '/issues', label: 'Issue Management', icon: Ticket },
  { to: '/testing', label: 'Automation Testing', icon: FlaskConical },
]

const ALL_NAV = [...NAV_GROUPS.flatMap((group) => group.items), ...LEGACY_NAV]
const PALETTE_NAV = [...NAV_GROUPS.flatMap((group) => group.items), ...LEGACY_NAV].filter(
  (item, index, list) => list.findIndex((candidate) => candidate.to === item.to) === index,
)

function UtcClock() {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  return (
    <span className="hidden font-mono text-[9px] tabular-nums tracking-[0.08em] text-subtle lg:inline">
      {now.toISOString().slice(11, 19)} UTC
    </span>
  )
}

function Sidebar({ open, onClose, monitoringEnabled, summary }) {
  const { user } = useAuth()
  const location = useLocation()
  const [legacyOpen, setLegacyOpen] = useState(false)
  const monitoringKnown = typeof monitoringEnabled === 'boolean'
  const monitoringActive = monitoringEnabled === true
  const legacyActive = LEGACY_NAV.some((item) => location.pathname.startsWith(item.to))

  const badgeFor = (to) => {
    if (to === '/incidents' && summary?.active_incidents) {
      return <span className="ml-auto font-mono text-[8.5px] font-bold text-warning">{summary.active_incidents}</span>
    }
    if (to === '/security' && summary?.security_threats) {
      return <span className="ml-auto font-mono text-[8.5px] font-bold text-danger">{summary.security_threats}</span>
    }
    if (to === '/devices' && summary?.total_devices) {
      return <span className="ml-auto font-mono text-[8.5px] font-bold text-subtle">{summary.total_devices}</span>
    }
    return null
  }

  useEffect(() => {
    if (legacyActive) setLegacyOpen(true)
  }, [legacyActive])

  return (
    <>
      {open && <div className="fixed inset-0 z-30 bg-black/75 backdrop-blur-sm lg:hidden" onClick={onClose} aria-hidden="true" />}
      <aside
        className={cn(
          'fixed inset-y-0 left-0 z-40 flex w-[264px] flex-col border-r border-app transition-transform duration-200 lg:translate-x-0',
          open ? 'translate-x-0' : '-translate-x-full',
        )}
        style={{ background: 'var(--sidebar)', backdropFilter: 'blur(22px) saturate(1.18)' }}
      >
        <div className="relative flex h-[70px] items-center justify-between border-b border-app px-5">
          <Brand />
          <button onClick={onClose} className="rounded-md p-1.5 text-subtle hover:bg-accent/5 hover:text-app lg:hidden" aria-label="Close navigation">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="mx-3 mt-3 flex items-center gap-2 rounded-md border border-[rgb(var(--lime-rgb)/0.22)] bg-[var(--lime-soft)] px-2.5 py-1.5">
          <span className="signal-dot animate-neon-breathe" />
          <span className="font-mono text-[8px] font-bold uppercase tracking-[0.16em] text-signal">Autonomous telemetry</span>
          <span className="ml-auto font-mono text-[8px] text-subtle">{summary?.agents_online ?? '—'}/{(summary?.agents_total ?? 0) || '—'}</span>
        </div>

        <nav className="no-scrollbar flex-1 overflow-y-auto px-3 py-4" aria-label="Main navigation">
          {NAV_GROUPS.map((group) => (
            <div key={group.label} className="mb-4">
              <p className="mb-1.5 px-3 font-mono text-[8px] font-bold uppercase tracking-[0.24em] text-subtle">{group.label}</p>
              <div className="space-y-0.5">
                {group.items.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    onClick={onClose}
                    aria-label={item.label}
                    className={({ isActive }) => cn('nav-link', isActive && 'nav-link-active')}
                  >
                    <item.icon className="h-[15px] w-[15px] shrink-0" strokeWidth={1.9} />
                    <span>{item.label}</span>
                    {badgeFor(item.to)}
                  </NavLink>
                ))}
              </div>
            </div>
          ))}

          <div className="mb-4">
            <button
              type="button"
              onClick={() => setLegacyOpen((value) => !value)}
              className="flex w-full items-center gap-2 px-3 py-1 font-mono text-[8px] font-bold uppercase tracking-[0.24em] text-subtle transition hover:text-muted"
              aria-expanded={legacyOpen}
            >
              <ChevronDown className={cn('h-3 w-3 transition-transform', !legacyOpen && '-rotate-90')} />
              Extended modules
              <span className="ml-auto text-[8px] font-normal normal-case tracking-normal">{LEGACY_NAV.length}</span>
            </button>
            {legacyOpen && (
              <div className="mt-1 space-y-0.5">
                {LEGACY_NAV.map((item) => (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    onClick={onClose}
                    aria-label={item.label}
                    className={({ isActive }) => cn('nav-link', isActive && 'nav-link-active')}
                  >
                    <item.icon className="h-[14px] w-[14px] shrink-0" strokeWidth={1.8} />
                    <span className="text-[10px]">{item.label}</span>
                  </NavLink>
                ))}
              </div>
            )}
          </div>
        </nav>

        <div className="mx-3 mb-3 rounded-lg border border-app bg-accent/[0.03] p-3">
          <div className="flex items-center gap-2">
            <span className="relative flex h-2 w-2">
              {monitoringActive && <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-success opacity-50" />}
              <span className={cn('relative inline-flex h-2 w-2 rounded-full', !monitoringKnown ? 'bg-neutral' : monitoringActive ? 'bg-success' : 'bg-warning')} />
            </span>
            <p className="text-[10px] font-semibold text-app">{!monitoringKnown ? 'Monitoring status' : monitoringActive ? 'Scheduler active' : 'Scheduler paused'}</p>
          </div>
          <div className="mt-2 space-y-1 font-mono text-[8px] uppercase tracking-[0.08em] text-subtle">
            <p className="flex justify-between"><span>Host telemetry</span><span className={monitoringActive ? 'text-success' : 'text-warning'}>{monitoringActive ? 'psutil live' : 'manual'}</span></p>
            <p className="flex justify-between"><span>Agents online</span><span className="text-app">{summary?.agents_online ?? '—'} / {summary?.agents_total ?? '—'}</span></p>
            <p className="flex justify-between"><span>Devices seen</span><span className="text-app">{summary?.online_devices ?? '—'} / {summary?.total_devices ?? '—'}</span></p>
          </div>
        </div>

        <div className="flex items-center gap-2.5 border-t border-app px-4 py-3.5">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md border border-[rgb(var(--lime-rgb)/0.45)] bg-[rgb(var(--lime-rgb)/0.14)] font-mono text-[10px] font-bold text-signal">
            {(user?.full_name || 'NP').split(' ').map((part) => part[0]).slice(0, 2).join('')}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-[11px] font-semibold text-app">{user?.full_name}</p>
            <p className="truncate font-mono text-[8.5px] uppercase tracking-[0.06em] text-subtle"><span className="capitalize">{user?.role}</span> · {user?.email}</p>
          </div>
          <ShieldCheck className="h-3.5 w-3.5 text-success" />
        </div>
      </aside>
    </>
  )
}

function CommandPalette({ open, onClose }) {
  const [query, setQuery] = useState('')
  const navigate = useNavigate()
  const inputRef = useRef(null)
  const matches = useMemo(() => {
    const term = query.toLowerCase().trim()
    if (!term) return PALETTE_NAV
    return PALETTE_NAV.filter((item) => item.label.toLowerCase().includes(term))
  }, [query])

  useEffect(() => {
    if (open) {
      setQuery('')
      window.setTimeout(() => inputRef.current?.focus(), 60)
    }
  }, [open])

  function choose(item) {
    navigate(item.to)
    onClose()
  }

  return (
    <Modal open={open} onClose={onClose} title="Command palette" subtitle="Jump to any workspace module" size="md">
      <div className="search-box">
        <Search className="h-4 w-4" />
        <input
          ref={inputRef}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="input pl-10"
          placeholder="Search modules…"
          onKeyDown={(event) => {
            if (event.key === 'Enter' && matches[0]) choose(matches[0])
          }}
        />
      </div>
      <div className="mt-3 max-h-[52vh] space-y-1 overflow-y-auto">
        {matches.map((item) => (
          <button
            key={item.to}
            onClick={() => choose(item)}
            className="flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left text-xs text-muted transition hover:bg-accent/5 hover:text-app"
          >
            <item.icon className="h-4 w-4 text-accent" />
            <span className="flex-1">{item.label}</span>
            <span className="font-mono text-[9px] text-subtle">{item.to}</span>
          </button>
        ))}
        {!matches.length && <p className="py-8 text-center text-xs text-subtle">No modules match “{query}”.</p>}
      </div>
      <div className="mt-4 flex items-center gap-3 border-t border-app pt-3 text-[9px] text-subtle">
        <span><span className="code-chip">Enter</span> open first result</span>
        <span><span className="code-chip">Esc</span> close</span>
      </div>
    </Modal>
  )
}

function LivePill() {
  const { snapshot, transport, lastMessageAt } = useLive()
  const measurement = snapshot?.measurement
  const label =
    transport === 'websocket'
      ? 'Live socket'
      : transport === 'polling'
        ? 'Live polling'
        : transport === 'connecting'
          ? 'Connecting'
          : 'Feed offline'
  const tone =
    transport === 'websocket'
      ? 'border-accent/30 bg-accent/[0.07] text-accent'
      : transport === 'polling'
        ? 'border-signal/30 bg-[var(--lime-soft)] text-signal'
        : transport === 'connecting'
          ? 'border-warning/25 bg-warning/[0.06] text-warning'
          : 'border-danger/25 bg-danger/[0.06] text-danger'
  return (
    <div
      className={cn('hidden items-center gap-2 rounded-md border px-2.5 py-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] md:flex', tone)}
      title={lastMessageAt ? `Last snapshot ${relativeTime(lastMessageAt)}` : 'Awaiting live feed'}
    >
      <span className={cn('h-1.5 w-1.5 rounded-full bg-current', transport !== 'offline' && 'animate-pulse')} />
      <span>{label}</span>
      {measurement?.latency_ms != null && (
        <span className="border-l border-app pl-2 text-[8px] opacity-80">{Math.round(measurement.latency_ms)} ms</span>
      )}
    </div>
  )
}

function DashboardShell() {
  const [sidebarOpen, setSidebarOpen] = useState(false)
  const [paletteOpen, setPaletteOpen] = useState(false)
  const [notificationsOpen, setNotificationsOpen] = useState(false)
  const [userMenuOpen, setUserMenuOpen] = useState(false)
  const { user, logout, hasRole } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const location = useLocation()
  const navigate = useNavigate()
  const { data: dashboard } = useApi('/dashboard/overview', { params: { hours: 24 }, pollMs: 60000 })
  const { data: unreadData, reload: reloadUnread } = useApi('/notifications/unread-count', { pollMs: 30000 })

  const summary = dashboard?.summary
  const currentPage = [...ALL_NAV]
    .sort((a, b) => b.to.length - a.to.length)
    .find((item) => (item.to === '/' ? location.pathname === '/' : location.pathname.startsWith(item.to)))
  const unread = unreadData?.unread || 0

  useEffect(() => {
    const onKey = (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setPaletteOpen(true)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  async function signOut() {
    await logout()
    navigate('/login', { replace: true })
  }

  return (
    <div className="min-h-screen bg-app">
      <Sidebar
        open={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        monitoringEnabled={dashboard?.monitoring?.enabled}
        summary={summary}
      />
      <div className="lg:pl-[264px]">
        <header
          className="sticky top-0 z-20 flex h-[60px] items-center gap-3 border-b border-app px-4 backdrop-blur-xl sm:px-6"
          style={{ background: 'color-mix(in srgb, var(--bg) 84%, transparent)' }}
        >
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-[var(--accent)]/50 to-[var(--lime)]/30" />
          <button onClick={() => setSidebarOpen(true)} className="rounded-md p-2 text-muted transition hover:bg-accent/5 hover:text-app lg:hidden" aria-label="Open navigation">
            <Menu className="h-4 w-4" />
          </button>
          <div className="hidden items-center gap-2 font-mono text-[9px] font-bold uppercase tracking-[0.14em] text-subtle sm:flex">
            <CircleDot className="h-3 w-3 text-accent" />
            <span>NetPulse</span>
            <span className="text-accent/50">/</span>
            <span className="text-muted">{currentPage?.label || 'Workspace'}</span>
          </div>
          <div className="flex-1" />
          <UtcClock />
          <LivePill />
          <button
            onClick={() => setPaletteOpen(true)}
            className="hidden items-center gap-2 rounded-md border border-app bg-accent/[0.03] px-2.5 py-1.5 font-mono text-[9px] uppercase tracking-[0.08em] text-subtle transition hover:border-accent/40 hover:text-muted xl:flex"
          >
            <Search className="h-3.5 w-3.5" />
            <span>Search workspace</span>
            <span className="code-chip ml-4 inline-flex items-center gap-0.5"><Command className="h-2.5 w-2.5" />K</span>
          </button>
          <div
            className={cn(
              'hidden items-center gap-1.5 rounded-md border px-2 py-1 font-mono text-[8.5px] font-bold uppercase tracking-[0.1em] sm:flex',
              dashboard?.monitoring?.enabled === true
                ? 'border-signal/25 bg-[var(--lime-soft)] text-signal'
                : 'border-warning/25 bg-warning/[0.06] text-warning',
            )}
          >
            <span className={cn('h-1.5 w-1.5 rounded-full', dashboard?.monitoring?.enabled === true ? 'bg-signal animate-pulse-ring' : 'bg-warning')} />
            {dashboard?.monitoring?.enabled === true ? 'Probes on' : dashboard?.monitoring?.enabled === false ? 'Probes paused' : 'Probe status'}
          </div>
          <button onClick={toggleTheme} className="rounded-md p-2 text-muted transition hover:bg-accent/5 hover:text-app" aria-label={`Switch to ${theme === 'dark' ? 'light' : 'dark'} theme`}>
            {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
          <div className="relative">
            <button
              onClick={() => { setNotificationsOpen((value) => !value); setUserMenuOpen(false) }}
              className="relative rounded-md p-2 text-muted transition hover:bg-accent/5 hover:text-app"
              aria-label="Open notifications"
            >
              <Bell className="h-4 w-4" />
              {unread > 0 && (
                <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-[var(--lime)] px-1 font-mono text-[8px] font-bold text-[#0a1400] ring-2 ring-[var(--bg)]">
                  {unread > 9 ? '9+' : unread}
                </span>
              )}
            </button>
            <NotificationCenter
              open={notificationsOpen}
              onClose={() => setNotificationsOpen(false)}
              onChanged={reloadUnread}
            />
          </div>
          <div className="mx-1 hidden h-5 w-px bg-[var(--border)] sm:block" />
          <div className="relative">
            <button
              onClick={() => { setUserMenuOpen((value) => !value); setNotificationsOpen(false) }}
              className="flex items-center gap-2 rounded-md p-1 pr-1.5 transition hover:bg-accent/5"
              aria-label="Open user menu"
              aria-expanded={userMenuOpen}
            >
              <div className="flex h-7 w-7 items-center justify-center rounded-md border border-[rgb(var(--lime-rgb)/0.45)] bg-[rgb(var(--lime-rgb)/0.14)] font-mono text-[9px] font-bold text-signal">
                {(user?.full_name || 'NP').split(' ').map((part) => part[0]).slice(0, 2).join('')}
              </div>
              <ChevronDown className="hidden h-3 w-3 text-subtle sm:block" />
            </button>
            {userMenuOpen && (
              <>
                <button className="fixed inset-0 z-30 cursor-default" aria-label="Close menu" onClick={() => setUserMenuOpen(false)} />
                <div className="panel absolute right-0 top-10 z-40 w-56 animate-fade-up overflow-hidden p-1.5">
                  <div className="border-b border-app px-2.5 py-2">
                    <p className="truncate text-[11px] font-semibold text-app">{user?.full_name}</p>
                    <p className="truncate text-[9.5px] text-subtle">{user?.email}</p>
                    <Badge tone={hasRole('admin') ? 'info' : 'neutral'} className="mt-1.5 capitalize">{user?.role}</Badge>
                  </div>
                  <button onClick={() => { setUserMenuOpen(false); navigate('/settings') }} className="mt-1 flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[11px] text-muted hover:bg-accent/5 hover:text-app">
                    <UserRound className="h-3.5 w-3.5" /> Account & settings
                  </button>
                  <button onClick={signOut} className="flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left text-[11px] text-danger hover:bg-danger/10">
                    <LogOut className="h-3.5 w-3.5" /> Sign out
                  </button>
                </div>
              </>
            )}
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1640px] px-4 py-5 sm:px-6 sm:py-6">
          <Outlet />
        </main>
        <footer className="mx-auto flex w-full max-w-[1640px] flex-col items-center justify-between gap-2 px-6 pb-6 pt-2 font-mono text-[8.5px] uppercase tracking-[0.1em] text-subtle sm:flex-row">
          <span className="flex items-center gap-2"><Cpu className="h-3 w-3" /> NetPulse AI · unified local, remote & network telemetry</span>
          <span className="flex items-center gap-1.5"><Monitor className="h-3 w-3" /> Real measurements only · simulations always labelled</span>
        </footer>
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  )
}

export default function DashboardLayout() {
  return (
    <LiveProvider>
      <DashboardShell />
    </LiveProvider>
  )
}
