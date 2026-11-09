# run.ps1 - Starts the game and saves everything it prints to <work>\game.log
#   powershell -ExecutionPolicy Bypass -File scripts\run.ps1
#
# In game: F3 shows/hides the FPS counter, F4 toggles the 30 fps cap, F1 opens the debugger.

$Root = Split-Path -Parent $PSScriptRoot
$Work = if ($env:SVS_WORK) { $env:SVS_WORK } else { "C:\svs" }
$Game = Join-Path $Root "game"
$Exe  = Join-Path $Work "build-windows\ps2xRuntime\ps2EntryRunner.exe"
$Log  = Join-Path $Work "game.log"

if (-not (Test-Path $Exe)) { throw "Not built yet: run scripts\build.ps1 first." }
if (-not (Test-Path (Join-Path $Game "SLES_530.78"))) { throw "Game files missing in $Game (see README, 'Game files')." }

"=== $(Get-Date) ===" | Out-File $Log -Encoding utf8
# performance numbers in the log every 2 s (fps, GPU time, waits, VU1)
$env:SVS_GL_STATS = "1"
$env:SVS_FPS = "1"
# which music track plays and when
$env:SVS_MUS_TRACE = "1"
Write-Host "Starting the game... (output goes to $Log)"

$p = Start-Process -FilePath $Exe -ArgumentList "`"$Game\SLES_530.78`"" `
     -WorkingDirectory $Game -NoNewWindow -PassThru `
     -RedirectStandardOutput (Join-Path $Work "game.out.txt") -RedirectStandardError (Join-Path $Work "game.err.txt")
$p.WaitForExit()

$code = $p.ExitCode
Get-Content (Join-Path $Work "game.out.txt"), (Join-Path $Work "game.err.txt") -ErrorAction SilentlyContinue | Add-Content $Log
$hex = "0x{0:X8}" -f $code
$why = switch ($hex) {
    "0x00000000" { "closed normally" }
    "0xC0000005" { "CRASH: access violation" }
    "0xC00000FD" { "CRASH: stack overflow" }
    "0xC0000409" { "CRASH: abort / internal error" }
    "0x80000003" { "CRASH: breakpoint / assert" }
    default      { "exit code $hex" }
}
"=== Exit: $hex ($why) ===" | Add-Content $Log
Write-Host "`nThe game exited: $why (log: $Log)"
