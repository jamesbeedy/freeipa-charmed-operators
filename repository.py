#!/usr/bin/env python3
# Copyright 2026 James Beedy
# See LICENSE file for licensing details.

"""Build orchestration tool for the FreeIPA charmed operators monorepo.

Inspired by https://github.com/charmed-hpc/slurm-charms/blob/main/repository.py
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
CHARMS_DIR = REPO_ROOT / "charms"
K8S_CHARMS_DIR = REPO_ROOT / "k8s-charms"
BUILD_DIR = REPO_ROOT / "_build"

CHARM_DIRS = [CHARMS_DIR, K8S_CHARMS_DIR]

def _discover_charms() -> list[str]:
    """Discover all charm identifiers across charms/ and k8s-charms/.

    Returns identifiers like 'freeipa-server' or 'k8s:freeipa-server'
    that can be used with _find_charm_dir.
    """
    names = []
    for base in CHARM_DIRS:
        if not base.exists():
            continue
        prefix = "" if base == CHARMS_DIR else "k8s:"
        for d in sorted(base.iterdir()):
            if d.is_dir() and (d / "charmcraft.yaml").exists():
                names.append(f"{prefix}{d.name}")
    return names

CHARMS = _discover_charms()


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, printing it first."""
    print(f"  >> {' '.join(str(c) for c in cmd)}", flush=True)
    return subprocess.run(cmd, check=True, **kwargs)


def _resolve_charm_id(charm_id: str) -> Path | None:
    """Resolve a charm identifier to its directory path.

    Accepts 'name' (searches charms/ first), or 'k8s:name' for k8s-charms/.
    """
    if charm_id.startswith("k8s:"):
        d = K8S_CHARMS_DIR / charm_id[4:]
        return d if d.exists() and (d / "charmcraft.yaml").exists() else None
    for base in CHARM_DIRS:
        d = base / charm_id
        if d.exists() and (d / "charmcraft.yaml").exists():
            return d
    return None


def _charm_dirs(names: list[str] | None = None) -> list[Path]:
    """Return charm directories, optionally filtered by name."""
    if names:
        dirs = []
        for name in names:
            d = _resolve_charm_id(name)
            if d is None:
                print(f"Error: charm '{name}' not found", file=sys.stderr)
                sys.exit(1)
            dirs.append(d)
        return dirs
    return [d for name in CHARMS if (d := _resolve_charm_id(name))]


# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------


def _build_key(charm_dir: Path) -> str:
    """Return a unique build key for a charm directory.

    Machine charms use their directory name; k8s charms get a 'k8s-' prefix.
    """
    if K8S_CHARMS_DIR in charm_dir.parents or charm_dir.parent == K8S_CHARMS_DIR:
        return f"k8s-{charm_dir.name}"
    return charm_dir.name


def cmd_stage(args: argparse.Namespace) -> None:
    """Stage charms into _build/ for packing."""
    charm_dirs = _charm_dirs(args.charms)

    for charm_dir in charm_dirs:
        key = _build_key(charm_dir)
        dest = BUILD_DIR / key
        print(f"\n=== Staging {key} ===")

        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(charm_dir, dest, ignore=shutil.ignore_patterns(
            "__pycache__", "*.egg-info", "*.charm", ".venv", "venv", "build",
        ))

        # Ensure uv.lock exists
        lock = dest / "uv.lock"
        if not lock.exists():
            print(f"  Generating uv.lock for {key}")
            _run(["uv", "lock"], cwd=dest)

    print(f"\nStaged {len(charm_dirs)} charm(s) into {BUILD_DIR}")


def cmd_build(args: argparse.Namespace) -> None:
    """Stage and pack charms."""
    cmd_stage(args)
    charm_dirs = _charm_dirs(args.charms)

    for charm_dir in charm_dirs:
        key = _build_key(charm_dir)
        dest = BUILD_DIR / key
        print(f"\n=== Packing {key} ===")
        _run(["charmcraft", "pack"], cwd=dest)

    # Copy .charm files to project root for convenience
    for charm_file in BUILD_DIR.rglob("*.charm"):
        target = REPO_ROOT / charm_file.name
        shutil.copy2(charm_file, target)
        print(f"  Copied {charm_file.name} -> {target}")

    print("\nBuild complete.")


def cmd_clean(args: argparse.Namespace) -> None:
    """Remove build artifacts."""
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR)
        print(f"Removed {BUILD_DIR}")

    for charm_file in REPO_ROOT.glob("*.charm"):
        charm_file.unlink()
        print(f"Removed {charm_file}")

    # Clean charmcraft build environments
    for charm_dir in _charm_dirs(None):
        _run(["charmcraft", "clean"], cwd=charm_dir)


def cmd_fmt(args: argparse.Namespace) -> None:
    """Format code."""
    src_dirs = [str(d / "src") for d in _charm_dirs(args.charms)]
    test_dirs = [str(d / "tests") for d in _charm_dirs(args.charms) if (d / "tests").exists()]
    targets = src_dirs + test_dirs

    _run(["uvx", "ruff", "format", *targets])
    _run(["uvx", "ruff", "check", "--fix", *targets])


def cmd_lint(args: argparse.Namespace) -> None:
    """Lint code."""
    src_dirs = [str(d / "src") for d in _charm_dirs(args.charms)]
    test_dirs = [str(d / "tests") for d in _charm_dirs(args.charms) if (d / "tests").exists()]
    targets = src_dirs + test_dirs

    _run(["uvx", "ruff", "check", *targets])
    _run(["uvx", "codespell", *targets])


def cmd_typecheck(args: argparse.Namespace) -> None:
    """Type-check charm source."""
    src_dirs = [str(d / "src") for d in _charm_dirs(args.charms)]
    _run(["uvx", "pyright", *src_dirs])


def cmd_unit(args: argparse.Namespace) -> None:
    """Run unit tests."""
    charm_dirs = _charm_dirs(args.charms)
    for charm_dir in charm_dirs:
        test_dir = charm_dir / "tests" / "unit"
        if not test_dir.exists():
            print(f"  Skipping {charm_dir.name} (no unit tests)")
            continue
        print(f"\n=== Unit tests: {charm_dir.name} ===")
        _run(
            ["uv", "run", "--directory", str(charm_dir), "pytest", str(test_dir), *args.pytest_args],
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(
        description="FreeIPA charmed operators build tool",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Common charm filter argument
    def add_charm_filter(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "charms", nargs="*", default=None,
            help=f"Charm names to operate on (default: all). Available: {', '.join(CHARMS)}",
        )

    p = sub.add_parser("stage", help="Stage charms into _build/")
    add_charm_filter(p)

    p = sub.add_parser("build", help="Stage and pack charms")
    add_charm_filter(p)

    sub.add_parser("clean", help="Remove build artifacts")

    p = sub.add_parser("fmt", help="Format code")
    add_charm_filter(p)

    p = sub.add_parser("lint", help="Lint code")
    add_charm_filter(p)

    p = sub.add_parser("typecheck", help="Type-check code")
    add_charm_filter(p)

    p = sub.add_parser("unit", help="Run unit tests")
    add_charm_filter(p)
    p.add_argument("pytest_args", nargs="*", default=[], help="Extra args passed to pytest")

    args = parser.parse_args()

    commands = {
        "stage": cmd_stage,
        "build": cmd_build,
        "clean": cmd_clean,
        "fmt": cmd_fmt,
        "lint": cmd_lint,
        "typecheck": cmd_typecheck,
        "unit": cmd_unit,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()
