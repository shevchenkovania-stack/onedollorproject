# Build stats_code.tgz: the traffic-stats bundle (db/jobs/main/admin/webapp app + deploy script),
# LF-normalized, ASCII-only comments. Same stage/tar pattern as _mk_demo_tgz.ps1.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src  = Join-Path $root "bot"
$dpl  = Join-Path $root "deploy"
$stage = Join-Path $env:TEMP "stats_code"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "bot\handlers") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage "bot\webapp")   | Out-Null

# server-relative path -> local file
$map = @(
  @{ out = "bot\db.py";              in = (Join-Path $src "db.py") },
  @{ out = "bot\jobs.py";            in = (Join-Path $src "jobs.py") },
  @{ out = "bot\main.py";            in = (Join-Path $src "main.py") },
  @{ out = "bot\handlers\admin.py";  in = (Join-Path $src "handlers\admin.py") },
  @{ out = "bot\webapp\app.py";      in = (Join-Path $src "webapp\app.py") },
  @{ out = "stats_deploy.sh";        in = (Join-Path $dpl "stats_deploy.sh") }
)
foreach ($m in $map) {
  $out = Join-Path $stage $m.out
  $raw = [IO.File]::ReadAllText($m.in)
  $lf  = $raw -replace "`r`n", "`n"
  [IO.File]::WriteAllText($out, $lf, (New-Object Text.UTF8Encoding($false)))
}
$tgz = Join-Path $env:TEMP "stats_code.tgz"
if (Test-Path $tgz) { Remove-Item -Force $tgz }
Push-Location $stage
tar czf $tgz bot/db.py bot/jobs.py bot/main.py bot/handlers/admin.py bot/webapp/app.py stats_deploy.sh
Pop-Location
Write-Output ("built: " + $tgz + "  size=" + (Get-Item $tgz).Length)
Get-ChildItem -Recurse $stage | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
  $rel = $_.FullName.Substring($stage.Length + 1)
  $bytes = [IO.File]::ReadAllBytes($_.FullName)
  $cr = ($bytes | Where-Object { $_ -eq 13 }).Count
  Write-Output ("{0,-32} CR={1}" -f $rel, $cr)
}
