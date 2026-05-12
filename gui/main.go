package main

import (
	"embed"
	"log"

	"github.com/wailsapp/wails/v3/pkg/application"
)

//go:embed proxy-backend.exe
var proxyExe []byte

func main() {
	app := NewApp()

	// 释放内嵌的 Python 代理
	if err := extractProxyExe(); err != nil {
		log.Printf("WARNING: 无法释放代理 exe: %v", err)
	}

	wailsApp := application.New(application.Options{
		Name:        "JinDX Proxy",
		Description: "DeepSeek API Proxy",
		Width:       960,
		Height:      720,
		MinWidth:    800,
		MinHeight:   600,
		Icon:        nil,
		Bind: []interface{}{
			app,
		},
	})

	// 系统托盘
	wailsApp.NewSystemTray().
		SetTitle("JinDX Proxy").
		SetIcon(nil).
		SetMenu(createTrayMenu(app))

	// 窗口关闭时隐藏到托盘
	wailsApp.OnWindowClose(func() bool {
		return false // 默认行为关闭窗口，托盘始终可见
	})

	wailsApp.NewWebviewWindowWithOptions(application.WebviewWindowOptions{
		Title:  "JinDX Proxy Manager",
		Width:  960,
		Height: 720,
	})

	if err := wailsApp.Run(); err != nil {
		log.Fatal(err)
	}
}

func extractProxyExe() error {
	if len(proxyExe) == 0 {
		return nil // 开发模式，代理由外部启动
	}
	path, err := getProxyExePath()
	if err != nil {
		return err
	}
	if fileExists(path) {
		return nil
	}
	return writeEmbeddedExe(path, proxyExe)
}
