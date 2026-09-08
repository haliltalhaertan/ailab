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
    """Run policy-approved unit-test scripts through a fake container CLI.

    Production code still requires Docker/Podman and fails closed without it.
    The fake exists only for fast unit tests. Because it ultimately uses host
    ``exec``, it performs the production AST policy a second time immediately
    before execution so a bypass payload can never reach host Python through
    this fixture.
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
                source_text = script.read_text(encoding="utf-8")
                policy = code_experiment.GuardedExperimentWorkspace(
                    Path(source),
                    container_engine="docker",
                )
                policy._validate_python(source_text)
                sys.argv = [str(script), *script_args]
                namespace = {"__name__": "__main__", "__file__": str(script)}
                with contextlib.redirect_stdout(out_buffer), contextlib.redirect_stderr(err_buffer):
                    exec(compile(source_text, str(script), "exec"), namespace, namespace)
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

    real_run = code_experiment.subprocess.run

    def fake_run(command, *args, **kwargs):
        cmd = [str(x) for x in command]
        if len(cmd) >= 2 and cmd[0] in {"docker", "podman"} and cmd[1] == "info":
            return code_experiment.subprocess.CompletedProcess(
                cmd, 0, stdout="Server Version: 27.0.0\n", stderr=""
            )
        if len(cmd) >= 2 and cmd[0] in {"docker", "podman"} and cmd[1:3] == ["rm", "-f"]:
            return code_experiment.subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(code_experiment.shutil, "which", fake_which)
    monkeypatch.setattr(code_experiment.subprocess, "run", fake_run)
    monkeypatch.setattr(code_experiment.subprocess, "Popen", FakePopen)
    return commands
