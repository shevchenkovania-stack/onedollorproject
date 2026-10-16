# Build pack_code.tgz: the $1-pack files (config/db/keyboards/render/wallet + the deploy script),
# LF-normalized, ready to scp to the VPS. ASCII-only comments: PS 5.1 parses .ps1 as ANSI.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src  = Join-Path $root "bot"
$dpl  = Join-Path $root "deploy"
$stage = Join-Path $env:TEMP "pack_code"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "handlers") | Out-Null

# bot code (relative names) + the deploy script, all flattened into the stage
$map = @(
  @{ in = (Join-Path $src "config.py");          out = "config.py" },
  @{ in = (Join-Path $src "db.py");              out = "db.py" },
  @{ in = (Join-Path $src "keyboards.py");       out = "keyboards.py" },
  @{ in = (Join-Path $src "render.py");          out = "render.py" },
  @{ in = (Join-Path $src "handlers\wallet.py"); out = "handlers\wallet.py" },
  @{ in = (Join-Path $dpl "pack_deploy.sh");     out = "pack_deploy.sh" }
)
foreach ($m in $map) {
  $out = Join-Path $stage $m.out
  New-Item -ItemType Directory -Force -Path (Split-Path $out -Parent) | Out-Null
  $raw = [IO.File]::ReadAllText($m.in)
  $lf  = $raw -replace "`r`n", "`n"
  [IO.File]::WriteAllText($out, $lf, (New-Object Text.UTF8Encoding($false)))
}
$tgz = Join-Path $env:TEMP "pack_code.tgz"
if (Test-Path $tgz) { Remove-Item -Force $tgz }
Push-Location $stage
tar czf $tgz config.py db.py keyboards.py render.py handlers/wallet.py pack_deploy.sh
Pop-Location
Write-Output ("built: " + $tgz + "  size=" + (Get-Item $tgz).Length)
Get-ChildItem -Recurse $stage | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
  $rel = $_.FullName.Substring($stage.Length + 1)
  $bytes = [IO.File]::ReadAllBytes($_.FullName)
  $cr = ($bytes | Where-Object { $_ -eq 13 }).Count
  Write-Output ("{0,-28} CR={1}" -f $rel, $cr)
}
