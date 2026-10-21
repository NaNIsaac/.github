# Runs the setup steps in a README as a new user would, so the README cannot drift from what works.
# A comment before a fenced block marks it:
#   <!-- test -->          run the block on any OS
#   <!-- test windows -->  run it on Windows only; <!-- test unix --> on Linux and macOS only
#   <!-- test console -->  run each "$ command" line and compare its output with the lines below it
# The blocks install software and edit shell start-up files, so this refuses to run outside CI
# unless -Force is given.   pwsh readme-test.ps1 [-Readme README.ko.md] [-DryRun]
param(
    [string]$Readme = 'README.md',
    [switch]$DryRun,
    [switch]$Force
)
$ErrorActionPreference = 'Stop'
if (-not ($DryRun -or $Force -or $env:CI)) { throw 'this changes the machine; run it on CI, or pass -DryRun or -Force' }
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$os = $IsWindows ? 'windows' : 'unix'
$pwsh = (Get-Process -Id $PID).Path
$lines = Get-Content -Encoding utf8 $Readme

# Every block runs in a new process, so a PATH or profile change counts only if it was really saved.
function Update-Path {
    $env:PATH = if ($IsWindows) {
        [Environment]::GetEnvironmentVariable('PATH', 'Machine') + ';' + [Environment]::GetEnvironmentVariable('PATH', 'User')
    } else { "$HOME/.local/bin:$env:PATH" }
}

function Invoke-Block([string]$lang, [string[]]$body) {
    $ps = $lang -eq 'powershell'
    $file = Join-Path ([IO.Path]::GetTempPath()) ("readme-$([Guid]::NewGuid().ToString('n'))." + ($ps ? 'ps1' : 'sh'))
    Set-Content -Encoding utf8NoBOM $file ($ps ? @('$ErrorActionPreference = ''Stop''') + $body : $body)
    $global:LASTEXITCODE = 0
    try {
        $out = $ps ? (& $pwsh -File $file 2>&1) : (sh $file 2>&1)
        if ($LASTEXITCODE) { throw "exit code ${LASTEXITCODE}`n$($out -join "`n")" }
    } finally { Remove-Item $file -ErrorAction SilentlyContinue }
}

function Test-Output([string]$command, [string[]]$want) {
    $got = $IsWindows ? (& $pwsh -NoProfile -Command $command 2>&1) : (sh -c $command 2>&1)
    $got = @($got | ForEach-Object { "$_".TrimEnd() })
    if (($got -join "`n") -ne ($want -join "`n")) {
        throw "`$ $command`n  expected: $($want -join ' | ')`n  got:      $($got -join ' | ')"
    }
}

function Test-Console([string[]]$body) {
    $command = $null; $want = @()
    foreach ($line in $body) {
        if ($line -match '^\$ (.+)$') {
            if ($command) { Test-Output $command $want }
            $command = $Matches[1]; $want = @()
        } else { $want += $line.TrimEnd() }
    }
    if ($command) { Test-Output $command $want }
}

$marked = 0
$ran = 0
$failures = [Collections.Generic.List[string]]::new()
for ($i = 0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -notmatch '^<!-- test( (windows|unix|console))? -->$') { continue }
    $marked++
    $kind = $Matches[2]
    if ($kind -in @('windows', 'unix') -and $kind -ne $os) { continue }
    $start = $i + 1
    while ($lines[$start] -notmatch '^```') { $start++ }
    $end = $start + 1
    while ($lines[$end] -notmatch '^```') { $end++ }
    $body = $lines[($start + 1)..($end - 1)]
    $label = "$(Split-Path -Leaf $Readme):$($i + 1)"
    $ran++
    if ($DryRun) { Write-Host "would run $label ($($kind ? $kind : 'any')): $($body[0])"; continue }
    Update-Path
    try {
        if ($kind -eq 'console') { Test-Console $body } else { Invoke-Block ($lines[$start] -replace '^```') $body }
        Write-Host "ok   $label"
    } catch {
        $failures.Add($label)
        Write-Host "FAIL $label`n     $_"
    }
}
if (-not $marked) { throw "no <!-- test --> blocks in $Readme" }
if ($failures.Count) { throw "$($failures.Count) README step(s) failed: $($failures -join ', ')" }
Write-Host ($ran ? "readme: $ran step(s) ok on $os" : "readme: no steps for $os in $Readme")
