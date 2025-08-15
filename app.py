# app.py
from pathlib import Path
from typing import List, Dict, Any, Optional
import posixpath
import threading
import time

from fabric import Connection
from litestar import Litestar, get, post
from litestar.config.cors import CORSConfig
from litestar.exceptions import HTTPException
from litestar.params import Body

# --- SSH connection settings ---
SSH_KEY_PATH = Path("./ssh-key-2025-04-22.key")
SSH_USER = "opc"
SSH_HOST = "79.76.126.31"  # Minecraft's 25565 is NOT used for SSH; keep SSH on 22
SSH_PORT = 22

# Connection pool for reusing SSH connections
_connection_pool: Optional[Connection] = None
_connection_lock = threading.Lock()
_last_used = 0

# Simple cache for directory listings
_cache: Dict[str, Dict[str, Any]] = {}
_cache_timestamps: Dict[str, float] = {}
_cache_lock = threading.Lock()
CACHE_TTL = 30  # Cache for 30 seconds

def get_ssh_connection() -> Connection:
    """Get a reusable SSH connection from the pool."""
    global _connection_pool, _last_used
    
    if not SSH_KEY_PATH.exists():
        raise FileNotFoundError(f"SSH key not found: {SSH_KEY_PATH.resolve()}")
    
    with _connection_lock:
        current_time = time.time()
        
        # Reuse connection if it exists and was used recently (within 5 minutes)
        if _connection_pool and (current_time - _last_used) < 300:
            try:
                # Test if connection is still alive
                _connection_pool.run("echo test", hide=True, timeout=5)
                _last_used = current_time
                return _connection_pool
            except:
                # Connection is dead, create a new one
                _connection_pool = None
        
        # Create new connection
        _connection_pool = Connection(
            host=SSH_HOST,
            user=SSH_USER,
            port=SSH_PORT,
            connect_timeout=15,
            connect_kwargs={"key_filename": str(SSH_KEY_PATH)},
        )
        _last_used = current_time
        return _connection_pool

def list_remote_directory(path: str = "~") -> Dict[str, Any]:
    """List directory contents with file type information."""
    # Check cache first
    with _cache_lock:
        current_time = time.time()
        if path in _cache and path in _cache_timestamps:
            if (current_time - _cache_timestamps[path]) < CACHE_TTL:
                return _cache[path]
    
    conn = get_ssh_connection()
    
    # Combine commands into a single SSH call for better performance
    if path == "~":
        command = "pwd && ls -la"
    else:
        command = f"cd '{path}' && pwd && ls -la"
    
    result = conn.run(command, hide=True)
    output_lines = result.stdout.strip().splitlines()
    
    # First line is the current path from pwd
    current_path = output_lines[0].strip()
    
    # Rest of the lines are from ls -la (skip the "total" line)
    ls_lines = output_lines[2:] if len(output_lines) > 2 else []
    
    entries = []
    for line in ls_lines:
        if not line.strip():
            continue
            
        parts = line.split()
        if len(parts) < 9:
            continue
            
        permissions = parts[0]
        name = " ".join(parts[8:])  # Handle names with spaces
        
        # Skip . and .. entries for cleaner display
        if name in [".", ".."]:
            continue
            
        is_directory = permissions.startswith('d')
        entries.append({
            "name": name,
            "is_directory": is_directory,
            "permissions": permissions
        })
    
    result_data = {
        "current_path": current_path,
        "entries": entries
    }
    
    # Cache the result
    with _cache_lock:
        _cache[path] = result_data
        _cache_timestamps[path] = time.time()
    
    return result_data

# Run handler in a worker thread so Fabric's blocking I/O doesn't block the event loop
@get("/list-directory", sync_to_thread=True)
def list_directory(path: str = "~") -> dict:
    try:
        return list_remote_directory(path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SSH error: {e}")

@post("/change-directory", sync_to_thread=True)
def change_directory(data: Dict[str, str] = Body()) -> dict:
    try:
        current_path = data.get("current_path", "~")
        target_name = data.get("target_name", "")
        
        if target_name == "..":
            # Navigate up one directory
            new_path = posixpath.dirname(current_path)
        else:
            # Navigate into subdirectory
            new_path = posixpath.join(current_path, target_name)
        
        return list_remote_directory(new_path)
    except FileNotFoundError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"SSH error: {e}")

# Keep the old endpoint for backward compatibility
@get("/list-home", sync_to_thread=True)
def list_home() -> dict:
    try:
        result = list_remote_directory("~")
        return {"home_dir_listing": [entry["name"] for entry in result["entries"]]}
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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app = Litestar(route_handlers=[list_home, list_directory, change_directory], cors_config=cors)
