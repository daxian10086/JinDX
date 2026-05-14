package main

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	
	"strings"
	"sync"
	"time"

	"github.com/wailsapp/wails/v3/pkg/application"
	"golang.org/x/sys/windows/registry"
)

// App — Wails 绑定的 Go 结构体
type App struct {
	ctx       context.Context
	proxyCmd  *exec.Cmd
	proxyLock sync.Mutex
}

func NewApp() *App {
	return &App{}
}

func (a *App) Startup(ctx context.Context) {
	a.ctx = ctx
}

func (a *App) Shutdown(ctx context.Context) {
	a.StopProxy()
}

// ── 配置路径 ──────────────────────────────────────────────

func configPath() string {
	appdata := os.Getenv("APPDATA")
	if appdata == "" {
		home, _ := os.UserHomeDir()
		appdata = filepath.Join(home, "AppData", "Roaming")
	}
	return filepath.Join(appdata, "proxy-config.json")
}

func proxyExeDir() string {
	// 开发时：当前目录
	// 打包后：exe 所在目录
	exe, _ := os.Executable()
	return filepath.Dir(exe)
}

func getProxyExePath() (string, error) {
	if len(proxyExe) == 0 {
		// 开发模式，找当前目录
		return filepath.Join(proxyExeDir(), "proxy-backend.exe"), nil
	}
	tmpDir := filepath.Join(os.TempDir(), "jindx-proxy")
	os.MkdirAll(tmpDir, 0700)
	hash := sha256.Sum256(proxyExe)
	return filepath.Join(tmpDir, hex.EncodeToString(hash[:8])+".exe"), nil
}

func fileExists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func writeEmbeddedExe(path string, data []byte) error {
	return os.WriteFile(path, data, 0700)
}



// ── 代理控制 (Wails Binding) ─────────────────────────────

func (a *App) StartProxy() (string, error) {
	a.proxyLock.Lock()
	defer a.proxyLock.Unlock()

	if a.proxyCmd != nil {
		// 检查是否还在运行
		if a.proxyCmd.Process != nil && a.proxyCmd.Process.Pid > 0 {
			return "running", nil
		}
	}

	proxyPath, err := getProxyExePath()
	if err != nil {
		return "stopped", fmt.Errorf("获取代理路径失败: %w", err)
	}

	if !fileExists(proxyPath) {
		return "stopped", fmt.Errorf("代理程序不存在: %s", proxyPath)
	}

	// 设置环境变量
	cfg := a.loadConfig()
	env := os.Environ()
	env = append(env,
		fmt.Sprintf("PROXY_PORT=%s", getCfgStr(cfg, "PROXY_PORT", "8080")),
		fmt.Sprintf("ADMIN_PORT=%s", getCfgStr(cfg, "ADMIN_PORT", "8090")),
		fmt.Sprintf("TLS_PORT=%s", getCfgStr(cfg, "TLS_PORT", "8444")),
		fmt.Sprintf("CONNECT_PORT=%s", getCfgStr(cfg, "CONNECT_PORT", "8443")),
		fmt.Sprintf("PROXY_CONFIG_FILE=%s", configPath()),
	)
	if key, ok := cfg["deepseek_key"].(string); ok && key != "" {
		env = append(env, fmt.Sprintf("DEEPSEEK_KEY=%s", key))
	}
	if base, ok := cfg["deepseek_base"].(string); ok && base != "" {
		env = append(env, fmt.Sprintf("DEEPSEEK_BASE=%s", base))
	}
	if model, ok := cfg["default_model"].(string); ok && model != "" {
		env = append(env, fmt.Sprintf("DEFAULT_MODEL=%s", model))
	}

	a.proxyCmd = exec.Command(proxyPath)
	a.proxyCmd.Env = env
	a.proxyCmd.Stdout = log.Writer()
	a.proxyCmd.Stderr = log.Writer()
	a.proxyCmd.SysProcAttr = sysProcAttr // 隐藏控制台窗口

	if err := a.proxyCmd.Start(); err != nil {
		a.proxyCmd = nil
		return "stopped", fmt.Errorf("启动代理失败: %w", err)
	}

	// 等待端口监听
	proxyPort := getCfgStr(cfg, "PROXY_PORT", "8080")
	for i := 0; i < 30; i++ {
		time.Sleep(200 * time.Millisecond)
		resp, err := http.Get(fmt.Sprintf("http://127.0.0.1:%s/health", proxyPort))
		if err == nil {
			resp.Body.Close()
			return "running", nil
		}
	}

	return "running", nil // 进程已启动，允许稍后连接
}

func (a *App) StopProxy() (string, error) {
	a.proxyLock.Lock()
	defer a.proxyLock.Unlock()

	if a.proxyCmd == nil {
		return "stopped", nil
	}

	if a.proxyCmd.Process != nil {
		a.proxyCmd.Process.Kill()
	}
	a.proxyCmd = nil
	return "stopped", nil
}

func (a *App) RestartProxy() (string, error) {
	a.StopProxy()
	time.Sleep(1 * time.Second)
	return a.StartProxy()
}

func (a *App) GetProxyStatus() string {
	a.proxyLock.Lock()
	defer a.proxyLock.Unlock()

	if a.proxyCmd != nil && a.proxyCmd.Process != nil {
		// 尝试健康检查
		cfg := a.loadConfig()
		port := getCfgStr(cfg, "PROXY_PORT", "8080")
		resp, err := http.Get(fmt.Sprintf("http://127.0.0.1:%s/health", port))
		if err == nil {
			resp.Body.Close()
			return "running"
		}
		return "starting"
	}
	return "stopped"
}
func (a *App) GetCacheInfo() map[string]interface{} {
	return a.fetchFromAdmin("/cache-info")
}

func (a *App) ClearCache(source string) map[string]interface{} {
	cfg := a.loadConfig()
	adminPort := getCfgStr(cfg, "ADMIN_PORT", "8090")
	body, _ := json.Marshal(map[string]string{"source": source})
	url := fmt.Sprintf("http://127.0.0.1:%s/cache-clear", adminPort)
	resp, err := http.Post(url, "application/json", strings.NewReader(string(body)))
	if err != nil {
		return map[string]interface{}{"error": err.Error()}
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	var result map[string]interface{}
	json.Unmarshal(data, &result)
	return result
}


// ── 配置管理 (Wails Binding) ─────────────────────────────

func (a *App) loadConfig() map[string]interface{} {
	cfg := map[string]interface{}{}
	path := configPath()
	data, err := os.ReadFile(path)
	if err != nil {
		return cfg
	}
	json.Unmarshal(data, &cfg)
	// 注入端口信息
	cfg["PROXY_PORT"] = envOr("PROXY_PORT", "8080")
	cfg["ADMIN_PORT"] = envOr("ADMIN_PORT", "8090")
	cfg["TLS_PORT"] = envOr("TLS_PORT", "8444")
	cfg["CONNECT_PORT"] = envOr("CONNECT_PORT", "8443")
	return cfg
}


func (a *App) GetAllConfig() map[string]interface{} {
	result := a.fetchFromAdmin("/config")
	if result == nil {
		result = map[string]interface{}{}
	}
	result["PROXY_PORT"] = envOr("PROXY_PORT", "8080")
	result["ADMIN_PORT"] = envOr("ADMIN_PORT", "8090")
	result["TLS_PORT"] = envOr("TLS_PORT", "8444")
	result["CONNECT_PORT"] = envOr("CONNECT_PORT", "8443")
	return result
}

func (a *App) SaveConfigViaAdmin(cfg map[string]interface{}) error {
	a.SaveConfig(cfg)
	cfg2 := a.loadConfig()
	adminPort := getCfgStr(cfg2, "ADMIN_PORT", "8090")
	data, _ := json.Marshal(cfg)
	url := fmt.Sprintf("http://127.0.0.1:%s/config", adminPort)
	_, err := http.Post(url, "application/json", strings.NewReader(string(data)))
	return err
}

func (a *App) GetConfig() map[string]interface{} {
	return a.loadConfig()
}

func (a *App) SaveConfig(cfg map[string]interface{}) error {
	path := configPath()
	os.MkdirAll(filepath.Dir(path), 0700)
	// 过滤掉注入的端口字段，不持久化
	delete(cfg, "PROXY_PORT")
	delete(cfg, "ADMIN_PORT")
	delete(cfg, "TLS_PORT")
	delete(cfg, "CONNECT_PORT")
	data, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return err
	}
	return os.WriteFile(path, data, 0600)
}

// ── 统计 & 日志 (Wails Binding) ──────────────────────────

func (a *App) GetStats() map[string]interface{} {
	return a.fetchFromAdmin("/stats")
}

func (a *App) GetLogs(limit int) []map[string]interface{} {
	result := a.fetchFromAdmin(fmt.Sprintf("/logs?limit=%d", limit))
	if logs, ok := result["logs"].([]interface{}); ok {
		out := make([]map[string]interface{}, 0, len(logs))
		for _, l := range logs {
			if m, ok := l.(map[string]interface{}); ok {
				out = append(out, m)
			}
		}
		return out
	}
	return nil
}

func (a *App) GetSystemStatus() map[string]interface{} {
	return a.fetchFromAdmin("/health")
}

func (a *App) GetEnvVars(mode string) string {
	cfg := a.loadConfig()
	proxy := fmt.Sprintf("http://127.0.0.1:%s", getCfgStr(cfg, "PROXY_PORT", "8080"))
	key := getCfgStr(cfg, "deepseek_key", "sk-your-key")

	if mode == "claude" {
		model := getCfgStr(cfg, "default_model", "deepseek-v4-pro")
		return fmt.Sprintf(
			"$env:ANTHROPIC_BASE_URL=\"%s\"\n$env:ANTHROPIC_API_KEY=\"%s\"\n$env:ANTHROPIC_MODEL=\"%s\"\nclaude",
			proxy, key, model,
		)
	}
	return fmt.Sprintf(
		"$env:OPENAI_BASE_URL=\"%s\"\n$env:OPENAI_API_KEY=\"%s\"\ncodex",
		proxy, key,
	)
}

// ── 代理开关 ──────────────────────────────────────────────

func (a *App) GetProxySwitchStatus() map[string]interface{} {
	return a.fetchFromAdmin("/proxy-status")
}

func (a *App) ToggleProxySwitch(which string, enabled bool) error {
	cfg := a.loadConfig()
	proxyPort := getCfgStr(cfg, "PROXY_PORT", "8080")

	body := map[string]bool{which: enabled}
	data, _ := json.Marshal(body)
	resp, err := http.Post(
		fmt.Sprintf("http://127.0.0.1:%s/proxy-status", proxyPort),
		"application/json",
		strings.NewReader(string(data)),
	)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	return nil
}

// ── 系统 ──────────────────────────────────────────────────

func (a *App) IsAdmin() bool {
	// Windows 管理员检查
	_, err := os.Open("\\\\.\\PHYSICALDRIVE0")
	return err == nil
}

func (a *App) GetAutoStart() bool {
	k, err := registry.OpenKey(registry.CURRENT_USER,
		`Software\Microsoft\Windows\CurrentVersion\Run`,
		registry.QUERY_VALUE)
	if err != nil {
		return false
	}
	defer k.Close()
	val, _, err := k.GetStringValue("JinDX")
	return err == nil && val != ""
}

func (a *App) ToggleAutoStart(enabled bool) error {
	k, err := registry.OpenKey(registry.CURRENT_USER,
		`Software\Microsoft\Windows\CurrentVersion\Run`,
		registry.SET_VALUE)
	if err != nil {
		return fmt.Errorf("无法访问注册表: %w", err)
	}
	defer k.Close()

	if enabled {
		exe, _ := os.Executable()
		return k.SetStringValue("JinDX", exe)
	}
	return k.DeleteValue("JinDX")
}

// ── 辅助函数 ──────────────────────────────────────────────

func (a *App) fetchFromAdmin(path string) map[string]interface{} {
	cfg := a.loadConfig()
	adminPort := getCfgStr(cfg, "ADMIN_PORT", "8090")

	// 用 API Key 作为认证 token
	token := getCfgStr(cfg, "claude_deepseek_key", "")
	if token == "" {
		token = getCfgStr(cfg, "deepseek_key", "")
	}

	url := fmt.Sprintf("http://127.0.0.1:%s%s", adminPort, path)
	req, err := http.NewRequest("GET", url, nil)
	if err != nil {
		return map[string]interface{}{"error": err.Error()}
	}
	if token != "" && token != "sk-your-deepseek-api-key" {
		req.Header.Set("Authorization", "Bearer "+token)
	}

	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return map[string]interface{}{"error": err.Error()}
	}
	defer resp.Body.Close()

	body, _ := io.ReadAll(resp.Body)
	var result map[string]interface{}
	json.Unmarshal(body, &result)
	return result
}

func getCfgStr(cfg map[string]interface{}, key, fallback string) string {
	v, ok := cfg[key]
	if !ok {
		return fallback
	}
	s, ok := v.(string)
	if !ok {
		return fallback
	}
	if s == "" {
		return fallback
	}
	return s
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}

// ── 托盘菜单 ──────────────────────────────────────────────

func createTrayMenu(app *App) *application.Menu {
	menu := application.NewMenu()
	menu.Add("显示窗口").OnClick(func(_ *application.Context) {
		// Wails v3 自动处理
	})
	menu.Add("启动代理").OnClick(func(_ *application.Context) {
		app.StartProxy()
	})
	menu.Add("停止代理").OnClick(func(_ *application.Context) {
		app.StopProxy()
	})
	menu.AddSeparator()
	menu.Add("退出").OnClick(func(_ *application.Context) {
		app.StopProxy()
		os.Exit(0)
	})
	return menu
}
