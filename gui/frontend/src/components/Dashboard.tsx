import { useEffect, useState, useCallback } from 'react'
import { showToast } from '../App'

interface Props {
  api: any
}

export default function Dashboard({ api }: Props) {
  const [stats, setStats] = useState<Record<string, any>>({})
  const [systemStatus, setSystemStatus] = useState<Record<string, any>>({})
  const [switchStatus, setSwitchStatus] = useState<Record<string, boolean>>({})
  const [envText, setEnvText] = useState('')

  const refresh = useCallback(async () => {
    if (!api) return
    try { const s = await api.GetStats(); setStats(s) } catch { /* */ }
    try { const ss = await api.GetSystemStatus(); setSystemStatus(ss) } catch { /* */ }
    try { const sw = await api.GetProxySwitchStatus(); setSwitchStatus(sw) } catch { /* */ }
  }, [api])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 5000)
    return () => clearInterval(timer)
  }, [refresh])

  const handleSwitchToggle = async (which: string, enabled: boolean) => {
    if (!api) return
    try {
      await api.ToggleProxySwitch(which, enabled)
      refresh()
      showToast(`${which === 'codex' ? 'Codex' : 'Claude'} proxy ${enabled ? 'started' : 'stopped'}`)
    } catch {
      showToast('Operation failed', false)
    }
  }

  const handleCopyEnv = async (mode: 'codex' | 'claude') => {
    if (!api) return
    try {
      const text = await api.GetEnvVars(mode)
      setEnvText(text)
      await navigator.clipboard.writeText(text)
      showToast('Copied to clipboard')
    } catch {
      showToast('Copy failed', false)
    }
  }

  const fmtUptime = (sec: number) => {
    if (!sec || sec < 0) return '--'
    if (sec < 60) return `${sec}s`
    if (sec < 3600) return `${Math.floor(sec / 60)}m`
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
    return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h`
  }

  return (
    <>
      <div className="card">
        <h2>Realtime Stats</h2>
        <div className="stat-grid">
          <div className="stat-item">
            <div className="stat-value">{fmtUptime(stats.uptime || 0)}</div>
            <div className="stat-label">Uptime</div>
          </div>
          <div className="stat-item">
            <div className="stat-value" style={{ color: 'var(--accent)' }}>{stats.total_requests ?? '--'}</div>
            <div className="stat-label">API Requests</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.active_streams ?? 0}</div>
            <div className="stat-label">Active Streams</div>
          </div>
          <div className="stat-item">
            <div className={`stat-value ${(stats.error_rate || 0) > 10 ? 'danger' : (stats.error_rate || 0) > 3 ? 'orange' : ''}`}>
              {stats.error_rate ?? '--'}%
            </div>
            <div className="stat-label">Error Rate</div>
          </div>
          <div className="stat-item">
            <div className={`stat-value ${(stats.cache_hit_rate || 0) >= 70 ? 'green' : ''}`}>
              {stats.cache_hit_rate ?? '--'}%
            </div>
            <div className="stat-label">Cache Hit</div>
          </div>
          <div className="stat-item">
            <div className="stat-value" style={{ color: 'var(--accent)' }}>{stats.total_errors ?? 0}</div>
            <div className="stat-label">Total Errors</div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>System Status</h2>
        <div style={{ fontSize: 12 }}>
          <div style={{ marginBottom: 5 }}>
            <span className={`status-dot ${systemStatus.deepseek === 'connected' ? 'up' : 'down'}`} />
            DeepSeek API: {systemStatus.deepseek || '--'}
          </div>
          <div>
            <span className={`status-dot ${systemStatus.redis === 'connected' ? 'up' : 'down'}`} />
            Cache Backend: {systemStatus.redis || 'file'}
          </div>
        </div>
      </div>

      <div className="card">
        <h2>Terminal Env Vars</h2>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
          <label className="toggle" style={{ flexShrink: 0 }}>
            <input type="checkbox" checked={!!switchStatus.codex_enabled} onChange={e => handleSwitchToggle('codex', e.target.checked)} />
            <span className="slider" />
          </label>
          <span style={{ fontSize: 12 }}>Codex</span>
          <label className="toggle" style={{ flexShrink: 0 }}>
            <input type="checkbox" checked={!!switchStatus.claude_enabled} onChange={e => handleSwitchToggle('claude', e.target.checked)} />
            <span className="slider" />
          </label>
          <span style={{ fontSize: 12 }}>Claude</span>
        </div>
        <div style={{ display: 'flex', gap: 4, marginBottom: 4 }}>
          <button className="btn-copy" style={{ flex: 1, fontSize: 10, padding: '3px 6px' }} onClick={() => handleCopyEnv('codex')}>Copy Codex</button>
          <button className="btn-copy" style={{ flex: 1, fontSize: 10, padding: '3px 6px' }} onClick={() => handleCopyEnv('claude')}>Copy Claude</button>
        </div>
        {envText && <div className="env-box" style={{ maxHeight: 80, overflowY: 'auto' }}>{envText}</div>}
      </div>

      {stats.top_upstream_errors && (stats.top_upstream_errors as any[]).length > 0 && (
        <div className="card">
          <h2>Upstream Errors</h2>
          <div style={{ fontSize: 11, color: 'var(--muted)', maxHeight: 120, overflowY: 'auto' }}>
            {(stats.top_upstream_errors as any[]).map((e: any, i: number) => (
              <div key={i} style={{ marginBottom: 3 }}>
                <span style={{ color: 'var(--danger)', fontWeight: 600 }}>{e.count}x</span>{' '}
                {String(e.msg || '').substring(0, 100)}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}
