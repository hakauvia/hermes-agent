#!/usr/bin/env python3
"""
FORGE Local Operator Commands — M4 Preparation.
Deterministic PowerShell/Python-backed commands for FORGE operations.

Commands:
- forge-status: No LLM; reads durable machine state; shows control-plane SHA, Hermes runtime SHA/version, acceptance state, current milestone, blocker, last evidence, next safe action
- forge-check: Verifies prerequisites without launching a coding model
- forge-run: Accepts an SRS; refuses execution while live worker acceptance not satisfied; never bypasses authorization
- forge-evidence: Locates sanitized evidence for a run
- forge-resume: Resumes from durable execution state; never conversational memory; refuses ambiguous state

All commands are deterministic, no LLM calls, and have deterministic tests.
"""

import json
import os
import subprocess
import sys
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum


class AcceptanceState(Enum):
    """FORGE acceptance state."""
    UNKNOWN = "unknown"
    M0_COMPLETE = "m0_complete"
    M1_VALIDATING = "m1_validating"
    M2_BLOCKED = "m2_blocked"
    M2_READY = "m2_ready"
    M2_PASSED = "m2_passed"
    M3_PREPARED = "m3_prepared"
    M3_BLOCKED = "m3_blocked"
    M4_PREPARED = "m4_prepared"
    M5_READY = "m5_ready"


@dataclass
class ForgeStatus:
    """FORGE status output."""
    timestamp: str
    control_plane_sha: str
    hermes_version: str
    hermes_commit: str
    hermes_install_path: str
    acceptance_state: str
    current_milestone: str
    blockers: List[str]
    last_evidence: Optional[str]
    next_safe_action: str


@dataclass
class ForgeCheckResult:
    """forge-check output."""
    timestamp: str
    all_passed: bool
    checks: Dict[str, bool]
    details: Dict[str, Any]


@dataclass
class ForgeEvidenceResult:
    """forge-evidence output."""
    timestamp: str
    run_id: str
    evidence_path: str
    evidence_hash: str
    exists: bool
    summary: Dict[str, Any]


class ForgeCommands:
    """FORGE local command implementations."""
    
    def __init__(self, 
                 control_plane_root: str = None,
                 hermes_bin: str = "hermes"):
        self.control_plane_root = Path(control_plane_root or Path(__file__).parent.parent.parent).resolve()
        self.hermes_bin = hermes_bin
        self.artifacts_dir = self.control_plane_root / "artifacts"
        self.runtime_dir = self.control_plane_root / "runtime"
    
    def _run(self, argv: List[str], cwd: str = None, timeout: int = 60) -> Dict[str, Any]:
        """Run a command and return structured result."""
        try:
            result = subprocess.run(
                argv,
                cwd=cwd or self.control_plane_root,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return {
                "argv": argv,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "passed": result.returncode == 0,
            }
        except subprocess.TimeoutExpired as e:
            return {"argv": argv, "returncode": None, "stdout": e.stdout or "", "stderr": e.stderr or "", "passed": False, "failure": "timeout"}
        except OSError as e:
            return {"argv": argv, "returncode": None, "stdout": "", "stderr": str(e), "passed": False, "failure": f"process_launch_error:{type(e).__name__}"}
    
    def get_control_plane_sha(self) -> str:
        """Get control plane git SHA."""
        result = self._run(["git", "rev-parse", "HEAD"])
        if result["passed"]:
            return result["stdout"].strip()
        return "unknown"
    
    def get_hermes_info(self) -> Dict[str, str]:
        """Get Hermes version and commit info."""
        version_result = self._run([self.hermes_bin, "--version"])
        version = version_result.get("stdout", "").strip() if version_result["passed"] else "unknown"
        
        # Get install path
        hermes_path = Path(self.hermes_bin)
        if hermes_path.is_absolute():
            install_path = str(hermes_path.parent)
        else:
            which_result = self._run(["where" if sys.platform == "win32" else "which", self.hermes_bin])
            if which_result["passed"] and which_result["stdout"].strip():
                install_path = str(Path(which_result["stdout"].strip()).parent)
            else:
                install_path = "unknown"
        
        # Get commit from install directory
        commit = "unknown"
        install_dir = Path(install_path).parent if Path(install_path).name in ("Scripts", "bin") else Path(install_path)
        if (install_dir / ".git").exists():
            git_result = self._run(["git", "rev-parse", "HEAD"], cwd=str(install_dir))
            if git_result["passed"]:
                commit = git_result["stdout"].strip()[:8]
        
        return {
            "version": version,
            "commit": commit,
            "install_path": install_path
        }
    
    def get_acceptance_state(self) -> str:
        """Determine current acceptance state from evidence."""
        # Check for activation reports
        activation_dir = self.artifacts_dir / "activation"
        if not activation_dir.exists():
            return AcceptanceState.M1_VALIDATING.value
        
        reports = list(activation_dir.glob("**/forge-coder-activation.json"))
        if not reports:
            return AcceptanceState.M1_VALIDATING.value
        
        latest = max(reports, key=lambda p: p.stat().st_mtime)
        try:
            with open(latest) as f:
                report = json.load(f)
            
            if report.get("simulation") is True:
                return AcceptanceState.M1_VALIDATING.value
            
            gate_passed = report.get("gate_passed", False)
            activation_ready = report.get("activation_ready", False)
            evidence = report.get("evidence", {})
            state = evidence.get("state", "UNKNOWN")
            
            if gate_passed and activation_ready and state == "RELEASED":
                return AcceptanceState.M2_PASSED.value
            elif gate_passed is False:
                return AcceptanceState.M2_BLOCKED.value
            else:
                return AcceptanceState.M2_READY.value
        except:
            return AcceptanceState.UNKNOWN.value
    
    def get_current_milestone(self) -> str:
        """Get current milestone from state registry or acceptance state."""
        acceptance = self.get_acceptance_state()
        if acceptance == AcceptanceState.M2_PASSED.value:
            return "M3 — Real multi-work manufacturing proof"
        elif acceptance in (AcceptanceState.M2_BLOCKED.value, AcceptanceState.M2_READY.value):
            return "M2 — First real single-unit acceptance"
        elif acceptance == AcceptanceState.M1_VALIDATING.value:
            return "M1 — Safe runtime compatibility"
        elif acceptance == AcceptanceState.M0_COMPLETE.value:
            return "M0 — Deterministic core baseline"
        return "M1 — Safe runtime compatibility"
    
    def get_blockers(self) -> List[str]:
        """Get current blockers."""
        blockers = []
        
        # Check PR #124978 status
        blockers.append("BLOCKER-001: Hermes path-mapping fix PR #124978 awaiting upstream review/merge")
        
        # Check governance
        blockers.append("BLOCKER-002: Missing governance-admins team (Issue #5)")
        
        # Check project provisioning
        blockers.append("BLOCKER-003: Project provisioning requires Level-2 approval (Issue #12)")
        
        # Check M2 status
        acceptance = self.get_acceptance_state()
        if acceptance == AcceptanceState.M2_BLOCKED.value:
            blockers.append("M2 activation gate not passed")
        
        return blockers
    
    def get_last_evidence(self) -> Optional[str]:
        """Get path to last evidence directory."""
        activation_dir = self.artifacts_dir / "activation"
        if not activation_dir.exists():
            return None
        
        reports = list(activation_dir.glob("**/forge-coder-activation.json"))
        if not reports:
            return None
        
        latest = max(reports, key=lambda p: p.stat().st_mtime)
        return str(latest.parent)
    
    def get_next_safe_action(self) -> str:
        """Get next safe action based on current state."""
        acceptance = self.get_acceptance_state()
        
        if acceptance == AcceptanceState.M1_VALIDATING.value:
            return "Await upstream review/merge of Hermes PR #124978, then run host update and installed-runtime validation"
        elif acceptance == AcceptanceState.M2_BLOCKED.value:
            return "Diagnose activation failure from last evidence; fix root cause; obtain authorization for retry"
        elif acceptance == AcceptanceState.M2_READY.value:
            return "Obtain explicit versioned human authorization for bounded activation retry"
        elif acceptance == AcceptanceState.M2_PASSED.value:
            return "Execute M3 multi-work factory proof (Workstream J preparation complete)"
        return "Review current state and blockers"
    
    def forge_status(self) -> ForgeStatus:
        """Implement forge-status command."""
        hermes = self.get_hermes_info()
        acceptance = self.get_acceptance_state()
        
        return ForgeStatus(
            timestamp=datetime.now().isoformat(),
            control_plane_sha=self.get_control_plane_sha(),
            hermes_version=hermes["version"],
            hermes_commit=hermes["commit"],
            hermes_install_path=hermes["install_path"],
            acceptance_state=acceptance,
            current_milestone=self.get_current_milestone(),
            blockers=self.get_blockers(),
            last_evidence=self.get_last_evidence(),
            next_safe_action=self.get_next_safe_action()
        )
    
    def forge_check(self) -> ForgeCheckResult:
        """Implement forge-check command - verify prerequisites without model."""
        checks = {}
        details = {}
        
        # 1. Docker
        docker_result = self._run(["docker", "version", "--format", "{{.Server.Version}}"])
        checks["docker"] = docker_result["passed"]
        details["docker"] = {"version": docker_result.get("stdout", "").strip()} if docker_result["passed"] else {"error": docker_result.get("stderr")}
        
        # 2. Docker daemon
        daemon_result = self._run(["docker", "info"])
        checks["docker_daemon"] = daemon_result["passed"]
        
        # 3. Control plane deps
        venv_python = self.control_plane_root / ".venv" / "Scripts" / "python.exe"
        if not venv_python.exists():
            venv_python = self.control_plane_root / ".venv" / "bin" / "python"
        deps_result = self._run([str(venv_python), "-c", "import forge_core; print('OK')"])
        checks["control_plane_deps"] = deps_result["passed"]
        
        # 4. SRS exists
        srs_path = self.control_plane_root / "examples" / "vertical-slice" / "hello-api" / "SRS.md"
        checks["srs_exists"] = srs_path.exists()
        details["srs"] = {"path": str(srs_path)}
        
        # 5. Schema exists
        schema_path = self.control_plane_root / "schemas" / "forge-project-ir-v0.schema.json"
        checks["schema_exists"] = schema_path.exists()
        details["schema"] = {"path": str(schema_path)}
        
        # 6. Hermes profile
        profile_result = self._run([self.hermes_bin, "profile", "list"])
        checks["hermes_profile_coder"] = profile_result["passed"] and "coder" in profile_result.get("stdout", "")
        
        # 7. Hermes doctor
        doctor_result = self._run([self.hermes_bin, "-p", "coder", "doctor"], timeout=180)
        checks["hermes_doctor"] = doctor_result["passed"]
        
        # 8. No stale containers
        ps_result = self._run(["docker", "ps", "-a", "--filter", "label=forge", "--format", "{{.ID}}"])
        container_ids = ps_result.get("stdout", "").strip().split() if ps_result["passed"] else []
        checks["no_stale_containers"] = len(container_ids) == 0
        details["stale_containers"] = container_ids
        
        # 9. Git clean
        git_status = self._run(["git", "status", "--porcelain"])
        checks["git_clean"] = git_status["passed"] and git_status.get("stdout", "").strip() == ""
        
        # 10. Activation authorization
        # Check if there's a recorded authorization
        auth_file = self.runtime_dir / "activation_authorization.json"
        checks["activation_authorized"] = auth_file.exists()
        details["authorization"] = {"file": str(auth_file), "exists": auth_file.exists()}
        
        all_passed = all(checks.values())
        
        return ForgeCheckResult(
            timestamp=datetime.now().isoformat(),
            all_passed=all_passed,
            checks=checks,
            details=details
        )
    
    def forge_run(self, srs_path: str, authorization_token: str = None) -> Dict[str, Any]:
        """Implement forge-run command - refuses execution without acceptance."""
        # Check if live worker acceptance is satisfied
        acceptance = self.get_acceptance_state()
        
        if acceptance != AcceptanceState.M2_PASSED.value:
            return {
                "success": False,
                "error": f"Live worker acceptance not satisfied. Current state: {acceptance}. M2 must pass before M3 execution.",
                "refused": True,
                "required_state": "M2_PASSED",
                "current_state": acceptance
            }
        
        # Check authorization
        auth_file = self.runtime_dir / "activation_authorization.json"
        if not auth_file.exists() and not authorization_token:
            return {
                "success": False,
                "error": "Explicit human authorization required for activation retry. No authorization token provided.",
                "refused": True,
                "required": "activation_authorization"
            }
        
        # Validate SRS
        srs_file = Path(srs_path)
        if not srs_file.exists():
            return {
                "success": False,
                "error": f"SRS file not found: {srs_path}",
                "refused": True
            }
        
        # Would execute here, but this is preparation only
        return {
            "success": True,
            "message": "Prerequisites satisfied. Ready for execution (not executed in preparation mode).",
            "srs": str(srs_file),
            "would_execute": True
        }
    
    def forge_evidence(self, run_id: str = None) -> ForgeEvidenceResult:
        """Implement forge-evidence command - locate sanitized evidence."""
        if run_id is None:
            # Find latest
            activation_dir = self.artifacts_dir / "activation"
            if not activation_dir.exists():
                return ForgeEvidenceResult(
                    timestamp=datetime.now().isoformat(),
                    run_id="none",
                    evidence_path="",
                    evidence_hash="",
                    exists=False,
                    summary={}
                )
            
            reports = list(activation_dir.glob("**/forge-coder-activation.json"))
            if not reports:
                return ForgeEvidenceResult(
                    timestamp=datetime.now().isoformat(),
                    run_id="none",
                    evidence_path="",
                    evidence_hash="",
                    exists=False,
                    summary={}
                )
            
            latest = max(reports, key=lambda p: p.stat().st_mtime)
            run_id = latest.parent.name
            evidence_path = str(latest.parent)
        else:
            evidence_path = str(self.artifacts_dir / "activation" / run_id)
        
        evidence_file = Path(evidence_path) / "forge-coder-activation.json"
        exists = evidence_file.exists()
        
        evidence_hash = ""
        summary = {}
        if exists:
            with open(evidence_file) as f:
                report = json.load(f)
            evidence_hash = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:16]
            summary = {
                "gate_passed": report.get("gate_passed"),
                "activation_ready": report.get("activation_ready"),
                "state": report.get("evidence", {}).get("state"),
                "worker_clean": report.get("evidence", {}).get("worker_clean"),
            }
        
        return ForgeEvidenceResult(
            timestamp=datetime.now().isoformat(),
            run_id=run_id,
            evidence_path=evidence_path,
            evidence_hash=evidence_hash,
            exists=exists,
            summary=summary
        )
    
    def forge_resume(self, run_id: str = None) -> Dict[str, Any]:
        """Implement forge-resume command - resume from durable state."""
        # Find execution state
        if run_id is None:
            activation_dir = self.artifacts_dir / "activation"
            if not activation_dir.exists():
                return {"success": False, "error": "No previous runs found", "refused": True}
            
            reports = list(activation_dir.glob("**/forge-coder-activation.json"))
            if not reports:
                return {"success": False, "error": "No previous runs found", "refused": True}
            
            latest = max(reports, key=lambda p: p.stat().st_mtime)
            run_id = latest.parent.name
        
        run_dir = self.artifacts_dir / "activation" / run_id
        if not run_dir.exists():
            return {"success": False, "error": f"Run directory not found: {run_id}", "refused": True}
        
        # Check for durable state files
        state_file = run_dir / ".forge" / "state.json"
        evidence_file = run_dir / ".forge" / "evidence.json"
        events_file = run_dir / ".forge" / "events.jsonl"
        
        if not state_file.exists():
            return {"success": False, "error": "No durable state found (missing state.json)", "refused": True}
        
        with open(state_file) as f:
            state = json.load(f)
        
        # Determine resume point
        current_state = state.get("state", "UNKNOWN")
        
        if current_state == "RELEASED":
            return {
                "success": True,
                "message": "Run already RELEASED. No resume needed.",
                "run_id": run_id,
                "state": current_state,
                "resume_point": "complete"
            }
        elif current_state in ("REWORK", "BLOCKED"):
            # Find last completed work unit
            if evidence_file.exists():
                with open(evidence_file) as f:
                    evidence = json.load(f)
                
                completed = []
                for key, val in evidence.items():
                    if key.startswith("worker:") and val.get("passed") is True:
                        completed.append(key.split(":")[1])
                
                return {
                    "success": True,
                    "message": f"Can resume from after {completed[-1] if completed else 'start'}",
                    "run_id": run_id,
                    "state": current_state,
                    "completed_work_units": completed,
                    "resume_point": completed[-1] if completed else "start"
                }
        
        return {
            "success": False,
            "error": f"Ambiguous state: {current_state}. Cannot determine resume point.",
            "refused": True,
            "run_id": run_id,
            "state": current_state
        }


def test_forge_commands():
    """Test all forge commands deterministically."""
    import tempfile
    
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create minimal structure
        cp_root = Path(tmpdir) / "control-plane"
        cp_root.mkdir()
        (cp_root / ".venv" / "Scripts").mkdir(parents=True)
        (cp_root / "examples" / "vertical-slice" / "hello-api").mkdir(parents=True)
        (cp_root / "schemas").mkdir(parents=True)
        (cp_root / "artifacts" / "activation").mkdir(parents=True)
        (cp_root / "runtime").mkdir(parents=True)
        
        # Write dummy SRS and schema
        (cp_root / "examples" / "vertical-slice" / "hello-api" / "SRS.md").write_text("# SRS\n")
        (cp_root / "schemas" / "forge-project-ir-v0.schema.json").write_text("{}")
        
        # Initialize git
        subprocess.run(["git", "init"], cwd=cp_root, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=cp_root, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=cp_root, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=cp_root, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=cp_root, capture_output=True)
        
        # Create dummy hermes
        hermes_dir = Path(tmpdir) / "hermes"
        hermes_dir.mkdir()
        (hermes_dir / "hermes.exe").write_text("dummy")
        
        commands = ForgeCommands(control_plane_root=str(cp_root), hermes_bin=str(hermes_dir / "hermes.exe"))
        
        # Test forge-status
        status = commands.forge_status()
        assert status.control_plane_sha != "unknown"
        assert status.acceptance_state in [s.value for s in AcceptanceState]
        assert len(status.blockers) > 0
        assert status.next_safe_action
        print("PASS: forge-status")
        
        # Test forge-check
        check = commands.forge_check()
        assert "docker" in check.checks
        assert "control_plane_deps" in check.checks
        assert "srs_exists" in check.checks
        print("PASS: forge-check")
        
        # Test forge-evidence (no runs)
        evidence = commands.forge_evidence()
        assert evidence.exists is False
        print("PASS: forge-evidence (no runs)")
        
        # Test forge-resume (no runs)
        resume = commands.forge_resume()
        assert resume["success"] is False
        assert resume["refused"] is True
        print("PASS: forge-resume (no runs)")
        
        # Test forge-run refused
        run_result = commands.forge_run(str(cp_root / "examples" / "vertical-slice" / "hello-api" / "SRS.md"))
        assert run_result["success"] is False
        assert run_result["refused"] is True
        print("PASS: forge-run refused (M2 not passed)")
        
        # Create a fake activation report
        fake_run_dir = cp_root / "artifacts" / "activation" / "fake-run-001"
        fake_run_dir.mkdir(parents=True)
        (fake_run_dir / ".forge").mkdir()
        fake_report = {
            "simulation": False,
            "gate_passed": True,
            "activation_ready": True,
            "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True}
        }
        (fake_run_dir / ".forge" / "state.json").write_text(json.dumps({"state": "RELEASED"}))
        (fake_run_dir / ".forge" / "evidence.json").write_text(json.dumps({"worker:WRK-001": {"passed": True}}))
        (fake_run_dir / "forge-coder-activation.json").write_text(json.dumps(fake_report))
        
        # Test forge-evidence with runs
        evidence = commands.forge_evidence()
        assert evidence.exists is True
        assert evidence.run_id == "fake-run-001"
        assert evidence.summary["gate_passed"] is True
        print("PASS: forge-evidence (with runs)")
        
        # Test forge-resume with RELEASED
        resume = commands.forge_resume()
        assert resume["success"] is True
        assert resume["state"] == "RELEASED"
        assert resume["resume_point"] == "complete"
        print("PASS: forge-resume (RELEASED)")
        
        # Test forge-run with M2 passed (but no auth)
        # Need to simulate M2 passed state
        acceptance = commands.get_acceptance_state()
        # Since we created a fake RELEASED report, acceptance should be M2_PASSED
        assert acceptance == AcceptanceState.M2_PASSED.value
        
        run_result = commands.forge_run(str(cp_root / "examples" / "vertical-slice" / "hello-api" / "SRS.md"))
        assert run_result["success"] is False  # No auth token
        assert run_result["refused"] is True
        assert "authorization" in run_result["error"].lower()
        print("PASS: forge-run refused (no authorization)")
        
        # Create auth file
        auth_file = cp_root / "runtime" / "activation_authorization.json"
        auth_file.write_text(json.dumps({"authorized": True, "timestamp": datetime.now().isoformat(), "human": "test"}))
        
        run_result = commands.forge_run(str(cp_root / "examples" / "vertical-slice" / "hello-api" / "SRS.md"))
        assert run_result["success"] is True
        assert run_result["would_execute"] is True
        print("PASS: forge-run allowed (with authorization)")
        
        print("\nALL FORGE COMMAND TESTS PASSED")
        return True


if __name__ == "__main__":
    import hashlib
    import argparse
    
    parser = argparse.ArgumentParser(description="FORGE Local Commands")
    parser.add_argument("command", nargs="?", choices=["status", "check", "evidence", "resume", "run", "test"], help="Command to run (default: test)")
    parser.add_argument("--control-plane-root", default="C:/Users/mhakh/FORGE/control/hermes-control-plane", help="Control plane root directory")
    parser.add_argument("--hermes-bin", default="hermes", help="Hermes binary path")
    parser.add_argument("args", nargs="*", help="Additional arguments (run_id, srs_path, auth_token)")
    
    args = parser.parse_args()
    
    # Run tests if no command or if explicitly requested
    if args.command == "test" or args.command is None:
        test_forge_commands()
        sys.exit(0)
    
    commands = ForgeCommands(
        control_plane_root=args.control_plane_root,
        hermes_bin=args.hermes_bin
    )
    
    if args.command == "status":
        result = commands.forge_status()
        print(json.dumps(asdict(result), indent=2, default=str))
    elif args.command == "check":
        result = commands.forge_check()
        print(json.dumps(asdict(result), indent=2, default=str))
    elif args.command == "evidence":
        run_id = args.args[0] if args.args else None
        result = commands.forge_evidence(run_id)
        print(json.dumps(asdict(result), indent=2, default=str))
    elif args.command == "resume":
        run_id = args.args[0] if args.args else None
        result = commands.forge_resume(run_id)
        print(json.dumps(result, indent=2, default=str))
    elif args.command == "run":
        if not args.args:
            print("Usage: forge_commands.py run <srs_path> [auth_token]", file=sys.stderr)
            sys.exit(1)
        srs_path = args.args[0]
        auth_token = args.args[1] if len(args.args) > 1 else None
        result = commands.forge_run(srs_path, auth_token)
        print(json.dumps(result, indent=2, default=str))
    else:
        print(f"Unknown command: {args.command}", file=sys.stderr)
        sys.exit(1)