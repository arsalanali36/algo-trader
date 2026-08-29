# =============================================================================
# KHAZANA — Local trading data  ->  Google Drive  (seamless incremental backup)
# =============================================================================
# G:\My Drive is a Google Drive Desktop mount. Anything mirrored into
#   G:\My Drive\KHAZANA_BACKUP\  is auto-uploaded to the cloud by Drive Desktop.
#
# HYBRID strategy (Google Drive Desktop chokes on hundreds-of-thousands of
# tiny files, so we do NOT raw-mirror those):
#   * RAW incremental mirror (robocopy, no-delete)  -> Drive-friendly folders
#         (few / big files: OptChainLake, index CSVs, runs, runtime, config)
#   * TAR archive, only when source changed          -> tiny-file monsters
#         (Equity lake = ~633k small CSVs -> one .tar.gz instead of 633k files)
#
# One-way local -> Drive. NEVER deletes anything on Drive.
#
# Usage:
#   Normal :  powershell -ExecutionPolicy Bypass -File drive_backup.ps1
#   Dry run :  powershell -ExecutionPolicy Bypass -File drive_backup.ps1 -DryRun
#
# Scheduled daily 16:30 via Task Scheduler (see register_drive_backup.ps1)
# =============================================================================

param([switch]$DryRun)
$ErrorActionPreference = 'Continue'

# ---- Paths -------------------------------------------------------------------
$PY_ROOT   = 'D:\KHAZANA\KHAZANA\PYTHON'
$CODE3B    = Join-Path $PY_ROOT 'CODE3B- TV BACKTEST ENGINE'
$DRIVE_DST = 'G:\My Drive\KHAZANA_BACKUP'
$LOG_DIR   = Join-Path $DRIVE_DST '_backup_log'

# ---- RAW MIRROR jobs (robocopy, additive, no delete) -------------------------
# Only Drive-friendly folders (few / large files). Equity is EXCLUDED here and
# handled by the archive step below.
$jobs = @(
    @{ Name = 'local_data';   Src = (Join-Path $PY_ROOT '._TRADING DATA');
       Dst = (Join-Path $DRIVE_DST 'local_data');
       XD  = @('Debug','__pycache__'); XF = @() },

    @{ Name = 'code3b_lakes'; Src = (Join-Path $CODE3B '_TRADING_DATA');
       Dst = (Join-Path $DRIVE_DST 'code3b_lakes');
       XD  = @('Equity','__pycache__'); XF = @() },   # Equity -> tar (see below)

    @{ Name = 'runs';         Src = (Join-Path $CODE3B 'scratch\nifty_trend\runs');
       Dst = (Join-Path $DRIVE_DST 'runs');
       XD  = @('__pycache__'); XF = @() },

    @{ Name = 'runtime';      Src = (Join-Path $CODE3B 'data');
       Dst = (Join-Path $DRIVE_DST 'runtime');
       XD  = @('__pycache__'); XF = @('*.wal','*.shm','*.db-wal','*.db-shm') },

    @{ Name = 'nifty_1min';   Src = (Join-Path $CODE3B 'scratch\nifty_trend');
       Dst = (Join-Path $DRIVE_DST 'lakes');
       File = 'nifty_1min.csv'; XD = @(); XF = @() },

    @{ Name = 'live_config';  Src = $CODE3B;
       Dst = (Join-Path $DRIVE_DST 'latest');
       File = 'nifty_config.json'; XD = @(); XF = @() }
)

# ---- TAR-archive jobs (tiny-file monsters, only re-tar when changed) ---------
$archives = @(
    @{ Name = 'Equity';
       Src  = (Join-Path $CODE3B '_TRADING_DATA\Equity');
       Dst  = (Join-Path $DRIVE_DST 'code3b_lakes\_archive\Equity.tar.gz') }
)

# ---- Setup -------------------------------------------------------------------
if (-not (Test-Path 'G:\My Drive')) {
    Write-Host "[FATAL] G:\My Drive not found - is Google Drive Desktop running?" -ForegroundColor Red
    exit 2
}
New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null
$today   = Get-Date -Format 'yyyy-MM-dd'
$logFile = Join-Path $LOG_DIR "backup_$today.log"
$mode    = if ($DryRun) { 'DRY-RUN (no copy)' } else { 'LIVE' }

function Log($msg) {
    $line = "$(Get-Date -Format 'HH:mm:ss')  $msg"
    Write-Host $line
    Add-Content -Path $logFile -Value $line -Encoding utf8
}

Log "==================================================================="
Log "KHAZANA Drive backup  |  $mode  |  $today"
Log "==================================================================="

$fail = 0

# ---- 1) RAW MIRROR -----------------------------------------------------------
foreach ($j in $jobs) {
    if (-not (Test-Path $j.Src)) { Log "[SKIP] $($j.Name): source missing -> $($j.Src)"; continue }
    $flags = @('/R:1','/W:1','/NP','/NFL','/NDL','/NJH','/NJS')
    if ($DryRun) { $flags += '/L' }
    if ($j.ContainsKey('File')) {
        $rcArgs = @("`"$($j.Src)`"", "`"$($j.Dst)`"", $j.File) + $flags
    } else {
        $rcArgs = @("`"$($j.Src)`"", "`"$($j.Dst)`"", '/E') + $flags
        if ($j.XD.Count) { $rcArgs += '/XD'; $rcArgs += $j.XD }
        if ($j.XF.Count) { $rcArgs += '/XF'; $rcArgs += $j.XF }
    }
    Log "[MIRR] $($j.Name):  $($j.Src)  ->  $($j.Dst)"
    & robocopy @rcArgs | Out-Null
    $rc = $LASTEXITCODE
    if ($rc -ge 8) { Log "[FAIL] $($j.Name): robocopy exit $rc"; $fail++ }
    else           { Log "[ OK ] $($j.Name): robocopy exit $rc" }
}

# ---- 2) TAR ARCHIVE (only when source newer than archive) --------------------
foreach ($a in $archives) {
    if (-not (Test-Path $a.Src)) { Log "[SKIP] archive $($a.Name): source missing"; continue }

    # newest file mtime in source
    $newest = Get-ChildItem -LiteralPath $a.Src -Recurse -File -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1
    $srcTime = if ($newest) { $newest.LastWriteTime } else { Get-Date '1970-01-01' }
    $arcTime = if (Test-Path $a.Dst) { (Get-Item $a.Dst).LastWriteTime } else { Get-Date '1970-01-01' }

    if ($arcTime -ge $srcTime) {
        Log "[ OK ] archive $($a.Name): up-to-date (archive $($arcTime.ToString('yyyy-MM-dd')) >= data $($srcTime.ToString('yyyy-MM-dd'))), skip"
        continue
    }

    if ($DryRun) {
        Log "[MOCK] archive $($a.Name): WOULD re-tar (data newer: $($srcTime.ToString('yyyy-MM-dd')) > archive $($arcTime.ToString('yyyy-MM-dd')))"
        continue
    }

    New-Item -ItemType Directory -Force -Path (Split-Path $a.Dst) | Out-Null
    $parent = Split-Path $a.Src
    $leaf   = Split-Path $a.Src -Leaf
    $tmp    = "$($a.Dst).tmp"
    Log "[ TAR] archive $($a.Name): building (data changed) ..."
    & tar -czf "$tmp" -C "$parent" "$leaf" 2>$null
    if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp)) {
        Move-Item -Force $tmp $a.Dst
        $mb = [math]::Round((Get-Item $a.Dst).Length/1MB,1)
        Log "[ OK ] archive $($a.Name): $mb MB -> $($a.Dst)"
    } else {
        Log "[FAIL] archive $($a.Name): tar exit $LASTEXITCODE"; $fail++
        if (Test-Path $tmp) { Remove-Item -Force $tmp }
    }
}

# ---- Finalise ----------------------------------------------------------------
$status = if ($fail -eq 0) { 'OK' } else { "FAIL($fail)" }
if (-not $DryRun) {
    "$status  $today $(Get-Date -Format 'HH:mm')  mode=$mode" |
        Set-Content -Path (Join-Path $DRIVE_DST 'last_backup.txt') -Encoding utf8
}
Log "DONE  status=$status  (failed jobs: $fail)"
Log ""
if ($fail -gt 0) { exit 1 } else { exit 0 }
