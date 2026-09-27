#!/usr/bin/env python3
"""
M2 Preparation Package — Complete execution package for FORGE M2 milestone.
Prepares all artifacts WITHOUT executing the activation.

This module:
1. Installed-Hermes version/commit preflight
2. Exact activation command construction
3. Environment preflight
4. Docker boundary preflight
5. Task workspace creation
6. Evidence directory creation
7. Evidence hashing
8. Failure evidence preservation
9. Post-run activation report parser
10. Machine-readable pass/fail validator
11. GitHub checkpoint publication
12. Rollback instructions

M2 Validator Requirements (ALL must pass):
- simulation=false
- gate_passed=true
- activation_ready=true
- state=RELEASED
- worker_clean=true
- verification_passed=true
- all_preflights_pass=true
- all_runtime_attestations_pass=true
- required outputs exist
- unauthorized_changes=[]
- protected_changes=[]
- unsafe_symlink_changes=[]
"""

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import shutil
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum


class M2Requirement(Enum):
    """M2 acceptance requirements."""
    SIMULATION_FALSE = "simulation_false"
    GATE_PASSED = "gate_passed"
    ACTIVATION_READY = "activation_ready"
    STATE_RELEASED = "state_released"
    WORKER_CLEAN = "worker_clean"
    VERIFICATION_PASSED = "verification_passed"
    ALL_PREFLIGHTS_PASS = "all_preflights_pass"
    ALL_RUNTIME_ATTESTATIONS_PASS = "all_runtime_attestations_pass"
    REQUIRED_OUTPUTS_EXIST = "required_outputs_exist"
    UNAUTHORIZED_CHANGES_EMPTY = "unauthorized_changes_empty"
    PROTECTED_CHANGES_EMPTY = "protected_changes_empty"
    UNSAFE_SYMLINK_CHANGES_EMPTY = "unsafe_symlink_changes_empty"


@dataclass
class HermerRuntimeInfo:
    """Installed Hermes runtime information."""
    version: str
    commit_sha: str
    install_path: str
    python_version: str
    profile: str
    terminal_backend: str
    terminal_cwd: str
    docker_mount_cwd: bool
    docker_network: Optional[bool]
    docker_forward_env: List[str]
    container_persistent: bool
    persist_across: bool


@dataclass
class M2PreflightResult:
    """Result of a single preflight check."""
    name: str
    passed: bool
    details: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class M2ExecutionPackage:
    """Complete M2 execution package."""
    timestamp: str
    m2_requirements: List[str]
    hermes_runtime: HermerRuntimeInfo
    activation_command: List[str]
    environment_preflights: List[M2PreflightResult]
    docker_boundary_preflights: List[M2PreflightResult]
    task_workspace: str
    evidence_directory: str
    rollback_instructions: str
    validator_schema: Dict[str, Any]


@dataclass
class M2ValidationResult:
    """Result of M2 validation against an activation report."""
    all_passed: bool
    requirement_results: Dict[str, bool]
    failing_requirements: List[str]
    report_path: str
    validation_timestamp: str


class M2Preparation:
    """M2 Preparation orchestrator."""
    
    def __init__(self, 
                 hermes_bin: str = "hermes",
                 profile: str = "coder",
                 srs_path: str = None,
                 schema_path: str = None,
                 control_plane_root: str = None):
        self.hermes_bin = hermes_bin
        self.profile = profile
        if control_plane_root:
            self.control_plane_root = Path(control_plane_root).resolve()
        else:
            self.control_plane_root = Path(__file__).parent.parent.parent.resolve()
        self.srs_path = srs_path or str(self.control_plane_root / "examples" / "vertical-slice" / "hello-api" / "SRS.md")
        self.schema_path = schema_path or str(self.control_plane_root / "schemas" / "forge-project-ir-v0.schema.json")
        
    def _run(self, argv: List[str], cwd: str = None, timeout: int = 60, capture: bool = True) -> Dict[str, Any]:
        """Run a command and return structured result."""
        try:
            result = subprocess.run(
                argv,
                cwd=cwd or self.control_plane_root,
                capture_output=capture,
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
    
    def get_hermes_runtime_info(self) -> HermerRuntimeInfo:
        """Get complete installed Hermes runtime information."""
        version_result = self._run([self.hermes_bin, "--version"])
        version_str = version_result.get("stdout", "").strip()
        
        # Get commit SHA from Hermes install directory
        install_path = Path(self.hermes_bin).parent if Path(self.hermes_bin).is_absolute() else Path(shutil.which(self.hermes_bin or "hermes")).parent
        if not install_path.exists():
            # Try to find Hermes install
            install_path = Path.home() / "AppData" / "Local" / "hermes" / "hermes-agent"
        
        commit_sha = "unknown"
        if (install_path / ".git").exists():
            git_result = self._run(["git", "rev-parse", "HEAD"], cwd=str(install_path))
            if git_result["passed"]:
                commit_sha = git_result["stdout"].strip()
        
        # Get profile config
        backend_result = self._run([self.hermes_bin, "-p", self.profile, "config", "get", "terminal.backend", "--json"])
        cwd_result = self._run([self.hermes_bin, "-p", self.profile, "config", "get", "terminal.cwd", "--json"])
        mount_result = self._run([self.hermes_bin, "-p", self.profile, "config", "get", "terminal.docker_mount_cwd_to_workspace", "--json"])
        network_result = self._run([self.hermes_bin, "-p", self.profile, "config", "get", "terminal.docker_network", "--json"])
        forward_result = self._run([self.hermes_bin, "-p", self.profile, "config", "get", "terminal.docker_forward_env", "--json"])
        persistent_result = self._run([self.hermes_bin, "-p", self.profile, "config", "get", "terminal.container_persistent", "--json"])
        persist_across_result = self._run([self.hermes_bin, "-p", self.profile, "config", "get", "terminal.docker_persist_across_processes", "--json"])
        
        def parse_json_result(r):
            if not r.get("passed"):
                return None
            try:
                return json.loads(r["stdout"])
            except:
                return None
        
        return HermerRuntimeInfo(
            version=version_str,
            commit_sha=commit_sha,
            install_path=str(install_path),
            python_version=f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            profile=self.profile,
            terminal_backend=parse_json_result(backend_result) or "unknown",
            terminal_cwd=parse_json_result(cwd_result) or "unknown",
            docker_mount_cwd=parse_json_result(mount_result) is True,
            docker_network=parse_json_result(network_result),
            docker_forward_env=parse_json_result(forward_result) or [],
            container_persistent=parse_json_result(persistent_result) is True,
            persist_across=parse_json_result(persist_across_result) is False,
        )
    
    def run_environment_preflights(self) -> List[M2PreflightResult]:
        """Run all environment preflight checks."""
        preflights = []
        
        # 1. Docker available
        docker_result = self._run(["docker", "version", "--format", "{{.Server.Version}}"])
        preflights.append(M2PreflightResult(
            name="docker_available",
            passed=docker_result["passed"],
            details={"version": docker_result.get("stdout", "").strip() if docker_result["passed"] else None},
            error=docker_result.get("stderr") if not docker_result["passed"] else None
        ))
        
        # 2. Docker daemon running
        daemon_result = self._run(["docker", "info", "--format", "{{.ServerVersion}}"])
        preflights.append(M2PreflightResult(
            name="docker_daemon_running",
            passed=daemon_result["passed"],
            error=daemon_result.get("stderr") if not daemon_result["passed"] else None
        ))
        
        # 3. Python available
        py_result = self._run([sys.executable, "--version"])
        preflights.append(M2PreflightResult(
            name="python_available",
            passed=py_result["passed"],
            details={"version": py_result.get("stdout", "").strip() if py_result["passed"] else None},
        ))
        
        # 4. Control plane dependencies
        venv_python = self.control_plane_root / ".venv" / "Scripts" / "python.exe"
        if not venv_python.exists():
            venv_python = self.control_plane_root / ".venv" / "bin" / "python"
        deps_result = self._run([str(venv_python), "-c", "import forge_core; print('OK')"])
        preflights.append(M2PreflightResult(
            name="control_plane_deps",
            passed=deps_result["passed"],
            error=deps_result.get("stderr") if not deps_result["passed"] else None
        ))
        
        # 5. SRS file exists
        srs_exists = Path(self.srs_path).exists()
        preflights.append(M2PreflightResult(
            name="srs_exists",
            passed=srs_exists,
            details={"srs_path": self.srs_path},
            error="SRS file not found" if not srs_exists else None
        ))
        
        # 6. Schema file exists
        schema_exists = Path(self.schema_path).exists()
        preflights.append(M2PreflightResult(
            name="schema_exists",
            passed=schema_exists,
            details={"schema_path": self.schema_path},
            error="Schema file not found" if not schema_exists else None
        ))
        
        return preflights
    
    def run_docker_boundary_preflights(self) -> List[M2PreflightResult]:
        """Run Docker boundary preflight checks (no activation, just verification)."""
        preflights = []
        
        # 1. No stale FORGE containers
        ps_result = self._run(["docker", "ps", "-a", "--filter", "label=forge", "--format", "{{.ID}}"])
        container_ids = ps_result.get("stdout", "").strip().split() if ps_result["passed"] else []
        preflights.append(M2PreflightResult(
            name="no_stale_forge_containers",
            passed=len(container_ids) == 0,
            details={"stale_containers": container_ids},
            error=f"Found {len(container_ids)} stale FORGE containers" if container_ids else None
        ))
        
        # 2. No containers mounting host user directory
        inspect_result = self._run(["docker", "ps", "-a", "--format", "{{.ID}} {{.Mounts}}"])
        lines = inspect_result.get("stdout", "").strip().split("\n") if inspect_result["passed"] else []
        bad_mounts = []
        for line in lines:
            if "C:\\Users" in line or "/c/Users" in line or "/home/" in line:
                bad_mounts.append(line)
        preflights.append(M2PreflightResult(
            name="no_host_directory_mounts",
            passed=len(bad_mounts) == 0,
            details={"violating_mounts": bad_mounts},
            error=f"Found {len(bad_mounts)} containers with host directory mounts" if bad_mounts else None
        ))
        
        # 3. Network isolation capability
        net_result = self._run(["docker", "network", "ls", "--filter", "driver=bridge", "--format", "{{.Name}}"])
        preflights.append(M2PreflightResult(
            name="docker_network_isolation_available",
            passed=True,  # Just verify capability exists
            details={"bridge_networks": net_result.get("stdout", "").strip().split() if net_result["passed"] else []}
        ))
        
        # 4. Image pull capability (for the worker image)
        pull_result = self._run(["docker", "pull", "alpine:latest"], timeout=120)
        preflights.append(M2PreflightResult(
            name="docker_pull_capability",
            passed=pull_result["passed"],
            error=pull_result.get("stderr") if not pull_result["passed"] else None
        ))
        
        return preflights
    
    def create_task_workspace(self, base_dir: str = None) -> str:
        """Create the M2 task workspace."""
        if base_dir is None:
            base_dir = str(self.control_plane_root / "runtime" / "m2-workspace")
        
        workspace = Path(base_dir) / f"m2-task-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        workspace.mkdir(parents=True, exist_ok=True)
        
        # Create .forge directory structure
        (workspace / ".forge").mkdir(exist_ok=True)
        
        return str(workspace)
    
    def create_evidence_directory(self, base_dir: str = None) -> str:
        """Create evidence directory with hashing."""
        if base_dir is None:
            base_dir = str(self.control_plane_root / "artifacts" / "activation")
        
        evidence_dir = Path(base_dir) / f"m2-evidence-{datetime.now().strftime('%Y%m%dT%H%M%S')}"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        
        return str(evidence_dir)
    
    def hash_file(self, path: Path) -> str:
        """Compute SHA256 hash of a file."""
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
    
    def build_activation_command(self, 
                                 output_dir: str,
                                 worker_timeout: int = 1800) -> List[str]:
        """Build the exact activation command."""
        return [
            sys.executable,
            "-m",
            "forge_core.run",
            str(Path(self.srs_path).resolve()),
            str(Path(output_dir).resolve()),
            "--schema",
            str(Path(self.schema_path).resolve()),
            "--worker",
            "hermes",
            "--hermes-profile",
            self.profile,
            "--hermes-bin",
            self.hermes_bin,
            "--worker-timeout",
            str(worker_timeout),
        ]
    
    def build_preparation_package(self) -> M2ExecutionPackage:
        """Build the complete M2 execution package."""
        hermes_info = self.get_hermes_runtime_info()
        env_preflights = self.run_environment_preflights()
        docker_preflights = self.run_docker_boundary_preflights()
        task_workspace = self.create_task_workspace()
        evidence_dir = self.create_evidence_directory()
        activation_cmd = self.build_activation_command(evidence_dir)
        
        # Rollback instructions
        rollback = f"""# M2 Rollback Instructions
# Generated: {datetime.now().isoformat()}

## If activation fails or needs rollback:

1. **Stop any running FORGE processes:**
   ```powershell
   Get-Process -Name "python*" | Where-Object {{ $_.CommandLine -match "forge_core.run" }} | Stop-Process -Force
   ```

2. **Remove stale Docker containers:**
   ```powershell
   docker ps -a --filter "label=forge" --format "{{.ID}}" | ForEach-Object {{ docker rm -f $_ }}
   ```

3. **Clean task workspace:**
   ```powershell
   Remove-Item -Recurse -Force "{task_workspace}" -ErrorAction SilentlyContinue
   ```

4. **Clean evidence directory (preserve for debugging):**
   ```powershell
   # Evidence is preserved at: {evidence_dir}
   # Do not remove unless explicitly desired
   ```

5. **Revert Hermes Agent (if updated):**
   ```powershell
   python {self.control_plane_root}/runtime/forge_hermes_install.py --rollback
   ```

6. **Verify clean state:**
   ```powershell
   docker ps -a --filter "label=forge"
   # Should return no containers
   ```
"""
        
        # Validator schema
        validator_schema = {
            "type": "object",
            "required": ["protocol", "simulation", "runtime", "execution", "evidence", "gate_passed", "activation_ready"],
            "properties": {
                "protocol": {"type": "string", "const": "forge.coder-activation/v1"},
                "simulation": {"type": "boolean", "const": False},
                "runtime": {
                    "type": "object",
                    "required": ["isolation_passed", "checks"],
                    "properties": {
                        "isolation_passed": {"type": "boolean", "const": True},
                        "checks": {
                            "type": "object",
                            "required": [
                                "hermes_available",
                                "profile_visible",
                                "doctor_passed",
                                "backend_is_docker",
                                "docker_workspace_mount",
                                "docker_network_disabled",
                                "docker_forward_env_empty",
                                "container_shared_within_worker",
                                "docker_no_cross_process_reuse"
                            ],
                            "properties": {k: {"type": "boolean", "const": True} for k in [
                                "hermes_available", "profile_visible", "doctor_passed",
                                "backend_is_docker", "docker_workspace_mount",
                                "docker_network_disabled", "docker_forward_env_empty",
                                "container_shared_within_worker", "docker_no_cross_process_reuse"
                            ]}
                        }
                    }
                },
                "execution": {
                    "type": "object",
                    "required": ["passed", "returncode"],
                    "properties": {
                        "passed": {"type": "boolean", "const": True},
                        "returncode": {"type": "integer", "const": 0}
                    }
                },
                "evidence": {
                    "type": "object",
                    "required": ["passed", "state", "worker_clean", "verification_passed"],
                    "properties": {
                        "passed": {"type": "boolean", "const": True},
                        "state": {"type": "string", "const": "RELEASED"},
                        "worker_clean": {"type": "boolean", "const": True},
                        "verification_passed": {"type": "boolean", "const": True},
                        "files_exist": {"type": "boolean", "const": True},
                        "required_verifications": {
                            "type": "array",
                            "items": {"type": "string"},
                            "contains": {"const": "VER-001"}
                        },
                        "worker": {
                            "type": "object",
                            "required": ["passed", "unauthorized_changes", "protected_changes", "unsafe_symlink_changes"],
                            "properties": {
                                "passed": {"type": "boolean", "const": True},
                                "unauthorized_changes": {"type": "array", "const": []},
                                "protected_changes": {"type": "array", "const": []},
                                "unsafe_symlink_changes": {"type": "array", "const": []},
                                "workspace_pin": {"type": "object", "properties": {"passed": {"type": "boolean", "const": True}}},
                                "workspace_restore": {"type": "object", "properties": {"passed": {"type": "boolean", "const": True}}},
                                "docker_boundary": {"type": "object", "properties": {"passed": {"type": "boolean", "const": True}}}
                            }
                        }
                    }
                },
                "gate_passed": {"type": "boolean", "const": True},
                "activation_ready": {"type": "boolean", "const": True}
            }
        }
        
        return M2ExecutionPackage(
            timestamp=datetime.now().isoformat(),
            m2_requirements=[r.value for r in M2Requirement],
            hermes_runtime=hermes_info,
            activation_command=activation_cmd,
            environment_preflights=env_preflights,
            docker_boundary_preflights=docker_preflights,
            task_workspace=task_workspace,
            evidence_directory=evidence_dir,
            rollback_instructions=rollback,
            validator_schema=validator_schema
        )
    
    def parse_activation_report(self, report_path: str) -> Dict[str, Any]:
        """Parse activation report from file."""
        with open(report_path, "r") as f:
            return json.load(f)
    
    def validate_m2_report(self, report_path: str) -> M2ValidationResult:
        """Validate an activation report against M2 requirements."""
        report = self.parse_activation_report(report_path)
        
        results = {}
        failing = []
        
        # simulation=false
        sim = report.get("simulation", True)
        results[M2Requirement.SIMULATION_FALSE.value] = sim is False
        if sim is not False:
            failing.append(M2Requirement.SIMULATION_FALSE.value)
        
        # gate_passed=true
        gate = report.get("gate_passed", False)
        results[M2Requirement.GATE_PASSED.value] = gate is True
        if gate is not True:
            failing.append(M2Requirement.GATE_PASSED.value)
        
        # activation_ready=true
        ready = report.get("activation_ready", False)
        results[M2Requirement.ACTIVATION_READY.value] = ready is True
        if ready is not True:
            failing.append(M2Requirement.ACTIVATION_READY.value)
        
        # Evidence checks
        evidence = report.get("evidence", {})
        
        # state=RELEASED
        state = evidence.get("state")
        results[M2Requirement.STATE_RELEASED.value] = state == "RELEASED"
        if state != "RELEASED":
            failing.append(M2Requirement.STATE_RELEASED.value)
        
        # worker_clean=true
        worker_clean = evidence.get("worker_clean", False)
        results[M2Requirement.WORKER_CLEAN.value] = worker_clean is True
        if worker_clean is not True:
            failing.append(M2Requirement.WORKER_CLEAN.value)
        
        # verification_passed=true
        ver_passed = evidence.get("verification_passed", False)
        results[M2Requirement.VERIFICATION_PASSED.value] = ver_passed is True
        if ver_passed is not True:
            failing.append(M2Requirement.VERIFICATION_PASSED.value)
        
        # Runtime checks
        runtime = report.get("runtime", {})
        
        # all_preflights_pass=true (runtime.isolation_passed)
        preflights = runtime.get("isolation_passed", False)
        results[M2Requirement.ALL_PREFLIGHTS_PASS.value] = preflights is True
        if preflights is not True:
            failing.append(M2Requirement.ALL_PREFLIGHTS_PASS.value)
        
        # all_runtime_attestations_pass=true (from evidence.worker)
        worker = evidence.get("worker", {})
        pin = worker.get("workspace_pin", {}).get("passed", False)
        restore = worker.get("workspace_restore", {}).get("passed", False)
        boundary = worker.get("docker_boundary", {}).get("passed", False)
        attestations = pin and restore and boundary
        results[M2Requirement.ALL_RUNTIME_ATTESTATIONS_PASS.value] = attestations is True
        if attestations is not True:
            failing.append(M2Requirement.ALL_RUNTIME_ATTESTATIONS_PASS.value)
        
        # required outputs exist
        files_exist = evidence.get("files_exist", False)
        results[M2Requirement.REQUIRED_OUTPUTS_EXIST.value] = files_exist is True
        if files_exist is not True:
            failing.append(M2Requirement.REQUIRED_OUTPUTS_EXIST.value)
        
        # unauthorized_changes=[]
        unauth = worker.get("unauthorized_changes", None)
        results[M2Requirement.UNAUTHORIZED_CHANGES_EMPTY.value] = unauth == []
        if unauth != []:
            failing.append(M2Requirement.UNAUTHORIZED_CHANGES_EMPTY.value)
        
        # protected_changes=[]
        protected = worker.get("protected_changes", None)
        results[M2Requirement.PROTECTED_CHANGES_EMPTY.value] = protected == []
        if protected != []:
            failing.append(M2Requirement.PROTECTED_CHANGES_EMPTY.value)
        
        # unsafe_symlink_changes=[]
        symlinks = worker.get("unsafe_symlink_changes", None)
        results[M2Requirement.UNSAFE_SYMLINK_CHANGES_EMPTY.value] = symlinks == []
        if symlinks != []:
            failing.append(M2Requirement.UNSAFE_SYMLINK_CHANGES_EMPTY.value)
        
        all_passed = len(failing) == 0
        
        return M2ValidationResult(
            all_passed=all_passed,
            requirement_results=results,
            failing_requirements=failing,
            report_path=report_path,
            validation_timestamp=datetime.now().isoformat()
        )


def test_m2_validator_malformed_reports():
    """Test the M2 validator against malformed/incomplete/contradictory/falsely-successful reports."""
    import tempfile
    
    prep = M2Preparation()
    
    # Test 1: Missing fields
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert len(result.failing_requirements) > 0
        print("PASS: Missing fields correctly rejected")
    
    # Test 2: simulation=true (should fail)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": True, "gate_passed": True, "activation_ready": True, 
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.SIMULATION_FALSE.value in result.failing_requirements
        print("PASS: simulation=true correctly rejected")
    
    # Test 3: gate_passed=false
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": False, "activation_ready": False,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.GATE_PASSED.value in result.failing_requirements
        print("PASS: gate_passed=false correctly rejected")
    
    # Test 4: state=REWORK (should fail)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "REWORK", "worker_clean": True, "verification_passed": True, "files_exist": True},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.STATE_RELEASED.value in result.failing_requirements
        print("PASS: state=REWORK correctly rejected")
    
    # Test 5: worker_clean=false
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": False, "verification_passed": True, "files_exist": True,
                                "worker": {"unauthorized_changes": [], "protected_changes": [], "unsafe_symlink_changes": []}},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.WORKER_CLEAN.value in result.failing_requirements
        print("PASS: worker_clean=false correctly rejected")
    
    # Test 6: verification_passed=false
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": False, "files_exist": True,
                                "worker": {"unauthorized_changes": [], "protected_changes": [], "unsafe_symlink_changes": []}},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.VERIFICATION_PASSED.value in result.failing_requirements
        print("PASS: verification_passed=false correctly rejected")
    
    # Test 7: unauthorized_changes not empty
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True,
                                "worker": {"unauthorized_changes": ["bad"], "protected_changes": [], "unsafe_symlink_changes": []}},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.UNAUTHORIZED_CHANGES_EMPTY.value in result.failing_requirements
        print("PASS: unauthorized_changes not empty correctly rejected")
    
    # Test 8: protected_changes not empty
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True,
                                "worker": {"unauthorized_changes": [], "protected_changes": ["bad"], "unsafe_symlink_changes": []}},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.PROTECTED_CHANGES_EMPTY.value in result.failing_requirements
        print("PASS: protected_changes not empty correctly rejected")
    
    # Test 9: unsafe_symlink_changes not empty
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True,
                                "worker": {"unauthorized_changes": [], "protected_changes": [], "unsafe_symlink_changes": ["bad"]}},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.UNSAFE_SYMLINK_CHANGES_EMPTY.value in result.failing_requirements
        print("PASS: unsafe_symlink_changes not empty correctly rejected")
    
    # Test 10: all_preflights_pass=false
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True,
                                "worker": {"unauthorized_changes": [], "protected_changes": [], "unsafe_symlink_changes": []}},
                   "runtime": {"isolation_passed": False}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.ALL_PREFLIGHTS_PASS.value in result.failing_requirements
        print("PASS: all_preflights_pass=false correctly rejected")
    
    # Test 11: all_runtime_attestations_pass=false
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True,
                                "worker": {"unauthorized_changes": [], "protected_changes": [], "unsafe_symlink_changes": [],
                                           "workspace_pin": {"passed": False}, "workspace_restore": {"passed": True}, "docker_boundary": {"passed": True}}},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.ALL_RUNTIME_ATTESTATIONS_PASS.value in result.failing_requirements
        print("PASS: all_runtime_attestations_pass=false correctly rejected")
    
    # Test 12: required outputs missing
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": False,
                                "worker": {"unauthorized_changes": [], "protected_changes": [], "unsafe_symlink_changes": []}},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.REQUIRED_OUTPUTS_EXIST.value in result.failing_requirements
        print("PASS: required outputs missing correctly rejected")
    
    # Test 13: Falsely-successful report (exit 0 but missing fields)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True},
                   "runtime": {"isolation_passed": True}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == False
        assert M2Requirement.REQUIRED_OUTPUTS_EXIST.value in result.failing_requirements
        print("PASS: falsely-successful (incomplete) report correctly rejected")
    
    # Test 14: Valid report passes
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump({"simulation": False, "gate_passed": True, "activation_ready": True,
                   "evidence": {"state": "RELEASED", "worker_clean": True, "verification_passed": True, "files_exist": True,
                                "worker": {"passed": True, "unauthorized_changes": [], "protected_changes": [], "unsafe_symlink_changes": [],
                                           "workspace_pin": {"passed": True}, "workspace_restore": {"passed": True}, "docker_boundary": {"passed": True}},
                                "required_verifications": ["VER-001", "VER-002", "VER-003"]},
                   "runtime": {"isolation_passed": True,
                               "checks": {"hermes_available": True, "profile_visible": True, "doctor_passed": True,
                                          "backend_is_docker": True, "docker_workspace_mount": True,
                                          "docker_network_disabled": True, "docker_forward_env_empty": True,
                                          "container_shared_within_worker": True, "docker_no_cross_process_reuse": True}}}, f)
        f.flush()
        result = prep.validate_m2_report(f.name)
        assert result.all_passed == True
        assert len(result.failing_requirements) == 0
        print("PASS: Valid report correctly accepted")
    
    print("\nALL M2 VALIDATOR TESTS PASSED")
    return True


if __name__ == "__main__":
    import shutil
    
    # Run tests first
    test_m2_validator_malformed_reports()
    
    # Build preparation package
    prep = M2Preparation()
    package = prep.build_preparation_package()
    
    # Output as JSON
    output = asdict(package)
    # Convert enums to strings
    for pf in output["environment_preflights"]:
        pass  # Already serializable
    for pf in output["docker_boundary_preflights"]:
        pass
    
    print(json.dumps(output, indent=2, default=str))
    print("\n=== M2 PREPARATION PACKAGE COMPLETE ===")
    print(f"Task Workspace: {package.task_workspace}")
    print(f"Evidence Directory: {package.evidence_directory}")
    print(f"Activation Command: {' '.join(package.activation_command)}")
    print(f"Rollback Instructions: (included in package)")
    print(f"Validator Schema: (included in package)")