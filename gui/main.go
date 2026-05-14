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

	window := wailsApp.Window.NewWithOptions(application.WebviewWindowOptions{
		Name:      "main",
		Title:     "JinDX Proxy Manager",
		Width:     960,
		Height:    720,
		MinWidth:  800,
		MinHeight: 600,
	})

	window.OnWindowEvent(events.Common.WindowClosing, func(_ *application.WindowEvent) {
		window.Hide()
	})

	tray := wailsApp.SystemTray.New()
	tray.SetTooltip("JinDX Proxy")
	menu := wailsApp.NewMenu()
	menu.Add("Show Window").OnClick(func(_ *application.Context) {
		window.Show()
		window.Focus()
	})
	menu.Add("Start Proxy").OnClick(func(_ *application.Context) {
		app.StartProxy()
	})
	menu.Add("Stop Proxy").OnClick(func(_ *application.Context) {
		app.StopProxy()
	})
	menu.AddSeparator()
	menu.Add("Quit").OnClick(func(_ *application.Context) {
		app.StopProxy()
		wailsApp.Quit()
	})
	tray.SetMenu(menu)
	tray.OnClick(func() {
		if window.IsVisible() {
			window.Hide()
		} else {
			window.Show()
			window.Focus()
		}
	})
	tray.Show()

	window.Show()
	window.Focus()

	if err := wailsApp.Run(); err != nil {
		log.Fatal(err)
	}
}
