<#
.SYNOPSIS
    FORGE Resume - Resume from durable execution state.

.DESCRIPTION
    Resumes from durable execution state (state.json, evidence.json, events.jsonl).
    Never uses conversational memory. Refuses ambiguous state.

.EXAMPLE
    forge-resume

.EXAMPLE
    forge-resume -RunId "forge-hermes-activation-abc123"

.EXAMPLE
    forge-resume -ControlPlaneRoot "C:\FORGE\control\hermes-control-plane"

.NOTES
    Part of M4 local operating commands (Workstream K).
    No model calls. Deterministic.
#>

param(
    [Parameter(Mandatory=$false)]
    [string]$RunId,
    
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
$command = "$python `"$pythonScript`" resume"
if ($RunId) {
    $command += " `"$RunId`""
}

& $python $pythonScript resume $RunId --control-plane-root $ControlPlaneRoot --hermes-bin $HermesBin