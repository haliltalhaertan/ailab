from __future__ import annotations

import pytest

from lab.code_experiment import GuardedExperimentWorkspace, UnsafeExperimentCode


@pytest.fixture
def workspace(tmp_path):
    return GuardedExperimentWorkspace(tmp_path / "workspace", container_engine="")


@pytest.mark.parametrize(
    "source",
    [
        'import random; random._os.system("x")',
        'import typing; typing.sys.modules["os"].system("x")',
        'import dataclasses; dataclasses.sys.modules["os"]',
        'import operator; operator.attrgetter("__class__.__base__.__subclasses__")(())()',
        '"{0.__class__}".format(1)',
        "import os",
        "o = open",
        "e = eval",
        'from operator import attrgetter; attrgetter("x")',
        'from operator import methodcaller; methodcaller("x")',
        '"{x}".format_map({"x": 1})',
    ],
)
def test_verified_ast_bypass_patterns_are_rejected(workspace, source):
    with pytest.raises(UnsafeExperimentCode):
        workspace._validate_python(source)


def test_normal_deterministic_math_script_is_allowed(workspace):
    workspace._validate_python(
        """from itertools import combinations
values = [1, 2, 3, 4]
print(sum(a + b for a, b in combinations(values, 2)))
"""
    )
