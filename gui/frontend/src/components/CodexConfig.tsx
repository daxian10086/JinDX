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
      showToast('Codex Config saved')
      onConfigSaved()
    } catch {
      showToast('Save failed', false)
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

  const Section: React.FC<{ title: string; children: React.ReactNode; defaultCollapsed?: boolean }> = ({ title, children, defaultCollapsed }) => {
    const [collapsed, setCollapsed] = useState(defaultCollapsed ?? false)
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
      <Section title="Upstream Connection">
        <div className="row">
          <label>API Key</label>
          <input type="password" value={form.deepseek_key || ''} onChange={e => set('deepseek_key', e.target.value)} placeholder="sk-..." autoComplete="off" />
          <span className="hint">Shared by Codex and Claude</span>
        </div>
        <div className="row">
          <label>Base URL</label>
          <input type="text" value={form.deepseek_base || ''} onChange={e => set('deepseek_base', e.target.value)} placeholder="https://api.deepseek.com" />
        </div>
        <div className="row">
          <label>Default Model</label>
          <input type="text" value={form.default_model || ''} onChange={e => set('default_model', e.target.value)} placeholder="deepseek-v4-pro" />
        </div>
      </Section>

      <Section title="Model Mapping">
        {modelMapping.map(([k, v], i) => (
          <div className="model-row" key={i}>
            <input placeholder="OpenAI model (e.g. gpt-5.5)" value={k} onChange={e => updateModelRow(i, 'k', e.target.value)} />
            <input placeholder="DeepSeek model (e.g. deepseek-v4-pro)" value={v} onChange={e => updateModelRow(i, 'v', e.target.value)} />
            <button onClick={() => removeModelRow(i)}>X</button>
          </div>
        ))}
        <button id="add-model" onClick={addModelRow}>+ Add Mapping</button>
      </Section>

      <Section title="Generation Params">
        <div className="row">
          <label>Reasoning Effort</label>
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
          <label>Context Window</label>
          <input type="number" min={1024} max={10000000} step={1024} value={form.max_position_embeddings || 1000000} onChange={e => set('max_position_embeddings', parseInt(e.target.value))} />
        </div>
        <div className="row">
          <label>Max Output Tokens</label>
          <input type="number" min={1} max={131072} value={form.max_output_tokens || ''} onChange={e => set('max_output_tokens', parseInt(e.target.value) || null)} placeholder="16384" />
        </div>
        <div className="row">
          <label>Temperature</label>
          <input type="number" step={0.01} min={0} max={2} value={form.temperature != null ? form.temperature : ''} onChange={e => set('temperature', e.target.value ? parseFloat(e.target.value) : null)} placeholder="(unset)" />
        </div>
        <div className="row">
          <label>Top P</label>
          <input type="number" step={0.01} min={0} max={1} value={form.top_p != null ? form.top_p : ''} onChange={e => set('top_p', e.target.value ? parseFloat(e.target.value) : null)} placeholder="(unset)" />
        </div>
      </Section>

      <Section title="Web Fetch">
        <div className="row">
          <label>Max URLs</label>
          <input type="number" min={0} max={50} value={form.web_fetch_max_urls ?? 5} onChange={e => set('web_fetch_max_urls', parseInt(e.target.value))} />
        </div>
        <div className="row">
          <label>Timeout (s)</label>
          <input type="number" min={1} max={120} value={form.web_fetch_timeout ?? 10} onChange={e => set('web_fetch_timeout', parseInt(e.target.value))} />
        </div>
        <div className="row">
          <label>Max Body (bytes)</label>
          <input type="number" min={1000} max={1000000} value={form.web_fetch_max_body ?? 80000} onChange={e => set('web_fetch_max_body', parseInt(e.target.value))} />
        </div>
      </Section>

      <Section title="Reasoning Cache">
        <div className="row">
          <label>Enable Cache</label>
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
        <button className="btn btn-primary" onClick={handleSave}>Save Codex Config</button>
        <button className="btn btn-secondary" onClick={onConfigSaved}>Reload</button>
      </div>
    </>
  )
}
