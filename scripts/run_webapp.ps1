$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
New-Item -ItemType Directory -Force "logs" | Out-Null
& "C:\Python314\python.exe" -u -m webapp.server --host 127.0.0.1 --port 8765 *> "logs\webapp.persistent.log"
