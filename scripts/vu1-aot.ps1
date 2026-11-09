# vu1-aot.ps1 - Precompiles the game's VU1 microprograms to C++ (big speed-up).
#   powershell -ExecutionPolicy Bypass -File scripts\vu1-aot.ps1
#
# 1. Starts the game with SVS_VU_AOT_DUMP: every VU1 code image it runs is saved to
#    <work>\vudump. Play a bit (menus + one or two levels), then close the game.
# 2. tools\vu1_aot.py turns the dumps into generated\svs_vu1_aot.inc.
# 3. Run scripts\build.ps1 (or quick.ps1 after build.ps1) to compile it in.
# Programs that were not dumped keep running in the interpreter, so a partial dump is
# safe; you can repeat step 1 later to cover more levels (dumps accumulate).
# The generated file is derived from the game's code: keep it out of the repository.

$Root = Split-Path -Parent $PSScriptRoot
$Work = if ($env:SVS_WORK) { $env:SVS_WORK } else { "C:\svs" }
$Dump = Join-Path $Work "vudump"
$Inc  = Join-Path $Root "generated\svs_vu1_aot.inc"
$py = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } elseif (Get-Command py -ErrorAction SilentlyContinue) { "py" } else { $null }
if (-not $py) { throw "Python 3 is missing: install it from https://www.python.org (tick 'Add to PATH')." }

New-Item -ItemType Directory -Force $Dump | Out-Null
$env:SVS_VU_AOT_DUMP = $Dump
Write-Host "Play for a while, then close the game. Dumps go to $Dump" -ForegroundColor Cyan
& (Join-Path $PSScriptRoot "run.ps1")
Remove-Item Env:SVS_VU_AOT_DUMP

New-Item -ItemType Directory -Force (Split-Path $Inc) | Out-Null
& $py (Join-Path $Root "tools\vu1_aot.py") $Dump $Inc
if ($LASTEXITCODE -ne 0) { throw "vu1_aot.py failed." }
Write-Host "Done: $Inc. Now run scripts\build.ps1" -ForegroundColor Green
