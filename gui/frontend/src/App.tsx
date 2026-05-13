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
    try { setProxyStatus(await api.GetProxyStatus()) } catch { /* */ }
  }, [api])

  const loadConfig = useCallback(async () => {
    if (!api) return
    try { setConfig(await api.GetConfig()) } catch { /* */ }
  }, [api])

  useEffect(() => {
    refreshStatus(); loadConfig()
    const t = setInterval(refreshStatus, 5000)
    return () => clearInterval(t)
  }, [refreshStatus, loadConfig])

  useEffect(() => {
    if (!api) { showToast('Wails 桥接未就绪，请重启应用', false); return }
    if (!config || !Object.keys(config).length) return
    const key = config.deepseek_key || ''
    if (!key || key === 'sk-your-deepseek-api-key' || !key.startsWith('sk-'))
      showToast('未配置 API Key，请前往 Codex 或 Claude 标签页设置', false)
  }, [config, api])

  const handleProxyAction = async (action: 'start' | 'stop' | 'restart') => {
    if (!api) return
    try {
      if (action === 'start') {
        const r = await api.StartProxy()
        if (r === 'stopped') { showToast('启动失败', false); return }
      } else if (action === 'stop') await api.StopProxy()
      else if (action === 'restart') { await api.StopProxy(); await new Promise(r => setTimeout(r, 1000)); await api.StartProxy() }
      refreshStatus()
      showToast(action === 'start' ? '代理已启动' : action === 'stop' ? '代理已停止' : '代理已重启')
    } catch (e: any) { showToast('操作失败: ' + (e?.message || e), false) }
  }

  const tabs: { id: Tab; label: string }[] = [
    { id: 'overview', label: '概览' },
    { id: 'codex', label: 'Codex 代理' },
    { id: 'claude', label: 'Claude 代理' },
    { id: 'logs', label: '日志' },
    { id: 'settings', label: '设置' },
  ]

  const statusText = proxyStatus === 'running' ? '运行中' : proxyStatus === 'starting' ? '启动中...' : '已停止'

  return (
    <>
      <div id="topbar">
        <h1>
          <span className={`dot ${proxyStatus}`} />
          JinDX Proxy
        </h1>
        <div className="topbar-right">
          <span className={`status-tag ${proxyStatus !== 'running' ? 'stopped' : ''}`}>{statusText}</span>
          <button className="primary" onClick={() => handleProxyAction('start')}>启动</button>
          <button className="danger" onClick={() => handleProxyAction('stop')}>停止</button>
          <button onClick={() => handleProxyAction('restart')}>重启</button>
        </div>
      </div>

      <div id="tab-bar">
        {tabs.map(t => (
          <button key={t.id} className={`tab-btn ${activeTab === t.id ? 'active' : ''}`} onClick={() => setActiveTab(t.id)}>{t.label}</button>
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
          <div id="right"><Dashboard api={api} /></div>
        )}
      </div>
      <div id="toast" />
    </>
  )
}

export function showToast(msg: string, ok = true) {
  const el = document.getElementById('toast')
  if (!el) return
  el.textContent = msg; el.className = (ok ? 'ok' : 'err') + ' show'
  setTimeout(() => el.classList.remove('show'), 2500)
}
