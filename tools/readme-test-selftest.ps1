$ErrorActionPreference = 'Stop'
$runner = Join-Path $PSScriptRoot 'readme-test.ps1'
$fixture = Join-Path ([IO.Path]::GetTempPath()) "docs-test-$([Guid]::NewGuid().ToString('n'))"
New-Item -ItemType Directory -Path (Join-Path $fixture 'docs/guides') -Force | Out-Null
Push-Location $fixture
try {
    @'
<!-- test -->
```powershell
Set-Content -Path example-result.txt -Value verified
```
'@ | Set-Content docs/guides/example.md
    '# No runnable block' | Set-Content docs/index.md
    & $runner -Docs -DryRun
    if (Test-Path example-result.txt) { throw 'DryRun executed a block' }
    & $runner -Docs -Force
    if ((Get-Content example-result.txt) -ne 'verified') { throw 'Nested documentation block did not execute' }
    @'
<!-- test -->
```powershell
throw 'expected fixture failure'
```
'@ | Set-Content docs/failure.md
    $failed = $false
    try { & $runner -Docs -Force } catch { $failed = $true }
    if (-not $failed) { throw 'Documentation failure did not fail the runner' }
    foreach ($text in @('<!-- test -->', "<!-- test -->`n``````powershell`nWrite-Output unfinished")) {
        $text | Set-Content malformed.md
        $failed = $false
        try { & $runner -Readme malformed.md -DryRun } catch { $failed = $_ -match 'missing (opening|closing) code fence' }
        if (-not $failed) { throw 'Malformed marked fence was not rejected' }
    }
    @'
<!-- test -->
```powershell
pwsh -NoProfile -Command 'exit 7'
Set-Content -Path after-failure.txt -Value unexpected
```
'@ | Set-Content native-failure.md
    $failed = $false
    try { & $runner -Readme native-failure.md -Force } catch { $failed = $true }
    if (-not $failed -or (Test-Path after-failure.txt)) { throw 'Native command failure was hidden' }
    if (Get-Command sh -ErrorAction SilentlyContinue) {
        @'
<!-- test -->
```sh
false
printf unexpected > after-failure.txt
```
'@ | Set-Content shell-failure.md
        $failed = $false
        try { & $runner -Readme shell-failure.md -Force } catch { $failed = $true }
        if (-not $failed -or (Test-Path after-failure.txt)) { throw 'Shell command failure was hidden' }
    } else {
        Write-Host 'skip shell failure fixture: sh is unavailable'
    }
    Write-Host 'docs runner selftest: passed'
} finally {
    Pop-Location
    $resolved = (Resolve-Path -LiteralPath $fixture).Path
    if (-not $resolved.StartsWith([IO.Path]::GetTempPath(), [StringComparison]::OrdinalIgnoreCase)) { throw 'Fixture escaped the temp directory' }
    Remove-Item -LiteralPath $fixture -Recurse -Force
}
