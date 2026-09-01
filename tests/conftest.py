from __future__ import annotations

import contextlib
import io
import sys
import traceback
from pathlib import Path

import pytest

import lab.code_experiment as code_experiment


@pytest.fixture
def fake_container_runtime(monkeypatch):
    """Run hard-coded test scripts through a fake container CLI.

    Production code still requires Docker/Podman and fails closed without it.
    This fixture only replaces the external container process so unit tests do
    not depend on Docker Hub/network availability. It also records the exact
    command, allowing tests to assert the production isolation flags.
    """

    commands: list[list[str]] = []

    def fake_which(name: str):
        if name in {"docker", "podman"}:
            return f"/fake/{name}"
        return None

    class FakePopen:
        def __init__(self, command, *, stdin=None, stdout=None, stderr=None, env=None, **kwargs):
            del stdin, env, kwargs
            self.command = [str(x) for x in command]
            commands.append(self.command)
            self.returncode = 0

            mount = self.command[self.command.index("--mount") + 1]
            source = mount.split("source=", 1)[1].split(",target=", 1)[0]
            script_arg = next(
                value
                for value in self.command
                if value.startswith("/workspace/") and value.endswith(".py")
            )
            script = Path(source) / script_arg.removeprefix("/workspace/")
            script_index = self.command.index(script_arg)
            script_args = self.command[script_index + 1 :]

            out_buffer = io.StringIO()
            err_buffer = io.StringIO()
            old_argv = sys.argv
            try:
                sys.argv = [str(script), *script_args]
                namespace = {"__name__": "__main__", "__file__": str(script)}
                with contextlib.redirect_stdout(out_buffer), contextlib.redirect_stderr(err_buffer):
                    exec(compile(script.read_text(encoding="utf-8"), str(script), "exec"), namespace, namespace)
            except BaseException:
                self.returncode = 1
                traceback.print_exc(file=err_buffer)
            finally:
                sys.argv = old_argv

            if stdout is not None:
                stdout.write(out_buffer.getvalue().encode("utf-8"))
                stdout.flush()
            if stderr is not None:
                stderr.write(err_buffer.getvalue().encode("utf-8"))
                stderr.flush()

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            del timeout
            return self.returncode

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(code_experiment.shutil, "which", fake_which)
    monkeypatch.setattr(code_experiment.subprocess, "Popen", FakePopen)
    return commands
