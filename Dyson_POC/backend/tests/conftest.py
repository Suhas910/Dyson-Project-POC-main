"""Shared fixtures: builds each calibration part once per test session."""

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import features  # noqa: E402
import step_loader  # noqa: E402
from tests import geometry_fixtures  # noqa: E402

from OCC.Core.gp import gp_Dir  # noqa: E402


PULL_DIRECTION = gp_Dir(0, 0, 1)


@pytest.fixture(scope="session")
def parts_dir(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("calibration_parts")


@pytest.fixture(scope="session")
def built_parts(parts_dir) -> dict:
    """Writes every calibration part to STEP and reads it back through the loader.

    Going through a real STEP round trip rather than measuring the in-memory
    shape means the tests cover the import path too, which is where unit
    handling and healing live.
    """
    built = {}
    for name, builder in geometry_fixtures.ALL_PARTS.items():
        shape, expected = builder()
        path = geometry_fixtures.write_step(shape, parts_dir / f"{name}.step")
        loaded = step_loader.load_step(path)
        model = features.build_part_model(loaded.shape, PULL_DIRECTION)
        built[name] = {
            "path": path,
            "expected": expected,
            "loaded": loaded,
            "model": model,
        }
    return built
