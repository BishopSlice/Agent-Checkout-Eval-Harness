from __future__ import annotations

import os
import shutil
import signal
import subprocess
import time
from typing import Optional

import requests


class FlaskManager:
    """
    Starts a Flask app in a subprocess for grading, then tears it down.
    Usage: as a context manager or manually via start()/stop().
    """

    def __init__(
        self,
        app_dir: str,
        app_module: str = "app:app",
        port: int = 5001,
        startup_timeout: float = 10.0,
        env_override: Optional[dict] = None,
    ):
        self.app_dir = app_dir
        self.app_module = app_module
        self.port = port
        self.startup_timeout = startup_timeout
        self.env_override = env_override or {}
        self._process: Optional[subprocess.Popen] = None

    @property
    def url(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self) -> None:
        if self._process is not None:
            return

        flask_bin = shutil.which("flask") or "flask"
        env = {**os.environ, "FLASK_APP": self.app_module, "FLASK_ENV": "development", **self.env_override}
        # Disable the reloader — it forks a child process that the harness can't kill cleanly
        self._process = subprocess.Popen(
            [flask_bin, "run", "--port", str(self.port), "--no-reload"],
            cwd=self.app_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self._wait_for_ready()

    def _wait_for_ready(self) -> None:
        deadline = time.time() + self.startup_timeout
        while time.time() < deadline:
            if self._process.poll() is not None:
                stdout = self._process.stdout.read().decode(errors="replace")
                stderr = self._process.stderr.read().decode(errors="replace")
                raise RuntimeError(
                    f"Flask process exited early (rc={self._process.returncode}).\n"
                    f"stdout: {stdout}\nstderr: {stderr}"
                )
            try:
                resp = requests.get(f"{self.url}/success", timeout=1)
                if resp.status_code == 200:
                    return
            except requests.RequestException:
                pass
            time.sleep(0.25)

        self.stop()
        raise TimeoutError(
            f"Flask app did not become ready within {self.startup_timeout}s "
            f"on port {self.port}"
        )

    def stop(self) -> None:
        if self._process is None:
            return
        try:
            self._process.send_signal(signal.SIGTERM)
            self._process.wait(timeout=5)
        except Exception:
            self._process.kill()
        finally:
            self._process = None

    def __enter__(self) -> "FlaskManager":
        self.start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()
