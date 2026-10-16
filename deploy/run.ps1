# Windows deploy helper: copy a repo file to the VPS and run it there, with clean output.
#
#   powershell -File deploy\run.ps1 deploy\apply_backup.sh "bash /tmp/apply_backup.sh"
#   powershell -File deploy\run.ps1 bot\keyboards.py "cd /opt/imobiliare/bot && python -m py_compile keyboards.py" -RemoteDir /opt/imobiliare/bot
#
# Why this exists: Windows PowerShell renders a long inline ssh payload one character per line and
# turns any remote stderr into a NativeCommandError, which hides the real result. So we redirect the
# whole remote session into a temp file and print it back with the console forced to UTF-8.
# Keep this file ASCII-only: PowerShell 5.1 reads .ps1 as ANSI unless there is a BOM, and Cyrillic
# comments then break the parser in the middle of the script.
# The VPS address lives in ACCESS.md (gitignored), never in this file.
# Exit code of this script = exit code of the REMOTE command (stderr of the remote is merged into
# its stdout, so nothing here is a PowerShell error).
param(
    [Parameter(Mandatory = $true)][string]$Local,
    [Parameter(Mandatory = $true)][string]$RemoteCmd,
    [string]$User = "root",
    # where to put the file on the server: /tmp for scripts, the app dir for code
    [string]$RemoteDir = "/tmp"
)

$ErrorActionPreference = "Continue"
$root = Split-Path $PSScriptRoot -Parent
$access = Join-Path $root "ACCESS.md"
if (-not (Test-Path $access)) { Write-Error "ACCESS.md not found next to the repo"; exit 1 }
$hit = Select-String -Path $access -Pattern "^IP:\s*(\S+)" | Select-Object -First 1
if (-not $hit) { Write-Error "no 'IP: <address>' line in ACCESS.md"; exit 1 }
$ip = $hit.Matches[0].Groups[1].Value
$target = $User + "@" + $ip

# PowerShell decodes the stdout of any native command using [console]::OutputEncoding, and the remote
# side prints UTF-8. Without this every Cyrillic line comes back as mojibake, which makes a deploy
# log unreadable exactly when we need to read it. Must happen BEFORE ssh is started.
[console]::OutputEncoding = [text.encoding]::UTF8

$name = Split-Path $Local -Leaf
$out = Join-Path $env:TEMP ("run-" + [io.path]::GetRandomFileName() + ".txt")

scp -o BatchMode=yes $Local ($target + ":" + $RemoteDir + "/" + $name) 2>&1 | Out-Null
# the remote shell merges its own stderr into stdout, so nothing arrives as a PowerShell error record
# (otherwise every remote warning renders as a red NativeCommandError block on top of the real output).
# The merge must wrap the WHOLE command list in { ...; } 2>&1 - a trailing 2>&1 would only apply to
# the last command and the earlier stderr would still come back as error records.
# Image files and archives are binary: sed 's/\r$//' would silently corrupt them, so only text gets the
# CRLF strip. (A .tgz/.gz with a 0x0D byte in its deflate stream would otherwise arrive undecodable.)
$ext = [IO.Path]::GetExtension($name).ToLower()
if ($ext -eq ".png" -or $ext -eq ".jpg" -or $ext -eq ".jpeg" -or $ext -eq ".tgz" -or $ext -eq ".gz" -or $name.ToLower().EndsWith(".tar.gz")) {
    $cmd = "{ " + $RemoteCmd + "; } 2>&1"
} else {
    $cmd = "{ sed -i 's/\r$//' " + $RemoteDir + "/" + $name + "; " + $RemoteCmd + "; } 2>&1"
}
ssh -o BatchMode=yes $target $cmd | Out-File -FilePath $out -Encoding utf8
$code = $LASTEXITCODE

Write-Output ("--- " + $name + " -> " + $ip + " -> exit=" + $code + " ---")
Get-Content $out -Encoding UTF8
Write-Output ("--- full log: " + $out + " ---")
# pass the REMOTE exit code out, so this helper can be used in a longer "and stop if it failed" chain
exit $code
