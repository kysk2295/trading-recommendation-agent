from __future__ import annotations

import socket

with socket.create_connection(("1.1.1.1", 53), timeout=0.2):
    pass
