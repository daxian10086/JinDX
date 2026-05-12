//go:build windows

package main

import "syscall"

var sysProcAttr = &syscall.SysProcAttr{
	HideWindow:    true,
	CreationFlags: 0x08000000, // CREATE_NO_WINDOW
}
