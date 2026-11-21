import re
from pathlib import Path

import hidee


def test_version_is_the_same_in_the_code_and_the_package():
    pyproject = (Path(__file__).resolve().parent.parent / "pyproject.toml").read_text(encoding="utf-8")
    assert re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1) == hidee.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", hidee.__version__)
