import { useEffect, useState, useCallback } from 'react'
import { showToast } from '../App'

interface Props {
  api: any
  config: Record<string, any>
}

export default function Settings({ api, config }: Props) {
  const [isAdmin, setIsAdmin] = useState(false)
  const [autoStart, setAutoStart] = useState(false)
  const [cacheInfo, setCacheInfo] = useState<Record<string, any>>({})
  const proxyPort = config.PROXY_PORT || '8080'
  const adminPort = config.ADMIN_PORT || '8090'
  const tlsPort = config.TLS_PORT || '8444'
  const connectPort = config.CONNECT_PORT || '8443'

  useEffect(() => {
    if (!api) return
    api.IsAdmin().then(setIsAdmin).catch(() => {})
    api.GetAutoStart().then(setAutoStart).catch(() => {})
    api.GetCacheInfo().then((d: any) => setCacheInfo((d && d.cache) ? d.cache : (d || {}))).catch(() => {})
  }, [api])

  const handleAutoStartToggle = async (enabled: boolean) => {
    if (!api) return
    try {
      await api.ToggleAutoStart(enabled)
      setAutoStart(enabled)
      showToast(enabled ? 'EnabledAuto Start' : 'DisabledAuto Start')
    } catch {
      showToast('Action failed', false)
    }
  }

  const handleClearCache = async (source: string) => {
    if (!api) return
    try {
      await api.ClearCache(source)
      showToast(source ? `已Clear ${source} 缓存` : '已Clear全部缓存')
      api.GetCacheInfo().then((d: any) => setCacheInfo((d && d.cache) ? d.cache : (d || {}))).catch(() => {})
    } catch {
      showToast('Clear失败', false)
    }
  }

  const refreshCache = async () => {
    if (!api) return
    try {
      const d = await api.GetCacheInfo()
      setCacheInfo(d || {})
    } catch { /* */ }
  }

  return (
    <>
      <div className="card">
        <h2>服务端口</h2>
        <div style={{ fontSize: 12 }}>
          <div className="row">
            <label>代理端口 (HTTP/WS)</label>
            <code style={{ fontFamily: 'monospace' }}>{proxyPort}</code>
          </div>
          <div className="row">
            <label>管理面板</label>
            <code style={{ fontFamily: 'monospace' }}>{adminPort}</code>
          </div>
          <div className="row">
            <label>TLS 直连</label>
            <code style={{ fontFamily: 'monospace' }}>{tlsPort}</code>
          </div>
          <div className="row">
            <label>CONNECT 隧道</label>
            <code style={{ fontFamily: 'monospace' }}>{connectPort}</code>
          </div>
        </div>
      </div>

      <div className="card">
        <h2>系统设置</h2>
        <div className="row">
          <label>Auto Start</label>
          <label className="toggle">
            <input type="checkbox" checked={autoStart} onChange={e => handleAutoStartToggle(e.target.checked)} />
            <span className="slider" />
          </label>
        </div>
        <div className="row">
          <label>管理员权限</label>
          <span style={{ fontSize: 12, color: isAdmin ? 'var(--green)' : 'var(--orange)' }}>
            {isAdmin ? '已获取' : '未获取 (hosts/端口转发功能受限)'}
          </span>
        </div>
      </div>

      <div className="card">
        <h2>推理缓存</h2>
        <div style={{ fontSize: 12, marginBottom: 8 }}>
          <div>文件数量: <strong>{cacheInfo.file_count ?? '--'}</strong></div>
          <div>文件大小: <strong>{cacheInfo.file_size_str ?? '--'}</strong></div>
          <div>内存entries: <strong>{cacheInfo.memory_count ?? '--'}</strong></div>
        </div>
        <div style={{ display: 'flex', gap: 6 }}>
          <button className="btn btn-secondary" style={{ fontSize: 11 }} onClick={() => handleClearCache('')}>Clear全部</button>
          <button className="btn btn-secondary" style={{ fontSize: 11 }} onClick={() => handleClearCache('codex')}>Clear Codex</button>
          <button className="btn btn-secondary" style={{ fontSize: 11 }} onClick={() => handleClearCache('claude')}>Clear Claude</button>
          <button className="btn btn-secondary" style={{ fontSize: 11 }} onClick={refreshCache}>刷新</button>
        </div>
      </div>
    </>
  )
}
