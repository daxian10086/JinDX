import { useEffect, useState, useCallback } from 'react'

interface Props {
  api: any
}

export default function LogViewer({ api }: Props) {
  const [logs, setLogs] = useState<Array<Record<string, any>>>([])

  const refresh = useCallback(async () => {
    if (!api) return
    try {
      const data = await api.GetLogs(50)
      setLogs(data || [])
    } catch { /* noop */ }
  }, [api])

  useEffect(() => {
    refresh()
    const timer = setInterval(refresh, 10000)
    return () => clearInterval(timer)
  }, [refresh])

  return (
    <div className="card" style={{ height: '100%', overflow: 'auto' }}>
      <h2>最近日志</h2>
      {logs.length === 0 ? (
        <div style={{ fontSize: 12, color: 'var(--muted)' }}>暂无错误日志</div>
      ) : (
        <div id="log-list" style={{ maxHeight: 'none' }}>
          {logs.map((l, i) => (
            <div className="log-entry" key={i}>
              <span className="log-time">
                {new Date((l.ts || 0) * 1000).toLocaleTimeString()}
              </span>
              {String(l.msg || '')}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
