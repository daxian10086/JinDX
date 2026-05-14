//go:build windows

package main

import (
	"os/exec"
	"fmt"
	"syscall"
)

var sysProcAttr = &syscall.SysProcAttr{
	HideWindow:    true,
	CreationFlags: 0x08000000, // CREATE_NO_WINDOW
}

// killProcessTree kills the process and all its children on Windows
func killProcessTree(pid int) error {
	return exec.Command("taskkill", "/T", "/F", "/PID", fmt.Sprintf("%d", pid)).Run()
}
