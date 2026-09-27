#!/usr/bin/env python3
"""
Security Threat Model Closure — Workstream M.
Practical threat model for the current FORGE runtime.

Prioritizes:
- generated-code execution
- generated malicious tests
- path traversal
- symlinks
- Docker mount escape
- Docker socket exposure
- credential/environment leakage
- network access
- Git remote mutation
- repository prompt injection
- fake model completion
- state/evidence tampering
- verification tampering
- cross-task contamination
- persistent-container reuse
- retry-budget bypass
- host execution exposure

For each records: threat, current mitigation, remaining gap, severity, deterministic control, test coverage, required milestone.
Converts concrete unresolved P0/P1 findings into focused GitHub issues.
"""

import json
import os
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from enum import Enum


class Severity(Enum):
    """Threat severity levels."""
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"


class Milestone(Enum):
    """FORGE milestones."""
    M0 = "M0"
    M1 = "M1"
    M2 = "M2"
    M3 = "M3"
    M4 = "M4"
    M5 = "M5"
    M6 = "M6"
    M7 = "M7"


@dataclass
class ThreatModelEntry:
    """Single threat model entry."""
    id: str
    category: str
    threat: str
    current_mitigation: str
    remaining_gap: str
    severity: Severity
    deterministic_control: str
    test_coverage: str
    required_milestone: Milestone
    issue_id: Optional[str] = None
    status: str = "OPEN"  # OPEN, MITIGATED, ACCEPTED, DEFERRED


class SecurityThreatModel:
    """FORGE Security Threat Model."""
    
    def __init__(self):
        self.entries = self._define_threats()
    
    def _define_threats(self) -> List[ThreatModelEntry]:
        """Define all threat model entries."""
        return [
            # 1. Generated-code execution
            ThreatModelEntry(
                id="THREAT-001",
                category="generated-code execution",
                threat="Worker executes generated code that performs malicious actions (file deletion, network calls, privilege escalation, crypto mining, data exfiltration)",
                current_mitigation="Docker container with network=none, no Docker socket, read-only root filesystem, workspace mount only at /workspace, workspace_restore check validates no unauthorized changes, workspace_pin validates no protected changes",
                remaining_gap="Generated code runs with same UID as container; if container escape vulnerability exists, host compromise possible. No seccomp profile. No capability dropping beyond defaults.",
                severity=Severity.CRITICAL,
                deterministic_control="Docker boundary guard enforces network=none, exact /workspace mount, no host paths. Workspace pin/restore checks run after every work unit.",
                test_coverage="docker_guard.py tests verify network isolation, mount scope, workspace integrity. No test for generated code behavior inside container.",
                required_milestone=Milestone.M1,
                issue_id="SEC-001"
            ),
            
            # 2. Generated malicious tests
            ThreatModelEntry(
                id="THREAT-002",
                category="generated malicious tests",
                threat="Worker generates test code that exfiltrates data, modifies evidence, or attacks the verification process",
                current_mitigation="Tests run in same container as implementation; same Docker isolation applies. Verification runs after implementation and checks deterministic criteria only.",
                remaining_gap="Test code runs with same privileges as implementation. Could generate false-positive verification results if verification logic is flawed. No separate test sandbox.",
                severity=Severity.HIGH,
                deterministic_control="Verification checks are deterministic (schema validation, HTTP contract checks). Tests cannot influence verification outcome unless they modify implementation outputs.",
                test_coverage="Verifier tests check correct pass/fail for valid/invalid implementations. No test for malicious test generation.",
                required_milestone=Milestone.M2,
                issue_id="SEC-002"
            ),
            
            # 3. Path traversal
            ThreatModelEntry(
                id="THREAT-003",
                category="path traversal",
                threat="Worker uses path traversal (../) to escape /workspace and access host filesystem",
                current_mitigation="file_tools_paths.py _resolve_path_for_task rejects out-of-root paths in container mode. _host_to_container_path only translates paths within configured host root. Docker mount only exposes /workspace.",
                remaining_gap="If worker runs commands directly (not via file tools), shell path resolution could traverse. Docker mount is scoped to /workspace so host paths not accessible anyway.",
                severity=Severity.HIGH,
                deterministic_control="Docker mount scope limits visible filesystem to /workspace. File tools reject out-of-root paths. Terminal tool uses Docker with exact mount.",
                test_coverage="test_container_workspace_translation.py tests out-of-root rejection. docker_guard tests verify mount scope.",
                required_milestone=Milestone.M1,
                issue_id="SEC-003"
            ),
            
            # 4. Symlinks
            ThreatModelEntry(
                id="THREAT-004",
                category="symlinks",
                threat="Worker creates symlinks inside /workspace pointing to host filesystem or sensitive locations",
                current_mitigation="workspace_restore check in docker_guard.py validates no unsafe symlink changes. Symlinks created in container don't resolve on host (different filesystem).",
                remaining_gap="Symlinks inside container could point to other container paths. If combined with mount escape, could be dangerous. No prevention of symlink creation, only detection.",
                severity=Severity.MEDIUM,
                deterministic_control="workspace_restore check runs after every work unit. Detects and reports unsafe symlinks.",
                test_coverage="docker_guard tests include symlink detection scenarios.",
                required_milestone=Milestone.M1,
                issue_id="SEC-004"
            ),
            
            # 5. Docker mount escape
            ThreatModelEntry(
                id="THREAT-005",
                category="Docker mount escape",
                threat="Worker escapes the /workspace mount and accesses host filesystem via Docker vulnerabilities or misconfiguration",
                current_mitigation="docker.py mounts only the exact task directory at /workspace. No parent directories. docker_guard.py validates mount scope before and after execution. network=none prevents network-based escape.",
                remaining_gap="Docker itself could have vulnerabilities (CVE). Host kernel shared with containers. No user namespace mapping (root in container = root on host). No seccomp profile.",
                severity=Severity.CRITICAL,
                deterministic_control="Docker boundary guard validates exact mount configuration. Mount is verified at container start and end.",
                test_coverage="docker_guard tests verify mount configuration. No test for Docker CVE exploitation.",
                required_milestone=Milestone.M1,
                issue_id="SEC-005"
            ),
            
            # 6. Docker socket exposure
            ThreatModelEntry(
                id="THREAT-006",
                category="Docker socket exposure",
                threat="Worker gains access to Docker socket (/var/run/docker.sock) and can spawn privileged containers",
                current_mitigation="Docker socket NOT mounted into worker containers. docker.py does not bind /var/run/docker.sock. network=none prevents network access to Docker API.",
                remaining_gap="If host has Docker API exposed on TCP port (not default), worker could reach it via network (but network=none blocks). No explicit check for Docker socket absence in container.",
                severity=Severity.CRITICAL,
                deterministic_control="Docker socket never mounted. network=none prevents TCP access to Docker daemon.",
                test_coverage="docker_guard tests verify network=none. No explicit test for Docker socket absence.",
                required_milestone=Milestone.M1,
                issue_id="SEC-006"
            ),
            
            # 7. Credential/environment leakage
            ThreatModelEntry(
                id="THREAT-007",
                category="credential/environment leakage",
                threat="Worker container receives host environment variables containing secrets (API keys, tokens, passwords)",
                current_mitigation="terminal.docker_forward_env configured as empty list []. Hermes config enforces no environment forwarding. docker.py does not pass host env by default.",
                remaining_gap="If forward_env is misconfigured, secrets could leak. No automated check that forward_env is empty in activation gate.",
                severity=Severity.HIGH,
                deterministic_control="Activation gate checks terminal.docker_forward_env == [] as isolation_preflight.",
                test_coverage="activation.py inspect_hermes_runtime checks docker_forward_env_empty.",
                required_milestone=Milestone.M1,
                issue_id="SEC-007"
            ),
            
            # 8. Network access
            ThreatModelEntry(
                id="THREAT-008",
                category="network access",
                threat="Worker container accesses network (internet, internal services, metadata endpoints)",
                current_mitigation="terminal.docker_network configured as false. docker.py passes network_mode='none'. docker_guard.py validates network isolation.",
                remaining_gap="If network mode misconfigured, full network access. No egress filtering beyond network=none.",
                severity=Severity.HIGH,
                deterministic_control="Activation gate checks terminal.docker_network is False. docker_guard validates network=none at runtime.",
                test_coverage="activation.py and docker_guard.py tests verify network isolation.",
                required_milestone=Milestone.M1,
                issue_id="SEC-008"
            ),
            
            # 9. Git remote mutation
            ThreatModelEntry(
                id="THREAT-009",
                category="Git remote mutation",
                threat="Worker modifies Git remotes, pushes to unauthorized remotes, or alters Git history",
                current_mitigation="Worker container has no Git credentials. No SSH keys mounted. No GitHub token forwarded. network=none prevents Git push. Workspace mount is read-only for .git? Actually .git is in workspace.",
                remaining_gap=".git directory IS mounted in workspace. Worker could modify local Git state. Cannot push without credentials/network. Could create malicious commits locally.",
                severity=Severity.MEDIUM,
                deterministic_control="network=none prevents push. No credentials in container. Local Git modifications detected by workspace_pin/restore.",
                test_coverage="workspace_pin/restore checks would detect .git modifications.",
                required_milestone=Milestone.M2,
                issue_id="SEC-009"
            ),
            
            # 10. Repository prompt injection
            ThreatModelEntry(
                id="THREAT-010",
                category="repository prompt injection",
                threat="Malicious content in repository (README, code comments, issues) injected into model context to influence behavior",
                current_mitigation="Hermes reads files via file tools. Model sees file contents. No sanitization of file contents before sending to model. Model could be instructed to ignore security controls.",
                remaining_gap="No defense against prompt injection in file contents. Model could be tricked into bypassing checks or generating malicious code.",
                severity=Severity.HIGH,
                deterministic_control="Verification and review are deterministic, not model-based. Model only generates implementation; verification validates.",
                test_coverage="No test for prompt injection resistance.",
                required_milestone=Milestone.M3,
                issue_id="SEC-010"
            ),
            
            # 11. Fake model completion
            ThreatModelEntry(
                id="THREAT-011",
                category="fake model completion",
                threat="Model returns fake success (claims work done but produces nothing) or hallucinated outputs",
                current_mitigation="Worker result validated by deterministic verification (verify()). Verification checks actual files and HTTP responses. Model cannot fake verification.",
                remaining_gap="Model could generate code that passes verification but has hidden malicious behavior (time bombs, conditional logic). Verification only checks declared contracts.",
                severity=Severity.MEDIUM,
                deterministic_control="Verification is deterministic and runs actual tests/requests. Model output not trusted.",
                test_coverage="Verifier tests check correct pass/fail. No test for time-bomb code.",
                required_milestone=Milestone.M3,
                issue_id="SEC-011"
            ),
            
            # 12. State/evidence tampering
            ThreatModelEntry(
                id="THREAT-012",
                category="state/evidence tampering",
                threat="Worker or attacker modifies state.json, evidence.json, or events.jsonl to fake success",
                current_mitigation="Files written by engine (host process), not worker. Worker runs in container with no access to host .forge directory. Evidence written after verification.",
                remaining_gap="If host process compromised, evidence could be altered. No cryptographic signing of evidence. No immutable audit log.",
                severity=Severity.HIGH,
                deterministic_control="Evidence written by trusted host process after verification passes. Worker cannot access .forge directory.",
                test_coverage="Engine tests verify evidence writing. No test for host process compromise.",
                required_milestone=Milestone.M2,
                issue_id="SEC-012"
            ),
            
            # 13. Verification tampering
            ThreatModelEntry(
                id="THREAT-013",
                category="verification tampering",
                threat="Worker modifies verification logic or results to show false passing",
                current_mitigation="Verification runs on host (engine.py), not in worker container. verify() and verify_release() are trusted code. Worker only produces outputs.",
                remaining_gap="If worker can influence verification inputs (files, HTTP responses), could craft outputs that pass verification but are malicious. Verification only checks declared contracts.",
                severity=Severity.MEDIUM,
                deterministic_control="Verification code runs on host. Worker outputs are inputs to verification.",
                test_coverage="Verifier tests check correct behavior. No test for adversarial inputs.",
                required_milestone=Milestone.M3,
                issue_id="SEC-013"
            ),
            
            # 14. Cross-task contamination
            ThreatModelEntry(
                id="THREAT-014",
                category="cross-task contamination",
                threat="Data from one task/workspace leaks into another via shared containers, volumes, or host filesystem",
                current_mitigation="Each work unit gets fresh container (terminal.docker_persist_across_processes=false). Workspace directory unique per task. docker_guard validates workspace_pin (clean slate) and workspace_restore.",
                remaining_gap="If persist_across_processes accidentally true, container reused. Docker volume cleanup not explicit. Host temporary directories not isolated per task.",
                severity=Severity.MEDIUM,
                deterministic_control="Activation gate checks docker_persist_across_processes is False. docker_guard validates clean workspace per run.",
                test_coverage="activation.py checks persist_across is False. docker_guard tests verify workspace isolation.",
                required_milestone=Milestone.M1,
                issue_id="SEC-014"
            ),
            
            # 15. Persistent-container reuse
            ThreatModelEntry(
                id="THREAT-015",
                category="persistent-container reuse",
                threat="Container reused across work units retains state (files, processes, memory) from previous work",
                current_mitigation="terminal.container_persistent=true shares container WITHIN a single worker run (for efficiency). terminal.docker_persist_across_processes=false ensures fresh container per FORGE run. docker_guard validates both.",
                remaining_gap="Within a single run, container persists across work units. Malicious code in WRK-001 could leave artifacts affecting WRK-002. workspace_restore checks after each unit mitigate.",
                severity=Severity.MEDIUM,
                deterministic_control="workspace_restore check runs after each work unit. Container persistence within run is intentional for performance.",
                test_coverage="docker_guard tests verify workspace_restore per work unit.",
                required_milestone=Milestone.M2,
                issue_id="SEC-015"
            ),
            
            # 16. Retry-budget bypass
            ThreatModelEntry(
                id="THREAT-016",
                category="retry-budget bypass",
                threat="Failed activation retried without authorization, exhausting budget or bypassing security review",
                current_mitigation="Activation gate requires explicit human authorization recorded in activation_authorization.json. forge-run refuses without authorization. Two-attempt limit documented.",
                remaining_gap="Authorization file is local; could be forged. No cryptographic signature on authorization. No centralized retry budget enforcement.",
                severity=Severity.HIGH,
                deterministic_control="forge-run command checks for authorization file. Activation gate requires gate_passed AND authorization.",
                test_coverage="forge_commands.py tests verify forge-run refuses without authorization.",
                required_milestone=Milestone.M1,
                issue_id="SEC-016"
            ),
            
            # 17. Host execution exposure
            ThreatModelEntry(
                id="THREAT-017",
                category="host execution exposure",
                threat="Worker escapes container and executes code on host (via Docker breakout, kernel exploit, or misconfiguration)",
                current_mitigation="Docker with network=none, no privileged mode, no host PID/IPC namespace, no capability additions, exact workspace mount, no Docker socket. User namespace not enabled (root in container = root on host).",
                remaining_gap="Root in container = root on host. If kernel vulnerability exploited (CVE-2024-xxxx), full host compromise. No seccomp profile to restrict syscalls. No AppArmor/SELinux profile.",
                severity=Severity.CRITICAL,
                deterministic_control="Docker boundary guard enforces minimal container configuration. No privileged features enabled.",
                test_coverage="docker_guard tests verify container configuration. No test for kernel exploits.",
                required_milestone=Milestone.M1,
                issue_id="SEC-017"
            ),
        ]
    
    def analyze(self) -> Dict[str, Any]:
        """Analyze the threat model."""
        results = {
            "timestamp": datetime.now().isoformat(),
            "total_threats": len(self.entries),
            "by_severity": {},
            "by_milestone": {},
            "by_status": {},
            "open_critical": 0,
            "open_high": 0,
            "mitigated": 0,
            "entries": []
        }
        
        for entry in self.entries:
            e = asdict(entry)
            e["severity"] = entry.severity.value
            e["required_milestone"] = entry.required_milestone.value
            results["entries"].append(e)
            
            # Count by severity
            sev = entry.severity.value
            results["by_severity"][sev] = results["by_severity"].get(sev, 0) + 1
            if entry.status == "OPEN":
                if sev == "CRITICAL":
                    results["open_critical"] += 1
                elif sev == "HIGH":
                    results["open_high"] += 1
            
            # Count by milestone
            ms = entry.required_milestone.value
            results["by_milestone"][ms] = results["by_milestone"].get(ms, 0) + 1
            
            # Count by status
            results["by_status"][entry.status] = results["by_status"].get(entry.status, 0) + 1
            if entry.status == "MITIGATED":
                results["mitigated"] += 1
        
        return results
    
    def generate_github_issues(self) -> List[Dict[str, Any]]:
        """Generate GitHub issue payloads for unresolved P0/P1 findings."""
        issues = []
        
        for entry in self.entries:
            if entry.status == "OPEN" and entry.severity in (Severity.CRITICAL, Severity.HIGH) and entry.issue_id:
                issues.append({
                    "id": entry.issue_id,
                    "title": f"[SECURITY] {entry.category}: {entry.threat[:80]}...",
                    "body": f"""## Threat
{entry.threat}

## Current Mitigation
{entry.current_mitigation}

## Remaining Gap
{entry.remaining_gap}

## Severity
{entry.severity.value}

## Deterministic Control
{entry.deterministic_control}

## Test Coverage
{entry.test_coverage}

## Required Milestone
{entry.required_milestone.value}

## Acceptance Criteria
- [ ] Gap addressed or formally accepted with justification
- [ ] Deterministic control implemented and tested
- [ ] Test coverage added for threat scenario
- [ ] Documentation updated
""",
                    "labels": ["security", entry.required_milestone.value.lower(), entry.severity.value.lower()],
                    "assignees": []
                })
        
        return issues
    
    def generate_report(self) -> str:
        """Generate human-readable report."""
        analysis = self.analyze()
        issues = self.generate_github_issues()
        
        report = f"""# FORGE Security Threat Model (Workstream M)

**Timestamp:** {analysis['timestamp']}
**Total Threats Modeled:** {analysis['total_threats']}

## Summary by Severity
{chr(10).join(f"- **{sev}**: {count}" for sev, count in sorted(analysis['by_severity'].items()))}

## Summary by Milestone
{chr(10).join(f"- **{ms}**: {count}" for ms, count in sorted(analysis['by_milestone'].items()))}

## Open Critical: {analysis['open_critical']}
## Open High: {analysis['open_high']}
## Mitigated: {analysis['mitigated']}

## GitHub Issues to Create ({len(issues)})

"""
        for issue in issues:
            report += f"""### {issue['id']}: {issue['title']}
- **Labels**: {', '.join(issue['labels'])}
- **Severity**: {issue['labels'][2] if len(issue['labels']) > 2 else 'N/A'}
- **Milestone**: {issue['labels'][1] if len(issue['labels']) > 1 else 'N/A'}

"""
        
        report += "\n## Detailed Threat Model\n\n"
        
        for entry in self.entries:
            report += f"""### {entry.id}: {entry.category}
- **Threat**: {entry.threat}
- **Current Mitigation**: {entry.current_mitigation}
- **Remaining Gap**: {entry.remaining_gap}
- **Severity**: {entry.severity.value}
- **Deterministic Control**: {entry.deterministic_control}
- **Test Coverage**: {entry.test_coverage}
- **Required Milestone**: {entry.required_milestone.value}
- **Status**: {entry.status}
- **Issue ID**: {entry.issue_id or 'N/A'}

"""
        
        return report


def test_threat_model():
    """Test the threat model."""
    model = SecurityThreatModel()
    
    # Test entries defined
    assert len(model.entries) == 17
    print("PASS: 17 threat entries defined")
    
    # Test categories
    categories = set(e.category for e in model.entries)
    expected_categories = {
        "generated-code execution",
        "generated malicious tests",
        "path traversal",
        "symlinks",
        "Docker mount escape",
        "Docker socket exposure",
        "credential/environment leakage",
        "network access",
        "Git remote mutation",
        "repository prompt injection",
        "fake model completion",
        "state/evidence tampering",
        "verification tampering",
        "cross-task contamination",
        "persistent-container reuse",
        "retry-budget bypass",
        "host execution exposure",
    }
    assert categories == expected_categories
    print("PASS: All expected categories present")
    
    # Test severity distribution
    critical = sum(1 for e in model.entries if e.severity == Severity.CRITICAL)
    high = sum(1 for e in model.entries if e.severity == Severity.HIGH)
    medium = sum(1 for e in model.entries if e.severity == Severity.MEDIUM)
    low = sum(1 for e in model.entries if e.severity == Severity.LOW)
    print(f"PASS: Severity distribution - CRITICAL: {critical}, HIGH: {high}, MEDIUM: {medium}, LOW: {low}")
    
    # Test issue IDs for P0/P1
    p0_p1 = [e for e in model.entries if e.severity in (Severity.CRITICAL, Severity.HIGH) and e.issue_id]
    assert len(p0_p1) > 0
    print(f"PASS: {len(p0_p1)} P0/P1 issues have IDs")
    
    # Test analysis
    analysis = model.analyze()
    assert analysis["total_threats"] == 17
    assert analysis["open_critical"] > 0
    assert analysis["open_high"] > 0
    print("PASS: Analysis runs correctly")
    
    # Test GitHub issue generation
    issues = model.generate_github_issues()
    assert len(issues) == len(p0_p1)
    for issue in issues:
        assert "id" in issue
        assert "title" in issue
        assert "body" in issue
        assert "labels" in issue
    print("PASS: GitHub issues generated")
    
    print("\nALL THREAT MODEL TESTS PASSED")
    return True


if __name__ == "__main__":
    test_threat_model()
    
    model = SecurityThreatModel()
    report = model.generate_report()
    print(report)
    
    # Machine-readable output
    analysis = model.analyze()
    issues = model.generate_github_issues()
    output = {
        "analysis": analysis,
        "github_issues": issues
    }
    print("\n=== MACHINE-READABLE OUTPUT ===")
    print(json.dumps(output, indent=2, default=str))