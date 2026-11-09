# quick.ps1 - Rebuilds only what changed and starts the game.
# Use it after scripts\build.ps1 has run once (it does not re-apply the patch).
#   powershell -ExecutionPolicy Bypass -File scripts\quick.ps1 [-NoRun]
#
# Typical loop for game-specific fixes:
#   1. Edit overrides\svs_overrides.cpp
#   2. Run this script: it compiles that file + links (1-3 minutes) and starts the game.
param([switch]$NoRun)

$ErrorActionPreference = "Continue"
$Root   = Split-Path -Parent $PSScriptRoot
$Work   = if ($env:SVS_WORK) { $env:SVS_WORK } else { "C:\svs" }
$Recomp = Join-Path $Work "PS2Recomp"
$Build  = Join-Path $Work "build-windows"
$runner = Join-Path $Recomp "ps2xRuntime\src\runner"
if (-not (Test-Path (Join-Path $Build "build.ninja"))) { throw "Run scripts\build.ps1 once first." }

if ($env:VSCMD_ARG_TGT_ARCH -ne "x64") {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $vs = & $vswhere -latest -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    & (Join-Path $vs "Common7\Tools\Launch-VsDevShell.ps1") -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
}

robocopy (Join-Path $Root "overrides") $runner *.cpp /NFL /NDL /NJH /NJS /NP | Out-Null

$t0 = Get-Date
ninja -C $Build -j ([Environment]::ProcessorCount) ps2EntryRunner
if ($LASTEXITCODE -ne 0) { throw "Build error (see the message above)." }
Write-Host "`nBuilt in $([int]((Get-Date) - $t0).TotalSeconds) s" -ForegroundColor Green

if (-not $NoRun) { & (Join-Path $PSScriptRoot "run.ps1") }
