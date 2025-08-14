# app.py
from pathlib import Path
from typing import List

from fabric import Connection
from litestar import Litestar, get
from litestar.config.cors import CORSConfig
from litestar.exceptions import HTTPException  # <-- correct import

# --- SSH connection settings ---
SSH_KEY_PATH = Path("./ssh-key-2025-04-22.key")
SSH_USER = "opc"
SSH_HOST = "79.76.126.31"  # Minecraft's 25565 is NOT used for SSH; keep SSH on 22
SSH_PORT = 22

def list_remote_home() -> List[str]:
    """Open an SSH connection and return the remote home directory listing."""
    if not SSH_KEY_PATH.exists():
        raise FileNotFoundError(f"SSH key not found: {SSH_KEY_PATH.resolve()}")

    conn = Connection(
        host=SSH_HOST,
        user=SSH_USER,
        port=SSH_PORT,
        connect_timeout=15,
        connect_kwargs={"key_filename": str(SSH_KEY_PATH)},
    )

    # Run `ls` in the default login shell/home directory.
    result = conn.run("ls -1", hide=True)
    return [line for line in result.stdout.strip().splitlines() if line]

# Run handler in a worker thread so Fabric’s blocking I/O doesn’t block the event loop
@get("/list-home", sync_to_thread=True)
def list_home() -> dict:
    try:
        entries = list_remote_home()
        return {"home_dir_listing": entries}
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SSH error: {e}")

# CORS so your frontend at 8001 can call the API at 8000
cors = CORSConfig(
    allow_origins=[
        "http://127.0.0.1:8001",
        "http://localhost:8001",
    ],
    allow_methods=["GET"],
    allow_headers=["*"],
)

app = Litestar(route_handlers=[list_home], cors_config=cors)
