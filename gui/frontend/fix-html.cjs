// Post-build script: fix Vite output for Wails v3 WebView2 compatibility
// - Remove type="module" crossorigin (WebView2 doesn't support ES modules)
// - Inject Wails runtime scripts before the main app script

const fs = require('fs');
const path = require('path');

const distPath = path.join(__dirname, 'dist', 'index.html');

let html = fs.readFileSync(distPath, 'utf-8');

// Replace module script with regular script + Wails runtime injection
html = html.replace(
  '<script type="module" crossorigin>',
  '<script src="/wails/runtime.js"></script>\n<script src="/wails/ipc.js"></script>\n<script>'
);

fs.writeFileSync(distPath, html, 'utf-8');
console.log('✅ Fixed index.html for Wails v3 WebView2');
