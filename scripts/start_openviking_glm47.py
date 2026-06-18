"""Start the local OpenViking stack with GLM-4.7 semantic processing."""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.render_openviking_config import DEFAULT_OUTPUT, write_openviking_config


@dataclass
class ManagedProcess:
    """A child process plus the log handle backing stdout/stderr."""

    name: str
    process: subprocess.Popen
    log_path: Path
    log_handle: TextIO

    def close_log(self) -> None:
        if not self.log_handle.closed:
            self.log_handle.close()


def _tail_log(path: str | Path | None, *, max_chars: int = 4000) -> str:
    if path is None:
        return ""
    log_path = Path(path)
    if not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    return text[-max_chars:]


def _start_logged_process(
    name: str,
    cmd: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: str | Path,
) -> ManagedProcess:
    """Start a process detached from this shell's process group."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_handle = log_path.open("a", encoding="utf-8")
    try:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception:
        log_handle.close()
        raise
    return ManagedProcess(name=name, process=process, log_path=log_path, log_handle=log_handle)


def _wait_for_http(
    name: str,
    url: str,
    *,
    timeout: float,
    process: subprocess.Popen | None = None,
    log_path: str | Path | None = None,
) -> None:
    """Wait for an HTTP endpoint and fail early if its child process exits."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process is not None:
            returncode = process.poll()
            if returncode is not None:
                tail = _tail_log(log_path)
                detail = f"\nLast {name} log:\n{tail}" if tail else ""
                raise RuntimeError(
                    f"{name} exited before ready (exit={returncode}) while waiting for {url}.{detail}"
                )
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if 200 <= response.status < 500:
                    return
        except Exception as exc:
            last_error = exc
        time.sleep(1)

    tail = _tail_log(log_path)
    detail = f"\nLast {name} log:\n{tail}" if tail else ""
    raise TimeoutError(
        f"{name} not ready after {timeout:.0f}s at {url}; last_error={last_error}.{detail}"
    )


def _terminate_processes(processes: list[ManagedProcess]) -> None:
    for managed in reversed(processes):
        if managed.process.poll() is not None:
            managed.close_log()
            continue
        try:
            os.killpg(managed.process.pid, signal.SIGTERM)
        except Exception:
            managed.process.terminate()
        try:
            managed.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(managed.process.pid, signal.SIGKILL)
            except Exception:
                managed.process.kill()
            managed.process.wait(timeout=10)
        managed.close_log()


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="启动 GLM-4.7 OpenViking 本地服务")
    parser.add_argument("--config", default=DEFAULT_OUTPUT, help="生成并使用的 ov.conf 路径")
    parser.add_argument(
        "--skip-embedding",
        action="store_true",
        help="不启动本地 embedding endpoint，适合已有 11435 服务时使用",
    )
    parser.add_argument(
        "--openviking-bin",
        default=os.getenv("OPENVIKING_SERVER_BIN", "/tmp/openviking-runner/bin/openviking-server"),
        help="openviking-server 可执行文件路径",
    )
    parser.add_argument("--embedding-host", default="127.0.0.1")
    parser.add_argument("--embedding-port", default="11435")
    parser.add_argument("--server-host", default=os.getenv("OPENVIKING_SERVER_HOST", "127.0.0.1"))
    parser.add_argument("--server-port", default=os.getenv("OPENVIKING_SERVER_PORT", "1933"))
    parser.add_argument("--log-dir", default=".runtime/openviking/logs")
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=float(os.getenv("OPENVIKING_STARTUP_TIMEOUT", "180")),
        help="等待 embedding/server ready 的秒数，默认 180",
    )
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="ready 后保持前台运行，Ctrl-C 会停止本脚本启动的进程",
    )
    args = parser.parse_args()

    config_path = write_openviking_config(args.config)
    env = os.environ.copy()
    env["OPENVIKING_CONFIG_FILE"] = str(config_path)

    processes: list[ManagedProcess] = []
    embedding_health_url = f"http://{args.embedding_host}:{args.embedding_port}/health"
    server_ready_url = f"http://{args.server_host}:{args.server_port}/ready"

    try:
        if not args.skip_embedding:
            embedding = _start_logged_process(
                "embedding",
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "scripts.openviking_embedding_server:app",
                    "--host",
                    args.embedding_host,
                    "--port",
                    str(args.embedding_port),
                ],
                cwd=ROOT,
                env=env,
                log_path=Path(args.log_dir) / "embedding.log",
            )
            processes.append(embedding)
            _wait_for_http(
                "embedding",
                embedding_health_url,
                timeout=args.startup_timeout,
                process=embedding.process,
                log_path=embedding.log_path,
            )
        else:
            _wait_for_http("embedding", embedding_health_url, timeout=args.startup_timeout)

        server = _start_logged_process(
            "openviking",
            [
                args.openviking_bin,
                "--config",
                str(config_path),
                "--host",
                args.server_host,
                "--port",
                str(args.server_port),
            ],
            cwd=ROOT,
            env=env,
            log_path=Path(args.log_dir) / "openviking-server.log",
        )
        processes.append(server)
        _wait_for_http(
            "openviking",
            server_ready_url,
            timeout=args.startup_timeout,
            process=server.process,
            log_path=server.log_path,
        )
    except Exception:
        _terminate_processes(processes)
        raise

    print(f"OpenViking config: {config_path}")
    print(f"Logs: {Path(args.log_dir).resolve()}")
    print(f"Embedding ready: {embedding_health_url}")
    print(f"OpenViking ready: {server_ready_url}")
    print("Started processes:")
    for managed in processes:
        print(f"- {managed.name}: pid={managed.process.pid}, log={managed.log_path}")
    print("Stop with: kill " + " ".join(str(managed.process.pid) for managed in processes))

    if args.foreground:
        try:
            while all(managed.process.poll() is None for managed in processes):
                time.sleep(1)
        except KeyboardInterrupt:
            _terminate_processes(processes)
            return

    for managed in processes:
        managed.close_log()


if __name__ == "__main__":
    main()
