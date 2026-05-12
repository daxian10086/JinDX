//go:build !windows

package main

import "syscall"

var sysProcAttr = &syscall.SysProcAttr{}
