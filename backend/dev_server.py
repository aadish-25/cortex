"""
Development Server Launcher for Cortex.

On Windows, Uvicorn's default reload supervisor sends os.kill(pid, signal.CTRL_C_EVENT).
Because Windows console control events are broadcast to the entire console session, that synthetic
Ctrl+C signal leaks into runner scripts (run.py), Celery, and Vite, crashing the full development stack.

This launcher safely overrides BaseReload.restart on Windows to use process.terminate() instead of
broadcasting console Ctrl+C events, allowing seamless zero-downtime hot reloading during local development.
"""
import os
import sys
from pathlib import Path
import uvicorn

if sys.platform == "win32":
    import uvicorn.supervisors.basereload

    def _safe_restart(self) -> None:
        self.is_restarting = True
        if self.process and self.process.is_alive():
            self.process.terminate()
            self.process.join(timeout=3)
        self.process = uvicorn.supervisors.basereload.get_subprocess(
            config=self.config, target=self.target, sockets=self.sockets
        )
        self.process.start()

    uvicorn.supervisors.basereload.BaseReload.restart = _safe_restart


def main() -> None:
    base_dir = Path(__file__).resolve().parent.parent
    backend_dir = base_dir / "backend"

    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        reload_dirs=[str(backend_dir)],
    )


if __name__ == "__main__":
    main()
