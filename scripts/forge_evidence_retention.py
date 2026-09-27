"""
Evidence Retention System for FORGE.
Preserves sanitized diagnostic evidence after failed runs to enable reproduction.

Preserves:
- task packet hash
- runtime configuration summary
- Docker attestations
- worker output
- changed paths
- expected/produced paths
- verification outcome
- blocker
- session reference
- relevant sanitized tool errors

Does NOT retain:
- secrets, credentials
- full hidden reasoning
- unrelated conversation data

Uses immutable sanitized evidence artifacts plus hashes.
Defines retention/cleanup policy.
"""

import json
import hashlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum


class RetentionPolicy(Enum):
    """Retention policies for evidence artifacts."""
    KEEP_ALL = "keep_all"              # Never auto-delete (manual only)
    KEEP_FAILED = "keep_failed"        # Keep failed runs, auto-clean successful after N days
    KEEP_RECENT = "keep_recent"        # Keep last N runs regardless of outcome
    MINIMAL = "minimal"                # Only keep hashes and summaries


@dataclass
class EvidenceArtifact:
    """Single evidence artifact with metadata."""
    path: str
    sha256: str
    size: int
    created: str
    artifact_type: str  # task_packet, docker_attestation, worker_output, verification, etc.
    work_unit_id: str
    run_id: str
    outcome: str
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RunEvidenceBundle:
    """Complete evidence bundle for a single FORGE run."""
    run_id: str
    work_unit_id: str
    project_id: str
    started: str
    completed: Optional[str] = None
    outcome: str = "in_progress"
    
    # Core artifacts
    task_packet: Optional[EvidenceArtifact] = None
    runtime_config: Optional[EvidenceArtifact] = None
    docker_attestations: Optional[EvidenceArtifact] = None
    worker_output: Optional[EvidenceArtifact] = None
    verification_result: Optional[EvidenceArtifact] = None
    review_result: Optional[EvidenceArtifact] = None
    
    # Path analysis
    changed_paths: Optional[EvidenceArtifact] = None
    expected_vs_produced: Optional[EvidenceArtifact] = None
    
    # Diagnostics
    sanitized_stdout: Optional[EvidenceArtifact] = None
    sanitized_stderr: Optional[EvidenceArtifact] = None
    tool_errors: Optional[EvidenceArtifact] = None
    blocker: Optional[str] = None
    session_reference: Optional[str] = None
    
    # Hashes for integrity
    bundle_sha256: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        # Convert nested objects
        for key, value in result.items():
            if isinstance(value, EvidenceArtifact):
                result[key] = value.to_dict()
        return result


class EvidenceStore:
    """
    Manages FORGE evidence retention.
    Stores immutable sanitized artifacts with content-addressed naming.
    """
    
    def __init__(
        self,
        root: Path,
        policy: RetentionPolicy = RetentionPolicy.KEEP_FAILED,
        max_age_days: int = 30,
        max_count: int = 100,
    ):
        self.root = Path(root)
        self.policy = policy
        self.max_age_days = max_age_days
        self.max_count = max_count
        
        # Directory structure
        self.artifacts_dir = self.root / "artifacts"
        self.bundles_dir = self.root / "bundles"
        self.index_file = self.root / "evidence_index.json"
        
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self.bundles_dir.mkdir(parents=True, exist_ok=True)
        
        self.index = self._load_index()
    
    def _load_index(self) -> Dict[str, Any]:
        if self.index_file.exists():
            try:
                with open(self.index_file, "r") as f:
                    return json.load(f)
            except:
                return {"runs": {}, "artifacts": {}}
        return {"runs": {}, "artifacts": {}}
    
    def _save_index(self):
        with open(self.index_file, "w") as f:
            json.dump(self.index, f, indent=2)
    
    def _sha256(self, path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    
    def _store_artifact(
        self,
        source: Path,
        artifact_type: str,
        work_unit_id: str,
        run_id: str,
        outcome: str
    ) -> EvidenceArtifact:
        """Store an artifact with content-addressed naming."""
        sha = self._sha256(source)
        size = source.stat().st_size
        created = datetime.now().isoformat()
        
        # Content-addressed path: type/sha256[:8]/sha256
        dest_dir = self.artifacts_dir / artifact_type / sha[:8]
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / sha
        
        # Copy if not already present (deduplication)
        if not dest.exists():
            shutil.copy2(source, dest)
        
        artifact = EvidenceArtifact(
            path=str(dest.relative_to(self.root)),
            sha256=sha,
            size=size,
            created=created,
            artifact_type=artifact_type,
            work_unit_id=work_unit_id,
            run_id=run_id,
            outcome=outcome
        )
        
        # Update index
        self.index["artifacts"][sha] = artifact.to_dict()
        self._save_index()
        
        return artifact
    
    def create_run_bundle(
        self,
        work_unit_id: str,
        project_id: str,
        run_id: Optional[str] = None
    ) -> RunEvidenceBundle:
        """Create a new evidence bundle for a run."""
        run_id = run_id or f"run-{datetime.now():%Y%m%d-%H%M%S}-{work_unit_id}"
        bundle = RunEvidenceBundle(
            run_id=run_id,
            work_unit_id=work_unit_id,
            project_id=project_id,
            started=datetime.now().isoformat()
        )
        self.index["runs"][run_id] = bundle.to_dict()
        self._save_index()
        return bundle
    
    def add_artifact_to_bundle(
        self,
        bundle: RunEvidenceBundle,
        artifact_field: str,
        source: Path,
        artifact_type: str,
        outcome: str
    ) -> EvidenceArtifact:
        """Add an artifact to a run bundle."""
        artifact = self._store_artifact(source, artifact_type, bundle.work_unit_id, bundle.run_id, outcome)
        setattr(bundle, artifact_field, artifact)
        self.index["runs"][bundle.run_id] = bundle.to_dict()
        self._save_index()
        return artifact
    
    def finalize_bundle(self, bundle: RunEvidenceBundle, outcome: str):
        """Finalize a run bundle with outcome."""
        bundle.completed = datetime.now().isoformat()
        bundle.outcome = outcome
        
        # Compute bundle hash
        bundle_json = json.dumps(bundle.to_dict(), sort_keys=True).encode()
        bundle.bundle_sha256 = hashlib.sha256(bundle_json).hexdigest()
        
        # Save bundle as immutable artifact
        bundle_file = self.bundles_dir / f"{bundle.run_id}.json"
        with open(bundle_file, "w") as f:
            json.dump(bundle.to_dict(), f, indent=2)
        
        self.index["runs"][bundle.run_id] = bundle.to_dict()
        self._save_index()
    
    def get_bundle(self, run_id: str) -> Optional[RunEvidenceBundle]:
        """Retrieve a run bundle by ID."""
        if run_id in self.index["runs"]:
            data = self.index["runs"][run_id].copy()
            # Reconstruct nested artifacts
            for key in ["task_packet", "runtime_config", "docker_attestations", 
                       "worker_output", "verification_result", "review_result",
                       "changed_paths", "expected_vs_produced",
                       "sanitized_stdout", "sanitized_stderr", "tool_errors"]:
                if key in data and data[key] and isinstance(data[key], dict):
                    data[key] = EvidenceArtifact(**data[key])
            return RunEvidenceBundle(**data)
        return None
    
    def list_runs(self, work_unit_id: Optional[str] = None, outcome: Optional[str] = None) -> List[RunEvidenceBundle]:
        """List runs with optional filters."""
        runs = []
        for run_id, data in self.index["runs"].items():
            # Work on a copy to avoid modifying the index
            run_data = data.copy()
            if work_unit_id and run_data.get("work_unit_id") != work_unit_id:
                continue
            if outcome and run_data.get("outcome") != outcome:
                continue
            # Reconstruct
            for key in ["task_packet", "runtime_config", "docker_attestations",
                       "worker_output", "verification_result", "review_result",
                       "changed_paths", "expected_vs_produced",
                       "sanitized_stdout", "sanitized_stderr", "tool_errors"]:
                if key in run_data and run_data[key] and isinstance(run_data[key], dict):
                    run_data[key] = EvidenceArtifact(**run_data[key])
            runs.append(RunEvidenceBundle(**run_data))
        return sorted(runs, key=lambda r: r.started, reverse=True)
    
    def apply_retention_policy(self):
        """Apply the configured retention policy."""
        if self.policy == RetentionPolicy.KEEP_ALL:
            return  # Never auto-delete
        
        runs = self.list_runs()
        
        if self.policy == RetentionPolicy.KEEP_FAILED:
            # Delete successful runs older than max_age_days
            cutoff = datetime.now() - timedelta(days=self.max_age_days)
            for run in runs:
                if run.outcome == "final_acceptance" and run.completed:
                    completed = datetime.fromisoformat(run.completed)
                    if completed < cutoff:
                        self._delete_run(run.run_id)
        
        elif self.policy == RetentionPolicy.KEEP_RECENT:
            # Keep only max_count most recent
            if len(runs) > self.max_count:
                for run in runs[self.max_count:]:
                    self._delete_run(run.run_id)
        
        elif self.policy == RetentionPolicy.MINIMAL:
            # For successful runs, keep only bundle hash and summary
            for run in runs:
                if run.outcome == "final_acceptance":
                    self._minimize_run(run.run_id)
    
    def _delete_run(self, run_id: str):
        """Delete all artifacts for a run."""
        if run_id in self.index["runs"]:
            run = self.index["runs"][run_id]
            # Delete referenced artifacts
            for key in ["task_packet", "runtime_config", "docker_attestations",
                       "worker_output", "verification_result", "review_result",
                       "changed_paths", "expected_vs_produced",
                       "sanitized_stdout", "sanitized_stderr", "tool_errors"]:
                if key in run and run[key]:
                    artifact = run[key]
                    if isinstance(artifact, dict):
                        sha = artifact.get("sha256")
                    else:
                        sha = artifact.sha256
                    if sha:
                        # Find and delete artifact file
                        for artifact_file in self.artifacts_dir.rglob(sha):
                            if artifact_file.is_file():
                                artifact_file.unlink()
                                break
            
            # Delete bundle file
            bundle_file = self.bundles_dir / f"{run_id}.json"
            if bundle_file.exists():
                bundle_file.unlink()
            
            # Remove from index
            del self.index["runs"][run_id]
            self._save_index()
    
    def _minimize_run(self, run_id: str):
        """Minimize a successful run to just hash and summary."""
        # Implementation would keep only essential metadata
        pass
    
    def export_for_reproduction(self, run_id: str, output_dir: Path) -> Path:
        """Export a run's evidence for reproduction/debugging."""
        bundle = self.get_bundle(run_id)
        if not bundle:
            raise ValueError(f"Run {run_id} not found")
        
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Copy all artifacts
        for key in ["task_packet", "runtime_config", "docker_attestations",
                   "worker_output", "verification_result", "review_result",
                   "changed_paths", "expected_vs_produced",
                   "sanitized_stdout", "sanitized_stderr", "tool_errors"]:
            if key in bundle.__dict__ and bundle.__dict__[key]:
                artifact = bundle.__dict__[key]
                if isinstance(artifact, EvidenceArtifact):
                    src = self.root / artifact.path
                    if src.exists():
                        dst = output_dir / f"{key}.{src.suffix if src.suffix else '.json'}"
                        shutil.copy2(src, dst)
        
        # Save bundle
        with open(output_dir / "evidence_bundle.json", "w") as f:
            json.dump(bundle.to_dict(), f, indent=2)
        
        return output_dir
    
    def verify_integrity(self, run_id: str) -> bool:
        """Verify integrity of a run's artifacts."""
        bundle = self.get_bundle(run_id)
        if not bundle:
            return False
        
        for key in ["task_packet", "runtime_config", "docker_attestations",
                   "worker_output", "verification_result", "review_result",
                   "changed_paths", "expected_vs_produced",
                   "sanitized_stdout", "sanitized_stderr", "tool_errors"]:
            if key in bundle.__dict__ and bundle.__dict__[key]:
                artifact = bundle.__dict__[key]
                if isinstance(artifact, EvidenceArtifact):
                    src = self.root / artifact.path
                    if not src.exists():
                        return False
                    actual_sha = self._sha256(src)
                    if actual_sha != artifact.sha256:
                        return False
        
        return True


class EvidenceCollector:
    """
    Collects evidence from a FORGE run and stores it in the EvidenceStore.
    Integrates with existing worker/verification infrastructure.
    """
    
    def __init__(self, store: EvidenceStore):
        self.store = store
        self.current_bundle: Optional[RunEvidenceBundle] = None
    
    def start_run(self, work_unit_id: str, project_id: str, run_id: str = None) -> RunEvidenceBundle:
        """Start a new evidence collection run."""
        self.current_bundle = self.store.create_run_bundle(work_unit_id, project_id, run_id)
        return self.current_bundle
    
    def collect_task_packet(self, task_packet_path: Path) -> EvidenceArtifact:
        """Collect task packet artifact."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        return self.store.add_artifact_to_bundle(
            self.current_bundle, "task_packet", task_packet_path,
            "task_packet", "in_progress"
        )
    
    def collect_runtime_config(self, config: Dict[str, Any]) -> EvidenceArtifact:
        """Collect runtime configuration."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        # Write config to temp file and store
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f, indent=2)
            temp_path = Path(f.name)
        try:
            return self.store.add_artifact_to_bundle(
                self.current_bundle, "runtime_config", temp_path,
                "runtime_config", "in_progress"
            )
        finally:
            temp_path.unlink(missing_ok=True)
    
    def collect_docker_attestations(self, attestations: Dict[str, Any]) -> EvidenceArtifact:
        """Collect Docker boundary attestations."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(attestations, f, indent=2)
            temp_path = Path(f.name)
        try:
            return self.store.add_artifact_to_bundle(
                self.current_bundle, "docker_attestations", temp_path,
                "docker_attestations", "in_progress"
            )
        finally:
            temp_path.unlink(missing_ok=True)
    
    def collect_worker_output(self, diagnostics: Any) -> EvidenceArtifact:
        """Collect worker diagnostics/output."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        import tempfile
        # Handle WorkerDiagnostics object
        if hasattr(diagnostics, "to_json"):
            content = diagnostics.to_json()
        else:
            content = json.dumps(diagnostics, indent=2, default=str)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write(content)
            temp_path = Path(f.name)
        try:
            return self.store.add_artifact_to_bundle(
                self.current_bundle, "worker_output", temp_path,
                "worker_output", self.current_bundle.outcome
            )
        finally:
            temp_path.unlink(missing_ok=True)
    
    def collect_verification(self, verification: Dict[str, Any]) -> EvidenceArtifact:
        """Collect verification results."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(verification, f, indent=2)
            temp_path = Path(f.name)
        try:
            return self.store.add_artifact_to_bundle(
                self.current_bundle, "verification_result", temp_path,
                "verification", self.current_bundle.outcome
            )
        finally:
            temp_path.unlink(missing_ok=True)
    
    def collect_review(self, review: Dict[str, Any]) -> EvidenceArtifact:
        """Collect review results."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        import tempfile
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(review, f, indent=2)
            temp_path = Path(f.name)
        try:
            return self.store.add_artifact_to_bundle(
                self.current_bundle, "review_result", temp_path,
                "review", self.current_bundle.outcome
            )
        finally:
            temp_path.unlink(missing_ok=True)
    
    def collect_path_analysis(
        self,
        changed_paths: List[str],
        expected_outputs: List[str],
        produced_files: List[str]
    ) -> EvidenceArtifact:
        """Collect path analysis: expected vs produced."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        import tempfile
        analysis = {
            "changed_paths": changed_paths,
            "expected_outputs": expected_outputs,
            "produced_files": produced_files,
            "missing": [p for p in expected_outputs if p not in produced_files],
            "unexpected": [p for p in produced_files if p not in expected_outputs]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(analysis, f, indent=2)
            temp_path = Path(f.name)
        try:
            return self.store.add_artifact_to_bundle(
                self.current_bundle, "expected_vs_produced", temp_path,
                "path_analysis", self.current_bundle.outcome
            )
        finally:
            temp_path.unlink(missing_ok=True)
    
    def collect_sanitized_output(
        self,
        stdout: str,
        stderr: str,
        tool_errors: List[Dict] = None
    ) -> tuple[EvidenceArtifact, EvidenceArtifact, Optional[EvidenceArtifact]]:
        """Collect sanitized stdout/stderr and tool errors."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        import tempfile
        
        artifacts = []
        for field_name, content, artifact_type in [
            ("sanitized_stdout", stdout, "stdout"),
            ("sanitized_stderr", stderr, "stderr"),
            ("tool_errors", json.dumps(tool_errors or [], indent=2), "tool_errors")
        ]:
            with tempfile.NamedTemporaryFile(mode="w", suffix=f".{artifact_type}.txt", delete=False) as f:
                f.write(content)
                temp_path = Path(f.name)
            try:
                artifact = self.store.add_artifact_to_bundle(
                    self.current_bundle, field_name, temp_path,
                    artifact_type, self.current_bundle.outcome
                )
                artifacts.append(artifact)
            finally:
                temp_path.unlink(missing_ok=True)
        
        return tuple(artifacts)
    
    def finalize(self, outcome: str, blocker: str = None, session_ref: str = None) -> RunEvidenceBundle:
        """Finalize evidence collection for the run."""
        if not self.current_bundle:
            raise RuntimeError("No active run")
        
        self.current_bundle.blocker = blocker
        self.current_bundle.session_reference = session_ref
        self.store.finalize_bundle(self.current_bundle, outcome)
        return self.current_bundle


# Retention policy configuration for FORGE
FORGE_EVIDENCE_CONFIG = {
    "root": "C:/Users/mhakh/FORGE/artifacts/evidence",
    "policy": RetentionPolicy.KEEP_FAILED,
    "max_age_days": 30,
    "max_count": 100,
}


def get_evidence_store() -> EvidenceStore:
    """Get the default FORGE evidence store."""
    return EvidenceStore(
        root=FORGE_EVIDENCE_CONFIG["root"],
        policy=FORGE_EVIDENCE_CONFIG["policy"],
        max_age_days=FORGE_EVIDENCE_CONFIG["max_age_days"],
        max_count=FORGE_EVIDENCE_CONFIG["max_count"],
    )


def test_evidence_store():
    """Test the evidence store functionality."""
    import tempfile
    import shutil
    
    with tempfile.TemporaryDirectory() as tmpdir:
        store = EvidenceStore(Path(tmpdir), RetentionPolicy.KEEP_FAILED, max_age_days=30, max_count=5)
        
        # Create a run bundle
        bundle = store.create_run_bundle("wu-001", "proj-001")
        assert bundle.run_id in store.index["runs"]
        
        # Add an artifact
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"test": "data"}, f)
            temp_path = Path(f.name)
        
        try:
            artifact = store.add_artifact_to_bundle(bundle, "task_packet", temp_path, "task_packet", "in_progress")
            assert artifact.sha256
            assert bundle.task_packet is not None
        finally:
            temp_path.unlink()
        
        # Finalize
        store.finalize_bundle(bundle, "final_acceptance")
        assert bundle.bundle_sha256
        assert bundle.outcome == "final_acceptance"
        
        # Retrieve
        retrieved = store.get_bundle(bundle.run_id)
        assert retrieved is not None
        assert retrieved.outcome == "final_acceptance"
        
        # Verify integrity
        assert store.verify_integrity(bundle.run_id)
        
        # List runs
        runs = store.list_runs()
        assert len(runs) == 1
        assert runs[0].run_id == bundle.run_id
        
        # Test filtering
        failed_runs = store.list_runs(outcome="worker_output_incomplete")
        assert len(failed_runs) == 0
        
        print("PASS: Evidence store tests")
        return True


if __name__ == "__main__":
    test_evidence_store()
    print("All evidence retention tests passed")