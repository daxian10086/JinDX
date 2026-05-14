import { useState, useEffect, useContext } from 'react'
import { showToast, LangContext } from '../App'
import { App as AppBindings } from '../bindings/index.js'

interface Props { config: Record<string, any>; onConfigSaved: () => void }

export default function ClaudeConfig({ config, onConfigSaved }: Props) {
  const [form, setForm] = useState<Record<string, any>>({})
  const lang = useContext(LangContext)

  useEffect(() => { setForm({ ...config }) }, [config])

  const handleSave = async () => {
    try { await AppBindings.SaveConfig(form); showToast(lang === 'zh' ? 'Claude 配置已保存' : 'Claude config saved'); onConfigSaved() }
    catch { showToast(lang === 'zh' ? '保存失败' : 'Save failed', false) }
  }

  const set = (k: string, v: any) => setForm({ ...form, [k]: v })

  const Section = ({ title, children, defaultOpen }: { title: string; children: React.ReactNode; defaultOpen?: boolean }) => {
    const [open, setOpen] = useState(defaultOpen ?? true)
    return (<div className="card"><h2 onClick={() => setOpen(!open)}><span className={`arrow ${open ? 'open' : ''}`}>&#9654;</span>{title}</h2><div className={`section-body${open ? '' : ' collapsed'}`}>{children}</div></div>)
  }

  return (<>
    <Section title={lang === 'zh' ? '上游连接' : 'Upstream API'}>
      <div className="row"><label>API Key</label><input type="password" value={form.claude_deepseek_key || ''} onChange={e => set('claude_deepseek_key', e.target.value)} placeholder="sk-..." autoComplete="off" /><span className="hint">{lang === 'zh' ? '留空则使用 Codex Key' : 'Leave empty to use Codex key'}</span></div>
      <div className="row"><label>Base URL</label><input type="text" value={form.claude_deepseek_base || ''} onChange={e => set('claude_deepseek_base', e.target.value)} placeholder="https://api.deepseek.com" /><span className="hint">{lang === 'zh' ? '留空则使用 Codex Base URL' : 'Leave empty to use Codex Base URL'}</span></div>
      <div className="row"><label>{lang === 'zh' ? '默认模型' : 'Default Model'}</label><input type="text" value={form.claude_default_model || ''} onChange={e => set('claude_default_model', e.target.value)} placeholder="deepseek-v4-pro" /></div>
    </Section>

    <Section title={lang === 'zh' ? '生成参数' : 'Generation'}>
      <div className="row"><label>{lang === 'zh' ? '推理强度' : 'Reasoning'}</label><select value={form.claude_reasoning_effort || ''} onChange={e => set('claude_reasoning_effort', e.target.value || null)}><option value="">{lang === 'zh' ? '(由 DeepSeek 决定)' : '(let DeepSeek decide)'}</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option></select></div>
      <div className="row"><label>{lang === 'zh' ? '上下文窗口' : 'Context Window'}</label><input type="number" min={1024} max={10000000} value={form.claude_max_position_embeddings || 1000000} onChange={e => set('claude_max_position_embeddings', parseInt(e.target.value))} /></div>
      <div className="row"><label>{lang === 'zh' ? '最大输出 Token' : 'Max Output Tokens'}</label><input type="number" min={1} max={131072} value={form.claude_max_output_tokens || ''} onChange={e => set('claude_max_output_tokens', parseInt(e.target.value) || null)} placeholder="16384" /></div>
      <div className="row"><label>Temperature</label><input type="number" step={0.01} min={0} max={2} value={form.claude_temperature ?? ''} onChange={e => set('claude_temperature', e.target.value ? parseFloat(e.target.value) : null)} placeholder={lang === 'zh' ? '(默认)' : '(default)'} /></div>
      <div className="row"><label>Top P</label><input type="number" step={0.01} min={0} max={1} value={form.claude_top_p ?? ''} onChange={e => set('claude_top_p', e.target.value ? parseFloat(e.target.value) : null)} placeholder={lang === 'zh' ? '(默认)' : '(default)'} /></div>
    </Section>

    <Section title={lang === 'zh' ? '模型选项' : 'Model Options'}>
      <div className="row"><label>{lang === 'zh' ? '剥离思考过程' : 'Strip Thinking'}</label><label className="toggle"><input type="checkbox" checked={form.claude_strip_thinking !== false} onChange={e => set('claude_strip_thinking', e.target.checked)} /></label><span className="hint">{lang === 'zh' ? '在 Claude Code 中隐藏推理' : 'Hide reasoning in Claude Code'}</span></div>
      <div className="row"><label>{lang === 'zh' ? '跳过危险模式' : 'Skip Dangerous Mode'}</label><label className="toggle"><input type="checkbox" checked={form.claude_skip_dangerous_mode !== false} onChange={e => set('claude_skip_dangerous_mode', e.target.checked)} /></label></div>
      <div className="row"><label>{lang === 'zh' ? 'DeepSeek 思考' : 'DeepSeek Thinking'}</label><label className="toggle"><input type="checkbox" checked={!!form.claude_deepseek_thinking_enabled} onChange={e => set('claude_deepseek_thinking_enabled', e.target.checked)} /></label><span className="hint">{lang === 'zh' ? '关闭以避免 reasoning_content 错误' : 'Disable to avoid reasoning_content 400'}</span></div>
    </Section>

    <div className="btn-row"><button className="btn primary" onClick={handleSave}>{lang === 'zh' ? '保存 Claude 配置' : 'Save Claude'}</button><button className="btn" onClick={onConfigSaved}>{lang === 'zh' ? '重新加载' : 'Reload'}</button></div>
  </>)
}
