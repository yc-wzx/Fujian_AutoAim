param(
    [ValidateSet('start', 'stop', 'status', 'trial')][string]$Action = 'start',
    [ValidateRange(1, 50)][int]$ShotBudget = 5,
    [ValidateRange(5, 35)][double]$BulletSpeed = 24.5,
    [ValidateRange(0, 400)][double]$SimDelayMs = 200,
    [ValidateRange(-10, 10)][double]$PitchBiasDeg = 1.5,
    [switch]$CenterHold = $true,
    [ValidateRange(1, 15)][double]$FireWindowDeg = 8,
    [ValidateRange(200, 2000)][int]$SettleMs = 300,
    [switch]$Prearm = $true,
    [switch]$PrepareDuringSettle = $false,
    [switch]$FixedCenterPrediction = $false,
    [ValidateRange(0, 31)][int]$HitGoal = 0,
    [ValidatePattern('^[a-z0-9][a-z0-9-]{0,47}$')][string]$TrialName = 'diagnostic'
)
$ErrorActionPreference = 'Stop'
if ($Action -eq 'trial' -and $HitGoal -gt $ShotBudget) { throw 'HitGoal exceeds ShotBudget.' }
if ($PrepareDuringSettle -and ($Action -ne 'trial' -or -not $Prearm)) { throw 'PrepareDuringSettle requires trial and Prearm.' }
if ($FixedCenterPrediction) { throw 'FixedCenterPrediction is not available on this branch; the 26.10.22 mainline owns that implementation.' }
$repoRoot = Split-Path -Parent $PSScriptRoot
$logRoot = Join-Path $repoRoot 'logs\gestalt'
$stopFile = Join-Path $logRoot 'live.stop'
$stateFile = Join-Path $logRoot 'live-processes.json'
New-Item -ItemType Directory -Path $logRoot -Force | Out-Null

function Read-LiveJson([string]$Path) {
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        try {
            $value = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
            if ($null -ne $value) { return $value }
        } catch {
            if ($attempt -eq 11) { throw }
        }
        Start-Sleep -Milliseconds 50
    }
    throw "No complete JSON status in $Path"
}
function Get-LiveState {
    if (-not (Test-Path -LiteralPath $stateFile)) { return $null }
    return Read-LiveJson $stateFile
}
function Test-LiveProcess($ProcessId, $ExpectedName) {
    if (-not $ProcessId) { return $false }
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    return $process -and $process.ProcessName -eq $ExpectedName
}

$saved = Get-LiveState
if ($Action -eq 'status') {
    [pscustomobject]@{
        target_running = [bool](Test-LiveProcess $saved.target_pid 'node')
        follow_running = [bool](Test-LiveProcess $saved.follow_pid 'gestalt_vision_probe')
        stop_requested = Test-Path -LiteralPath $stopFile
        target_status = Join-Path $logRoot 'live-target.jsonl.summary.json'
        follow_status = if ($saved.follow_status) { Join-Path $repoRoot $saved.follow_status } else { Join-Path $logRoot 'live-follow.json' }
    } | ConvertTo-Json
    exit 0
}
if ($Action -eq 'stop') {
    Set-Content -LiteralPath $stopFile -Value 'stop' -Encoding ascii
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Test-LiveProcess $saved.target_pid 'node') -and
            -not (Test-LiveProcess $saved.follow_pid 'gestalt_vision_probe')) { break }
        Start-Sleep -Milliseconds 250
    }
    Write-Output 'Stop requested. Both processes release their controls and exit.'
    exit 0
}
$nodePath = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
if (-not (Test-Path -LiteralPath $nodePath)) { $nodePath = (Get-Command node -ErrorAction Stop).Source }
$visionPath = Join-Path $repoRoot 'build\gestalt_vision_probe.exe'
if (-not (Test-Path -LiteralPath $visionPath)) { throw 'Build gestalt_vision_probe first.' }
$port = [int](Get-Content -LiteralPath (Join-Path $logRoot 'fixed-target-game.log.wsport') -Raw)
$probe = Join-Path $PSScriptRoot 'gestalt_probe.mjs'

if ($Action -eq 'trial') {
    if (-not (Test-LiveProcess $saved.target_pid 'node') -or (Test-Path -LiteralPath $stopFile)) {
        throw 'Start the continuous target first; a trial reuses that target.'
    }
    $relativeOutput = "logs/gestalt/trial-$TrialName.json"
    if (Test-Path -LiteralPath (Join-Path $repoRoot $relativeOutput)) {
        throw 'This trial name already has results. Choose a new trial name.'
    }
    $statusPath = if ($saved.follow_status) { Join-Path $repoRoot $saved.follow_status } else { Join-Path $logRoot 'live-follow.json' }
    if (Test-LiveProcess $saved.follow_pid 'gestalt_vision_probe') {
        if (Test-Path -LiteralPath $statusPath) {
            $previous = Read-LiveJson $statusPath
            if ($previous.shot_trial -and $previous.shot_trial.state -notin @('complete', 'target_defeated', 'hit_goal_reached')) {
                throw 'The previous shot trial is still active. Wait for completion or stop it.'
            }
        }
        $previousProcess = Get-Process -Id $saved.follow_pid -ErrorAction Stop
        if ($previousProcess.Path -ne $visionPath) { throw 'Unexpected follower executable path.' }
        Stop-Process -Id $previousProcess.Id
    }
    Push-Location $repoRoot
    try {
        & $nodePath $probe --port $port --seconds 1 `
            --command 'UEExec RBExtAim 0 0 -5 0' --command 'ExtAimClaim 0 0' `
            --command 'SetAttribute 0 10000031 0' --command 'SetAttribute 0 10000033 0' `
            --command 'SetAttribute 92011 10000031 0' --command 'SetAttribute 92011 10000033 0' `
            --output "logs/gestalt/trial-$TrialName-preflight.jsonl" |
            Set-Content -LiteralPath (Join-Path $logRoot "trial-$TrialName-preflight.log")
        if ($LASTEXITCODE -ne 0) { throw 'Trial preflight failed.' }
        $scene = Get-Content -LiteralPath (Join-Path $logRoot "trial-$TrialName-preflight.jsonl.summary.json") -Raw | ConvertFrom-Json
        $shooter = @($scene.vehicles | Where-Object { $_.player_id -eq 0 -and $_.team -eq 1 -and $_.cls -eq 1003 -and $_.hp -gt 0 })
        $target = @($scene.vehicles | Where-Object { $_.player_id -eq 92011 -and $_.team -eq 0 -and $_.cls -eq 1003 -and $_.hp -gt 0 })
        if ($scene.match_status -ne 1 -or $shooter.Count -ne 1 -or $target.Count -ne 1) {
            throw 'Trial needs the running blue pid 0 and red pid 92011 infantry pair.'
        }
        $invariant = [Globalization.CultureInfo]::InvariantCulture
        $followArgs = @($port, '--enemy-color', 'red', '--refine-corners', '--quad-scale', '1.192',
            '--follow', '--bench-pose', '--continuous', '--stop-file', 'logs/gestalt/live.stop',
            '--shot-budget', $ShotBudget, '--bullet-speed', $BulletSpeed.ToString($invariant),
            '--sim-delay-ms', $SimDelayMs.ToString($invariant), '--pitch-bias-deg', $PitchBiasDeg.ToString($invariant),
            '--fire-window-deg', $FireWindowDeg.ToString($invariant), '--settle-ms', $SettleMs,
            '--hit-goal', $HitGoal, '--output', $relativeOutput)
        if ($CenterHold) { $followArgs += '--center-hold' }
        if ($Prearm) { $followArgs += '--prearm' }
        if ($PrepareDuringSettle) { $followArgs += '--prepare-during-settle' }
        $followProcess = Start-Process -FilePath $visionPath -ArgumentList $followArgs -WorkingDirectory $repoRoot -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logRoot "trial-$TrialName.stdout.log") `
            -RedirectStandardError (Join-Path $logRoot "trial-$TrialName.stderr.log") -PassThru
        $saved.follow_pid = $followProcess.Id
        foreach ($field in @{ follow_status = $relativeOutput; shot_budget = $ShotBudget;
                bullet_speed_mps = $BulletSpeed; sim_delay_ms = $SimDelayMs; pitch_bias_deg = $PitchBiasDeg;
                center_hold = [bool]$CenterHold; fire_mode = 'bounded-single-round';
                fire_window_deg = $FireWindowDeg;
                settle_ms = $SettleMs; prearm = [bool]$Prearm; hit_goal = $HitGoal;
                prepare_during_settle = [bool]$PrepareDuringSettle;
                trial_started = [DateTimeOffset]::Now.ToString('o') }.GetEnumerator()) {
            $saved | Add-Member -NotePropertyName $field.Key -NotePropertyValue $field.Value -Force
        }
        $saved.PSObject.Properties.Remove('phase_fit')
        $saved.PSObject.Properties.Remove('pitch_hold')
        $saved | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
        $saved | ConvertTo-Json
    } finally { Pop-Location }
    exit 0
}

if ((Test-LiveProcess $saved.target_pid 'node') -or
    (Test-LiveProcess $saved.follow_pid 'gestalt_vision_probe')) {
    throw 'A live simulation process is already running. Use -Action status or stop.'
}
Push-Location $repoRoot
try {
    # Read-only preflight: use the existing Training infantry pair, never
    # respawn an unrelated player or open physical camera/serial hardware.
    & $nodePath $probe --port $port --seconds 1 --output logs/gestalt/live-start-check.jsonl |
        Set-Content -LiteralPath (Join-Path $logRoot 'live-start-check.log')
    if ($LASTEXITCODE -ne 0) { throw 'Simulator telemetry preflight failed.' }
    $scene = Get-Content -LiteralPath (Join-Path $logRoot 'live-start-check.jsonl.summary.json') -Raw | ConvertFrom-Json
    $shooter = @($scene.vehicles | Where-Object { $_.player_id -eq 0 -and $_.team -eq 1 -and $_.cls -eq 1003 -and $_.hp -gt 0 })
    $target = @($scene.vehicles | Where-Object { $_.player_id -eq 92011 -and $_.team -eq 0 -and $_.cls -eq 1003 -and $_.hp -gt 0 })
    if ($scene.match_status -ne 1 -or $shooter.Count -ne 1 -or $target.Count -ne 1) {
        throw 'Need the running Training scene with blue pid 0 and red pid 92011 infantry.'
    }
    # Fresh game instances can omit the lazily created hit statistic. Only
    # initialize it at independently observed fired=damage=0; never reset a
    # used counter or infer missing post-shot evidence as zero.
    if ($null -eq $shooter[0].raw_hits_total) {
        if ($shooter[0].bullets_total -ne 0 -or $shooter[0].damage_total -ne 0) {
            throw 'Missing hit statistic on a used shooter; refusing to reset counters.'
        }
        & $nodePath $probe --port $port --seconds 1 `
            --command 'SetAttribute 0 63000003 0' --output logs/gestalt/live-counter-initialize.jsonl |
            Set-Content -LiteralPath (Join-Path $logRoot 'live-counter-initialize.log')
        if ($LASTEXITCODE -ne 0) { throw 'Fresh hit-counter initialization failed.' }
        $initialized = Read-LiveJson (Join-Path $logRoot 'live-counter-initialize.jsonl.summary.json')
        $initializedShooter = @($initialized.vehicles | Where-Object { $_.player_id -eq 0 })
        if ($initializedShooter.Count -ne 1 -or $initializedShooter[0].raw_hits_total -ne 0 -or
            $initializedShooter[0].bullets_total -ne 0 -or $initializedShooter[0].damage_total -ne 0) {
            throw 'Fresh hit-counter initialization lacks unchanged zero-counter evidence.'
        }
    }
    # A match reset moves the local vehicle back to its spawn. Restore the
    # nominal bench placement before attaching its camera and visual follower.
    & $nodePath $probe --port $port --seconds 1 `
        --command 'UEExec RBTeleport 0 -450 300 -95 0' `
        --command 'SetAttribute 0 10000031 0' --command 'SetAttribute 0 10000033 0' `
        --command 'SetAttribute 92011 10000031 0' --command 'SetAttribute 92011 10000033 0' `
        --output logs/gestalt/live-scene-prepare.jsonl |
        Set-Content -LiteralPath (Join-Path $logRoot 'live-scene-prepare.log')
    if ($LASTEXITCODE -ne 0) { throw 'Simulator bench placement failed.' }
    # This is a single, fixed control marker inside logs/gestalt, not user data.
    if (Test-Path -LiteralPath $stopFile) { Remove-Item -LiteralPath $stopFile }
    $targetArgs = @('scripts/gestalt_probe.mjs', '--port', $port, '--continuous',
        '--stop-file', 'logs/gestalt/live.stop', '--warmup-seconds', '1',
        '--player', '92011', '--expected-omega', '4.87', '--spin-omega', '4.87',
        '--spin-position=-132,300,-95', '--output', 'logs/gestalt/live-target.jsonl')
    $targetProcess = Start-Process -FilePath $nodePath -ArgumentList $targetArgs -WorkingDirectory $repoRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logRoot 'live-target.stdout.log') `
        -RedirectStandardError (Join-Path $logRoot 'live-target.stderr.log') -PassThru
    $invariant = [Globalization.CultureInfo]::InvariantCulture
    $followArgs = @($port, '--enemy-color', 'red', '--refine-corners', '--quad-scale', '1.192',
        '--follow', '--bench-pose', '--continuous', '--stop-file', 'logs/gestalt/live.stop',
        '--bullet-speed', $BulletSpeed.ToString($invariant), '--sim-delay-ms', $SimDelayMs.ToString($invariant),
        '--pitch-bias-deg', $PitchBiasDeg.ToString($invariant),
        '--fire-window-deg', $FireWindowDeg.ToString($invariant), '--output', 'logs/gestalt/live-follow.json')
    if ($CenterHold) { $followArgs += '--center-hold' }
    try {
        $followProcess = Start-Process -FilePath $visionPath -ArgumentList $followArgs -WorkingDirectory $repoRoot -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logRoot 'live-follow.stdout.log') `
            -RedirectStandardError (Join-Path $logRoot 'live-follow.stderr.log') -PassThru
    } catch {
        Set-Content -LiteralPath $stopFile -Value 'start failed' -Encoding ascii
        throw
    }
    $state = [ordered]@{target_pid = $targetProcess.Id; follow_pid = $followProcess.Id;
        port = $port; omega_rad_s = 4.87; target_position_cm = @(-132, 300, -95);
        fire = 0; shot_budget = 0; follow_status = 'logs/gestalt/live-follow.json';
        fire_mode = 'non-firing'; bullet_speed_mps = $BulletSpeed;
        sim_delay_ms = $SimDelayMs; pitch_bias_deg = $PitchBiasDeg; center_hold = [bool]$CenterHold;
        fire_window_deg = $FireWindowDeg;
        started = [DateTimeOffset]::Now.ToString('o');
        note = 'Nominal 3 m bench: five-round diagnostic 228/250; paired fast trials 94/100. start only follows, trial defaults to 300 ms settling and prearm. Native official damage and muzzle geometry remain unvalidated.'}
    $state | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding utf8
    $state | ConvertTo-Json
} finally { Pop-Location }
