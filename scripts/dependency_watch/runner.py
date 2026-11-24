"""Resolve real dependencies and confirm regressions against a passing environment.

Only disposable venvs and a temporary source copy are used. Application tests use
our existing offline runner; package installation alone has network access.
"""

# Standard Library
import argparse
import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path

SCHEMA = 1
PROFILES = {
    "base": None,
    "charlink": None,
    "aadiscordbot": "aadiscordbot",
    "discordnotify": "discordnotify",
    "frontend": None,
}
INFRASTRUCTURE = re.compile(
    r"ConnectionError|ReadTimeout|ConnectTimeout|Connection timed out|Temporary failure|Network is unreachable|No space left|Killed|502 Bad Gateway|503 Service|Could not fetch URL",
    re.I,
)


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def contract(root, profile):
    data = tomllib.loads((root / "pyproject.toml").read_text())
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(root / "tox.ini")
    tests = [
        line.strip()
        for line in parser["testenv"]["deps"].splitlines()
        if line.strip() and ": " not in line
    ]
    if profile != "charlink":
        tests = [value for value in tests if not value.startswith("aa-charlink")]
    extra = PROFILES[profile]
    return {
        "runtime": data["project"]["dependencies"],
        "build": data["build-system"]["requires"],
        "backend": data["build-system"]["build-backend"],
        "extra": data["project"].get("optional-dependencies", {}).get(extra, []),
        "tests": tests,
        "profile": profile,
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "platform": sys.platform,
        "schema": SCHEMA,
    }


def failure_key(phase, output):
    """Require stable failure evidence; arbitrary subprocess failure isn't enough."""
    if phase == "tests":
        failure = re.search(r"^(?:FAIL|ERROR): .+$", output, re.M)
        output = output[failure.start() :] if failure else ""
    if INFRASTRUCTURE.search(output):
        return None
    if phase == "install":
        return "resolver-conflict" if "ResolutionImpossible" in output else None
    if phase == "tests":
        names = sorted(set(re.findall(r"^(?:FAIL|ERROR): (.+)$", output, re.M)))
        return fingerprint(names) if names else None
    if phase == "frontend":
        lines = sorted(
            set(
                re.findall(
                    r"^(?:.*AssertionError.*|.*TypeError:.*|.*\berror\s{2,}.*)$",
                    output,
                    re.M,
                )
            )
        )
        return fingerprint(lines) if lines else None
    if phase == "check":
        lines = sorted(
            line
            for line in output.splitlines()
            if " requires " in line
            or " has requirement " in line
            or " is not installed" in line
        )
        return fingerprint(lines) if lines else None
    return None


def verdict(candidate, baseline, repeated):
    if candidate["status"] == "passed":
        return "healthy"
    if (
        baseline
        and baseline["status"] == "passed"
        and candidate.get("failure_key")
        and repeated
        and repeated.get("failure_key") == candidate["failure_key"]
        and repeated.get("phase") == candidate["phase"]
    ):
        return "confirmed"
    return "unverified"


def run(command, log, *, cwd, env):
    with log.open("w") as output:
        try:
            result = subprocess.run(
                command,
                cwd=cwd,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=1200,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return False, "Process exceeded the 1200-second limit"
    return result.returncode == 0, log.read_text(errors="replace")


def attempt(root, directory, spec, *, constraints=None):
    directory.mkdir()
    source = directory / "source"
    shutil.copytree(root, source)
    root = source
    environment = directory / "venv"
    venv.EnvBuilder(with_pip=True).create(environment)
    python = str(environment / "bin/python")
    env = {
        **os.environ,
        "INDY_HUB_TEST_CHARLINK": "1" if spec["profile"] == "charlink" else "0",
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PYTHONUNBUFFERED": "1",
    }
    for key in (
        "PYTHONPATH",
        "PIP_CONSTRAINT",
        "PIP_REQUIREMENT",
        "DJANGO_SETTINGS_MODULE",
        "INDY_HUB_TEST_DATABASE",
        "INDY_HUB_TEST_DB_HOST",
    ):
        env.pop(key, None)
    package = str(root) + (
        f'[{PROFILES[spec["profile"]]}]' if PROFILES[spec["profile"]] else ""
    )
    install = [
        python,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "--upgrade-strategy",
        "eager",
    ]
    if constraints:
        install += ["--constraint", str(constraints)]
    # Freeze the build backend too; pip's isolated build environment would
    # otherwise silently use the latest backend even during the old control.
    for filename, requirements in (("build-install", spec.get("build", [])),):
        if requirements:
            passed, output = run(
                install + requirements, directory / f"{filename}.log", cwd=root, env=env
            )
            if not passed:
                return {
                    "status": "failed",
                    "phase": "install",
                    "failure_key": failure_key("install", output),
                    "versions": {},
                }
    if spec.get("backend"):
        probe = (
            "import importlib,json; backend=importlib.import_module("
            + repr(spec["backend"])
            + "); print(json.dumps(backend.get_requires_for_build_editable({})))"
        )
        passed, output = run(
            [python, "-c", probe], directory / "build-requires.log", cwd=root, env=env
        )
        if not passed:
            return {
                "status": "failed",
                "phase": "build",
                "failure_key": None,
                "versions": {},
            }
        requirements = json.loads(output)
        if requirements:
            passed, output = run(
                install + requirements,
                directory / "build-dynamic.log",
                cwd=root,
                env=env,
            )
            if not passed:
                return {
                    "status": "failed",
                    "phase": "install",
                    "failure_key": failure_key("install", output),
                    "versions": {},
                }
    install += ["--no-build-isolation"]
    phases = [
        ("install", install + ["-e", package, *spec["tests"]]),
        ("check", [python, "-m", "pip", "check"]),
        ("tests", [python, "runtests.py", "--verbosity=2"]),
    ]
    resolved = {}
    for phase, command in phases:
        passed, output = run(command, directory / f"{phase}.log", cwd=root, env=env)
        if phase == "install" and passed:
            snapshot = subprocess.check_output(
                [python, "-m", "pip", "list", "--format=json"], env=env, text=True
            )
            resolved = {
                row["name"].lower().replace("_", "-"): row["version"]
                for row in json.loads(snapshot)
                if row["name"].lower().replace("_", "-")
                not in {"indy-hub", "pip", "setuptools", "wheel"}
            }
        if not passed:
            return {
                "status": "failed",
                "phase": phase,
                "failure_key": failure_key(phase, output),
                "versions": resolved,
                "log": f"{directory.name}/{phase}.log",
            }
    return {"status": "passed", "versions": resolved}


def write_constraints(path, versions):
    if not versions or any(
        not re.fullmatch(r"[a-z0-9][a-z0-9.-]*", name)
        or not re.fullmatch(r"[A-Za-z0-9.!+_-]+", version)
        for name, version in versions.items()
    ):
        raise ValueError("Invalid baseline package pins")
    path.write_text(
        "".join(f"{name}=={version}\n" for name, version in sorted(versions.items()))
    )


def frontend_main(root, output, baseline_path):
    identity = fingerprint(
        [
            SCHEMA,
            json.loads((root / "package.json").read_text()),
            subprocess.check_output(["node", "--version"], text=True).split(".")[0],
        ]
    )
    previous = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    if previous and previous.get("contract") != identity:
        previous = None

    def test(name, lock=None):
        directory = output / name
        directory.mkdir()
        source = directory / "source"
        # A complete independent copy of tracked sources, including scripts.
        for filename in filter(
            None,
            subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
            .decode()
            .split("\0"),
        ):
            path = root / filename
            if path.is_file():
                target = source / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        if lock:
            (source / "package-lock.json").write_text(json.dumps(lock))
        install = [
            "npm",
            "ci" if lock else "update",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ]
        passed, log = run(
            install, directory / "install.log", cwd=source, env=os.environ.copy()
        )
        resolved = (
            json.loads((source / "package-lock.json").read_text()) if passed else None
        )
        if passed:
            passed, log = run(
                ["npm", "test"],
                directory / "tests.log",
                cwd=source,
                env=os.environ.copy(),
            )
        versions = {
            name: row["version"]
            for name, row in (resolved or {}).get("packages", {}).items()
            if name and "version" in row
        }
        return {
            "status": "passed" if passed else "failed",
            "phase": "frontend",
            "failure_key": failure_key("frontend", log),
            "versions": versions,
        }, resolved

    candidate, lock = test("candidate")
    control, repeated = None, None
    if candidate["status"] != "passed" and previous:
        control, _ = test("control", previous["lock"])
        if control["status"] == "passed" and candidate.get("failure_key"):
            repeated, _ = test("repeat", lock)
    status = verdict(candidate, control, repeated)
    result = {
        "schema": SCHEMA,
        "lane": "node22-frontend",
        "status": status,
        "candidate": candidate,
        "control": control,
        "repeat": repeated,
        "previous_versions": previous["versions"] if previous else {},
        "run_url": os.environ.get("WATCH_RUN_URL", ""),
    }
    (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    if status == "healthy":
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(
            json.dumps(
                {"contract": identity, "versions": candidate["versions"], "lock": lock}
            )
        )
    if github_output := os.environ.get("GITHUB_OUTPUT"):
        with Path(github_output).open("a") as stream:
            stream.write(f"status={status}\n")
    print(json.dumps({"status": status, "lane": result["lane"]}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=PROFILES, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    output, baseline_path = args.output.resolve(), args.baseline.resolve()
    output.mkdir(parents=True, exist_ok=True)
    if args.profile == "frontend":
        frontend_main(root, args.output.resolve(), args.baseline.resolve())
        return
    spec = contract(root, args.profile)
    identity = fingerprint(spec)
    previous = json.loads(baseline_path.read_text()) if baseline_path.exists() else None
    if previous and (
        previous.get("schema") != SCHEMA or previous.get("contract") != identity
    ):
        previous = None
    # Each attempt gets an independent source tree: editable builds or test
    # fixtures must not carry state from the candidate into its control.
    with tempfile.TemporaryDirectory(prefix="indyhub-dependency-watch-") as temporary:
        temporary = Path(temporary)
        source = temporary / "source"
        tracked = (
            subprocess.check_output(["git", "ls-files", "-z"], cwd=root)
            .decode()
            .split("\0")
        )
        for filename in filter(None, tracked):
            path = root / filename
            if path.is_file():
                target = source / filename
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
        candidate = attempt(source, output / "candidate", spec)
        control, repeated = None, None
        if candidate["status"] != "passed" and previous:
            pins = output / "baseline-constraints.txt"
            write_constraints(pins, previous["versions"])
            control = attempt(source, output / "control", spec, constraints=pins)
            if control.get("versions") != previous["versions"]:
                control["status"] = "unverified"
            if control["status"] == "passed" and candidate.get("failure_key"):
                repeat_pins = output / "repeat-constraints.txt"
                if candidate["versions"]:
                    write_constraints(repeat_pins, candidate["versions"])
                repeated = attempt(
                    source,
                    output / "repeat",
                    spec,
                    constraints=repeat_pins if repeat_pins.exists() else None,
                )
                if repeated.get("versions") != candidate["versions"]:
                    repeated["failure_key"] = None

        status = verdict(candidate, control, repeated)
        result = {
            "schema": SCHEMA,
            "lane": f'{spec["python"]}-{args.profile}',
            "status": status,
            "contract": identity,
            "spec": spec,
            "candidate": candidate,
            "control": control,
            "repeat": repeated,
            "previous_versions": previous["versions"] if previous else {},
            "run_url": os.environ.get("WATCH_RUN_URL", ""),
        }
        (output / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        if status == "healthy":
            baseline_path.parent.mkdir(parents=True, exist_ok=True)
            baseline_path.write_text(
                json.dumps(
                    {
                        "schema": SCHEMA,
                        "contract": identity,
                        "versions": candidate["versions"],
                    },
                    indent=2,
                )
                + "\n"
            )
        if github_output := os.environ.get("GITHUB_OUTPUT"):
            with Path(github_output).open("a") as stream:
                stream.write(f"status={status}\n")
        print(json.dumps({"status": status, "lane": result["lane"]}))


if __name__ == "__main__":
    main()
