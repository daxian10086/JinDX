import { useState, useEffect, useCallback } from 'react'
import { showToast } from '../App'

interface Props {
  config: Record<string, any>
  api: any
  onConfigSaved: () => void
}

export default function CodexConfig({ config, api, onConfigSaved }: Props) {
  const [form, setForm] = useState<Record<string, any>>({})
  const [modelMapping, setModelMapping] = useState<Array<[string, string]>>([])

  useEffect(() => {
    setForm({ ...config })
    const map = config.model_mapping || {}
    const entries: Array<[string, string]> = Object.entries(map)
    setModelMapping(entries.length > 0 ? entries : [['', '']])
  }, [config])

  const handleSave = async () => {
    if (!api) return
    const mapping: Record<string, string> = {}
    modelMapping.forEach(([k, v]) => {
      if (k.trim() && v.trim()) mapping[k.trim()] = v.trim()
    })

    const payload = {
      ...form,
      model_mapping: mapping,
    }

    try {
      await api.SaveConfig(payload)
      showToast('Codex 配置已保存')
      onConfigSaved()
    } catch {
      showToast('保存失败', false)
    }
  }

  const addModelRow = () => setModelMapping([...modelMapping, ['', '']])

  const updateModelRow = (index: number, key: 'k' | 'v', value: string) => {
    const next = [...modelMapping] as Array<[string, string]>
    if (key === 'k') next[index][0] = value
    else next[index][1] = value
    setModelMapping(next)
  }

  const removeModelRow = (index: number) => {
    setModelMapping(modelMapping.filter((_, i) => i !== index))
  }

  const set = (key: string, value: any) => setForm({ ...form, [key]: value })

  const Section: React.FC<{ title: string; children: React.ReactNode }> = ({ title, children }) => {
    const [collapsed, setCollapsed] = useState(false)
    return (
      <div className="card">
        <h2 className={collapsed ? 'collapsed' : ''} onClick={() => setCollapsed(!collapsed)}>
          <span style={{ fontSize: 12 }}>{collapsed ? '▶' : '▼'}</span> {title}
        </h2>
        <div className={`section-body${collapsed ? ' collapsed' : ''}`}>
          {children}
        </div>
      </div>
    )
  }

  return (
    <>
      <Section title="上游连接">
        <div className="row">
          <label>API Key</label>
          <input type="password" value={form.deepseek_key || ''} onChange={e => set('deepseek_key', e.target.value)} placeholder="sk-..." autoComplete="off" />
          <span className="hint">Codex 和 Claude 共用此 Key</span>
        </div>
        <div className="row">
          <label>Base URL</label>
          <input type="text" value={form.deepseek_base || ''} onChange={e => set('deepseek_base', e.target.value)} placeholder="https://api.deepseek.com" />
        </div>
        <div className="row">
          <label>默认模型</label>
          <input type="text" value={form.default_model || ''} onChange={e => set('default_model', e.target.value)} placeholder="deepseek-v4-pro" />
        </div>
      </Section>

      <Section title="模型映射">
        {modelMapping.map(([k, v], i) => (
          <div className="model-row" key={i}>
            <input placeholder="OpenAI model (e.g. gpt-5.5)" value={k} onChange={e => updateModelRow(i, 'k', e.target.value)} />
            <input placeholder="DeepSeek model (e.g. deepseek-v4-pro)" value={v} onChange={e => updateModelRow(i, 'v', e.target.value)} />
            <button onClick={() => removeModelRow(i)}>X</button>
          </div>
        ))}
        <button id="add-model" onClick={addModelRow}>+ 添加映射</button>
      </Section>

      <Section title="生成参数">
        <div className="row">
          <label>推理强度</label>
          <select value={form.reasoning_effort || ''} onChange={e => set('reasoning_effort', e.target.value || null)}>
            <option value="">(由 DeepSeek 决定)</option>
            <option value="min">min</option>
            <option value="low">low</option>
            <option value="medium">medium</option>
            <option value="high">high</option>
            <option value="max">max</option>
          </select>
        </div>
        <div className="row">
          <label>上下文窗口</label>
          <input type="number" min={1024} max={10000000} step={1024} value={form.max_position_embeddings || 1000000} onChange={e => set('max_position_embeddings', parseInt(e.target.value))} />
        </div>
        <div className="row">
          <label>最大输出 Tokens</label>
          <input type="number" min={1} max={131072} value={form.max_output_tokens || ''} onChange={e => set('max_output_tokens', parseInt(e.target.value) || null)} placeholder="16384" />
        </div>
        <div className="row">
          <label>温度</label>
          <input type="number" step={0.01} min={0} max={2} value={form.temperature != null ? form.temperature : ''} onChange={e => set('temperature', e.target.value ? parseFloat(e.target.value) : null)} placeholder="(unset)" />
        </div>
        <div className="row">
          <label>Top P</label>
          <input type="number" step={0.01} min={0} max={1} value={form.top_p != null ? form.top_p : ''} onChange={e => set('top_p', e.target.value ? parseFloat(e.target.value) : null)} placeholder="(unset)" />
        </div>
      </Section>

      <Section title="网页抓取">
        <div className="row">
          <label>最大 URL 数</label>
          <input type="number" min={0} max={50} value={form.web_fetch_max_urls ?? 5} onChange={e => set('web_fetch_max_urls', parseInt(e.target.value))} />
        </div>
        <div className="row">
          <label>超时 (秒)</label>
          <input type="number" min={1} max={120} value={form.web_fetch_timeout ?? 10} onChange={e => set('web_fetch_timeout', parseInt(e.target.value))} />
        </div>
        <div className="row">
          <label>最大响应体 (字节)</label>
          <input type="number" min={1000} max={1000000} value={form.web_fetch_max_body ?? 80000} onChange={e => set('web_fetch_max_body', parseInt(e.target.value))} />
        </div>
      </Section>

      <Section title="推理缓存">
        <div className="row">
          <label>启用缓存</label>
          <label className="toggle">
            <input type="checkbox" checked={!!form.enable_reasoning_cache} onChange={e => set('enable_reasoning_cache', e.target.checked)} />
            <span className="slider" />
          </label>
        </div>
        <div className="row">
          <label>Cache TTL (s)</label>
          <input type="number" min={30} max={86400} value={form.reasoning_cache_ttl ?? 600} onChange={e => set('reasoning_cache_ttl', parseInt(e.target.value))} />
        </div>
      </Section>

      <div className="btn-row">
        <button className="btn btn-primary" onClick={handleSave}>保存 Codex 配置</button>
        <button className="btn btn-secondary" onClick={onConfigSaved}>重新加载</button>
      </div>
    </>
  )
}
