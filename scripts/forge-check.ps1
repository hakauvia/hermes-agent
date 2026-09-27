<#
.SYNOPSIS
    FORGE Check - Verify prerequisites without launching a coding model.

.DESCRIPTION
    Checks: Docker, Docker daemon, control-plane dependencies, SRS/schema existence,
    Hermes profile/doctor, no stale containers, git clean, activation authorization.

.EXAMPLE
    forge-check

.EXAMPLE
    forge-check -ControlPlaneRoot "C:\FORGE\control\hermes-control-plane"

.NOTES
    Part of M4 local operating commands (Workstream K).
    No model calls. Deterministic.
#>

param(
    [Parameter(Mandatory=$false)]
    [string]$ControlPlaneRoot = "C:\Users\mhakh\FORGE\control\hermes-control-plane",
    
    [Parameter(Mandatory=$false)]
    [string]$HermesBin = "hermes"
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$pythonScript = Join-Path $scriptDir "forge_commands.py"

if (-not (Test-Path $pythonScript)) {
    Write-Error "forge_commands.py not found at $pythonScript"
    exit 1
}

$python = "python"
& $python $pythonScript check --control-plane-root $ControlPlaneRoot --hermes-bin $HermesBin