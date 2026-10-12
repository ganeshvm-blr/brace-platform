"""Repository-internal packaging guards for brace-platform.

These tests verify only facts visible inside the public repository:

- the pyproject.toml metadata is parseable and internally consistent;
- every third-party import made by retained production modules is declared
  in the runtime dependency list, and nothing extra is declared;
- the packaging files carry no private repository references, credentials,
  or environment-specific paths;
- the source layout matches the configured package discovery root.

The tests never touch the network, never create virtual environments, and
never depend on the private Brace repository.
"""

import ast
import pathlib
import re
import sys

import pytest

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    tomllib = None

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_PYPROJECT_PATH = _REPO_ROOT / "pyproject.toml"
_SRC_BRACE = _REPO_ROOT / "src" / "brace"

_FORBIDDEN_PACKAGING_TOKENS = (
    "http://",
    "https://",
    "@",
    "Documents\\",
    "C:",
    "/home/",
    "~",
    "ssh:",
    "git@",
)


def _read_pyproject():
    if tomllib is None:  # pragma: no cover - Python < 3.11
        pytest.skip("tomllib is unavailable on this Python")
    return tomllib.loads(_PYPROJECT_PATH.read_text(encoding="utf-8"))


def _retained_modules():
    return sorted(p for p in _SRC_BRACE.rglob("*.py") if p.is_file())


def _third_party_imports():
    imports = set()
    for module in _retained_modules():
        tree = ast.parse(module.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name.split(".", 1)[0])
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".", 1)[0])
    stdlib = set(sys.stdlib_module_names)
    return {
        name
        for name in imports
        if name not in stdlib and name != "brace" and not name.startswith("_")
    }


@pytest.mark.skipif(tomllib is None, reason="tomllib unavailable")
def test_pyproject_metadata_is_well_formed():
    data = _read_pyproject()
    project = data["project"]
    assert project["name"] == "brace-platform"
    assert project["version"] == "0.1.0"
    assert project["requires-python"] == ">=3.10"
    assert data["build-system"]["build-backend"] == "setuptools.build_meta"
    assert data["tool"]["setuptools"]["packages"]["find"]["where"] == ["src"]
    assert data["tool"]["pytest"]["ini_options"]["testpaths"] == ["tests"]


@pytest.mark.skipif(tomllib is None, reason="tomllib unavailable")
def test_runtime_dependencies_match_retained_imports():
    data = _read_pyproject()
    declared = {
        re.split(r"[<>=!~; ]", dep, maxsplit=1)[0].strip()
        for dep in data["project"]["dependencies"]
    }
    imported = _third_party_imports()
    assert imported == declared, (
        f"declared dependencies {sorted(declared)} differ from "
        f"retained-module imports {sorted(imported)}"
    )


@pytest.mark.skipif(tomllib is None, reason="tomllib unavailable")
def test_packaging_files_have_no_private_references():
    for path in (_PYPROJECT_PATH,):
        content = path.read_text(encoding="utf-8")
        for token in _FORBIDDEN_PACKAGING_TOKENS:
            assert token not in content, (
                f"{path.name} contains forbidden token {token!r}"
            )


def test_source_layout_matches_discovery_root():
    assert _PYPROJECT_PATH.is_file()
    assert (_REPO_ROOT / "src").is_dir()
    assert (_SRC_BRACE / "__init__.py").is_file()
    assert all(
        rel in {"__init__.py", "reasoning/__init__.py", "reasoning/boundary.py"}
        or rel.startswith("_")
        for module in _retained_modules()
        for rel in (module.relative_to(_SRC_BRACE).as_posix(),)
    )


def _load_ci_distribution_audit():
    import textwrap

    ci_workflow_path = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
    content = ci_workflow_path.read_text(encoding="utf-8")
    step_marker = "- name: Audit distribution contents"
    step_code = content.split(step_marker, 1)[1].split("python - <<'PY'", 1)[1].split("\n          PY", 1)[0]
    dedented = textwrap.dedent(step_code)

    setup_lines = []
    for line in dedented.splitlines():
        if line.strip().startswith("wheels = sorted(dist.glob"):
            break
        setup_lines.append(line)
    setup_code = "\n".join(setup_lines)

    scope = {"dist": _REPO_ROOT / "dist"}
    exec(compile(setup_code, "<ci-dist-setup>", "exec"), scope)
    return scope


def test_distribution_content_scanner_detects_synthetic_credential_in_metadata(tmp_path):
    import zipfile

    ci_audit = _load_ci_distribution_audit()
    audit_wheel_archive = ci_audit["audit_wheel_archive"]
    retained_modules = ci_audit["retained"]

    # 1. Clean wheel archive with METADATA, RECORD, and WHEEL is accepted
    clean_wheel = tmp_path / "brace_platform-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(clean_wheel, "w") as zf:
        zf.writestr("brace_platform-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: brace-platform\n")
        zf.writestr("brace_platform-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: bdist_wheel\n")
        zf.writestr("brace_platform-0.1.0.dist-info/RECORD", "brace/__init__.py,sha256=xxx,123\n")
        for mod in retained_modules:
            zf.writestr(mod, "# public module\n")

    scanned = audit_wheel_archive(clean_wheel)
    assert "brace_platform-0.1.0.dist-info/METADATA" in scanned
    assert "brace_platform-0.1.0.dist-info/WHEEL" in scanned
    assert "brace_platform-0.1.0.dist-info/RECORD" in scanned

    # 2. Synthetic credential in METADATA is rejected
    bad_meta_wheel = tmp_path / "bad_meta-0.1.0-py3-none-any.whl"
    synthetic_aws = "AKIA" + "0" * 16
    with zipfile.ZipFile(bad_meta_wheel, "w") as zf:
        zf.writestr(
            "brace_platform-0.1.0.dist-info/METADATA",
            f"Metadata-Version: 2.1\nDescription: {synthetic_aws}\n",
        )
        zf.writestr("brace_platform-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        zf.writestr("brace_platform-0.1.0.dist-info/RECORD", "brace/__init__.py,sha256=xxx,123\n")
        for mod in retained_modules:
            zf.writestr(mod, "# public module\n")

    with pytest.raises(AssertionError, match="credential-like pattern \\(AWS access key ID\\)"):
        audit_wheel_archive(bad_meta_wheel)

    # 3. Synthetic credential in WHEEL is rejected
    bad_wheel_file = tmp_path / "bad_wheel-0.1.0-py3-none-any.whl"
    synthetic_secret = 'api_key = "' + "x" * 20 + '"'
    with zipfile.ZipFile(bad_wheel_file, "w") as zf:
        zf.writestr("brace_platform-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: brace-platform\n")
        zf.writestr("brace_platform-0.1.0.dist-info/WHEEL", f"Comment: {synthetic_secret}\n")
        zf.writestr("brace_platform-0.1.0.dist-info/RECORD", "brace/__init__.py,sha256=xxx,123\n")
        for mod in retained_modules:
            zf.writestr(mod, "# public module\n")

    with pytest.raises(AssertionError, match="credential-like pattern \\(secret assignment\\)"):
        audit_wheel_archive(bad_wheel_file)

    # 4. Synthetic credential in RECORD is rejected
    bad_record_wheel = tmp_path / "bad_record-0.1.0-py3-none-any.whl"
    synthetic_key = "-----BEGIN " + "RSA " + "PRIVATE KEY-----"
    with zipfile.ZipFile(bad_record_wheel, "w") as zf:
        zf.writestr("brace_platform-0.1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: brace-platform\n")
        zf.writestr("brace_platform-0.1.0.dist-info/WHEEL", "Wheel-Version: 1.0\n")
        zf.writestr("brace_platform-0.1.0.dist-info/RECORD", f"# {synthetic_key}\n")
        for mod in retained_modules:
            zf.writestr(mod, "# public module\n")

    with pytest.raises(AssertionError, match="credential-like pattern \\(private key\\)"):
        audit_wheel_archive(bad_record_wheel)
