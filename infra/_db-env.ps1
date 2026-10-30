<#
    Shared helpers for the FinTrack DB backup/restore scripts (G6 deliverable 2).
    Dot-sourced by backup-db.ps1 and restore-db.ps1 — not run directly.

    Resolves the Postgres connection settings and container name the same way
    both scripts need, in one place:
      * credentials come from infra/.env (POSTGRES_USER/PASSWORD/DB) when that
        file exists, else the docker-compose defaults (fintrack/fintrack_secret);
      * the container is the compose default `fintech-infra-postgres-1`, with a
        label-based fallback discovery so a differently-numbered container works.
#>

function Get-FinTrackDbEnv {
    <#
        Return a hashtable @{ User; Password; Db } from infra/.env when present,
        else the compose defaults. Only the three POSTGRES_* keys are read; the
        DATABASE_URL* values are the app's and are intentionally not parsed here.
    #>
    param([Parameter(Mandatory)][string]$ScriptDir)

    # docker-compose.yml defaults (keep in sync with infra/docker-compose.yml).
    $user = "fintrack"
    $pass = "fintrack_secret"
    $db   = "fintrack"

    $envFile = Join-Path $ScriptDir ".env"
    if (Test-Path $envFile) {
        foreach ($line in Get-Content $envFile) {
            $t = $line.Trim()
            if ($t -eq "" -or $t.StartsWith("#")) { continue }
            $kv = $t.Split("=", 2)
            if ($kv.Count -ne 2) { continue }
            $k = $kv[0].Trim()
            $v = $kv[1].Trim().Trim('"')
            switch ($k) {
                "POSTGRES_USER"     { $user = $v }
                "POSTGRES_PASSWORD" { $pass = $v }
                "POSTGRES_DB"       { $db   = $v }
            }
        }
    }
    return @{ User = $user; Password = $pass; Db = $db }
}

function Resolve-PostgresContainer {
    <#
        Find the running Postgres container. Prefer the compose default name;
        fall back to discovery by the compose service label so a container named
        e.g. fintech-infra-postgres-2 is still found. Throws a clear error (with
        the Docker-down hint) when none is running.
    #>
    $default = "fintech-infra-postgres-1"

    $names = @()
    try {
        $names = docker ps --format "{{.Names}}" 2>$null
    }
    catch {
        throw "Cannot reach the Docker daemon. Start Docker Desktop, then retry. ($_)"
    }
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot reach the Docker daemon (docker ps failed). Start Docker Desktop, then retry."
    }

    if ($names -contains $default) { return $default }

    # Fallback: discover by the compose service label.
    $byLabel = docker ps --filter "label=com.docker.compose.service=postgres" --format "{{.Names}}" 2>$null
    if ($byLabel) { return (@($byLabel))[0] }

    throw "No running Postgres container found (looked for '$default' and the compose 'postgres' service). Is the infra stack up? Run: docker compose -f infra/docker-compose.yml up -d"
}

function Assert-PostgresReady {
    <#
        Fail fast with a readable message if the server isn't accepting
        connections yet (e.g. container just started / still in healthcheck).
    #>
    param(
        [Parameter(Mandatory)][string]$Container,
        [Parameter(Mandatory)][string]$User,
        [Parameter(Mandatory)][string]$Database
    )
    docker exec $Container pg_isready -U $User -d $Database *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Postgres in container '$Container' is not ready (pg_isready failed; db '$Database'). Wait for the healthcheck to report healthy and retry."
    }
}
