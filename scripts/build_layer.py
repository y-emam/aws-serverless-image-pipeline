"""Build the Pillow Lambda layer for the Lambda CPU architecture.

Pillow has compiled C extensions, so a wheel built for Windows or macOS will
not import on Lambda (Amazon Linux). This script asks pip for the manylinux
wheel that matches the Lambda runtime and architecture instead, so it works
from any OS without Docker.

    python scripts/build_layer.py                 # arm64 (default)
    python scripts/build_layer.py --arch x86_64
    python scripts/build_layer.py --docker        # fallback: build inside the official SAM build image

Output: build/layers/pillow-<arch>/python/...  (Terraform zips this folder)
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "src" / "layers" / "pillow" / "requirements.txt"
PYTHON_VERSION = "3.13"  # must match the Lambda runtime in infra/lambda.tf

PLATFORMS = {
    "arm64": ["manylinux_2_28_aarch64", "manylinux2014_aarch64"],
    "x86_64": ["manylinux_2_28_x86_64", "manylinux2014_x86_64"],
}


def build_with_pip(arch, target):
    cmd = [
        sys.executable, "-m", "pip", "install",
        "--requirement", str(REQUIREMENTS),
        "--target", str(target),
        "--implementation", "cp",
        "--python-version", PYTHON_VERSION,
        "--only-binary=:all:",
        "--upgrade",
        "--no-compile",
    ]
    for platform in PLATFORMS[arch]:
        cmd += ["--platform", platform]
    subprocess.run(cmd, check=True)


def build_with_docker(arch, target):
    docker_arch = "arm64" if arch == "arm64" else "x86_64"
    image = f"public.ecr.aws/sam/build-python{PYTHON_VERSION}:latest-{docker_arch}"
    platform = "linux/arm64" if arch == "arm64" else "linux/amd64"
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "docker", "run", "--rm", "--platform", platform,
            "-v", f"{REQUIREMENTS.parent}:/req:ro",
            "-v", f"{target}:/out",
            image,
            "pip", "install", "-r", "/req/requirements.txt", "-t", "/out", "--no-compile",
        ],
        check=True,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--arch", choices=sorted(PLATFORMS), default="arm64")
    parser.add_argument("--docker", action="store_true", help="build inside the SAM build image")
    args = parser.parse_args()

    layer_root = ROOT / "build" / "layers" / f"pillow-{args.arch}"
    target = layer_root / "python"  # Lambda adds /opt/python to sys.path
    shutil.rmtree(layer_root, ignore_errors=True)

    if args.docker:
        build_with_docker(args.arch, target)
    else:
        build_with_pip(args.arch, target)

    # Trim files Lambda never needs; keeps the layer small and the zip stable.
    for path in list(target.rglob("__pycache__")):
        shutil.rmtree(path, ignore_errors=True)

    sos = sorted(p.name for p in target.rglob("*.so"))[:3]
    size_mb = sum(p.stat().st_size for p in target.rglob("*") if p.is_file()) / 1e6
    print(f"Layer built at {layer_root} ({size_mb:.1f} MB unzipped)")
    print(f"Sample native modules: {sos}")
    if not any("linux" in s for s in sos):
        sys.exit("ERROR: no Linux native modules found - wrong wheel platform?")


if __name__ == "__main__":
    main()
