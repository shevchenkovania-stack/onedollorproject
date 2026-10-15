# Build /tmp-style staging of the Stage-2 files with LF endings and pack into st2_code.tgz.
# ASCII-only comments: PS 5.1 parses .ps1 as ANSI.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src  = Join-Path $root "bot"
$stage = Join-Path $env:TEMP "st2_code"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }

$files = @(
  "handlers\admin.py","handlers\fallback.py","keyboards.py","main.py","publish.py",
  "webapp\app.py","webapp\static\index.html"
)
foreach ($f in $files) {
  $in  = Join-Path $src $f
  $out = Join-Path $stage $f
  New-Item -ItemType Directory -Force -Path (Split-Path $out -Parent) | Out-Null
  $raw = [IO.File]::ReadAllText($in)
  $lf  = $raw -replace "`r`n", "`n"
  [IO.File]::WriteAllText($out, $lf, (New-Object Text.UTF8Encoding($false)))
}
$tgz = Join-Path $env:TEMP "st2_code.tgz"
if (Test-Path $tgz) { Remove-Item -Force $tgz }
Push-Location $stage
tar czf $tgz handlers keyboards.py main.py publish.py webapp
Pop-Location
Write-Output ("built: " + $tgz + "  size=" + (Get-Item $tgz).Length)
Get-ChildItem -Recurse $stage | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
  $rel = $_.FullName.Substring($stage.Length + 1)
  $bytes = [IO.File]::ReadAllBytes($_.FullName)
  $cr = ($bytes | Where-Object { $_ -eq 13 }).Count
  Write-Output ("{0,-32} CR={1}" -f $rel, $cr)
}
