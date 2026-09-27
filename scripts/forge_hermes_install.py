#!/usr/bin/env python3
"""
Python wrapper for FORGE Hermes host installation automation.
Can be called from control plane or run directly.

Usage:
    python forge_hermes_install.py --commit <sha> [--dry-run]
    python forge_hermes_install.py --tag <tag> [--dry-run]
    python forge_hermes_install.py --rollback [--dry-run]
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path


class HermesInstaller:
    def __init__(self, dry_run=False):
        self.dry_run = dry_run
        self.hermes_home = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local")) / "hermes"
        self.hermes_agent_dir = self.hermes_home / "hermes-agent"
        self.profiles_dir = self.hermes_home / "profiles"
        self.rollback_file = self.hermes_home / ".forge-rollback.json"
        self.log_file = self.hermes_home / "logs" / f"forge-install-{datetime.now():%Y%m%d-%H%M%S}.log"
        
        self.log_dir = self.log_file.parent
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def log(self, message, level="INFO"):
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = f"[{timestamp}] [{level}] {message}"
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
        print(entry)

    def run_cmd(self, cmd, cwd=None, capture=True):
        """Run a command and return (success, output)."""
        self.log(f"Running: {cmd}", "DEBUG")
        if self.dry_run:
            self.log(f"[DRY RUN] Would run: {cmd}", "INFO")
            return True, "[DRY RUN]"
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=cwd, capture_output=capture, text=True, timeout=300
            )
            if result.stdout:
                self.log(result.stdout.strip(), "DEBUG")
            if result.stderr:
                self.log(result.stderr.strip(), "DEBUG")
            return result.returncode == 0, result.stdout.strip()
        except subprocess.TimeoutExpired:
            self.log(f"Command timed out: {cmd}", "ERROR")
            return False, "timeout"
        except Exception as e:
            self.log(f"Command failed: {cmd} - {e}", "ERROR")
            return False, str(e)

    def get_current_info(self):
        info = {
            "exe_path": None,
            "version": None,
            "commit": None,
            "source": None,
            "profiles": []
        }

        # Find hermes.exe
        for candidate in [
            self.hermes_home / "hermes.exe",
            self.hermes_agent_dir / "hermes.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "hermes" / "hermes.exe"
        ]:
            if candidate.exists():
                info["exe_path"] = str(candidate)
                break

        # Get version
        if info["exe_path"]:
            success, output = self.run_cmd(f'"{info["exe_path"]}" --version')
            if success and output:
                import re
                match = re.search(r'v?(\d+\.\d+\.\d+)', output)
                if match:
                    info["version"] = match.group(1)

        # Get git info
        git_dir = self.hermes_agent_dir / ".git"
        if git_dir.exists():
            success, commit = self.run_cmd("git rev-parse HEAD", cwd=self.hermes_agent_dir)
            if success:
                info["commit"] = commit
            success, source = self.run_cmd("git remote get-url origin", cwd=self.hermes_agent_dir)
            if success:
                info["source"] = source

        # List profiles
        if self.profiles_dir.exists():
            info["profiles"] = [d.name for d in self.profiles_dir.iterdir() if d.is_dir()]

        return info

    def save_rollback_info(self, current_info, target_commit=None, target_tag=None):
        rollback = {
            "timestamp": datetime.now().isoformat(),
            "previous_exe_path": current_info["exe_path"],
            "previous_version": current_info["version"],
            "previous_commit": current_info["commit"],
            "previous_source": current_info["source"],
            "profiles": current_info["profiles"],
            "target_commit": target_commit,
            "target_tag": target_tag
        }
        with open(self.rollback_file, "w", encoding="utf-8") as f:
            json.dump(rollback, f, indent=2)
        self.log(f"Rollback info saved to {self.rollback_file}")

    def load_rollback_info(self):
        if self.rollback_file.exists():
            with open(self.rollback_file, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def verify_install(self, exe_path, expected_commit=None, expected_tag=None):
        self.log(f"Verifying installation at {exe_path}")

        if not Path(exe_path).exists():
            raise RuntimeError(f"Hermes executable not found at {exe_path}")

        success, output = self.run_cmd(f'"{exe_path}" --version')
        self.log(f"Version output: {output}")

        if expected_commit:
            success, actual_commit = self.run_cmd("git rev-parse HEAD", cwd=self.hermes_agent_dir)
            if not success or actual_commit != expected_commit:
                raise RuntimeError(f"Commit mismatch: expected {expected_commit}, got {actual_commit}")
            self.log(f"Commit verified: {actual_commit}")

        if expected_tag:
            success, actual_tag = self.run_cmd("git describe --tags --exact-match", cwd=self.hermes_agent_dir)
            if not success or actual_tag != expected_tag:
                raise RuntimeError(f"Tag mismatch: expected {expected_tag}, got {actual_tag}")
            self.log(f"Tag verified: {actual_tag}")

        # Smoke test
        success, smoke = self.run_cmd(f'"{exe_path}" doctor')
        self.log(f"Doctor output: {smoke}")

        return True

    def install_from_source(self, commit=None, tag=None, source_repo="https://github.com/NousResearch/hermes-agent.git"):
        self.log(f"Installing Hermes from {source_repo}")
        if commit:
            self.log(f"Target commit: {commit}")
        if tag:
            self.log(f"Target tag: {tag}")

        if self.dry_run:
            self.log("[DRY RUN] Would clone/fetch source repo")
            self.log(f"[DRY RUN] Would checkout {commit or tag}")
            self.log("[DRY RUN] Would run: pip install -e .[dev]")
            return

        # Ensure parent directory exists
        self.hermes_home.mkdir(parents=True, exist_ok=True)

        # Clone or fetch
        if (self.hermes_agent_dir / ".git").exists():
            self.log("Existing repo found, fetching...")
            self.run_cmd("git fetch origin --tags --prune", cwd=self.hermes_agent_dir)
        else:
            self.log(f"Cloning {source_repo}...")
            success, _ = self.run_cmd(f"git clone {source_repo} {self.hermes_agent_dir}")
            if not success:
                raise RuntimeError("Git clone failed")

        # Checkout target
        target_ref = commit or tag
        self.log(f"Checking out {target_ref}...")
        success, _ = self.run_cmd(f"git checkout {target_ref}", cwd=self.hermes_agent_dir)
        if not success:
            raise RuntimeError(f"Git checkout {target_ref} failed")

        # Install dependencies
        self.log("Installing dependencies...")
        if self.run_cmd("uv --version")[0]:
            self.log("Using uv for installation...")
            success, _ = self.run_cmd(f"uv pip install -e \"{self.hermes_agent_dir}[dev]\"")
        else:
            self.log("Using pip for installation...")
            success, _ = self.run_cmd(f"python -m pip install -e \"{self.hermes_agent_dir}[dev]\"")
        
        if not success:
            raise RuntimeError("Dependency installation failed")

        # Verify hermes.exe exists
        exe_path = self.hermes_home / "hermes.exe"
        if not exe_path.exists():
            self.log("hermes.exe not found, checking PATH...")
            import shutil
            hermes_cmd = shutil.which("hermes")
            if hermes_cmd:
                import shutil
                shutil.copy2(hermes_cmd, exe_path)
                self.log(f"Copied hermes from {hermes_cmd} to {exe_path}")

    def rollback(self):
        rollback = self.load_rollback_info()
        if not rollback:
            raise RuntimeError(f"No rollback information found at {self.rollback_file}")

        self.log(f"Rolling back to previous version...")
        self.log(f"Previous commit: {rollback.get('previous_commit')}")
        self.log(f"Previous version: {rollback.get('previous_version')}")

        if self.dry_run:
            self.log(f"[DRY RUN] Would checkout {rollback.get('previous_commit')}")
            self.log("[DRY RUN] Would reinstall dependencies")
            return

        if rollback.get("previous_commit"):
            success, _ = self.run_cmd(f"git checkout {rollback['previous_commit']}", cwd=self.hermes_agent_dir)
            if not success:
                raise RuntimeError("Git checkout for rollback failed")

            # Reinstall dependencies
            if self.run_cmd("uv --version")[0]:
                self.run_cmd(f"uv pip install -e \"{self.hermes_agent_dir}[dev]\"")
            else:
                self.run_cmd(f"python -m pip install -e \"{self.hermes_agent_dir}[dev]\"")

        exe_path = self.hermes_home / "hermes.exe"
        self.verify_install(str(exe_path), expected_commit=rollback.get("previous_commit"))
        self.log("Rollback completed successfully")


def main():
    parser = argparse.ArgumentParser(description="FORGE Hermes Host Install Automation")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--commit", help="Exact commit SHA to install (40-char)")
    group.add_argument("--tag", help="Exact tag to install (e.g., v0.21.4)")
    group.add_argument("--rollback", action="store_true", help="Rollback to previous version")
    parser.add_argument("--source-repo", default="https://github.com/NousResearch/hermes-agent.git",
                        help="Git repository to install from")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    parser.add_argument("--force", action="store_true", help="Skip confirmation prompts")

    args = parser.parse_args()

    if args.commit and len(args.commit) != 40:
        parser.error("Commit SHA must be 40 characters")

    installer = HermesInstaller(dry_run=args.dry_run)

    installer.log("=== FORGE Hermes Host Install Automation ===")
    installer.log(f"Hermes Home: {installer.hermes_home}")
    installer.log(f"Hermes Agent Dir: {installer.hermes_agent_dir}")

    try:
        if args.rollback:
            installer.rollback()
            return 0

        # Get current state
        current_info = installer.get_current_info()
        installer.log("Current Hermes Info:")
        for k, v in current_info.items():
            installer.log(f"  {k}: {v}")

        # Save rollback info
        installer.save_rollback_info(current_info, args.commit, args.tag)

        # Confirm unless --force
        if not args.force and not args.dry_run:
            confirm = input("Proceed with installation? (y/N): ").strip().lower()
            if confirm not in ('y', 'yes'):
                installer.log("Installation cancelled by user")
                return 1

        # Install
        installer.install_from_source(args.commit, args.tag, args.source_repo)

        # Verify (skip in dry-run)
        if not args.dry_run:
            exe_path = installer.hermes_home / "hermes.exe"
            installer.verify_install(str(exe_path), args.commit, args.tag)

        installer.log("Installation completed successfully")
        installer.log(f"Log file: {installer.log_file}")
        return 0

    except Exception as e:
        installer.log(f"ERROR: {e}", "ERROR")
        import traceback
        installer.log(traceback.format_exc(), "ERROR")

        # Attempt automatic rollback on failure
        if not args.dry_run and not args.rollback:
            installer.log("Attempting automatic rollback...", "WARN")
            try:
                installer.rollback()
            except Exception as e2:
                installer.log(f"Rollback also failed: {e2}", "ERROR")
        return 1


if __name__ == "__main__":
    sys.exit(main())