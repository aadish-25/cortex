import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
import time

BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend"

# ANSI Colors for clean terminal log multiplexing
CYAN = "\033[96m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
MAGENTA = "\033[95m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"

# Track running child processes for graceful shutdown
active_processes = []
is_shutting_down = False


def log(prefix: str, color: str, message: str) -> None:
    timestamp = time.strftime("%H:%M:%S")
    print(f"{color}[{prefix}] {timestamp} |{RESET} {message}", flush=True)


def check_and_start_docker() -> bool:
    """Verifies Docker daemon is running; automatically launches Docker Desktop if offline."""
    log("DOCKER", CYAN, "Checking Docker daemon health...")

    def is_docker_ready() -> bool:
        try:
            res = subprocess.run(["docker", "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=4)
            return res.returncode == 0
        except Exception:
            return False

    if not is_docker_ready():
        log("DOCKER", YELLOW, "Docker daemon is offline. Attempting to start Docker Desktop...")
        docker_desktop_paths = [
            Path(r"C:\Program Files\Docker\Docker\Docker Desktop.exe"),
            Path(os.path.expandvars(r"%LOCALAPPDATA%\Docker\Docker Desktop.exe")),
        ]
        exe_path = next((p for p in docker_desktop_paths if p.exists()), None)

        if exe_path:
            try:
                subprocess.Popen([str(exe_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                log("DOCKER", CYAN, f"Launched Docker Desktop from {exe_path}. Waiting for engine...")
            except Exception as e:
                log("DOCKER", RED, f"Failed to launch Docker Desktop automatically: {e}")
                return False
        else:
            log("DOCKER", RED, "Docker Desktop executable not found in standard paths. Please start Docker manually.")
            return False

        # Wait for Docker engine to become responsive (up to 90s)
        start_wait = time.time()
        while time.time() - start_wait < 90:
            if is_docker_ready():
                log("DOCKER", GREEN, "Docker engine is now online and healthy!")
                break
            time.sleep(3)
            elapsed = int(time.time() - start_wait)
            log("DOCKER", YELLOW, f"Waiting for Docker engine to initialize ({elapsed}s)...")
        else:
            log("DOCKER", RED, "Docker startup timed out. Engine is not responding.")
            return False

    # Start Postgres, Qdrant, and Redis containers via docker-compose
    log("DOCKER", CYAN, "Starting required containers (cortex-postgres, cortex-qdrant, cortex-redis)...")
    try:
        subprocess.run(
            ["docker", "compose", "up", "-d", "postgres", "qdrant", "redis"],
            cwd=str(BASE_DIR),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        log("DOCKER", GREEN, "Postgres (localhost:5432), Qdrant (http://localhost:6333), and Redis (localhost:6379) are active.")
        return True
    except Exception as e:
        log("DOCKER", RED, f"Failed to run docker compose: {e}")
        return False


def stream_output(pipe, prefix: str, color: str) -> None:
    """Reads stdout/stderr line-by-line and prints with colored prefix."""
    try:
        for line in iter(pipe.readline, ""):
            if not line:
                break
            line_str = line.strip("\r\n")
            if line_str:
                log(prefix, color, line_str)
    except (ValueError, OSError):
        pass
    finally:
        try:
            pipe.close()
        except Exception:
            pass


def shutdown_all(*args) -> None:
    """Graceful teardown on Ctrl+C (SIGINT / SIGTERM)."""
    global is_shutting_down
    if is_shutting_down:
        return
    is_shutting_down = True

    print("\n" + "=" * 60, flush=True)
    log("SYSTEM", BOLD + YELLOW, "Shutdown signal (Ctrl+C) received. Stopping all services...")

    for proc, name in active_processes:
        if proc.poll() is None:
            log("SYSTEM", CYAN, f"Terminating {name} (PID {proc.pid})...")
            try:
                if sys.platform == "win32":
                    # Force kill process tree on Windows to ensure child workers exit
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )
                else:
                    proc.terminate()
            except Exception as e:
                log("SYSTEM", RED, f"Error terminating {name}: {e}")

    log("SYSTEM", BOLD + GREEN, "Application processes stopped. Docker data services are left running.\n")
    sys.exit(0)


def ensure_environment_file() -> bool:
    env_file = BASE_DIR / ".env"
    example_file = BASE_DIR / ".env.example"
    if env_file.exists():
        return True
    if not example_file.exists():
        log("CONFIG", RED, "Missing .env and .env.example. Cannot start Cortex.")
        return False
    shutil.copyfile(example_file, env_file)
    log("CONFIG", YELLOW, "Created .env from .env.example. Add your provider keys before using chat integrations.")
    return True


def prepare_python_environment() -> str | None:
    """Install/sync Python dependencies and return the interpreter to launch services with."""
    venv_python = BASE_DIR / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    uv_cmd = shutil.which("uv")
    if uv_cmd:
        log("PYTHON", CYAN, "Syncing Python dependencies from uv.lock...")
        try:
            subprocess.run([uv_cmd, "sync", "--frozen"], cwd=str(BASE_DIR), check=True)
        except subprocess.CalledProcessError:
            log("PYTHON", RED, "uv sync failed. Check the Python version and dependency errors above.")
            return None
        return str(venv_python) if venv_python.exists() else sys.executable

    if venv_python.exists():
        return str(venv_python)

    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        import celery  # noqa: F401
        import sqlmodel  # noqa: F401
        import qdrant_client  # noqa: F401
        import redis  # noqa: F401
        import pydantic_settings  # noqa: F401
        return sys.executable
    except ImportError:
        log("PYTHON", YELLOW, "Python dependencies are missing. Installing the project with pip...")
        try:
            subprocess.run([sys.executable, "-m", "pip", "install", "-e", str(BASE_DIR)], check=True)
            return sys.executable
        except subprocess.CalledProcessError:
            log("PYTHON", RED, "Dependency installation failed. Install Python 3.14 and run 'uv sync'.")
            return None


def prepare_frontend_dependencies() -> str | None:
    npm_cmd = shutil.which("npm.cmd") or shutil.which("npm")
    if not npm_cmd:
        log("FRONTEND", RED, "Node.js/npm was not found. Install Node.js 18+ before starting the frontend.")
        return None
    lock_file = FRONTEND_DIR / "package-lock.json"
    package_file = FRONTEND_DIR / "package.json"
    needs_install = (
        not (FRONTEND_DIR / "node_modules").exists()
        or not lock_file.exists()
        or package_file.stat().st_mtime > lock_file.stat().st_mtime
    )
    if needs_install:
        log("FRONTEND", CYAN, "Installing/synchronizing frontend dependencies...")
        try:
            command = ["cmd", "/c", npm_cmd, "install"] if sys.platform == "win32" else [npm_cmd, "install"]
            subprocess.run(command, cwd=str(FRONTEND_DIR), check=True)
        except subprocess.CalledProcessError:
            log("FRONTEND", RED, "npm install failed. Check your Node.js version and network connection.")
            return None
    return npm_cmd


def start_process(command: list[str], name: str, prefix: str, color: str, cwd: Path = BASE_DIR) -> subprocess.Popen:
    proc = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    active_processes.append((proc, name))
    threading.Thread(target=stream_output, args=(proc.stdout, prefix, color), daemon=True).start()
    return proc


def main() -> None:
    # Register Ctrl+C / interrupt handlers
    signal.signal(signal.SIGINT, shutdown_all)
    signal.signal(signal.SIGTERM, shutdown_all)

    # Enable ANSI escape codes in Windows cmd/powershell
    if sys.platform == "win32":
        os.system("")

    print("=" * 60)
    print(f"{BOLD}{GREEN} Cortex Enterprise Unified Development Environment{RESET}")
    print(f" Working Directory: {BASE_DIR}")
    print(f" Press {BOLD}Ctrl+C{RESET} to stop all services simultaneously.")
    print("=" * 60)

    # 1. Prepare local configuration and runtimes.
    if not ensure_environment_file():
        sys.exit(1)
    python_exe = prepare_python_environment()
    if not python_exe:
        sys.exit(1)
    npm_cmd = prepare_frontend_dependencies()
    if not npm_cmd:
        sys.exit(1)

    # 2. Ensure Docker & infrastructure containers are online.
    if not check_and_start_docker():
        log("DOCKER", BOLD + RED, "Startup halted: Required Docker containers could not be started.")
        log("DOCKER", YELLOW, "Please ensure Docker Desktop is running, then run 'python run.py' again.")
        sys.exit(1)

    # 3. Launch FastAPI.
    backend_cmd = [python_exe, "-m", "uvicorn", "backend.main:app", "--reload", "--host", "0.0.0.0", "--port", "8000"]
    log("BACKEND", GREEN, "Starting FastAPI backend on http://localhost:8000...")
    backend_proc = start_process(backend_cmd, "Backend (FastAPI)", "BACKEND", GREEN)

    # 4. Launch Celery worker and beat for document reindexing and scheduled reconciliation.
    worker_cmd = [python_exe, "-m", "celery", "-A", "backend.worker.celery_app.celery_app", "worker", "--loglevel=INFO", "--pool=solo"]
    beat_cmd = [python_exe, "-m", "celery", "-A", "backend.worker.celery_app.celery_app", "beat", "--loglevel=INFO"]
    log("WORKER", CYAN, "Starting Celery worker for document indexing...")
    start_process(worker_cmd, "Celery Worker", "WORKER", CYAN)
    log("WORKER", CYAN, "Starting Celery Beat for scheduled reconciliation...")
    start_process(beat_cmd, "Celery Beat", "BEAT", CYAN)

    # 5. Launch the React/Vite frontend.
    log("FRONTEND", MAGENTA, "Starting React frontend on http://localhost:5173...")
    frontend_cmd = ["cmd", "/c", npm_cmd, "run", "dev"] if sys.platform == "win32" else [npm_cmd, "run", "dev"]
    start_process(frontend_cmd, "Frontend (Vite)", "FRONTEND", MAGENTA, cwd=FRONTEND_DIR)

    log("SYSTEM", BOLD + GREEN, "Cortex is live! Terminal is locked. Press Ctrl+C at any time to exit.")

    # 4. Keep terminal occupied in foreground until Ctrl+C
    try:
        while True:
            time.sleep(0.5)
            # If any core service exits unexpectedly, stop the full stack.
            for process, name in active_processes:
                if process.poll() is not None and not is_shutting_down:
                    log("SYSTEM", RED, f"{name} exited unexpectedly with code {process.returncode}.")
                    shutdown_all()
    except KeyboardInterrupt:
        shutdown_all()


if __name__ == "__main__":
    main()
