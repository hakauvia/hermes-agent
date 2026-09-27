<#
.SYNOPSIS
    FORGE Run - Execute an SRS through the FORGE manufacturing loop.

.DESCRIPTION
    Accepts an SRS file path. Refuses execution while live worker acceptance
    is not satisfied. Never bypasses authorization.

.EXAMPLE
    forge-run -SRSPath "C:\FORGE\control\hermes-control-plane\examples\vertical-slice\hello-api\SRS.md"

.EXAMPLE
    forge-run -SRSPath "C:\FORGE\control\hermes-control-plane\examples\multi-work\hello-api-multi\SRS.md" -AuthorizationToken "auth-token"

.NOTES
    Part of M4 local operating commands (Workstream K).
    No model calls in check phase. Requires M2_PASSED and explicit authorization.
#>

param(
    [Parameter(Mandatory=$true)]
    [string]$SRSPath,
    
    [Parameter(Mandatory=$false)]
    [string]$AuthorizationToken,
    
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
$command = "$python `"$pythonScript`" run `"$SRSPath`""
if ($AuthorizationToken) {
    $command += " `"$AuthorizationToken`""
}

& $python $pythonScript run "$SRSPath" $AuthorizationToken --control-plane-root $ControlPlaneRoot --hermes-bin $HermesBin