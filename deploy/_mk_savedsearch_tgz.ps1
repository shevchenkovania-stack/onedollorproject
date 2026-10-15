# Build ss_code.tgz: live-search + saved-search files, LF-normalized, ready to scp to the VPS.
# ASCII-only comments: PS 5.1 parses .ps1 as ANSI.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src  = Join-Path $root "bot"
$stage = Join-Path $env:TEMP "ss_code"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }

# Bot service (imobiliare): db.py scheduler.py handlers/admin.py
# Webapp service (imobiliare-web): webapp/app.py webapp/static/index.html
$files = @("db.py","scheduler.py","handlers\admin.py","webapp\app.py","webapp\static\index.html")
foreach ($f in $files) {
  $in  = Join-Path $src $f
  $out = Join-Path $stage $f
  New-Item -ItemType Directory -Force -Path (Split-Path $out -Parent) | Out-Null
  $raw = [IO.File]::ReadAllText($in)
  $lf  = $raw -replace "`r`n", "`n"
  [IO.File]::WriteAllText($out, $lf, (New-Object Text.UTF8Encoding($false)))
}
$tgz = Join-Path $env:TEMP "ss_code.tgz"
if (Test-Path $tgz) { Remove-Item -Force $tgz }
Push-Location $stage
tar czf $tgz db.py scheduler.py handlers/admin.py webapp/app.py webapp/static/index.html
Pop-Location
Write-Output ("built: " + $tgz + "  size=" + (Get-Item $tgz).Length)
