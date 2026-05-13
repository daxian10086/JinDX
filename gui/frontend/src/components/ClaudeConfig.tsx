import { useState, useEffect } from 'react'
import { showToast } from '../App'

interface Props { config: Record<string, any>; api: any; onConfigSaved: () => void }

export default function ClaudeConfig({ config, api, onConfigSaved }: Props) {
  const [form, setForm] = useState<Record<string, any>>({})

  useEffect(() => { setForm({ ...config }) }, [config])

  const handleSave = async () => {
    if (!api) return
    try { await api.SaveConfig(form); showToast('Claude 配置已保存'); onConfigSaved() }
    catch { showToast('保存失败', false) }
  }

  const set = (k: string, v: any) => setForm({ ...form, [k]: v })

  const Section = ({ title, children, defaultOpen }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) => {
    const [open, setOpen] = useState(defaultOpen ?? true)
    return (<div className="card"><h2 onClick={() => setOpen(!open)}><span className={`arrow ${open ? 'open' : ''}`}>&#9654;</span>{title}</h2><div className={`section-body${open ? '' : ' collapsed'}`}>{children}</div></div>)
  }

  return (<>
    <Section title="上游连接">
      <div className="row"><label>API Key</label><input type="password" value={form.claude_deepseek_key || ''} onChange={e => set('claude_deepseek_key', e.target.value)} placeholder="sk-..." autoComplete="off" /><span className="hint">留空则使用 Codex Key</span></div>
      <div className="row"><label>Base URL</label><input type="text" value={form.claude_deepseek_base || ''} onChange={e => set('claude_deepseek_base', e.target.value)} placeholder="https://api.deepseek.com" /><span className="hint">留空则使用 Codex Base URL</span></div>
      <div className="row"><label>默认模型</label><input type="text" value={form.claude_default_model || ''} onChange={e => set('claude_default_model', e.target.value)} placeholder="deepseek-v4-pro" /></div>
    </Section>

    <Section title="生成参数">
      <div className="row"><label>推理力度</label><select value={form.claude_reasoning_effort || ''} onChange={e => set('claude_reasoning_effort', e.target.value || null)}><option value="">(由 DeepSeek 决定)</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></div>
      <div className="row"><label>上下文窗口</label><input type="number" min={1024} max={10000000} value={form.claude_max_position_embeddings || 1000000} onChange={e => set('claude_max_position_embeddings', parseInt(e.target.value))} /></div>
      <div className="row"><label>最大输出 Token</label><input type="number" min={1} max={131072} value={form.claude_max_output_tokens || ''} onChange={e => set('claude_max_output_tokens', parseInt(e.target.value) || null)} placeholder="16384" /></div>
      <div className="row"><label>Temperature</label><input type="number" step={0.01} min={0} max={2} value={form.claude_temperature ?? ''} onChange={e => set('claude_temperature', e.target.value ? parseFloat(e.target.value) : null)} placeholder="(默认)" /></div>
      <div className="row"><label>Top P</label><input type="number" step={0.01} min={0} max={1} value={form.claude_top_p ?? ''} onChange={e => set('claude_top_p', e.target.value ? parseFloat(e.target.value) : null)} placeholder="(默认)" /></div>
    </Section>

    <Section title="模型选项">
      <div className="row"><label>剥离思考过程</label><label className="toggle"><input type="checkbox" checked={form.claude_strip_thinking !== false} onChange={e => set('claude_strip_thinking', e.target.checked)} /><span className="slider" /></label><span className="hint">在 Claude Code 中隐藏推理</span></div>
      <div className="row"><label>跳过危险模式</label><label className="toggle"><input type="checkbox" checked={form.claude_skip_dangerous_mode !== false} onChange={e => set('claude_skip_dangerous_mode', e.target.checked)} /><span className="slider" /></label></div>
      <div className="row"><label>DeepSeek 思考</label><label className="toggle"><input type="checkbox" checked={!!form.claude_deepseek_thinking_enabled} onChange={e => set('claude_deepseek_thinking_enabled', e.target.checked)} /><span className="slider" /></label><span className="hint">关闭以避免 reasoning_content 400 错误</span></div>
    </Section>

    <div className="btn-row"><button className="btn primary" onClick={handleSave}>保存 Claude 配置</button><button className="btn" onClick={onConfigSaved}>重新加载</button></div>
  </>)
}
