# build.ps1 - Builds the Spy vs. Spy port for Windows in one step.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build.ps1
#
# What it does:
#   1. Opens the Visual Studio x64 developer environment.
#   2. Clones PS2Recomp (pinned commit) into the work folder (default C:\svs) and applies
#      patches\ps2recomp-svs.patch, copying only the files whose content changed (so Ninja
#      only rebuilds what changed).
#   3. Copies the generated game code (generated\output_fixed, made by regenerate.ps1),
#      overrides\svs_overrides.cpp and, if present, generated\svs_vu1_aot.inc.
#   4. Configures with CMake and builds with as many jobs as your free RAM allows.
# Everything is logged to <work>\build.log. The work folder is kept outside OneDrive on
# purpose (tens of thousands of build files). Set SVS_WORK to use another folder.

$ErrorActionPreference = "Continue"   # errors are checked by hand with $LASTEXITCODE
$Root   = Split-Path -Parent $PSScriptRoot
$Work   = if ($env:SVS_WORK) { $env:SVS_WORK } else { "C:\svs" }
$Recomp = Join-Path $Work "PS2Recomp"
$Out    = Join-Path $Work "output_fixed"
$Build  = Join-Path $Work "build-windows"
$Gen    = Join-Path $Root "generated\output_fixed"
$Aot    = Join-Path $Root "generated\svs_vu1_aot.inc"
$Commit = "e563698"

New-Item -ItemType Directory -Force $Work | Out-Null
Start-Transcript -Path (Join-Path $Work "build.log") -Append | Out-Null

function Step($t) { Write-Host "`n=== $t ===" -ForegroundColor Cyan }
function Fail($t) { Stop-Transcript | Out-Null; throw $t }

if (-not (Test-Path (Join-Path $Gen "register_functions.cpp"))) {
    Fail "No generated game code in $Gen. Run scripts\regenerate.ps1 first (see README)."
}

# --- 1. Visual Studio x64 environment ------------------------------------------------
Step "Visual Studio environment (x64)"
if ($env:VSCMD_ARG_TGT_ARCH -ne "x64") {
    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    if (-not (Test-Path $vswhere)) { Fail "Visual Studio not found (vswhere.exe)." }
    $vs = & $vswhere -latest -prerelease -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
    if (-not $vs) { Fail "Visual Studio is missing the 'Desktop development with C++' workload." }
    & (Join-Path $vs "Common7\Tools\Launch-VsDevShell.ps1") -Arch amd64 -HostArch amd64 -SkipAutomaticLocation | Out-Null
}
foreach ($tool in "cl", "cmake", "ninja", "git") {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        if ($tool -eq "git") { Fail "Git is missing: install it from https://git-scm.com" }
        Fail "'$tool' is missing: add 'C++ CMake tools for Windows' in the Visual Studio installer."
    }
}
Write-Host "cl:    $((Get-Command cl).Source)"
Write-Host "cmake: $(cmake --version | Select-Object -First 1)"

# --- 2. PS2Recomp + patch -------------------------------------------------------------
Step "PS2Recomp $Commit + patch"
if (-not (Test-Path (Join-Path $Recomp ".git"))) {
    git clone https://github.com/ran-j/PS2Recomp.git $Recomp
    if ($LASTEXITCODE -ne 0) { Fail "Could not clone PS2Recomp (no internet?)." }
    git -C $Recomp checkout --quiet --detach $Commit
}
$Patch = Join-Path $Root "patches\ps2recomp-svs.patch"
# The patch is applied in a separate worktree (<work>\patch-stage) and only files whose
# content changed are copied into PS2Recomp, so Ninja rebuilds only what the patch touches.
$Stage = Join-Path $Work "patch-stage"
if (-not (Test-Path (Join-Path $Stage ".git"))) {
    git -C $Recomp worktree add --force --detach $Stage $Commit
    if ($LASTEXITCODE -ne 0) { Fail "Could not create $Stage." }
}
git -C $Stage checkout --quiet --force --detach $Commit
git -C $Stage checkout --quiet --force -- .
git -C $Stage clean -fdq   # only the patch worktree (<work>\patch-stage)
git -C $Stage apply $Patch
if ($LASTEXITCODE -ne 0) { Fail "The patch did not apply." }
# new files added by the patch do not show up in "git diff"
$files = @(git -C $Stage diff --name-only $Commit) + @(git -C $Stage ls-files --others --exclude-standard) + @(git -C $Recomp diff --name-only $Commit -- . ":!ps2xRuntime/src/runner" ":!ps2xRecomp")
$files = $files | Where-Object { $_ } | Sort-Object -Unique
$changed = 0
foreach ($f in $files) {
    $src = Join-Path $Stage $f
    $dst = Join-Path $Recomp $f
    if (-not (Test-Path $src)) { continue }
    if ((Test-Path $dst) -and ((Get-FileHash $src).Hash -eq (Get-FileHash $dst).Hash)) { continue }
    New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
    Copy-Item -Force $src $dst
    Write-Host "  updated: $f"
    $changed++
}
Write-Host "$changed runtime files updated."

# Precompiled VU1 microprograms (optional, made from your own copy of the game by
# scripts\vu1-aot.ps1). Without it the VU1 runs in the (slower) interpreter.
$vuDir = Join-Path $Recomp "ps2xRuntime\src\lib\vu"
$aotDst = Join-Path $vuDir "svs_vu1_aot.inc"
if (Test-Path $Aot) {
    if (-not (Test-Path $aotDst) -or (Get-FileHash $Aot).Hash -ne (Get-FileHash $aotDst).Hash) {
        Copy-Item -Force $Aot $aotDst
        # ps2_vu1_core.cpp includes it through __has_include: make sure it is rebuilt
        (Get-Item (Join-Path $vuDir "ps2_vu1_core.cpp")).LastWriteTime = Get-Date
        Write-Host "  updated: precompiled VU1 (svs_vu1_aot.inc)"
    }
} else {
    Write-Host "  no generated\svs_vu1_aot.inc: VU1 runs in the interpreter (see scripts\vu1-aot.ps1)" -ForegroundColor Yellow
}

# --- 3. Game code -------------------------------------------------------------------
Step "Copying generated game code"
# recomp\obsolete_files.txt: files left over from older generations (ignored, never deleted)
$skip = @(Get-Content (Join-Path $Root "recomp\obsolete_files.txt") -ErrorAction SilentlyContinue | Where-Object { $_ })
if ($skip.Count -gt 0) { robocopy $Gen $Out /MIR /NFL /NDL /NJH /NJS /NP /XF @skip | Out-Null }
else { robocopy $Gen $Out /MIR /NFL /NDL /NJH /NJS /NP | Out-Null }
$runner = Join-Path $Recomp "ps2xRuntime\src\runner"
# robocopy only copies what changed and keeps the original timestamps,
# so Ninja does not rebuild the ~10000 generated files every time.
$keep = @{}; Get-ChildItem $Out -Filter *.cpp | ForEach-Object { $keep[$_.Name] = $true }
Get-ChildItem $runner -Filter *.cpp | Where-Object {
    ($_.Name -like "sub_*" -or $_.Name -like "FUN_*" -or $_.Name -like "entry_*") -and -not $keep[$_.Name]
} | Remove-Item -ErrorAction SilentlyContinue      # leftovers inside the build copy only
robocopy $Out $runner *.cpp /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy $Out (Join-Path $Recomp "ps2xRuntime\include") *.h /NFL /NDL /NJH /NJS /NP | Out-Null
robocopy (Join-Path $Root "overrides") $runner *.cpp /NFL /NDL /NJH /NJS /NP | Out-Null
Write-Host "$((Get-ChildItem $runner -Filter *.cpp).Count) .cpp files ready."

# --- 4. Jobs from free RAM ----------------------------------------------------------
$cores = [Environment]::ProcessorCount
$freeGB = [math]::Floor((Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1MB)
$jobs = [math]::Max(1, [math]::Min($cores, [math]::Floor($freeGB / 3)))
Write-Host "CPU: $cores threads, free RAM: $freeGB GB -> $jobs parallel jobs"

# --- 5. Configure and build ---------------------------------------------------------
Step "Configuring (CMake)"
cmake -S $Recomp -B $Build -G Ninja -DCMAKE_BUILD_TYPE=Release `
  "-DCMAKE_CXX_FLAGS=/DWIN32 /D_WINDOWS /EHsc /GR /bigobj" `
  -DPS2X_BUILD_RECOMP=OFF -DPS2X_BUILD_ANALYZER=OFF -DPS2X_BUILD_TEST=OFF -DPS2X_BUILD_STUDIO=OFF `
  -DPS2X_ENABLE_AGRESSIVE_LOGS=OFF -DPS2X_RUNNER_UNITY_BUILD_BATCH_SIZE=16 -DPS2X_ENABLE_SCCACHE=OFF
if ($LASTEXITCODE -ne 0) { Fail "CMake configuration failed (see $Work\build.log)." }

Step "Building (the first build is long; later ones only rebuild what changed)"
$t0 = Get-Date
cmake --build $Build --parallel $jobs
if ($LASTEXITCODE -ne 0) {
    Write-Host "`nSomething failed. Retrying what is left with 1 job (in case it ran out of memory)..." -ForegroundColor Yellow
    cmake --build $Build --parallel 1
    if ($LASTEXITCODE -ne 0) { Fail "Build failed. See the end of $Work\build.log" }
}
$exe = Join-Path $Build "ps2xRuntime\ps2EntryRunner.exe"
Step "DONE in $([int]((Get-Date) - $t0).TotalMinutes) minutes"
Write-Host "Executable: $exe"
Write-Host "Play:                       scripts\run.ps1"
Write-Host "Rebuild after small edits:  scripts\quick.ps1"
Stop-Transcript | Out-Null
