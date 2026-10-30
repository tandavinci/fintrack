<#
.SYNOPSIS
    Restore the FinTrack PostgreSQL database from a backup dump (ONE command).

.DESCRIPTION
    G6 deliverable 2 (STEERING Step 6.4). The one-command restore counterpart to
    backup-db.ps1. Streams a custom-format dump from the host INTO the Postgres
    container's own `pg_restore` via `docker exec`, so no host PostgreSQL client
    install is needed and the server version always matches.

    DESTRUCTIVE: uses `pg_restore --clean --if-exists`, which DROPS existing
    objects before recreating them, overwriting the current database contents.
    Because the blast radius is the whole database, the script REQUIRES explicit
    confirmation: either answer the y/N prompt, or pass -Force to skip it (e.g.
    for scripted/CI restores).

    If -InFile is omitted, the newest *.dump in infra/backups is used.

.PARAMETER InFile
    Path to the .dump file to restore. Default: newest *.dump in infra/backups.

.PARAMETER Force
    Skip the confirmation prompt (non-interactive restore). Use with care.

.PARAMETER Container
    Override the Postgres container name. Default: auto-detected.

.EXAMPLE
    .\infra\restore-db.ps1
    # restores the newest backup, after a y/N confirmation

.EXAMPLE
    .\infra\restore-db.ps1 -InFile infra\backups\fintrack-20261027-120000.dump -Force
#>
[CmdletBinding()]
param(
    [string]$InFile,
    [switch]$Force,
    [string]$Container
)

$ErrorActionPreference = "Stop"

# --- resolve paths ----------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path   # ...\infra

# --- read DB settings (infra/.env -> compose defaults) ----------------------
. (Join-Path $ScriptDir "_db-env.ps1")
$db = Get-FinTrackDbEnv -ScriptDir $ScriptDir

if (-not $Container) { $Container = Resolve-PostgresContainer }

# --- pick the dump file -----------------------------------------------------
if (-not $InFile) {
    $backupDir = Join-Path $ScriptDir "backups"
    if (-not (Test-Path $backupDir)) {
        throw "No -InFile given and no backups dir at $backupDir. Run backup-db.ps1 first."
    }
    $latest = Get-ChildItem -Path $backupDir -Filter "*.dump" -File |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if (-not $latest) {
        throw "No -InFile given and no *.dump files in $backupDir. Run backup-db.ps1 first."
    }
    $InFile = $latest.FullName
    Write-Host ("Using newest backup: {0}" -f $InFile)
}

if (-not (Test-Path $InFile)) { throw "Backup file not found: $InFile" }
$InFile = (Resolve-Path $InFile).Path
$size = (Get-Item $InFile).Length
if ($size -le 0) { throw "Backup file is empty: $InFile" }

# --- preflight --------------------------------------------------------------
Write-Host "FinTrack DB restore"
Write-Host ("  container : {0}" -f $Container)
Write-Host ("  database  : {0}" -f $db.Db)
Write-Host ("  user      : {0}" -f $db.User)
Write-Host ("  from file : {0} ({1} KB)" -f $InFile, [math]::Round($size/1KB,1))

Assert-PostgresReady -Container $Container -User $db.User -Database $db.Db

# --- confirm (destructive) --------------------------------------------------
if (-not $Force) {
    Write-Host ""
    Write-Warning "This DROPS and recreates objects in database '$($db.Db)', overwriting current data."
    $answer = Read-Host "Type 'yes' to proceed"
    if ($answer -ne "yes") {
        Write-Host "Aborted. No changes made." -ForegroundColor Yellow
        return
    }
}

# --- restore ----------------------------------------------------------------
# The dump is BINARY, so (as in backup-db.ps1) it is `docker cp`-ed INTO the
# container and pg_restore reads the in-container file — avoiding PowerShell
# byte-stream corruption. --clean --if-exists drops existing objects first so a
# re-restore is idempotent; --no-owner / --no-privileges match how the dump was
# taken so role mismatches don't error.
$inContainerPath = "/tmp/fintrack-restore.dump"
Write-Host "Copying the dump into the container..."
docker cp "$InFile" "${Container}:$inContainerPath"
if ($LASTEXITCODE -ne 0) {
    docker exec $Container rm -f $inContainerPath *> $null
    throw "docker cp into the container failed (exit $LASTEXITCODE); nothing restored."
}

Write-Host "Restoring..."
docker exec -e PGPASSWORD=$($db.Password) $Container `
    pg_restore -U $db.User -d $db.Db --clean --if-exists --no-owner --no-privileges $inContainerPath
$restoreExit = $LASTEXITCODE
docker exec $Container rm -f $inContainerPath *> $null   # always clean up in-container

if ($restoreExit -ne 0) {
    throw "pg_restore reported errors (exit $restoreExit). The database may be partially restored; review the output above."
}

Write-Host ""
Write-Host "OK  restore complete." -ForegroundColor Green
Write-Host "Tip: verify with a row count, e.g. docker exec $Container psql -U $($db.User) -d $($db.Db) -c '\dt'"
