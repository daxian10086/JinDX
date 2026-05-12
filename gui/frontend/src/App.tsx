import { useState, useEffect, useCallback } from 'react'
import Dashboard from './components/Dashboard'
import CodexConfig from './components/CodexConfig'
import ClaudeConfig from './components/ClaudeConfig'
import LogViewer from './components/LogViewer'

type Tab = 'codex' | 'claude' | 'logs'

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('codex')
  const [proxyStatus, setProxyStatus] = useState('stopped')
  const [config, setConfig] = useState<Record<string, any>>({})

  const api = window.go?.main?.App

  const refreshStatus = useCallback(async () => {
    if (!api) return
    try {
      const s = await api.GetProxyStatus()
      setProxyStatus(s)
    } catch { /* Wails 不可用 */ }
  }, [api])

  const loadConfig = useCallback(async () => {
    if (!api) return
    try {
      const cfg = await api.GetConfig()
      setConfig(cfg)
    } catch { /* Wails 不可用 */ }
  }, [api])

  useEffect(() => {
    refreshStatus()
    loadConfig()
    const timer = setInterval(refreshStatus, 5000)
    return () => clearInterval(timer)
  }, [refreshStatus, loadConfig])

  const handleProxyAction = async (action: 'start' | 'stop' | 'restart') => {
    if (!api) return
    try {
      if (action === 'start') await api.StartProxy()
      else if (action === 'stop') await api.StopProxy()
      else if (action === 'restart') {
        await api.StopProxy()
        await new Promise(r => setTimeout(r, 1000))
        await api.StartProxy()
      }
      refreshStatus()
    } catch (e) {
      console.error(e)
    }
  }

  return (
    <>
      <div id="topbar">
        <h1>
          <span className={`dot ${proxyStatus === 'running' ? '' : proxyStatus === 'starting' ? 'starting' : 'stopped'}`} />
          JinDX Proxy
        </h1>
        <div className="topbar-right">
          <span className="stat-label" style={{ color: proxyStatus === 'running' ? 'var(--green)' : proxyStatus === 'starting' ? 'var(--orange)' : 'var(--muted)' }}>
            {proxyStatus === 'running' ? '运行中' : proxyStatus === 'starting' ? '启动中...' : '已停止'}
          </span>
          <button className="primary" onClick={() => handleProxyAction('start')}>启动</button>
          <button className="danger" onClick={() => handleProxyAction('stop')}>停止</button>
          <button onClick={() => handleProxyAction('restart')}>重启</button>
        </div>
      </div>

      <div id="tab-bar">
        <button className={`tab-btn ${activeTab === 'codex' ? 'active' : ''}`} onClick={() => setActiveTab('codex')}>Codex 代理</button>
        <button className={`tab-btn ${activeTab === 'claude' ? 'active' : ''}`} onClick={() => setActiveTab('claude')}>Claude Code</button>
        <button className={`tab-btn ${activeTab === 'logs' ? 'active' : ''}`} onClick={() => setActiveTab('logs')}>日志</button>
      </div>

      <div id="main">
        <div id="left">
          {activeTab === 'codex' && <CodexConfig config={config} api={api} onConfigSaved={loadConfig} />}
          {activeTab === 'claude' && <ClaudeConfig config={config} api={api} onConfigSaved={loadConfig} />}
          {activeTab === 'logs' && <LogViewer api={api} />}
        </div>
        {activeTab !== 'logs' && (
          <div id="right">
            <Dashboard api={api} />
          </div>
        )}
      </div>

      <div id="toast" />
    </>
  )
}

// Toast helper (global)
export function showToast(msg: string, ok = true) {
  const el = document.getElementById('toast')
  if (!el) return
  el.textContent = msg
  el.className = (ok ? 'ok' : 'err') + ' show'
  setTimeout(() => el.classList.remove('show'), 2200)
}
