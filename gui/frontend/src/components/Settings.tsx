import { useEffect, useState, useCallback, useContext } from 'react'
import { showToast, LangContext } from '../App'
import { App as AppBindings } from '../bindings/index.js'

interface Props { config: Record<string, any> }

export default function Settings({ config }: Props) {
  const [isAdmin, setIsAdmin] = useState(false)
  const [autoStart, setAutoStart] = useState(false)
  const [cacheInfo, setCacheInfo] = useState<Record<string, any>>({})
  const lang = useContext(LangContext)

  const refresh = useCallback(async () => {
    try { setIsAdmin(await AppBindings.IsAdmin()) } catch { /* */ }
    try { setAutoStart(await AppBindings.GetAutoStart()) } catch { /* */ }
    try { const d = await AppBindings.GetCacheInfo(); setCacheInfo(d || {}) } catch { /* */ }
  }, [])

  useEffect(() => { refresh() }, [refresh])

  const handleAutoStartToggle = async (enabled: boolean) => {
    try { await AppBindings.ToggleAutoStart(enabled); setAutoStart(enabled); showToast(enabled ? (lang === 'zh' ? '已启用开机自启' : 'Auto-start enabled') : (lang === 'zh' ? '已禁用开机自启' : 'Auto-start disabled')) }
    catch { showToast(lang === 'zh' ? '操作失败' : 'Operation failed', false) }
  }

  const handleClearCache = async (source: string) => {
    try { await AppBindings.ClearCache(source); showToast(source ? `${lang === 'zh' ? '已清空' : 'Cleared'} ${source} ${lang === 'zh' ? '缓存' : 'cache'}` : (lang === 'zh' ? '已清空全部缓存' : 'Cleared all cache')); refresh() }
    catch { showToast(lang === 'zh' ? '清空失败' : 'Clear failed', false) }
  }

  return (<>
    <div className="card">
      <h2>{lang === 'zh' ? '服务端口' : 'Service Ports'}</h2>
      <div style={{ fontSize: 12 }}>
        <div className="row"><label>{lang === 'zh' ? '代理端口 (HTTP/WS)' : 'Proxy Port (HTTP/WS)'}</label><code style={{ fontFamily: 'monospace' }}>{config.PROXY_PORT || '8080'}</code></div>
        <div className="row"><label>{lang === 'zh' ? '管理面板' : 'Admin Panel'}</label><code style={{ fontFamily: 'monospace' }}>{config.ADMIN_PORT || '8090'}</code></div>
        <div className="row"><label>{lang === 'zh' ? 'TLS 直连' : 'TLS Direct'}</label><code style={{ fontFamily: 'monospace' }}>{config.TLS_PORT || '8444'}</code></div>
        <div className="row"><label>{lang === 'zh' ? 'CONNECT 隧道' : 'CONNECT Tunnel'}</label><code style={{ fontFamily: 'monospace' }}>{config.CONNECT_PORT || '8443'}</code></div>
      </div>
    </div>

    <div className="card">
      <h2>{lang === 'zh' ? '系统设置' : 'System'}</h2>
      <div className="row">
        <label>{lang === 'zh' ? '开机自启' : 'Auto Start'}</label>
        <label className="toggle"><input type="checkbox" checked={autoStart} onChange={e => handleAutoStartToggle(e.target.checked)} /></label>
      </div>
      <div className="row">
        <label>{lang === 'zh' ? '管理员权限' : 'Admin Rights'}</label>
        <span style={{ fontSize: 12, color: isAdmin ? 'var(--green)' : 'var(--orange)' }}>{isAdmin ? (lang === 'zh' ? '已获取' : 'Granted') : (lang === 'zh' ? '未获取 (hosts/端口转发功能受限)' : 'Not granted (hosts/forwarding limited)')}</span>
      </div>
    </div>

    <div className="card">
      <h2>{lang === 'zh' ? '推理缓存' : 'Reasoning Cache'}</h2>
      <div style={{ fontSize: 12, marginBottom: 10 }}>
        <div>{lang === 'zh' ? '文件数量' : 'Files'}: <strong>{cacheInfo.file_count ?? '--'}</strong></div>
        <div>{lang === 'zh' ? '文件大小' : 'Size'}: <strong>{cacheInfo.file_size_str ?? '--'}</strong></div>
        <div>{lang === 'zh' ? '内存条目' : 'Memory entries'}: <strong>{cacheInfo.memory_count ?? '--'}</strong></div>
      </div>
      <div className="btn-row">
        <button className="btn" onClick={() => handleClearCache('')}>{lang === 'zh' ? '清空全部' : 'Clear All'}</button>
        <button className="btn" onClick={() => handleClearCache('codex')}>{lang === 'zh' ? '清空 Codex' : 'Clear Codex'}</button>
        <button className="btn" onClick={() => handleClearCache('claude')}>{lang === 'zh' ? '清空 Claude' : 'Clear Claude'}</button>
        <button className="btn" onClick={refresh}>{lang === 'zh' ? '刷新' : 'Refresh'}</button>
      </div>
    </div>
  </>)
}
