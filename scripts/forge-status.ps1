<#
.SYNOPSIS
    FORGE Status - Show current FORGE state and next safe action.

.DESCRIPTION
    Reads durable machine state without any LLM calls.
    Shows: control-plane SHA, Hermes runtime SHA/version, acceptance state,
    current milestone, blockers, last evidence, next safe action.

.EXAMPLE
    forge-status

.EXAMPLE
    forge-status -ControlPlaneRoot "C:\FORGE\control\hermes-control-plane"

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
$command = "$python `"$pythonScript`" status"
if ($ControlPlaneRoot) {
    $command += " --control-plane-root `"$ControlPlaneRoot`""
}
if ($HermesBin) {
    $command += " --hermes-bin `"$HermesBin`""
}

& $python $pythonScript status --control-plane-root $ControlPlaneRoot --hermes-bin $HermesBin