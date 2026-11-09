# regenerate.ps1 - Generates the game's C++ code from YOUR copy of the game.
#   powershell -ExecutionPolicy Bypass -File scripts\regenerate.ps1
#
# Needs: game\SLES_530.78 (see README, "Game files"), Visual Studio C++, Git and Python 3.
# Steps:
#   1. Builds PS2Recomp's recompiler (ps2_recomp.exe) in <work>\build-recomp.
#   2. Runs it with recomp\spyvsspy.toml + recomp\functions.csv -> generated\output_fixed.
#   3. Applies tools\fix_sqrt.py (SQRT.S operand fix) and tools\compact_register.py
#      (makes register_functions.cpp compile in seconds instead of an hour).
#   4. Checks the result against the reference build (tools\verify_generated.py).
# The output folder must not exist yet (rename or delete an old generated\output_fixed first).

$ErrorActionPreference = "Continue"
$Root   = Split-Path -Parent $PSScriptRoot
$Work   = if ($env:SVS_WORK) { $env:SVS_WORK } else { "C:\svs" }
$Recomp = Join-Path $Work "PS2Recomp"
$BuildR = Join-Path $Work "build-recomp"
$Elf    = Join-Path $Root "game\SLES_530.78"
$Csv    = Join-Path $Root "recomp\functions.csv"
$Out    = Join-Path $Root "generated\output_fixed"
$Commit = "e563698"
$ElfMd5 = "032B00F673340701DF5A77F5453FC2A5"

function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }

if (-not (Test-Path $Elf)) { throw "Missing $Elf (copy the game files into game\, see README)." }
if (Test-Path $Out) { throw "$Out already exists. Rename or delete it first if you really want to regenerate." }
$md5 = (Get-FileHash -Algorithm MD5 $Elf).Hash
if ($md5 -ne $ElfMd5) {
    Write-Host "WARNING: SLES_530.78 has MD5 $md5, expected $ElfMd5 (PAL SLES-53078 v1.01)." -ForegroundColor Yellow
    Write-Host "Other versions are not supported: the function map and overrides will not match." -ForegroundColor Yellow
}
$py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { $null }
if (-not $py) { throw "Python 3 is missing: install it from https://www.python.org (tick 'Add to PATH')." }

Step "Visual Studio environment (x64)"
if ($env:VSCMD_ARG_TGT_ARCH -ne "x64") {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { throw "Visual Studio not found (vswhere.exe)." }
    $vs = & $vswhere -latest -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    & (Join-Path $vs "Common7\Tools\Launch-VsDevShell.ps1") -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
}

Step "PS2Recomp $Commit"
New-Item -ItemType Directory -Force $Work | Out-Null
if (-not (Test-Path (Join-Path $Recomp ".git"))) {
    git clone https://github.com/ran-j/PS2Recomp.git $Recomp
    if ($LASTEXITCODE -ne 0) { throw "Could not clone PS2Recomp (no internet?)." }
    git -C $Recomp checkout --quiet --detach $Commit
}

Step "Building the recompiler"
cmake -S $Recomp -B $BuildR -G Ninja -DCMAKE_BUILD_TYPE=Release `
  -DPS2X_BUILD_RECOMP=ON -DPS2X_BUILD_RUNTIME=OFF -DPS2X_BUILD_ANALYZER=OFF -DPS2X_BUILD_TEST=OFF -DPS2X_BUILD_STUDIO=OFF
if ($LASTEXITCODE -ne 0) { throw "CMake configuration of the recompiler failed." }
cmake --build $BuildR --target ps2_recomp
if ($LASTEXITCODE -ne 0) { throw "Building the recompiler failed." }
$tool = Get-ChildItem $BuildR -Recurse -Filter ps2_recomp.exe | Select-Object -First 1
if (-not $tool) { throw "ps2_recomp.exe not found in $BuildR." }

Step "Recompiling the game (a few seconds)"
New-Item -ItemType Directory -Force (Split-Path $Out) | Out-Null
$toml = Join-Path $Work "spyvsspy.toml"
$cfg = Get-Content (Join-Path $Root "recomp\spyvsspy.toml") -Raw
$cfg = $cfg.Replace("@ELF@", ($Elf -replace '\\', '/'))
$cfg = $cfg.Replace("@OUT@", ($Out -replace '\\', '/'))
$cfg = $cfg.Replace("@CSV@", ($Csv -replace '\\', '/'))
[System.IO.File]::WriteAllText($toml, $cfg)   # UTF-8 without BOM (the TOML parser may not like a BOM)
& $tool.FullName $toml | Out-File (Join-Path $Work "regenerate.log") -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw "The recompiler failed (see $Work\regenerate.log)." }

Step "Post-processing"
& $py (Join-Path $Root "tools\fix_sqrt.py") $Out
& $py (Join-Path $Root "tools\compact_register.py") (Join-Path $Out "register_functions.cpp")

Step "Checking against the reference build"
& $py (Join-Path $Root "tools\verify_generated.py") $Out
Write-Host "`nNext: scripts\build.ps1"
