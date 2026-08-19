import os
import sys
import time
import socket
import logging
import subprocess
import signal
from typing import Optional, Set

def is_port_in_use(port: int, host: str = "127.0.0.1") -> bool:
    """Check if a network port is currently in use."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
            return False
        except (OSError, socket.error):
            return True

def get_pids_on_port(port: int) -> Set[int]:
    """Find all process IDs listening or connected on a given TCP port."""
    pids: Set[int] = set()
    current_pid = os.getpid()

    if sys.platform == "win32":
        try:
            output = subprocess.check_output(
                "netstat -ano -p tcp",
                shell=True,
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in output.splitlines():
                parts = line.strip().split()
                if len(parts) >= 5 and parts[0].upper() == "TCP":
                    local_addr = parts[1]
                    pid_str = parts[4]
                    if local_addr.rsplit(":", 1)[-1] == str(port):
                        if pid_str.isdigit():
                            pid = int(pid_str)
                            if pid > 0 and pid != current_pid:
                                pids.add(pid)
        except Exception:
            pass
    else:
        # Unix / Linux / macOS
        try:
            output = subprocess.check_output(
                ["lsof", "-t", f"-i:{port}"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            for line in output.splitlines():
                line = line.strip()
                if line.isdigit():
                    pid = int(line)
                    if pid > 0 and pid != current_pid:
                        pids.add(pid)
        except Exception:
            pass

    return pids

def kill_port(port: int, host: str = "127.0.0.1", logger: Optional[logging.Logger] = None, max_wait: float = 2.0) -> bool:
    """Terminates processes occupying the specified port and waits until free.
    
    Returns True if the port was freed or was already free, False otherwise.
    """
    pids = get_pids_on_port(port)
    if not pids and not is_port_in_use(port, host=host):
        return True

    if logger:
        if pids:
            logger.info(f"Port {port} is in use by PID(s): {list(pids)}. Terminating...")
        else:
            logger.info(f"Port {port} is occupied. Attempting to free it...")

    for pid in pids:
        try:
            if sys.platform == "win32":
                subprocess.run(
                    f"taskkill /F /PID {pid}",
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            else:
                os.kill(pid, signal.SIGKILL)
            if logger:
                logger.info(f"Terminated process {pid} on port {port}")
        except Exception as e:
            if logger:
                logger.warning(f"Could not kill process {pid}: {e}")

    # On Unix, also try fuser as fallback if still occupied
    if sys.platform != "win32" and is_port_in_use(port, host=host):
        try:
            subprocess.run(
                ["fuser", "-k", "-9", f"{port}/tcp"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass

    # Wait for the OS to release the socket
    start_time = time.time()
    while time.time() - start_time < max_wait:
        if not is_port_in_use(port, host=host):
            if logger:
                logger.info(f"Port {port} is now available.")
            return True
        time.sleep(0.1)

    return not is_port_in_use(port, host=host)
