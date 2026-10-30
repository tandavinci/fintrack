<#
.SYNOPSIS
    Back up the FinTrack PostgreSQL database to a compressed dump file.

.DESCRIPTION
    G6 deliverable 2 (STEERING Step 6.4). Runs the Postgres container's OWN
    `pg_dump` via `docker exec`, so no host PostgreSQL client install is needed
    and the dump is produced by the exact server version (pgvector/pgvector:pg16)
    that holds the data — avoiding host-vs-container version-mismatch errors.

    The dump uses the custom format (`-Fc`): compressed and restorable with
    `pg_restore` (see restore-db.ps1). The vector extension's data is included
    like any other table data.

    Connection settings are read from infra/.env when present, else fall back to
    the docker-compose defaults (user/db `fintrack`). The container is located by
    the compose default name `fintech-infra-postgres-1`, with a label-based
    fallback so a differently-numbered container is still found.

.PARAMETER OutDir
    Directory to write the dump into. Default: infra/backups (created if absent).

.PARAMETER Container
    Override the Postgres container name. Default: auto-detected.

.EXAMPLE
    .\infra\backup-db.ps1
    # -> infra/backups/fintrack-YYYYMMDD-HHMMSS.dump

.EXAMPLE
    .\infra\backup-db.ps1 -OutDir D:\backups
#>
[CmdletBinding()]
param(
    [string]$OutDir,
    [string]$Container
)

$ErrorActionPreference = "Stop"

# --- resolve paths ----------------------------------------------------------
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path   # ...\infra
$RepoRoot  = Split-Path -Parent $ScriptDir

if (-not $OutDir) { $OutDir = Join-Path $ScriptDir "backups" }

# --- read DB settings (infra/.env -> compose defaults) ----------------------
. (Join-Path $ScriptDir "_db-env.ps1")
$db = Get-FinTrackDbEnv -ScriptDir $ScriptDir

if (-not $Container) { $Container = Resolve-PostgresContainer }

# --- preflight --------------------------------------------------------------
Write-Host "FinTrack DB backup"
Write-Host ("  container : {0}" -f $Container)
Write-Host ("  database  : {0}" -f $db.Db)
Write-Host ("  user      : {0}" -f $db.User)

# Confirm the container is actually up and the DB answers before dumping.
Assert-PostgresReady -Container $Container -User $db.User -Database $db.Db

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }
$stamp   = Get-Date -Format "yyyyMMdd-HHmmss"
$outFile = Join-Path $OutDir ("{0}-{1}.dump" -f $db.Db, $stamp)

# --- dump -------------------------------------------------------------------
# The custom-format dump is BINARY. Rather than pipe it through PowerShell's
# (object/text) pipeline — which can corrupt bytes — pg_dump writes to a file
# INSIDE the container, then `docker cp` copies that file out to the host and
# the in-container temp file is removed. PGPASSWORD is set in the exec env so no
# password prompt blocks the run.
$inContainerPath = "/tmp/fintrack-backup.dump"
Write-Host "Dumping inside the container..."

docker exec -e PGPASSWORD=$($db.Password) $Container `
    pg_dump -U $db.User -d $db.Db -Fc --no-owner --no-privileges -f $inContainerPath
if ($LASTEXITCODE -ne 0) {
    docker exec $Container rm -f $inContainerPath *> $null
    throw "pg_dump failed (exit $LASTEXITCODE); no backup written."
}

Write-Host "Copying the dump out to the host..."
docker cp "${Container}:$inContainerPath" "$outFile"
$cpExit = $LASTEXITCODE
docker exec $Container rm -f $inContainerPath *> $null   # always clean up in-container
if ($cpExit -ne 0) {
    if (Test-Path $outFile) { Remove-Item $outFile -Force }
    throw "docker cp failed (exit $cpExit); no backup written."
}

$size = (Get-Item $outFile).Length
if ($size -le 0) {
    Remove-Item $outFile -Force
    throw "pg_dump produced an empty file; removed. Is the database reachable?"
}

$kb = [math]::Round($size / 1KB, 1)
Write-Host ""
Write-Host ("OK  wrote {0} ({1} KB)" -f $outFile, $kb) -ForegroundColor Green
Write-Host "Restore with:  .\infra\restore-db.ps1 -InFile `"$outFile`""
