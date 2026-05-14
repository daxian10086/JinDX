import { useEffect, useState, useCallback, useContext } from 'react'
import { showToast, LangContext } from '../App'
import { App as AppBindings } from '../bindings/index.js'

export default function RightPanel() {
  const [stats, setStats] = useState<Record<string, any>>({})
  const [switchStatus, setSwitchStatus] = useState<Record<string, boolean>>({})
  const [envText, setEnvText] = useState('')
  const [logs, setLogs] = useState<Array<Record<string, any>>>([])
  const lang = useContext(LangContext)

  const refresh = useCallback(async () => {
    try { setStats(await AppBindings.GetStats() || {}) } catch { /* */ }
    try { setSwitchStatus(await AppBindings.GetProxySwitchStatus() || {}) } catch { /* */ }
    try { setLogs(await AppBindings.GetLogs(20) || []) } catch { /* */ }
  }, [])

  useEffect(() => {
    refresh()
    const t = setInterval(refresh, 5000)
    return () => clearInterval(t)
  }, [refresh])

  const handleSwitchToggle = async (which: string, enabled: boolean) => {
    try {
      await AppBindings.ToggleProxySwitch(which, enabled)
      refresh()
      const name = which === 'codex' ? 'Codex' : 'Claude'
      showToast(name + (enabled ? (lang === 'zh' ? ' 代理已启用' : ' proxy enabled') : (lang === 'zh' ? ' 代理已停用' : ' proxy disabled')))
    } catch { showToast(lang === 'zh' ? '操作失败' : 'Error', false) }
  }

  const handleCopyEnv = async (mode: 'codex' | 'claude') => {
    try {
      const text = await AppBindings.GetEnvVars(mode)
      setEnvText(text)
      await navigator.clipboard.writeText(text)
      showToast(lang === 'zh' ? '已复制' : 'Copied')
    } catch { showToast(lang === 'zh' ? '复制失败' : 'Copy failed', false) }
  }

  const fmtUptime = (sec: number) => {
    if (!sec || sec < 0) return '--'
    if (sec < 60) return sec + 's'
    if (sec < 3600) return Math.floor(sec / 60) + 'm'
    if (sec < 86400) return Math.floor(sec / 3600) + 'h ' + Math.floor((sec % 3600) / 60) + 'm'
    return Math.floor(sec / 86400) + 'd ' + Math.floor((sec % 86400) / 3600) + 'h'
  }

  return (
    <>
      <div className="card">
        <h2>{lang === 'zh' ? '统计' : 'Stats'}</h2>
        <div className="stat-grid">
          <div className="stat-item">
            <div className="stat-value">{fmtUptime(stats.uptime || 0)}</div>
            <div className="stat-label">{lang === 'zh' ? '运行时长' : 'Uptime'}</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.total_requests ?? '--'}</div>
            <div className="stat-label">{lang === 'zh' ? '总请求数' : 'Requests'}</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.active_streams ?? 0}</div>
            <div className="stat-label">{lang === 'zh' ? '活跃流' : 'Streams'}</div>
          </div>
          <div className="stat-item">
            <div className={'stat-value ' + ((stats.error_rate || 0) > 10 ? 'danger' : (stats.error_rate || 0) > 3 ? 'orange' : '')}>
              {stats.error_rate ?? '--'}%
            </div>
            <div className="stat-label">{lang === 'zh' ? '错误率' : 'Error Rate'}</div>
          </div>
          <div className="stat-item">
            <div className={'stat-value ' + ((stats.cache_hit_rate || 0) >= 70 ? 'green' : '')}>
              {stats.cache_hit_rate ?? '--'}%
            </div>
            <div className="stat-label">{lang === 'zh' ? '缓存命中' : 'Cache Hit'}</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.total_errors ?? 0}</div>
            <div className="stat-label">{lang === 'zh' ? '错误总数' : 'Total Errors'}</div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>{lang === 'zh' ? '代理开关' : 'Proxy Toggle'}</h2>
        <div className="row">
          <label>Codex</label>
          <label className="toggle">
            <input type="checkbox" checked={!!switchStatus.codex_enabled} onChange={e => handleSwitchToggle('codex', e.target.checked)} />
          </label>
        </div>
        <div className="row">
          <label>Claude</label>
          <label className="toggle">
            <input type="checkbox" checked={!!switchStatus.claude_enabled} onChange={e => handleSwitchToggle('claude', e.target.checked)} />
          </label>
        </div>
        <div className="btn-row" style={{ marginTop: 8 }}>
          <button className="btn copy-btn" style={{ flex: 1, fontSize: 11, padding: '4px 10px' }} onClick={() => handleCopyEnv('codex')}>{lang === 'zh' ? '复制 Codex' : 'Copy Codex'}</button>
          <button className="btn copy-btn" style={{ flex: 1, fontSize: 11, padding: '4px 10px' }} onClick={() => handleCopyEnv('claude')}>{lang === 'zh' ? '复制 Claude' : 'Copy Claude'}</button>
        </div>
        {envText && <div className="env-box" style={{ maxHeight: 60, overflowY: 'auto', marginTop: 8 }}>{envText}</div>}
      </div>

      {stats.top_upstream_errors && (stats.top_upstream_errors as any[]).length > 0 && (
        <div className="card">
          <h2>{lang === 'zh' ? '上游错误' : 'Upstream Errors'}</h2>
          <div style={{ fontSize: 12, color: 'var(--muted)', maxHeight: 150, overflowY: 'auto' }}>
            {(stats.top_upstream_errors as any[]).map((e: any, i: number) => (
              <div key={i} style={{ marginBottom: 4 }}>
                <span style={{ color: 'var(--danger)', fontWeight: 600 }}>{e.count}x</span>{' '}
                {String(e.msg || '').substring(0, 100)}
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h2>{lang === 'zh' ? '最近日志' : 'Recent Logs'}</h2>
        {logs.length === 0
          ? <div style={{ fontSize: 12, color: 'var(--muted)' }}>{lang === 'zh' ? '暂无错误' : 'No errors'}</div>
          : <div id="log-list">
              {logs.map((l, i) => (
                <div className="log-entry" key={i}>
                  <span className="log-time">{new Date((l.ts || 0) * 1000).toLocaleTimeString()}</span>
                  {String(l.msg || '').substring(0, 120)}
                </div>
              ))}
            </div>
        }
      </div>
    </>
  )
}
