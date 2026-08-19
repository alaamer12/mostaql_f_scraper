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
