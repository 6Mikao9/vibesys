"""Resolve CI base images from one digest-pinned GHCR mirror inventory."""

from __future__ import annotations

import argparse
import ast
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

PINS = Path(__file__).with_suffix(".toml")
MIRROR_ROOT = "ghcr.io/uw-syfi/vibesys/ci-base"


@dataclass(frozen=True)
class ImagePin:
    """An upstream multi-platform manifest and its byte-identical mirror."""

    source: str
    digest: str

    @property
    def upstream(self) -> str:
        """Return the immutable upstream reference used only by the mirror owner."""
        repository = self.source.split(":", 1)[0]
        if "/" not in repository:
            repository = "library/" + repository
        registry = repository.split("/", 1)[0]
        if "." not in registry:
            repository = "docker.io/" + repository
        return f"{repository}@{self.digest}"

    @property
    def mirror(self) -> str:
        """Return the same manifest in GHCR, retaining its upstream digest."""
        return f"{MIRROR_ROOT}@{self.digest}"

    @property
    def publish_tag(self) -> str:
        """Give the manifest a retained tag without making consumers depend on it."""
        return self.mirror.split("@", 1)[0] + ":" + self.digest.replace(":", "-")


def image_pins(path: Path = PINS) -> tuple[ImagePin, ...]:
    """Validate and load the sole inventory, rejecting unknown fields and duplicates."""
    data = tomllib.loads(path.read_text())
    if set(data) != {"images"}:
        message = f"{path}: expected only images, got {sorted(data)}"
        raise ValueError(message)
    if not isinstance(data["images"], list) or not data["images"]:
        message = f"{path}: images must be a nonempty list"
        raise ValueError(message)
    pins = []
    names = set()
    for row in data["images"]:
        if not isinstance(row, dict) or set(row) != {"source", "digest"}:
            message = f"{path}: unknown or missing image fields: {row!r}"
            raise ValueError(message)
        source, digest = row["source"], row["digest"]
        if not isinstance(source, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9./_-]*:[a-zA-Z0-9_.-]+", source
        ):
            message = f"{path}: invalid source: {source!r}"
            raise ValueError(message)
        if (
            not isinstance(digest, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest)
            or source in names
        ):
            message = f"{path}: invalid digest or duplicate source: {source}"
            raise ValueError(message)
        names.add(source)
        pins.append(ImagePin(source, digest))
    return tuple(pins)


def mirrored_image(source: str) -> str:
    """Resolve a CI base by its upstream name; an unlisted image is an error."""
    for pin in image_pins():
        if source == pin.source:
            return pin.mirror
    message = f"unmirrored CI image {source!r}; add its digest to {PINS.name}"
    raise ValueError(message)


def main(argv: list[str] | None = None) -> None:
    """Print a mirror reference or the copy plan for the mirror workflow."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?")
    args = parser.parse_args(argv)
    if args.source:
        print(mirrored_image(args.source))
    else:
        for pin in image_pins():
            print(pin.upstream, pin.publish_tag, pin.mirror)


def ci_image_problems(root: Path, paths: list[str], pins: tuple[ImagePin, ...]) -> list[str]:
    """Check CI's literal image inputs and Dockerfiles against the mirror inventory.

    Upstream names are permitted only as keys passed to ``mirrored_image`` or
    the Feature matrix resolver. Example task Dockerfiles are user workloads,
    not images built by CI. Dynamic FROM values come from the resolved pins or
    from locally built task/Feature stages.
    """
    problems = []
    sources = {pin.source for pin in pins}
    mirrors = {pin.mirror for pin in pins}
    for relative in paths:
        path = root / relative
        if path.is_file() and _ci_image_source(relative):
            text = path.read_text()
            problems.extend(_literal_image_problems(relative, text, sources, mirrors))
    return problems


def _ci_image_source(path: str) -> bool:
    if path.startswith("examples/"):
        return False
    return (
        "Dockerfile" in Path(path).name
        or (path.startswith(".github/") and Path(path).suffix in {".yml", ".yaml", ".sh"})
        or (path.startswith("scripts/") and path.endswith(".sh"))
        or (
            path.endswith(".py")
            and (
                path.startswith(("tests/e2e/", "tests/minimal_container/", "tests/slurm_cluster/"))
                or path.endswith("_e2e.py")
            )
        )
    )


def _literal_image_problems(
    path: str, text: str, sources: set[str], mirrors: set[str]
) -> list[str]:
    problems = []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if re.search(r"(?:docker[.]io|registry-1[.]docker[.]io|index[.]docker[.]io)/", stripped):
            problems.append(
                f"{path}:{number}: Docker Hub reference in CI; resolve a listed GHCR mirror"
            )
        # FROM stages, shell pulls, and workflow container/service image fields.
        match = re.search(
            r"(?:^FROM\s+(?:--platform=\S+\s+)?|docker\s+pull\s+|^\s*(?:image|container):\s+)([^\s]+)",
            line,
            re.IGNORECASE if "Dockerfile" in Path(path).name else 0,
        )
        if match and not path.endswith(".py"):
            reference = match[1].strip("\"'")
            if (
                "docker.io/" not in reference
                and reference not in mirrors
                and reference != "scratch"
                and not reference.startswith(("$", "{"))
            ):
                problems.append(
                    f"{path}:{number}: unmirrored CI image {reference}; resolve it from ci_base_images.toml"
                )
        if "Dockerfile" in Path(path).name and stripped.startswith("FROM "):
            continue
        if path.endswith(".py"):
            continue
        # Matrix keys are resolved through the inventory before any build.
        if stripped.startswith("base:") and stripped.split(maxsplit=1)[-1] not in sources:
            problems.append(f"{path}:{number}: base is absent from ci_base_images.toml")
    if path.endswith(".py"):
        problems.extend(_python_image_problems(path, text, sources, mirrors))
    return problems


def _python_image_problems(path: str, text: str, sources: set[str], mirrors: set[str]) -> list[str]:
    tree = ast.parse(text)
    resolved = set()
    problems = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "mirrored_image"
        ):
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    resolved.add(id(arg))
                    if arg.value not in sources:
                        problems.append(
                            f"{path}:{arg.lineno}: unlisted CI image {arg.value}; add its mirror pin"
                        )
    for node in ast.walk(tree):
        if (
            not isinstance(node, ast.Constant)
            or not isinstance(node.value, str)
            or id(node) in resolved
        ):
            continue
        value = node.value
        if value in mirrors:
            continue
        image = re.fullmatch(
            r"(?:docker[.]io/)?[a-z0-9][a-z0-9./_-]*(?::[a-zA-Z0-9_.-]+|@sha256:[0-9a-f]+)", value
        )
        if image:
            problems.append(
                f"{path}:{node.lineno}: direct CI image {value}; use mirrored_image with a listed pin"
            )
    return problems


if __name__ == "__main__":
    main()
