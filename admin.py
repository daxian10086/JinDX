"""管理 API 和 Web 管理面板。"""

import json
import logging

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .config import config, DEEPSEEK_BASE
from .stats import get_stats as stats_get_stats, get_logs as stats_get_logs
from .cache import get_memory_sessions_count, get_redis_session_count, is_redis_available, get_redis_info

logger = logging.getLogger(__name__)

admin_app = FastAPI(title="Proxy Admin")


async def _get_http_client() -> httpx.AsyncClient:
    import asyncio
    loop = asyncio.get_event_loop()
    return httpx.AsyncClient(timeout=httpx.Timeout(300.0))


@admin_app.get("/health")
async def admin_health():
    ds_ok = True
    try:
        client = await _get_http_client()
        deepseek_base = config.get("deepseek_base", DEEPSEEK_BASE)
        headers = {"Authorization": f"Bearer {config.get('deepseek_key', '')}", "Content-Type": "application/json"}
        r = await client.get(f"{deepseek_base}/v1/models", headers=headers, timeout=5)
        ds_ok = r.status_code < 500
    except Exception:
        ds_ok = False
    return {
        "status": "ok",
        "deepseek": "connected" if ds_ok else "unreachable",
        "redis": "connected" if is_redis_available() else "unavailable",
    }


@admin_app.get("/", response_class=HTMLResponse)
async def admin_page():
    return _ADMIN_HTML


@admin_app.get("/config")
async def admin_get_config():
    return JSONResponse(content=config.config_dict)


@admin_app.post("/config")
async def admin_set_config(request: Request):
    try:
        body = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    config.update(body)
    return JSONResponse(content={"status": "ok", "config": config.config_dict})


@admin_app.get("/stats")
async def admin_stats():
    return stats_get_stats()


@admin_app.get("/sessions")
async def admin_sessions():
    return {
        "memory_sessions": get_memory_sessions_count(),
        "redis_sessions": get_redis_session_count(),
    }


@admin_app.get("/logs")
async def admin_logs(limit: int = 50):
    return {"logs": stats_get_logs(limit)}


# ══════════════════════════════════════════════════════════════════════
# 内嵌管理面板 HTML（从原始 proxy.py 完整搬移）
# ══════════════════════════════════════════════════════════════════════

_ADMIN_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>JinDX</title>
<style>
:root { --bg: #0d1117; --fg: #c9d1d9; --border: #30363d; --accent: #58a6ff; --danger: #f85149; --green: #3fb950; --orange: #d2991d; --input-bg: #161b22; --card: #161b22; --muted: #8b949e; }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font: 14px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; background: var(--bg); color: var(--fg); min-height: 100vh; }
#topbar { display: flex; justify-content: space-between; align-items: center; padding: 12px 24px; border-bottom: 1px solid var(--border); background: var(--card); position: sticky; top: 0; z-index: 10; }
#topbar h1 { font-size: 20px; color: var(--accent); display: flex; align-items: center; gap: 10px; }
#topbar h1 .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); }
#lang-btn { padding: 4px 14px; border: 1px solid var(--border); border-radius: 4px; background: var(--input-bg); color: var(--fg); cursor: pointer; font-size: 13px; }
#lang-btn:hover { border-color: var(--accent); }
#tab-bar { display: flex; border-bottom: 2px solid var(--border); margin-bottom: 16px; }
#tab-bar .tab-btn { padding: 10px 24px; border: none; background: none; color: var(--muted); cursor: pointer; font-size: 14px; font-weight: 600; border-bottom: 2px solid transparent; margin-bottom: -2px; }
#tab-bar .tab-btn:hover { color: var(--fg); }
#tab-bar .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
.tab-content { display: none; }
.tab-content.active { display: block; }
#main { display: flex; gap: 20px; padding: 20px 24px; max-width: 1400px; margin: 0 auto; }
#left { flex: 1; min-width: 0; }
#right { width: 420px; flex-shrink: 0; }
@media (max-width: 900px) { #main { flex-direction: column; } #right { width: 100%; } }
.card { background: var(--card); border: 1px solid var(--border); border-radius: 6px; padding: 16px; margin-bottom: 16px; }
.card h2 { font-size: 15px; margin-bottom: 14px; padding-bottom: 8px; border-bottom: 1px solid var(--border); color: var(--accent); display: flex; align-items: center; gap: 8px; }
.card h2 .icon { font-size: 16px; }
.stat-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; }
.stat-item { background: var(--bg); border: 1px solid var(--border); border-radius: 4px; padding: 12px; text-align: center; }
.stat-value { font-size: 24px; font-weight: 700; color: var(--accent); }
.stat-value.green { color: var(--green); }
.stat-value.orange { color: var(--orange); }
.stat-value.danger { color: var(--danger); }
.stat-label { font-size: 11px; color: var(--muted); margin-top: 2px; }
#log-list { max-height: 200px; overflow-y: auto; font-size: 12px; font-family: monospace; }
#log-list .log-entry { padding: 4px 8px; border-bottom: 1px solid var(--border); color: var(--muted); }
#log-list .log-entry .log-time { color: var(--accent); margin-right: 8px; }
.row { display: flex; gap: 12px; align-items: center; margin-bottom: 10px; flex-wrap: wrap; }
.row label { min-width: 150px; font-weight: 500; font-size: 13px; }
.row input, .row select, .row textarea { flex: 1; min-width: 180px; background: var(--input-bg); border: 1px solid var(--border); border-radius: 4px; color: var(--fg); padding: 6px 10px; font-size: 13px; }
.row textarea { min-height: 56px; font-family: monospace; }
.row input[type="checkbox"] { flex: 0; min-width: 40px; width: 40px; height: 22px; appearance: none; -webkit-appearance: none; background: #30363d; border: 1px solid var(--border); border-radius: 11px; cursor: pointer; position: relative; transition: background 0.2s; }
.row input[type="checkbox"]::after { content: ''; position: absolute; top: 1px; left: 1px; width: 18px; height: 18px; border-radius: 50%; background: #8b949e; transition: all 0.2s; }
.row input[type="checkbox"]:checked { background: #238636; border-color: #238636; }
.row input[type="checkbox"]:checked::after { left: 19px; background: #fff; }
.row input:focus, .row select:focus, .row textarea:focus { outline: none; border-color: var(--accent); box-shadow: 0 0 0 2px rgba(88,166,255,0.2); }
.btn-row { display: flex; gap: 10px; margin-top: 16px; }
.btn { padding: 8px 20px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; font-weight: 600; cursor: pointer; transition: all 0.15s; }
.btn-primary { background: #238636; color: #fff; border-color: #238636; }
.btn-primary:hover { background: #2ea043; }
.btn-secondary { background: var(--input-bg); color: var(--fg); }
.btn-secondary:hover { background: #30363d; }
.model-row { display: flex; gap: 8px; align-items: center; margin-bottom: 6px; }
.model-row input { flex: 1; }
.model-row button { background: none; border: 1px solid var(--border); border-radius: 4px; color: var(--danger); cursor: pointer; padding: 4px 10px; font-size: 12px; }
#add-model { margin-top: 6px; font-size: 12px; background: none; border: 1px dashed var(--border); border-radius: 4px; color: var(--accent); cursor: pointer; padding: 4px 12px; }
#toast { position: fixed; top: 16px; right: 16px; padding: 10px 18px; border-radius: 6px; font-size: 13px; font-weight: 500; opacity: 0; transition: opacity 0.25s; z-index: 999; pointer-events: none; }
#toast.show { opacity: 1; }
#toast.ok { background: #238636; color: #fff; }
#toast.err { background: var(--danger); color: #fff; }
.section-toggle { cursor: pointer; user-select: none; }
.section-toggle:hover { color: #fff; }
.section-body { display: block; }
.section-body.collapsed { display: none; }
.status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; }
.status-dot.up { background: var(--green); }
.status-dot.down { background: var(--danger); }
</style>
</head>
<body>
<div id="topbar">
  <h1><span class="dot" id="status-dot"></span><span data-i18n-zh="JinDX 代理管理" data-i18n-en="JinDX Proxy Manager">JinDX 代理管理</span></h1>
  <button id="lang-btn" onclick="toggleLang()" data-i18n-zh="English" data-i18n-en="中文">English</button>
</div>
<div id="toast"></div>
<div id="main">
<div id="left">
  <div id="tab-bar">
    <button class="tab-btn active" onclick="switchTab('tab-codex')"><span data-i18n-zh="Codex 代理" data-i18n-en="Codex Proxy">Codex 代理</span></button>
    <button class="tab-btn" onclick="switchTab('tab-claude')"><span data-i18n-zh="Claude Code" data-i18n-en="Claude Code">Claude Code</span></button>
  </div>

  <!-- === TAB: Codex === -->
  <div id="tab-codex" class="tab-content active">
  <div class="card"><h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#128268;</span> <span data-i18n-zh="上游连接" data-i18n-en="Upstream API">上游连接</span></h2><div class="section-body">
    <div class="row"><label>API Key</label><input id="deepseek_key" type="password" placeholder="sk-..." autocomplete="off"></div>
    <div class="row"><label>Base URL</label><input id="deepseek_base" type="text" placeholder="https://api.deepseek.com"></div>
    <div class="row"><label data-i18n-zh="默认模型" data-i18n-en="Default Model">默认模型</label><input id="default_model" type="text" placeholder="deepseek-v4-pro"></div>
  </div></div>
  <div class="card"><h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#9881;</span> <span data-i18n-zh="模型映射" data-i18n-en="Model Mapping">模型映射</span></h2><div class="section-body">
    <div id="model-rows"></div>
    <button id="add-model" onclick="addModelRow('','')">+ <span data-i18n-zh="添加映射" data-i18n-en="Add Mapping">添加映射</span></button>
  </div></div>
  <div class="card"><h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#9881;</span> <span data-i18n-zh="生成参数" data-i18n-en="Generation Defaults">生成参数</span></h2><div class="section-body">
    <div class="row"><label data-i18n-zh="推理强度" data-i18n-en="Reasoning Effort">推理强度</label><select id="reasoning_effort"><option value="" data-i18n-zh="(由 DeepSeek 决定)" data-i18n-en="(let DeepSeek decide)">(由 DeepSeek 决定)</option><option value="min">min</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="max">max</option></select></div>
    <div class="row"><label data-i18n-zh="上下文窗口" data-i18n-en="Context Window">上下文窗口</label><input id="max_position_embeddings" type="number" min="1024" max="10000000" step="1024"></div>
    <div class="row"><label data-i18n-zh="最大输出 Tokens" data-i18n-en="Max Output Tokens">最大输出 Tokens</label><input id="max_output_tokens" type="number" min="1" max="131072"></div>
    <div class="row"><label data-i18n-zh="温度" data-i18n-en="Temperature">温度</label><input id="temperature" type="number" step="0.01" min="0" max="2" placeholder="(unset)"></div>
    <div class="row"><label data-i18n-zh="Top P" data-i18n-en="Top P">Top P</label><input id="top_p" type="number" step="0.01" min="0" max="1" placeholder="(unset)"></div>
  </div></div>
  <div class="card"><h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#127760;</span> <span data-i18n-zh="网页抓取" data-i18n-en="Web Fetch">网页抓取</span></h2><div class="section-body">
    <div class="row"><label data-i18n-zh="最大 URL 数" data-i18n-en="Max URLs">最大 URL 数</label><input id="web_fetch_max_urls" type="number" min="0" max="50"></div>
    <div class="row"><label data-i18n-zh="超时 (秒)" data-i18n-en="Timeout (seconds)">超时 (秒)</label><input id="web_fetch_timeout" type="number" min="1" max="120"></div>
    <div class="row"><label data-i18n-zh="最大响应体 (字节)" data-i18n-en="Max Body (bytes)">最大响应体 (字节)</label><input id="web_fetch_max_body" type="number" min="1000" max="1000000"></div>
  </div></div>
  <div class="card"><h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#128190;</span> <span data-i18n-zh="推理缓存" data-i18n-en="Reasoning Cache">推理缓存</span></h2><div class="section-body">
    <div class="row"><label for="enable_reasoning_cache" data-i18n-zh="启用缓存" data-i18n-en="Enable Cache">启用缓存</label><input id="enable_reasoning_cache" type="checkbox"></div>
    <div class="row"><label>Cache TTL (s)</label><input id="reasoning_cache_ttl" type="number" min="30" max="86400"></div>
  </div></div>
  <div class="btn-row">
    <button class="btn btn-primary" onclick="saveConfig()"><span data-i18n-zh="保存 Codex 配置" data-i18n-en="Save Codex">保存 Codex 配置</span></button>
    <button class="btn btn-secondary" onclick="loadConfig()"><span data-i18n-zh="重新加载" data-i18n-en="Reload">重新加载</span></button>
  </div>
  </div>

  <!-- === TAB: Claude === -->
  <div id="tab-claude" class="tab-content">
  <div class="card"><h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#128268;</span> <span data-i18n-zh="上游连接" data-i18n-en="Upstream API">上游连接</span></h2><div class="section-body">
    <div class="row"><label>API Key</label><input id="claude_deepseek_key" type="password" placeholder="sk-..." autocomplete="off"></div>
    <div class="row"><label>Base URL</label><input id="claude_deepseek_base" type="text" placeholder="https://api.deepseek.com"></div>
    <div class="row"><label data-i18n-zh="默认模型" data-i18n-en="Default Model">默认模型</label><input id="claude_default_model" type="text" placeholder="deepseek-v4-pro"></div>
  </div></div>
  <div class="card"><h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#9881;</span> <span data-i18n-zh="生成参数" data-i18n-en="Generation Defaults">生成参数</span></h2><div class="section-body">
    <div class="row"><label data-i18n-zh="推理强度" data-i18n-en="Reasoning Effort">推理强度</label><select id="claude_reasoning_effort"><option value="" data-i18n-zh="(由 DeepSeek 决定)" data-i18n-en="(let DeepSeek decide)">(由 DeepSeek 决定)</option><option value="min">min</option><option value="low">low</option><option value="medium">medium</option><option value="high">high</option><option value="max">max</option></select></div>
    <div class="row"><label data-i18n-zh="上下文窗口" data-i18n-en="Context Window">上下文窗口</label><input id="claude_max_position_embeddings" type="number" min="1024" max="10000000" step="1024"></div>
    <div class="row"><label data-i18n-zh="最大输出 Tokens" data-i18n-en="Max Output Tokens">最大输出 Tokens</label><input id="claude_max_output_tokens" type="number" min="1" max="131072"></div>
    <div class="row"><label data-i18n-zh="温度" data-i18n-en="Temperature">温度</label><input id="claude_temperature" type="number" step="0.01" min="0" max="2" placeholder="(unset)"></div>
    <div class="row"><label data-i18n-zh="Top P" data-i18n-en="Top P">Top P</label><input id="claude_top_p" type="number" step="0.01" min="0" max="1" placeholder="(unset)"></div>
  </div></div>
  <div class="card"><h2 class="section-toggle" onclick="toggleSection(this)"><span class="icon">&#9881;</span> <span data-i18n-zh="模型选项" data-i18n-en="Model Options">模型选项</span></h2><div class="section-body">
    <div class="row"><label for="claude_strip_thinking" data-i18n-zh="过滤 Thinking" data-i18n-en="Strip Thinking">过滤 Thinking</label><input id="claude_strip_thinking" type="checkbox" checked></div>
    <div class="row"><label for="claude_skip_dangerous_mode" data-i18n-zh="跳过危险模式提示" data-i18n-en="Skip Dangerous Mode Prompt">跳过危险模式提示</label><input id="claude_skip_dangerous_mode" type="checkbox" checked></div>
  </div></div>
  <div class="btn-row">
    <button class="btn btn-primary" onclick="saveConfig()"><span data-i18n-zh="保存 Claude 配置" data-i18n-en="Save Claude">保存 Claude 配置</span></button>
    <button class="btn btn-secondary" onclick="loadConfig()"><span data-i18n-zh="重新加载" data-i18n-en="Reload">重新加载</span></button>
  </div>
  </div>

</div>

<!-- === RIGHT column === -->
<div id="right">
  <div class="card"><h2><span class="icon">&#128200;</span> <span data-i18n-zh="实时统计" data-i18n-en="Live Stats">实时统计</span></h2>
    <div class="stat-grid">
      <div class="stat-item"><div class="stat-value" id="stat-uptime">--</div><div class="stat-label" data-i18n-zh="运行时间" data-i18n-en="Uptime">运行时间</div></div>
      <div class="stat-item"><div class="stat-value" id="stat-requests">--</div><div class="stat-label" data-i18n-zh="API 请求数" data-i18n-en="API Requests">API 请求数</div></div>
      <div class="stat-item"><div class="stat-value" id="stat-streams">0</div><div class="stat-label" data-i18n-zh="活跃流" data-i18n-en="Active Streams">活跃流</div></div>
      <div class="stat-item"><div class="stat-value" id="stat-error-rate">--</div><div class="stat-label" data-i18n-zh="API 错误率" data-i18n-en="API Error Rate">API 错误率</div></div>
      <div class="stat-item"><div class="stat-value green" id="stat-cache-hit">--</div><div class="stat-label" data-i18n-zh="缓存命中率" data-i18n-en="Cache Hit">缓存命中率</div></div>
      <div class="stat-item"><div class="stat-value" id="stat-sessions">--</div><div class="stat-label" data-i18n-zh="活跃会话" data-i18n-en="Sessions">活跃会话</div></div>
    </div>
  </div>
  <div class="card"><h2><span class="icon">&#9888;</span> <span data-i18n-zh="上游错误" data-i18n-en="Upstream Errors">上游错误</span></h2>
    <div id="upstream-errors" style="font-size:12px;color:var(--muted);max-height:150px;overflow-y:auto;">--</div>
  </div>
  <div class="card"><h2><span class="icon">&#128220;</span> <span data-i18n-zh="最近日志" data-i18n-en="Recent Logs">最近日志</span></h2>
    <div id="log-list"><span style="color:var(--muted)">--</span></div>
  </div>
  <div class="card"><h2><span class="icon">&#128225;</span> <span data-i18n-zh="系统状态" data-i18n-en="System Status">系统状态</span></h2>
    <div style="font-size:13px;">
      <div style="margin-bottom:6px;"><span class="status-dot" id="ds-status-dot"></span><span data-i18n-zh="DeepSeek API：" data-i18n-en="DeepSeek API: ">DeepSeek API：</span><span id="ds-status">--</span></div>
      <div style="margin-bottom:6px;"><span class="status-dot" id="redis-status-dot"></span><span>Redis：</span><span id="redis-status">--</span></div>
    </div>
  </div>
</div>
</div>

<script>
// Tab switching
function switchTab(tabId) {
  document.querySelectorAll('.tab-btn').forEach(function(b) { b.classList.remove('active'); });
  document.querySelectorAll('.tab-content').forEach(function(c) { c.classList.remove('active'); });
  document.getElementById(tabId).classList.add('active');
  event.target.classList.add('active');
}
// i18n
var LANG_KEY='jindx_lang', currentLang=localStorage.getItem(LANG_KEY)||'zh';
function toggleLang(){ currentLang=currentLang==='zh'?'en':'zh'; localStorage.setItem(LANG_KEY,currentLang); applyLang(); }
function applyLang(){
  document.documentElement.lang=currentLang==='zh'?'zh-CN':'en';
  document.querySelectorAll('[data-i18n-zh]').forEach(function(el){
    var t=currentLang==='zh'?el.getAttribute('data-i18n-zh'):el.getAttribute('data-i18n-en');
    if(t) el.textContent=t;
  });
  document.getElementById('lang-btn').textContent=currentLang==='zh'?'English':'中文';
  refreshStats(); refreshSessions(); refreshLogs();
}
function t(zh,en){ return currentLang==='zh'?zh:en; }
function toggleSection(h){ h.nextElementSibling.classList.toggle('collapsed'); }
function toast(msg,ok){ var t=document.getElementById('toast'); t.textContent=msg; t.className=(ok?'ok':'err')+' show'; setTimeout(function(){t.classList.remove('show');},2200); }
function addModelRow(k,v){
  var d=document.createElement('div'); d.className='model-row';
  var ki=document.createElement('input'); ki.placeholder='OpenAI model (e.g. gpt-5.5)'; ki.value=k||'';
  var vi=document.createElement('input'); vi.placeholder='DeepSeek model (e.g. deepseek-v4-pro)'; vi.value=v||'';
  var del=document.createElement('button'); del.textContent='X'; del.onclick=function(){d.remove();};
  d.append(ki,vi,del); document.getElementById('model-rows').appendChild(d);
}
function getModelMapping(){
  var m={}; document.querySelectorAll('.model-row').forEach(function(r){
    var i=r.querySelectorAll('input');
    if(i[0].value.trim()&&i[1].value.trim()) m[i[0].value.trim()]=i[1].value.trim();
  }); return m;
}
function setModelMapping(map){ document.getElementById('model-rows').innerHTML=''; if(map&&Object.keys(map).length) Object.entries(map).forEach(function(e){addModelRow(e[0],e[1]);}); }
async function loadConfig(){
  try{
    var r=await fetch('/config'), cfg=await r.json();
    document.getElementById('deepseek_key').value=cfg.deepseek_key||'';
    document.getElementById('deepseek_base').value=cfg.deepseek_base||'';
    document.getElementById('default_model').value=cfg.default_model||'';
    setModelMapping(cfg.model_mapping);
    document.getElementById('reasoning_effort').value=cfg.reasoning_effort||'';
    document.getElementById('max_position_embeddings').value=cfg.max_position_embeddings||1000000;
    document.getElementById('max_output_tokens').value=cfg.max_output_tokens||'';
    document.getElementById('temperature').value=cfg.temperature!=null?cfg.temperature:'';
    document.getElementById('top_p').value=cfg.top_p!=null?cfg.top_p:'';
    document.getElementById('web_fetch_max_urls').value=cfg.web_fetch_max_urls||'';
    document.getElementById('web_fetch_timeout').value=cfg.web_fetch_timeout||'';
    document.getElementById('web_fetch_max_body').value=cfg.web_fetch_max_body||'';
    document.getElementById('enable_reasoning_cache').checked=cfg.enable_reasoning_cache;
    document.getElementById('reasoning_cache_ttl').value=cfg.reasoning_cache_ttl||'';
    // Claude
    document.getElementById('claude_deepseek_key').value=cfg.claude_deepseek_key||'';
    document.getElementById('claude_deepseek_base').value=cfg.claude_deepseek_base||'';
    document.getElementById('claude_default_model').value=cfg.claude_default_model||'';
    document.getElementById('claude_reasoning_effort').value=cfg.claude_reasoning_effort||'';
    document.getElementById('claude_max_position_embeddings').value=cfg.claude_max_position_embeddings||1000000;
    document.getElementById('claude_max_output_tokens').value=cfg.claude_max_output_tokens||'';
    document.getElementById('claude_temperature').value=cfg.claude_temperature!=null?cfg.claude_temperature:'';
    document.getElementById('claude_top_p').value=cfg.claude_top_p!=null?cfg.claude_top_p:'';
    document.getElementById('claude_strip_thinking').checked=cfg.claude_strip_thinking!==false;
    document.getElementById('claude_skip_dangerous_mode').checked=cfg.claude_skip_dangerous_mode!==false;
    toast(t('配置已加载','Config loaded'),true);
  }catch(e){ toast(t('加载失败','Load failed')+': '+e,false); }
}
async function saveConfig(){
  var cfg={
    deepseek_key:document.getElementById('deepseek_key').value.trim(),
    deepseek_base:document.getElementById('deepseek_base').value.trim(),
    default_model:document.getElementById('default_model').value.trim(),
    model_mapping:getModelMapping(),
    reasoning_effort:document.getElementById('reasoning_effort').value||null,
    max_position_embeddings:parseInt(document.getElementById('max_position_embeddings').value)||1000000,
    max_output_tokens:parseInt(document.getElementById('max_output_tokens').value)||16384,
    temperature:document.getElementById('temperature').value?parseFloat(document.getElementById('temperature').value):null,
    top_p:document.getElementById('top_p').value?parseFloat(document.getElementById('top_p').value):null,
    web_fetch_max_urls:parseInt(document.getElementById('web_fetch_max_urls').value),
    web_fetch_timeout:parseInt(document.getElementById('web_fetch_timeout').value),
    web_fetch_max_body:parseInt(document.getElementById('web_fetch_max_body').value),
    enable_reasoning_cache:document.getElementById('enable_reasoning_cache').checked,
    reasoning_cache_ttl:parseInt(document.getElementById('reasoning_cache_ttl').value),
    // Claude
    claude_deepseek_key:document.getElementById('claude_deepseek_key').value.trim(),
    claude_deepseek_base:document.getElementById('claude_deepseek_base').value.trim(),
    claude_default_model:document.getElementById('claude_default_model').value.trim(),
    claude_reasoning_effort:document.getElementById('claude_reasoning_effort').value||null,
    claude_max_position_embeddings:parseInt(document.getElementById('claude_max_position_embeddings').value)||1000000,
    claude_max_output_tokens:parseInt(document.getElementById('claude_max_output_tokens').value)||16384,
    claude_temperature:document.getElementById('claude_temperature').value?parseFloat(document.getElementById('claude_temperature').value):null,
    claude_top_p:document.getElementById('claude_top_p').value?parseFloat(document.getElementById('claude_top_p').value):null,
    claude_strip_thinking:document.getElementById('claude_strip_thinking').checked,
    claude_skip_dangerous_mode:document.getElementById('claude_skip_dangerous_mode').checked,
  };
  try{
    var r=await fetch('/config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(cfg)});
    if(r.ok){ toast(t('已保存并生效','Saved & applied'),true); loadConfig(); }
    else{ var e=await r.json(); toast(t('保存失败','Save failed')+': '+(e.detail||r.status),false); }
  }catch(e){ toast(t('保存失败','Save failed')+': '+e,false); }
}
function fmtUptime(sec){
  if(sec<60) return sec+'s';
  if(sec<3600) return Math.floor(sec/60)+'m';
  if(sec<86400) return Math.floor(sec/3600)+'h '+Math.floor((sec%3600)/60)+'m';
  return Math.floor(sec/86400)+'d '+Math.floor((sec%86400)/3600)+'h';
}
async function refreshStats(){
  try{
    var r=await fetch('/stats'), s=await r.json();
    document.getElementById('stat-uptime').textContent=fmtUptime(s.uptime);
    document.getElementById('stat-requests').textContent=s.total_requests;
    document.getElementById('stat-streams').textContent=s.active_streams;
    var er=document.getElementById('stat-error-rate');
    er.textContent=s.error_rate+'%';
    er.className='stat-value'+(s.error_rate>10?' danger':s.error_rate>3?' orange':'');
    var ch=document.getElementById('stat-cache-hit');
    ch.textContent=s.cache_hit_rate+'%';
    ch.className='stat-value'+(s.cache_hit_rate>=70?' green':'');
    var ue=document.getElementById('upstream-errors');
    if(s.top_upstream_errors&&s.top_upstream_errors.length){
      ue.innerHTML=s.top_upstream_errors.map(function(e){
        return '<div style="margin-bottom:4px;"><span style="color:var(--danger)">'+e.count+'x</span> '+escHtml(e.msg.substring(0,100))+'</div>';
      }).join('');
    }else{ ue.textContent=t('无','None'); }
  }catch(e){}
}
async function refreshSessions(){
  try{
    var r=await fetch('/sessions'), s=await r.json();
    document.getElementById('stat-sessions').textContent=s.memory_sessions+s.redis_sessions;
  }catch(e){}
}
async function refreshLogs(){
  try{
    var r=await fetch('/logs?limit=20'), data=await r.json(), list=document.getElementById('log-list');
    if(data.logs&&data.logs.length){
      list.innerHTML=data.logs.map(function(l){
        return '<div class="log-entry"><span class="log-time">'+new Date(l.ts*1000).toLocaleTimeString()+'</span>'+escHtml(l.msg)+'</div>';
      }).join('');
    }else{ list.innerHTML='<span style="color:var(--muted)">'+t('暂无错误','No errors')+'</span>'; }
  }catch(e){}
}
function escHtml(s){ var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }
async function checkStatus(){
  try{
    var r=await fetch('/health');
    if(r.ok){
      var s=await r.json();
      document.getElementById('ds-status').textContent=s.deepseek||'OK';
      document.getElementById('ds-status-dot').className='status-dot up';
      document.getElementById('redis-status').textContent=s.redis||'OK';
      document.getElementById('redis-status-dot').className='status-dot up';
      document.getElementById('status-dot').className='dot';
    }
  }catch(e){
    document.getElementById('ds-status').textContent='--';
    document.getElementById('ds-status-dot').className='status-dot down';
    document.getElementById('redis-status').textContent='--';
    document.getElementById('redis-status-dot').className='status-dot down';
    document.getElementById('status-dot').className='dot';
    document.getElementById('status-dot').style.background='var(--danger)';
  }
}
function init(){
  applyLang(); loadConfig(); refreshStats(); refreshSessions(); refreshLogs(); checkStatus();
  setInterval(refreshStats,5000); setInterval(refreshSessions,30000); setInterval(refreshLogs,15000); setInterval(checkStatus,30000);
}
init();
</script>
</body>
</html>"""
