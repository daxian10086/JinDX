import { useState, useEffect, useCallback, createContext, useContext } from 'react'
import CodexConfig from './components/CodexConfig'
import ClaudeConfig from './components/ClaudeConfig'
import Settings from './components/Settings'
import RightPanel from './components/RightPanel'
import { App as AppBindings } from './bindings/index.js'

type Tab = 'codex' | 'claude' | 'settings'
type Lang = 'zh' | 'en'

const LANG_KEY = 'jindx_gui_lang'

// i18n translations
const TXT: Record<string, Record<Lang, string>> = {
  title: { zh: 'JinDX 代理管理', en: 'JinDX Proxy Manager' },
  codex: { zh: 'Codex', en: 'Codex' },
  claude: { zh: 'Claude', en: 'Claude' },
  settings: { zh: 'Settings', en: 'Settings' },
  port: { zh: '端口:', en: 'Port:' },
  start: { zh: '启动', en: 'Start' },
  stop: { zh: '停止', en: 'Stop' },
  restart: { zh: '重启', en: 'Restart' },
  running: { zh: '运行中', en: 'Running' },
  starting: { zh: '启动中...', en: 'Starting...' },
  stopped: { zh: '已停止', en: 'Stopped' },
  started: { zh: '已启动', en: 'Started' },
  stoppedOk: { zh: '已停止', en: 'Stopped' },
  restarted: { zh: '已重启', en: 'Restarted' },
  noKey: { zh: '未配置 API Key', en: 'No API Key configured' },
  error: { zh: '错误', en: 'Error' },
  langLabel: { zh: 'English', en: '中文' },
}

export const t = (key: string): string => {
  return TXT[key]?.[currentLang] ?? key
}

let currentLang: Lang = 'zh'

export const setLang = (l: Lang) => { currentLang = l }

export function useLang() {
  const [lang, setLangState] = useState<Lang>(
    () => (localStorage.getItem(LANG_KEY) as Lang) || 'zh'
  )
  useEffect(() => {
    currentLang = lang
    localStorage.setItem(LANG_KEY, lang)
  }, [lang])
  return { lang, setLang: setLangState }
}

export const LangContext = createContext<Lang>('zh')

export default function App() {
  const [activeTab, setActiveTab] = useState<Tab>('codex')
  const [proxyStatus, setProxyStatus] = useState('stopped')
  const [config, setConfig] = useState<Record<string, any>>({})
  const { lang, setLang } = useLang()

  const refreshStatus = useCallback(async () => {
    try { setProxyStatus(await AppBindings.GetProxyStatus() || 'stopped') } catch { /* */ }
  }, [])

  const loadConfig = useCallback(async () => {
    try { setConfig(await AppBindings.GetConfig() || {}) } catch { /* */ }
  }, [])

  useEffect(() => {
    refreshStatus(); loadConfig()
    const t = setInterval(refreshStatus, 5000)
    return () => clearInterval(t)
  }, [refreshStatus, loadConfig])

  useEffect(() => {
    if (!config || !Object.keys(config).length) return
    const key = config.deepseek_key || ''
    if (!key || key === 'sk-your-deepseek-api-key' || !key.startsWith('sk-'))
      showToast(t('noKey'), false)
  }, [config])

  const handleProxyAction = async (action: 'start' | 'stop' | 'restart') => {
    try {
      if (action === 'start') await AppBindings.StartProxy()
      else if (action === 'stop') await AppBindings.StopProxy()
      else if (action === 'restart') { await AppBindings.StopProxy(); await new Promise(r => setTimeout(r, 1000)); await AppBindings.StartProxy() }
      refreshStatus()
      showToast(action === 'start' ? t('started') : action === 'stop' ? t('stoppedOk') : t('restarted'))
    } catch (e: any) { showToast(t('error') + ': ' + (e?.message || e), false) }
  }

  const tabs: { id: Tab; key: string }[] = [
    { id: 'codex', key: 'codex' },
    { id: 'claude', key: 'claude' },
    { id: 'settings', key: 'settings' },
  ]

  const statusText = proxyStatus === 'running' ? t('running') : proxyStatus === 'starting' ? t('starting') : t('stopped')
  const adminPort = config.ADMIN_PORT || '8090'

  return (
    <LangContext.Provider value={lang}>
      <div id="topbar">
        <h1>
          <span className={`dot ${proxyStatus}`} />
          {t('title')}
        </h1>
        <div className="topbar-right">
          <span style={{ fontSize: 12, color: 'var(--muted)' }}>{t('port')}</span>
          <span style={{ fontSize: 12, color: 'var(--accent)', fontFamily: 'monospace' }}>:{adminPort}</span>
          <span className={`status-tag ${proxyStatus !== 'running' ? 'stopped' : ''}`}>{statusText}</span>
          <button className="primary" onClick={() => handleProxyAction('start')}>{t('start')}</button>
          <button className="danger" onClick={() => handleProxyAction('stop')}>{t('stop')}</button>
          <button onClick={() => handleProxyAction('restart')}>{t('restart')}</button>
          <button id="lang-btn" onClick={() => setLang(lang === 'zh' ? 'en' : 'zh')} style={{ padding: '4px 12px', border: '1px solid var(--border)', borderRadius: 4, background: 'var(--input-bg)', color: 'var(--fg)', cursor: 'pointer', fontSize: 12 }}>{t('langLabel')}</button>
        </div>
      </div>

      <div id="tab-bar">
        {tabs.map(tab => (
          <button key={tab.id} className={`tab-btn ${activeTab === tab.id ? 'active' : ''}`} onClick={() => setActiveTab(tab.id)}>{t(tab.key)}</button>
        ))}
      </div>

      <div id="main">
        <div id="left">
          {activeTab === 'codex' && <CodexConfig config={config} onConfigSaved={loadConfig} />}
          {activeTab === 'claude' && <ClaudeConfig config={config} onConfigSaved={loadConfig} />}
          {activeTab === 'settings' && <Settings config={config} />}
        </div>
        <div id="right">
          <RightPanel />
        </div>
      </div>
      <div id="toast" />
    </LangContext.Provider>
  )
}

export function showToast(msg: string, ok = true) {
  const el = document.getElementById('toast')
  if (!el) return
  el.textContent = msg; el.className = (ok ? 'ok' : 'err') + ' show'
  setTimeout(() => el.classList.remove('show'), 2500)
}
