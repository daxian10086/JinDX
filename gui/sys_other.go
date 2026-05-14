//go:build !windows

package main

import "syscall"

var sysProcAttr = &syscall.SysProcAttr{}

func killProcessTree(pid int) error {
	return nil
}
