import socket
import pytest
from src.utils.port_utils import is_port_in_use, kill_port, get_pids_on_port

def test_is_port_in_use_free():
    # Find a free port dynamically
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    
    # Once closed, it should not be in use
    assert not is_port_in_use(free_port)
    assert not is_port_in_use(free_port, host="127.0.0.1")

def test_is_port_in_use_occupied():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.listen(1)
        assert is_port_in_use(port)
        assert is_port_in_use(port, host="127.0.0.1")

def test_kill_port_on_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    # Port is free
    result = kill_port(port, host="127.0.0.1")
    assert result is True

def test_host_determination_logic():
    import os
    # Default without Railway env vars should be 127.0.0.1
    for key in ["RAILWAY_ENVIRONMENT", "RAILWAY_STATIC_URL", "RAILWAY_PROJECT_ID", "HOST"]:
        os.environ.pop(key, None)
    
    is_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PROJECT_ID"))
    default_host = "0.0.0.0" if is_railway else "127.0.0.1"
    host = os.environ.get("HOST", default_host)
    assert host == "127.0.0.1"

    # With Railway env var, should default to 0.0.0.0
    os.environ["RAILWAY_ENVIRONMENT"] = "production"
    is_railway = bool(os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RAILWAY_STATIC_URL") or os.environ.get("RAILWAY_PROJECT_ID"))
    default_host = "0.0.0.0" if is_railway else "127.0.0.1"
    host = os.environ.get("HOST", default_host)
    assert host == "0.0.0.0"

    # If HOST explicitly set, should respect HOST
    os.environ["HOST"] = "192.168.1.100"
    host = os.environ.get("HOST", default_host)
    assert host == "192.168.1.100"

    os.environ.pop("RAILWAY_ENVIRONMENT", None)
    os.environ.pop("HOST", None)
