package main

import (
	"embed"
	"io/fs"
	"log"
	"os"

	"github.com/wailsapp/wails/v3/pkg/application"
	"github.com/wailsapp/wails/v3/pkg/events"
)

//go:embed all:frontend/dist
var frontendAssets embed.FS

// releaseProxyExe extracts the embedded proxy-backend.exe to a temp location
func releaseProxyExe() error {
	if len(proxyExe) == 0 {
		return nil
	}
	path, err := getProxyExePath()
	if err != nil {
		return err
	}
	if fileExists(path) {
		return nil
	}
	return os.WriteFile(path, proxyExe, 0700)
}

func main() {
	app := NewApp()

	if proxyExe != nil && len(proxyExe) > 0 {
		if err := releaseProxyExe(); err != nil {
			log.Printf("WARNING: cannot release proxy exe: %v", err)
		}
	}

	assetFS, err := fs.Sub(frontendAssets, "frontend/dist")
	if err != nil {
		log.Fatal(err)
	}

	wailsApp := application.New(application.Options{
		Name:        "JinDX Proxy",
		Description: "DeepSeek API Proxy",
		Assets: application.AssetOptions{
			Handler: application.BundledAssetFileServer(assetFS),
		},
	})

	wailsApp.RegisterService(application.NewService(app))

	// Create main window
	window := wailsApp.Window.NewWithOptions(application.WebviewWindowOptions{
		Name:     "main",
		Title:    "JinDX Proxy Manager",
		Width:    960,
		Height:   720,
		MinWidth:  800,
		MinHeight: 600,
		Windows: application.WindowsWindow{
			HiddenOnTaskbar: false,
		},
	})

	// Close to tray: hide instead of closing
	window.OnWindowEvent(events.Common.WindowClosing, func(_ *application.WindowEvent) {
		window.Hide()
	})

	// System tray
	tray := wailsApp.SystemTray.New()
	tray.SetTooltip("JinDX Proxy")
	tray.SetMenu(createTrayMenu(app))
	tray.OnClick(func() {
		if window.IsVisible() {
			window.Hide()
		} else {
			window.Show()
			window.Focus()
		}
	})
	tray.Show()

	// WebView 加载完成后聚焦到前台
	window.OnWindowEvent(events.Windows.WebViewNavigationCompleted, func(_ *application.WindowEvent) {
		window.Show()
		window.Focus()
	})

	// 启动时显示窗口
	window.Show()
	window.Focus()

	if err := wailsApp.Run(); err != nil {
		log.Fatal(err)
	}
}
