//go:build !dev

package main

import _ "embed"

//go:embed proxy-backend.exe
var proxyExe []byte
