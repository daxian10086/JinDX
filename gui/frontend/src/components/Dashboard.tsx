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
    try {
      const s = await api.GetStats()
      setStats(s)
    } catch { /* noop */ }
    try {
      const ss = await api.GetSystemStatus()
      setSystemStatus(ss)
    } catch { /* noop */ }
    try {
      const sw = await api.GetProxySwitchStatus()
      setSwitchStatus(sw)
    } catch { /* noop */ }
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
      showToast(`${which === 'codex' ? 'Codex' : 'Claude'} 代理${enabled ? '已启用' : '已停用'}`)
    } catch {
      showToast('操作失败', false)
    }
  }

  const handleCopyEnv = async (mode: 'codex' | 'claude') => {
    if (!api) return
    try {
      const text = await api.GetEnvVars(mode)
      setEnvText(text)
      await navigator.clipboard.writeText(text)
      showToast('已复制到剪贴板')
    } catch {
      showToast('复制失败', false)
    }
  }

  const fmtUptime = (sec: number) => {
    if (sec < 60) return `${sec}s`
    if (sec < 3600) return `${Math.floor(sec / 60)}m`
    if (sec < 86400) return `${Math.floor(sec / 3600)}h ${Math.floor((sec % 3600) / 60)}m`
    return `${Math.floor(sec / 86400)}d ${Math.floor((sec % 86400) / 3600)}h`
  }

  return (
    <>
      <div className="card">
        <h2>实时统计</h2>
        <div className="stat-grid">
          <div className="stat-item">
            <div className="stat-value">{fmtUptime(stats.uptime || 0)}</div>
            <div className="stat-label">运行时间</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.total_requests ?? '--'}</div>
            <div className="stat-label">API 请求数</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">{stats.active_streams ?? 0}</div>
            <div className="stat-label">活跃流</div>
          </div>
          <div className="stat-item">
            <div className={`stat-value ${(stats.error_rate || 0) > 10 ? 'danger' : (stats.error_rate || 0) > 3 ? 'orange' : ''}`}>
              {stats.error_rate ?? '--'}%
            </div>
            <div className="stat-label">API 错误率</div>
          </div>
          <div className="stat-item">
            <div className={`stat-value ${(stats.cache_hit_rate || 0) >= 70 ? 'green' : ''}`}>
              {stats.cache_hit_rate ?? '--'}%
            </div>
            <div className="stat-label">缓存命中率</div>
          </div>
          <div className="stat-item">
            <div className="stat-value">--</div>
            <div className="stat-label">活跃会话</div>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>系统状态</h2>
        <div style={{ fontSize: 12 }}>
          <div style={{ marginBottom: 5 }}>
            <span className={`status-dot ${systemStatus.deepseek === 'connected' ? 'up' : 'down'}`} />
            DeepSeek API：{systemStatus.deepseek || '--'}
          </div>
          <div>
            <span className={`status-dot ${systemStatus.redis === 'connected' ? 'up' : 'down'}`} />
            Redis：{systemStatus.redis || '--'}
          </div>
        </div>
      </div>

      <div className="card">
        <h2>终端环境变量</h2>
        <div className="row" style={{ marginBottom: 6 }}>
          <label>Codex 代理</label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={!!switchStatus.codex_enabled}
              onChange={e => handleSwitchToggle('codex', e.target.checked)}
            />
            <span className="slider" />
          </label>
        </div>
        <div className="row" style={{ marginBottom: 8 }}>
          <label>Claude 代理</label>
          <label className="toggle">
            <input
              type="checkbox"
              checked={!!switchStatus.claude_enabled}
              onChange={e => handleSwitchToggle('claude', e.target.checked)}
            />
            <span className="slider" />
          </label>
        </div>
        <div style={{ marginBottom: 8 }}>
          <button className="btn-copy" onClick={() => handleCopyEnv('codex')}>复制 Codex CLI</button>
          {' '}
          <button className="btn-copy" onClick={() => handleCopyEnv('claude')}>复制 Claude Code</button>
        </div>
        {envText && <div className="env-box">{envText}</div>}
      </div>

      {stats.top_upstream_errors && (stats.top_upstream_errors as any[]).length > 0 && (
        <div className="card">
          <h2>上游错误</h2>
          <div style={{ fontSize: 11, color: 'var(--muted)', maxHeight: 120, overflowY: 'auto' }}>
            {(stats.top_upstream_errors as any[]).map((e: any, i: number) => (
              <div key={i} style={{ marginBottom: 3 }}>
                <span style={{ color: 'var(--danger)' }}>{e.count}x</span>{' '}
                {String(e.msg || '').substring(0, 100)}
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  )
}
