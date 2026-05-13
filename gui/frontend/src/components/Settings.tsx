import { useEffect, useState, useCallback } from 'react'
import { showToast } from '../App'

interface Props { api: any; config: Record<string, any> }

export default function Settings({ api, config }: Props) {
  const [isAdmin, setIsAdmin] = useState(false)
  const [autoStart, setAutoStart] = useState(false)
  const [cacheInfo, setCacheInfo] = useState<Record<string, any>>({})

  const refresh = useCallback(async () => {
    if (!api) return
    try { setIsAdmin(await api.IsAdmin()) } catch { /* */ }
    try { setAutoStart(await api.GetAutoStart()) } catch { /* */ }
    try { const d = await api.GetCacheInfo(); setCacheInfo(d || {}) } catch { /* */ }
  }, [api])

  useEffect(() => { refresh() }, [refresh])

  const handleAutoStartToggle = async (enabled: boolean) => {
    if (!api) return
    try { await api.ToggleAutoStart(enabled); setAutoStart(enabled); showToast(enabled ? '已启用开机自启' : '已禁用开机自启') }
    catch { showToast('操作失败', false) }
  }

  const handleClearCache = async (source: string) => {
    if (!api) return
    try { await api.ClearCache(source); showToast(source ? `已清空 ${source} 缓存` : '已清空全部缓存'); refresh() }
    catch { showToast('清空失败', false) }
  }

  return (<>
    <div className="card">
      <h2>服务端口</h2>
      <div style={{ fontSize: 12 }}>
        <div className="row"><label>代理端口 (HTTP/WS)</label><code style={{ fontFamily: 'monospace' }}>{config.PROXY_PORT || '8080'}</code></div>
        <div className="row"><label>管理面板</label><code style={{ fontFamily: 'monospace' }}>{config.ADMIN_PORT || '8090'}</code></div>
        <div className="row"><label>TLS 直连</label><code style={{ fontFamily: 'monospace' }}>{config.TLS_PORT || '8444'}</code></div>
        <div className="row"><label>CONNECT 隧道</label><code style={{ fontFamily: 'monospace' }}>{config.CONNECT_PORT || '8443'}</code></div>
      </div>
    </div>

    <div className="card">
      <h2>系统设置</h2>
      <div className="row">
        <label>开机自启</label>
        <label className="toggle"><input type="checkbox" checked={autoStart} onChange={e => handleAutoStartToggle(e.target.checked)} /><span className="slider" /></label>
      </div>
      <div className="row">
        <label>管理员权限</label>
        <span style={{ fontSize: 12, color: isAdmin ? 'var(--green)' : 'var(--orange)' }}>{isAdmin ? '已获取' : '未获取 (hosts/端口转发功能受限)'}</span>
      </div>
    </div>

    <div className="card">
      <h2>推理缓存</h2>
      <div style={{ fontSize: 12, marginBottom: 10 }}>
        <div>文件数量: <strong>{cacheInfo.file_count ?? '--'}</strong></div>
        <div>文件大小: <strong>{cacheInfo.file_size_str ?? '--'}</strong></div>
        <div>内存条目: <strong>{cacheInfo.memory_count ?? '--'}</strong></div>
      </div>
      <div className="btn-row">
        <button className="btn" onClick={() => handleClearCache('')}>清空全部</button>
        <button className="btn" onClick={() => handleClearCache('codex')}>清空 Codex</button>
        <button className="btn" onClick={() => handleClearCache('claude')}>清空 Claude</button>
        <button className="btn" onClick={refresh}>刷新</button>
      </div>
    </div>
  </>)
}
