//go:build dev

package main

import (
	"log"
	"os"
	"path/filepath"
)

// In dev mode, load proxy-backend.exe from the gui directory.
var proxyExe []byte

func init() {
	exe, err := os.Executable()
	if err != nil {
		exe = "."
	}
	dir := filepath.Dir(exe)
	path := filepath.Join(dir, "proxy-backend.exe")
	data, err := os.ReadFile(path)
	if err != nil {
		log.Printf("DEV: proxy-backend.exe not found at %s, proxy start disabled", path)
		return
	}
	proxyExe = data
}
