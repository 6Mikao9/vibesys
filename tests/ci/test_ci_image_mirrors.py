"""CI bases come from byte-identical GHCR mirrors with one pin inventory."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from hypothesis import example, given
from hypothesis import strategies as st
from scripts.ci_base_images import ImagePin, ci_image_problems, image_pins, main, mirrored_image

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
        "tests/e2e/test_missing.py": f'BASE = mirrored_image("{name}:latest")\n',
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
            "tests/e2e/test_missing.py": f'BASE = mirrored_image("{pin.source}")\n',
        }.items():
            (root / relative).write_text(text)
        assert ci_image_problems(root, list(cases), (pin,)) == []


def test_every_mirror_keeps_the_exact_manifest_digest_and_a_retained_tag() -> None:
    pins = image_pins()
    assert pins
    for pin in pins:
        assert mirrored_image(pin.source) == pin.mirror
        assert pin.mirror.startswith("ghcr.io/")
        assert pin.upstream.rsplit("@", 1)[1] == pin.mirror.rsplit("@", 1)[1] == pin.digest
        assert pin.publish_tag.endswith(pin.digest.replace(":", "-"))
    with pytest.raises(ValueError, match="unmirrored CI image"):
        mirrored_image("unlisted:latest")


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
    assert "skopeo inspect --no-creds" in script
    workflow = (REPO / ".github/workflows/ci-base-images.yml").read_text()
    assert "packages: write" in workflow
    assert "./scripts/ci_mirror_base_images.sh" in workflow


def test_mirror_cli_prints_the_same_pin_inventory(capsys: pytest.CaptureFixture[str]) -> None:
    main([])
    assert capsys.readouterr().out.splitlines() == [
        f"{pin.upstream} {pin.publish_tag} {pin.mirror}" for pin in image_pins()
    ]
    main(["python:3.12-bookworm"])
    assert capsys.readouterr().out.strip() == mirrored_image("python:3.12-bookworm")


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
