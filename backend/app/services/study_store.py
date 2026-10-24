"""Durable local studies, frozen screenshots, and atomic run updates."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from ..utils.study_images import inspect_image


def now():
    return datetime.now(timezone.utc).isoformat()


class StudyStore:
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        self.assets = self.root / "assets"
        self.assets.mkdir(exist_ok=True, mode=0o700)
        self.assets.chmod(0o700)
        with self.db() as db:
            db.execute("CREATE TABLE IF NOT EXISTS studies (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            rows = db.execute("SELECT id,payload FROM studies").fetchall()
            for study_id, payload in rows:
                data = json.loads(payload)
                if data["status"] in {"queued", "running"}:
                    try:
                        os.kill(data.get("worker_pid", -1), 0)
                    except (ProcessLookupError, OSError):
                        data.update(status="failed", error="The backend stopped during this study. Resume to continue from saved sessions.")
                        db.execute("UPDATE studies SET payload=? WHERE id=?", (json.dumps(data), study_id))

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.root / "studies.sqlite3", timeout=30)
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def create(self, spec, owner):
        data = {"id": "study_" + uuid.uuid4().hex[:12], "status": "draft", "schema_version": 1,
                "created_at": now(), "updated_at": now(), "spec": spec, "owner": owner,
                "requests_used": 0, "runs": [], "report": None, "error": None, "owner_version": 2}
        with self.db() as db:
            db.execute("INSERT INTO studies VALUES (?,?)", (data["id"], json.dumps(data)))
        return deepcopy(data)

    def get(self, study_id):
        with self.db() as db:
            row = db.execute("SELECT payload FROM studies WHERE id=?", (study_id,)).fetchone()
        if not row:
            raise KeyError("Study not found.")
        return json.loads(row[0])

    def list(self, owner):
        with self.db() as db:
            values = [json.loads(row[0]) for row in db.execute("SELECT payload FROM studies")]
        return sorted((v for v in values if v["owner"] == owner), key=lambda v: v["created_at"], reverse=True)

    def update(self, study_id, change):
        with self.db() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT payload FROM studies WHERE id=?", (study_id,)).fetchone()
            if not row:
                raise KeyError("Study not found.")
            data = json.loads(row[0])
            change(data)
            data["updated_at"] = now()
            db.execute("UPDATE studies SET payload=? WHERE id=?", (json.dumps(data), study_id))
        return deepcopy(data)

    def save_asset(self, content, source=None):
        info = inspect_image(content)
        digest = hashlib.sha256(content).hexdigest()
        path = self.assets / digest
        if not path.exists():
            temporary = self.assets / (digest + "." + uuid.uuid4().hex + ".tmp")
            temporary.write_bytes(content)
            temporary.replace(path)
        return {"id": digest, "sha256": digest, **info, "captured_at": now(), "source": source or {"kind": "upload"}}

    def asset(self, asset):
        if len(asset["id"]) != 64 or any(c not in "0123456789abcdef" for c in asset["id"]):
            raise ValueError("Invalid screenshot reference.")
        content = (self.assets / asset["id"]).read_bytes()
        if hashlib.sha256(content).hexdigest() != asset["sha256"]:
            raise ValueError("The frozen screenshot changed. Create a new study.")
        return content
