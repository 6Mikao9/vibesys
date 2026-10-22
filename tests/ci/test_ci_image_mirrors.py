"""CI bases come from one pin inventory: a GHCR mirror, or the same digest upstream."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml
from hypothesis import example, given
from hypothesis import strategies as st
from scripts.ci_base_images import (
    RESOLUTION_ENV,
    ImagePin,
    base_image,
    ci_image_problems,
    image_pins,
    main,
    resolve_bases,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

REPO = Path(__file__).resolve().parents[2]


def test_ci_build_inputs_are_mirrored_and_pinned() -> None:
    paths = [
        path.relative_to(REPO).as_posix()
        for parent in (".github", "scripts", "tests", "libs")
        for path in (REPO / parent).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    ]
    assert ci_image_problems(REPO, paths, image_pins()) == []


@pytest.mark.regression
@given(name=st.from_regex(r"[a-z]{1,12}", fullmatch=True))
@example(name="python")
def test_new_docker_hub_images_cannot_escape_the_mirror_inventory(name: str) -> None:
    pin = ImagePin("python:3.12-bookworm", "sha256:" + "a" * 64)
    cases = {
        "tests/new/Dockerfile": f"FROM docker.io/library/{name}:latest\n",
        ".github/workflows/new.yml": f"run: docker pull {name}:latest\n",
        "tests/e2e/test_new.py": f'BASE = "{name}:latest"\n',
        "tests/e2e/test_missing.py": f'BASE = base_image("{name}:latest")\n',
    }
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for relative, text in cases.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        problems = ci_image_problems(root, list(cases), (pin,))
        assert len(problems) == len(cases)
        assert all(any(relative in problem for problem in problems) for relative in cases)
        for relative, text in {
            "tests/new/Dockerfile": f"FROM {pin.mirror}\n",
            ".github/workflows/new.yml": f"run: docker pull {pin.mirror}\n",
            "tests/e2e/test_new.py": f'BASE = "{pin.mirror}"\n',
            "tests/e2e/test_missing.py": f'BASE = base_image("{pin.source}")\n',
        }.items():
            (root / relative).write_text(text)
        assert ci_image_problems(root, list(cases), (pin,)) == []


def test_every_mirror_keeps_the_exact_manifest_digest_and_a_retained_tag() -> None:
    pins = image_pins()
    assert pins
    for pin in pins:
        assert base_image(pin.source, {}) == pin.upstream
        assert pin.mirror.startswith("ghcr.io/")
        assert pin.upstream.rsplit("@", 1)[1] == pin.mirror.rsplit("@", 1)[1] == pin.digest
        assert pin.publish_tag.endswith(pin.digest.replace(":", "-"))
    with pytest.raises(ValueError, match="unmirrored CI image"):
        base_image("unlisted:latest")


@pytest.mark.parametrize(
    "content",
    [
        "unknown = 1\n",
        '[[images]]\nsource = "python:3"\ndigest = "bad"\n',
        '[[images]]\nsource = "python:3"\ndigest = "bad"\nextra = true\n',
        '[[images]]\nsource = "--option"\ndigest = "bad"\n',
    ],
)
def test_invalid_pin_inventory_fails_with_its_path(tmp_path: Path, content: str) -> None:
    path = tmp_path / "pins.toml"
    path.write_text(content)
    with pytest.raises(ValueError, match=r"pins\.toml"):
        image_pins(path)


def test_the_one_mirror_owner_copies_all_platforms_and_preserves_digests() -> None:
    script = (REPO / "scripts/ci_mirror_base_images.sh").read_text()
    assert "skopeo copy --all --preserve-digests" in script
    assert "for attempt in 1 2 3" in script
    assert "python3 -m scripts.ci_base_images" in script
    assert "--no-creds" not in script
    assert "package public" not in script
    workflow = (REPO / ".github/workflows/ci-base-images.yml").read_text()
    assert "packages: write" in workflow
    assert "GHCR_TOKEN: ${{ github.token }}" in workflow
    assert "./scripts/ci_mirror_base_images.sh" in workflow


def test_mirror_cli_prints_the_same_pin_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    main([])
    assert capsys.readouterr().out.splitlines() == [
        f"{pin.upstream} {pin.publish_tag} {pin.mirror}" for pin in image_pins()
    ]
    main(["python:3.12-bookworm"])
    assert capsys.readouterr().out.strip() == base_image("python:3.12-bookworm")


@pytest.mark.parametrize(
    "content", ["images = []", "images = 1", "images = [1]", "[[images]]\nsource = 1\ndigest = 1"]
)
def test_malformed_inventory_shapes_are_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "pins.toml"
    path.write_text(content)
    with pytest.raises(ValueError, match=r"pins\.toml"):
        image_pins(path)


@pytest.mark.parametrize(
    ("source", "repository"),
    [
        ("python:3", "docker.io/library/python"),
        ("vendor/image:1", "docker.io/vendor/image"),
        ("registry.example.com/team/base:1", "registry.example.com/team/base"),
    ],
)
def test_mirror_sources_follow_docker_registry_naming(source: str, repository: str) -> None:
    pin = ImagePin(source, "sha256:" + "a" * 64)
    assert pin.upstream == f"{repository}@{pin.digest}"


class FakeMirrorRegistry:
    """A registry answering each manifest read with an HTTP status or a client failure."""

    def __init__(self, answers: Mapping[str, object]) -> None:
        self.answers = answers
        self.reads: list[str] = []

    def __call__(self, reference: str) -> None:
        self.reads.append(reference)
        answer = self.answers.get(reference, 200)
        if isinstance(answer, BaseException):
            raise answer
        if answer != 200:
            message = f"manifest inspect failed (exit 1): HTTP {answer}"
            raise OSError(message)


MIRROR_FAILURES = [
    OSError("HTTP 401: unauthorized"),
    OSError("HTTP 404: manifest unknown"),
    OSError("HTTP 500: internal error"),
    TimeoutError("probe deadline"),
    FileNotFoundError("docker"),
]


@pytest.mark.regression
@pytest.mark.parametrize("failure", MIRROR_FAILURES, ids=repr)
def test_an_unreadable_mirror_falls_back_to_the_same_digest_with_a_warning(
    failure: BaseException, capsys: pytest.CaptureFixture[str]
) -> None:
    pins = image_pins()
    broken = pins[0]
    registry = FakeMirrorRegistry({broken.mirror: failure})
    resolved = resolve_bases(pins, registry)
    assert resolved[broken.source] == broken.upstream
    assert broken.upstream.rsplit("@", 1)[1] == broken.mirror.rsplit("@", 1)[1]
    assert all(resolved[pin.source] == pin.mirror for pin in pins[1:])
    assert registry.reads == [pin.mirror for pin in pins]
    warnings = [line for line in capsys.readouterr().err.splitlines() if "::warning::" in line]
    assert len(warnings) == 1
    assert broken.mirror in warnings[0]
    assert broken.upstream in warnings[0]


def test_readable_mirrors_are_used_without_warnings(capsys: pytest.CaptureFixture[str]) -> None:
    pins = image_pins()
    assert resolve_bases(pins, FakeMirrorRegistry({})) == {pin.source: pin.mirror for pin in pins}
    assert capsys.readouterr().err == ""


@given(down=st.lists(st.booleans(), min_size=1, max_size=9))
def test_every_image_resolves_to_exactly_one_of_its_two_references(down: list[bool]) -> None:
    pins = image_pins()
    answers = {pin.mirror: 401 for pin, is_down in zip(pins, down, strict=False) if is_down}
    resolved = resolve_bases(pins, FakeMirrorRegistry(answers))
    assert set(resolved) == {pin.source for pin in pins}
    for pin in pins:
        assert resolved[pin.source] == (pin.upstream if pin.mirror in answers else pin.mirror)


def test_the_resolution_cli_prints_json_and_the_job_environment_selects_references(
    capsys: pytest.CaptureFixture[str],
) -> None:
    pins = image_pins()
    down = pins[0]
    main(["--resolve"], FakeMirrorRegistry({down.mirror: 404}))
    resolved = json.loads(capsys.readouterr().out)
    env = {RESOLUTION_ENV: json.dumps(resolved)}
    assert base_image(down.source, env) == down.upstream
    assert [base_image(pin.source, env) for pin in pins[1:]] == [pin.mirror for pin in pins[1:]]
    assert base_image(down.source, {RESOLUTION_ENV: "{}"}) == down.upstream


@pytest.mark.parametrize(
    "value",
    [
        "not json",
        "[1]",
        '{"python:3.12-bookworm": "ghcr.io/other/image:latest"}',
        '{"nope:1": "x"}',
    ],
)
def test_a_bad_job_resolution_names_its_variable(value: str) -> None:
    with pytest.raises(ValueError, match=RESOLUTION_ENV):
        base_image("python:3.12-bookworm", {RESOLUTION_ENV: value})


def test_upstream_digests_are_the_only_literal_docker_hub_references() -> None:
    pin = ImagePin("python:3.12-bookworm", "sha256:" + "a" * 64)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "tests").mkdir()
        (root / "tests/Dockerfile").write_text(
            f"ARG BASE_IMAGE={pin.upstream}\nFROM ${{BASE_IMAGE}}\n"
        )
        assert ci_image_problems(root, ["tests/Dockerfile"], (pin,)) == []
        (root / "tests/Dockerfile").write_text(
            "ARG BASE_IMAGE=docker.io/library/python@sha256:" + "b" * 64 + "\n"
        )
        (problem,) = ci_image_problems(root, ["tests/Dockerfile"], (pin,))
        assert "Docker Hub reference" in problem


def _workflow_jobs() -> dict[str, Any]:
    jobs: dict[str, Any] = yaml.safe_load((REPO / ".github/workflows/test.yml").read_text())["jobs"]
    return jobs


@pytest.mark.regression
@pytest.mark.parametrize(
    "job",
    [
        name
        for name, config in _workflow_jobs().items()
        if any("ci_docker_data_root.sh attach" in step.get("run", "") for step in config["steps"])
    ],
)
def test_every_job_that_builds_from_a_mirror_logs_in_first_with_read_access(job: str) -> None:
    config = _workflow_jobs()[job]
    steps = config["steps"]
    login = next(
        index
        for index, step in enumerate(steps)
        if step.get("uses") == "./.github/actions/ci-base-mirror"
    )
    first_docker_use = next(
        index
        for index, step in enumerate(steps)
        if "ci_docker_data_root.sh attach" in step.get("run", "")
        or "docker-image-cache" in step.get("uses", "")
        or "ci_base_images" in step.get("run", "")
    )
    assert login <= first_docker_use
    assert config["permissions"]["packages"] in {"read", "write"}


def test_the_mirror_action_logs_in_with_the_job_token_and_never_fails_the_job() -> None:
    action = yaml.safe_load((REPO / ".github/actions/ci-base-mirror/action.yml").read_text())
    (step,) = action["runs"]["steps"]
    assert "github.token" in step["env"]["GHCR_TOKEN"]
    script = (REPO / step["run"]).read_text()
    assert "docker login ghcr.io" in script
    assert "--password-stdin" in script
    assert "python3 -m scripts.ci_base_images --resolve" in script
    assert script.count("::warning::") == 2
    assert script.rstrip().endswith("exit 0")
    assert "set -e" not in script.replace("set -uo", "")
