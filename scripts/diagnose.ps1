# diagnose.ps1 - Starts the game with a GPU-renderer diagnostic option (for bug reports).
#   powershell -ExecutionPolicy Bypass -File scripts\diagnose.ps1 <mode>
#
#   1 = software texture sampling (no hardware sampler shortcut)
#   2 = no primitive state reuse
#   3 = slow framebuffer copy path
#   4 = locked command queue (no lock-free ring)
#   5 = 1-4 together
#   6 = CPU renderer (slow, no GPU)
#   7 = dump GPU images to <work>\gldump after ~10 s (keep the game open for 20 s)
#   8 = single GS thread (VU1 + GS on the game thread, as in older builds)
#   9 = separate GL thread with its own context (older GPU path)
# Output: <work>\diagnose_<mode>.log
param([int]$Mode = 1)

$Root = Split-Path -Parent $PSScriptRoot
$Work = if ($env:SVS_WORK) { $env:SVS_WORK } else { "C:\svs" }
$Game = Join-Path $Root "game"
$Exe  = Join-Path $Work "build-windows\ps2xRuntime\ps2EntryRunner.exe"

switch ($Mode) {
    1 { $env:SVS_GL_NO_HWS = "1" }
    2 { $env:SVS_GL_NO_FASTPRIM = "1" }
    3 { $env:SVS_GS_SLOW_RECT = "1" }
    4 { $env:SVS_GL_NO_RING = "1" }
    5 { $env:SVS_GL_NO_HWS = "1"; $env:SVS_GL_NO_FASTPRIM = "1"; $env:SVS_GS_SLOW_RECT = "1"; $env:SVS_GL_NO_RING = "1" }
    6 { $env:SVS_RENDERER = "cpu" }
    7 { $d = Join-Path $Work "gldump"; New-Item -ItemType Directory -Force $d | Out-Null; $env:SVS_GL_DUMP = $d; $env:SVS_GL_CHECK = "1" }
    8 { $env:SVS_GS_THREAD = "0" }
    9 { $env:SVS_GL_THREAD = "1" }
}
$env:SVS_GL_STATS = "1"
$env:SVS_FPS = "1"
Write-Host "Mode $Mode. Close the game window when you are done."
$p = Start-Process -FilePath $Exe -ArgumentList "`"$Game\SLES_530.78`"" -WorkingDirectory $Game -NoNewWindow -PassThru `
     -RedirectStandardOutput (Join-Path $Work "diagnose_$Mode.out.txt") -RedirectStandardError (Join-Path $Work "diagnose_$Mode.log")
$p.WaitForExit()
Write-Host "Done (mode $Mode)."
