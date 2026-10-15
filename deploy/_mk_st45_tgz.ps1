# Build st45_code.tgz: the Stage-4 + Stage-5 files, LF-normalized, ready to scp to the VPS.
# ASCII-only comments: PS 5.1 parses .ps1 as ANSI.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src  = Join-Path $root "bot"
$stage = Join-Path $env:TEMP "st45_code"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }

# Stage 4: config.py db.py jobs.py main.py keyboards.py handlers/admin.py handlers/profile.py
# Stage 5: fsm_sqlite.py handlers/add.py  (main.py shared)
$files = @(
  "config.py","db.py","jobs.py","fsm_sqlite.py","main.py","keyboards.py",
  "handlers\admin.py","handlers\profile.py","handlers\add.py"
)
foreach ($f in $files) {
  $in  = Join-Path $src $f
  $out = Join-Path $stage $f
  New-Item -ItemType Directory -Force -Path (Split-Path $out -Parent) | Out-Null
  $raw = [IO.File]::ReadAllText($in)
  $lf  = $raw -replace "`r`n", "`n"
  [IO.File]::WriteAllText($out, $lf, (New-Object Text.UTF8Encoding($false)))
}
$tgz = Join-Path $env:TEMP "st45_code.tgz"
if (Test-Path $tgz) { Remove-Item -Force $tgz }
Push-Location $stage
tar czf $tgz config.py db.py jobs.py fsm_sqlite.py main.py keyboards.py handlers/admin.py handlers/profile.py handlers/add.py
Pop-Location
Write-Output ("built: " + $tgz + "  size=" + (Get-Item $tgz).Length)
Get-ChildItem -Recurse $stage | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
  $rel = $_.FullName.Substring($stage.Length + 1)
  $bytes = [IO.File]::ReadAllBytes($_.FullName)
  $cr = ($bytes | Where-Object { $_ -eq 13 }).Count
  Write-Output ("{0,-32} CR={1}" -f $rel, $cr)
}
