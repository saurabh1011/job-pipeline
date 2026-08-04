"""Guards against Dockerfile drift: builds the image and checks that every
binary the app shells out to at runtime (pandoc, typst) is actually present
inside it. Excluded from the default `pytest tests/ -q` run (see pytest.ini)
because building the image is slow — run explicitly with
`pytest tests/test_docker_image.py -q -m docker`, or let CI run it on PRs.
"""
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.docker

IMAGE_TAG = "job-pipeline-dep-check"
REQUIRED_BINARIES = ["pandoc", "typst"]


def _docker_available():
    if shutil.which("docker") is None:
        return False
    return subprocess.run(
        ["docker", "info"], capture_output=True
    ).returncode == 0


@pytest.fixture(scope="module")
def built_image():
    if not _docker_available():
        pytest.skip("Docker is not installed or not running")
    result = subprocess.run(
        ["docker", "build", "-t", IMAGE_TAG, "."],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, f"docker build failed:\n{result.stderr}"
    yield IMAGE_TAG
    subprocess.run(["docker", "rmi", "-f", IMAGE_TAG], capture_output=True)


@pytest.mark.parametrize("binary", REQUIRED_BINARIES)
def test_required_binary_present_in_image(built_image, binary):
    result = subprocess.run(
        ["docker", "run", "--rm", built_image, binary, "--version"],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (
        f"'{binary}' is not available in the image "
        f"(subprocess calls in web/server.py and cli.py depend on it):\n"
        f"{result.stderr}"
    )
