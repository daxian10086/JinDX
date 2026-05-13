import { useState, useEffect, useCallback } from 'react'
import Dashboard from './components/Dashboard'
import CodexConfig from './components/CodexConfig'
import ClaudeConfig from './components/ClaudeConfig'
import LogViewer from './components/LogViewer'
import Settings from './components/Settings'

type Tab = 'overview' | 'codex' | 'claude' | 'logs' | 'settings'

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('overview')
  const [proxyStatus, setProxyStatus] = useState('stopped')
  const [config, setConfig] = useState<Record<string, any>>({})

  const api = window.go?.main?.App

  const refreshStatus = useCallback(async () => {
    if (!api) return
    try {
      const s = await api.GetProxyStatus()
      setProxyStatus(s)
    } catch { /* */ }
  }, [api])

  const loadConfig = useCallback(async () => {
    if (!api) return
    try {
      const cfg = await api.GetConfig()
      setConfig(cfg)
    } catch { /* */ }
  }, [api])

  useEffect(() => {
    refreshStatus()
    loadConfig()
    const timer = setInterval(refreshStatus, 5000)
    return () => clearInterval(timer)
  }, [refreshStatus, loadConfig])

  // Warn if no API key configured on first load
  useEffect(() => {
    if (!config || !Object.keys(config).length) return
    const key = config.deepseek_key || ''
    if (!key || key === 'sk-your-deepseek-api-key' || !key.startsWith('sk-')) {
      showToast('No DeepSeek API Key configured. Go to Codex Proxy tab.', false)
    }
  }, [config])

  // Check Wails bridge availability
  useEffect(() => {
    if (!api) {
      showToast('Wails bridge not ready. Restart GUI.', false)
    }
  }, [api])

  const handleProxyAction = async (action: 'start' | 'stop' | 'restart') => {
    if (!api) return
    try {
      if (action === 'start') {
        const result = await api.StartProxy()
        if (result === 'stopped') {
          showToast('Start failed', false)
          return
        }
      }
      else if (action === 'stop') await api.StopProxy()
      else if (action === 'restart') {
        await api.StopProxy()
        await new Promise(r => setTimeout(r, 1000))
        await api.StartProxy()
      }
      refreshStatus()
      if (action === 'start') showToast('?????')
      else if (action === 'stop') showToast('?????')
    } catch (e: any) {
      showToast('????: ' + (e?.message || e), false)
    }
  }

  const tabDefs: { id: Tab; label: string }[] = [
    { id: 'overview', label: 'Overview' },
    { id: 'codex', label: 'Codex Proxy' },
    { id: 'claude', label: 'Claude Code' },
    { id: 'logs', label: 'Logs' },
    { id: 'settings', label: 'Settings' },
  ]

  return (
    <>
      <div id="topbar">
        <h1>
          <span className={`dot ${proxyStatus === 'running' ? '' : proxyStatus === 'starting' ? 'starting' : 'stopped'}`} />
          JinDX Proxy
        </h1>
        <div className="topbar-right">
          <span className="stat-label" style={{ color: proxyStatus === 'running' ? 'var(--green)' : proxyStatus === 'starting' ? 'var(--orange)' : 'var(--muted)' }}>
            {proxyStatus === 'running' ? 'Running' : proxyStatus === 'starting' ? 'Starting...' : 'Stopped'}
          </span>
          <button className="primary" onClick={() => handleProxyAction('start')}>Start</button>
          <button className="danger" onClick={() => handleProxyAction('stop')}>Stop</button>
          <button onClick={() => handleProxyAction('restart')}>Restart</button>
        </div>
      </div>

      <div id="tab-bar">
        {tabDefs.map(t => (
          <button
            key={t.id}
            className={`tab-btn ${activeTab === t.id ? 'active' : ''}`}
            onClick={() => setActiveTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div id="main">
        <div id="left">
          {activeTab === 'overview' && <Dashboard api={api} />}
          {activeTab === 'codex' && <CodexConfig config={config} api={api} onConfigSaved={loadConfig} />}
          {activeTab === 'claude' && <ClaudeConfig config={config} api={api} onConfigSaved={loadConfig} />}
          {activeTab === 'logs' && <LogViewer api={api} />}
          {activeTab === 'settings' && <Settings api={api} config={config} />}
        </div>
        {(activeTab === 'codex' || activeTab === 'claude') && (
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
