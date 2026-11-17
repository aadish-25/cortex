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

    log("SYSTEM", BOLD + GREEN, "All services stopped cleanly. Terminal released. Goodbye!\n")
    sys.exit(0)


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

    # 1. Ensure Docker & infrastructure containers are online
    if not check_and_start_docker():
        log("DOCKER", BOLD + RED, "Startup halted: Required Docker containers could not be started.")
        log("DOCKER", YELLOW, "Please ensure Docker Desktop is running, then run 'python run.py' again.")
        sys.exit(1)

    # 2. Launch Backend (FastAPI / Uvicorn)
    python_exe = sys.executable
    backend_cmd = [
        python_exe,
        "-m",
        "uvicorn",
        "backend.main:app",
        "--reload",
        "--host",
        "0.0.0.0",
        "--port",
        "8000",
    ]
    log("BACKEND", GREEN, f"Starting FastAPI backend on http://localhost:8000 (PID pending)...")

    backend_proc = subprocess.Popen(
        backend_cmd,
        cwd=str(BASE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    active_processes.append((backend_proc, "Backend (FastAPI)"))
    threading.Thread(target=stream_output, args=(backend_proc.stdout, "BACKEND", GREEN), daemon=True).start()

    # 3. Launch Frontend (React + Vite, when Phase 8 is implemented)
    has_frontend = (FRONTEND_DIR / "package.json").exists()
    if has_frontend:
        npm_cmd = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
        log("FRONTEND", MAGENTA, f"Starting React frontend via '{npm_cmd} run dev' on http://localhost:5173...")
        frontend_proc = subprocess.Popen(
            [npm_cmd, "run", "dev"],
            cwd=str(FRONTEND_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        active_processes.append((frontend_proc, "Frontend (Vite)"))
        threading.Thread(target=stream_output, args=(frontend_proc.stdout, "FRONTEND", MAGENTA), daemon=True).start()
    else:
        log("FRONTEND", YELLOW, "Frontend directory not found yet (scheduled for Phase 8). Backend-only mode active.")
        log("FRONTEND", YELLOW, "Once frontend is initialized, it will automatically launch and stream here.")

    log("SYSTEM", BOLD + GREEN, "Cortex is live! Terminal is locked. Press Ctrl+C at any time to exit.")

    # 4. Keep terminal occupied in foreground until Ctrl+C
    try:
        while True:
            time.sleep(0.5)
            # If backend crashes unexpectedly, notify
            if backend_proc.poll() is not None and not is_shutting_down:
                log("BACKEND", RED, f"Backend process exited unexpectedly with code {backend_proc.returncode}.")
                shutdown_all()
    except KeyboardInterrupt:
        shutdown_all()


if __name__ == "__main__":
    main()
