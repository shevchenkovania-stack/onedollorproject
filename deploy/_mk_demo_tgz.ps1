# Build demo_code.tgz: the clean-demo-board files (bot db/render/start/app + test_data listings/seed
# + the deploy script), LF-normalized, laid out the way the server expects. ASCII-only comments.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$src  = Join-Path $root "bot"
$dpl  = Join-Path $root "deploy"
$stage = Join-Path $env:TEMP "demo_code"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force -Path (Join-Path $stage "bot\handlers")   | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage "bot\webapp")     | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage "bot\webapp\static") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stage "test_data")      | Out-Null

# server-path  ->  repo file
$map = @(
  @{ out = "bot\db.py";                 in = (Join-Path $src "db.py") },
  @{ out = "bot\render.py";             in = (Join-Path $src "render.py") },
  @{ out = "bot\handlers\start.py";     in = (Join-Path $src "handlers\start.py") },
  @{ out = "bot\webapp\app.py";         in = (Join-Path $src "webapp\app.py") },
  @{ out = "bot\webapp\static\index.html"; in = (Join-Path $src "webapp\static\index.html") },
  @{ out = "test_data\listings.json";   in = (Join-Path $dpl "test_data\listings.json") },
  @{ out = "test_data\seed_test_data.py"; in = (Join-Path $dpl "test_data\seed_test_data.py") },
  @{ out = "demo_board_deploy.sh";      in = (Join-Path $dpl "demo_board_deploy.sh") }
)
foreach ($m in $map) {
  $out = Join-Path $stage $m.out
  $raw = [IO.File]::ReadAllText($m.in)
  $lf  = $raw -replace "`r`n", "`n"
  [IO.File]::WriteAllText($out, $lf, (New-Object Text.UTF8Encoding($false)))
}
$tgz = Join-Path $env:TEMP "demo_code.tgz"
if (Test-Path $tgz) { Remove-Item -Force $tgz }
Push-Location $stage
tar czf $tgz bot/db.py bot/render.py bot/handlers/start.py bot/webapp/app.py bot/webapp/static/index.html test_data/listings.json test_data/seed_test_data.py demo_board_deploy.sh
Pop-Location
Write-Output ("built: " + $tgz + "  size=" + (Get-Item $tgz).Length)
# prove none of the text files arrived with a stray CR (would break the remote bash shebang)
Get-ChildItem -Recurse $stage | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
  $rel = $_.FullName.Substring($stage.Length + 1)
  $bytes = [IO.File]::ReadAllBytes($_.FullName)
  $cr = ($bytes | Where-Object { $_ -eq 13 }).Count
  Write-Output ("{0,-32} CR={1}" -f $rel, $cr)
}
