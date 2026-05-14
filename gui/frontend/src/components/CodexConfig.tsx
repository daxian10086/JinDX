import { useState, useEffect } from 'react'
import { showToast, LangContext } from '../App'
import { App as AppBindings } from '../bindings/index.js'
import { useContext } from 'react'

interface Props { config: Record<string, any>; onConfigSaved: () => void }

export default function CodexConfig({ config, onConfigSaved }: Props) {
  const [form, setForm] = useState<Record<string, any>>({})
  const [modelMapping, setModelMapping] = useState<Array<[string, string]>>([])
  const lang = useContext(LangContext)

  useEffect(() => {
    setForm({ ...config })
    const map = config.model_mapping || {}
    const entries: Array<[string, string]> = Object.entries(map)
    setModelMapping(entries.length > 0 ? entries : [['', '']])
  }, [config])

  const handleSave = async () => {
    const mapping: Record<string, string> = {}
    modelMapping.forEach(([k, v]) => { if (k.trim() && v.trim()) mapping[k.trim()] = v.trim() })
    try { await AppBindings.SaveConfig({ ...form, model_mapping: mapping }); showToast(lang === 'zh' ? 'Codex 配置已保存' : 'Codex config saved'); onConfigSaved() }
    catch { showToast(lang === 'zh' ? '保存失败' : 'Save failed', false) }
  }

  const addModelRow = () => setModelMapping([...modelMapping, ['', '']])
  const updateModelRow = (i: number, key: 'k' | 'v', val: string) => { const n = [...modelMapping] as Array<[string, string]>; n[i][key === 'k' ? 0 : 1] = val; setModelMapping(n) }
  const removeModelRow = (i: number) => setModelMapping(modelMapping.filter((_, j) => j !== i))
  const set = (k: string, v: any) => setForm({ ...form, [k]: v })

  const Section = ({ title, children, defaultOpen }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) => {
    const [open, setOpen] = useState(defaultOpen ?? true)
    return (<div className="card"><h2 onClick={() => setOpen(!open)}><span className={`arrow ${open ? 'open' : ''}`}>&#9654;</span>{title}</h2><div className={`section-body${open ? '' : ' collapsed'}`}>{children}</div></div>)
  }

  return (<>
    <Section title={lang === 'zh' ? '上游连接' : 'Upstream API'}>
      <div className="row"><label>API Key</label><input type="password" value={form.deepseek_key || ''} onChange={e => set('deepseek_key', e.target.value)} placeholder="sk-..." autoComplete="off" /><span className="hint">{lang === 'zh' ? 'Codex 与 Claude 共用此 Key' : 'Shared key for Codex & Claude'}</span></div>
      <div className="row"><label>Base URL</label><input type="text" value={form.deepseek_base || ''} onChange={e => set('deepseek_base', e.target.value)} placeholder="https://api.deepseek.com" /></div>
      <div className="row"><label>{lang === 'zh' ? '默认模型' : 'Default Model'}</label><input type="text" value={form.default_model || ''} onChange={e => set('default_model', e.target.value)} placeholder="deepseek-v4-pro" /></div>
    </Section>

    <Section title={lang === 'zh' ? '模型映射' : 'Model Mapping'}>
      {modelMapping.map(([k, v], i) => (
        <div className="model-row" key={i}>
          <input placeholder="OpenAI model (e.g. gpt-5.5)" value={k} onChange={e => updateModelRow(i, 'k', e.target.value)} />
          <input placeholder="DeepSeek model (e.g. deepseek-v4-pro)" value={v} onChange={e => updateModelRow(i, 'v', e.target.value)} />
          <button onClick={() => removeModelRow(i)}>X</button>
        </div>
      ))}
      <button className="row-btn" onClick={addModelRow}>+ {lang === 'zh' ? '添加映射' : 'Add Mapping'}</button>
    </Section>

    <Section title={lang === 'zh' ? '生成参数' : 'Generation'}>
      <div className="row"><label>{lang === 'zh' ? '推理强度' : 'Reasoning'}</label><select value={form.reasoning_effort || ''} onChange={e => set('reasoning_effort', e.target.value || null)}><option value="">{lang === 'zh' ? '(由 DeepSeek 决定)' : '(let DeepSeek decide)'}</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></div>
      <div className="row"><label>{lang === 'zh' ? '上下文窗口' : 'Context Window'}</label><input type="number" min={1024} max={10000000} value={form.max_position_embeddings || 1000000} onChange={e => set('max_position_embeddings', parseInt(e.target.value))} /></div>
      <div className="row"><label>{lang === 'zh' ? '最大输出 Token' : 'Max Output Tokens'}</label><input type="number" min={1} max={131072} value={form.max_output_tokens || ''} onChange={e => set('max_output_tokens', parseInt(e.target.value) || null)} placeholder="16384" /></div>
      <div className="row"><label>Temperature</label><input type="number" step={0.01} min={0} max={2} value={form.temperature ?? ''} onChange={e => set('temperature', e.target.value ? parseFloat(e.target.value) : null)} placeholder={lang === 'zh' ? '(默认)' : '(default)'} /></div>
      <div className="row"><label>Top P</label><input type="number" step={0.01} min={0} max={1} value={form.top_p ?? ''} onChange={e => set('top_p', e.target.value ? parseFloat(e.target.value) : null)} placeholder={lang === 'zh' ? '(默认)' : '(default)'} /></div>
    </Section>

    <Section title={lang === 'zh' ? '网页抓取' : 'Web Fetch'} defaultOpen={false}>
      <div className="row"><label>{lang === 'zh' ? '最大 URL 数' : 'Max URLs'}</label><input type="number" min={0} max={50} value={form.web_fetch_max_urls ?? 5} onChange={e => set('web_fetch_max_urls', parseInt(e.target.value))} /></div>
      <div className="row"><label>{lang === 'zh' ? '超时 (秒)' : 'Timeout (s)'}</label><input type="number" min={1} max={120} value={form.web_fetch_timeout ?? 10} onChange={e => set('web_fetch_timeout', parseInt(e.target.value))} /></div>
      <div className="row"><label>{lang === 'zh' ? '最大响应体 (字节)' : 'Max Body (bytes)'}</label><input type="number" min={1000} max={1000000} value={form.web_fetch_max_body ?? 80000} onChange={e => set('web_fetch_max_body', parseInt(e.target.value))} /></div>
    </Section>

    <Section title={lang === 'zh' ? '推理缓存' : 'Reasoning Cache'}>
      <div className="row"><label>{lang === 'zh' ? '启用缓存' : 'Enable Cache'}</label><label className="toggle"><input type="checkbox" checked={!!form.enable_reasoning_cache} onChange={e => set('enable_reasoning_cache', e.target.checked)} /></label></div>
      <div className="row"><label>{lang === 'zh' ? '缓存有效期 (秒)' : 'Cache TTL (s)'}</label><input type="number" min={30} max={86400} value={form.reasoning_cache_ttl ?? 600} onChange={e => set('reasoning_cache_ttl', parseInt(e.target.value))} /></div>
    </Section>

    <div className="btn-row"><button className="btn primary" onClick={handleSave}>{lang === 'zh' ? '保存 Codex 配置' : 'Save Codex'}</button><button className="btn" onClick={onConfigSaved}>{lang === 'zh' ? '重新加载' : 'Reload'}</button></div>
  </>)
}
