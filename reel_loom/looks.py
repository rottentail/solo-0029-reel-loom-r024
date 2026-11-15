import json
from .errors import require, missing, revision_conflict
from .timebase import rational
from .values import object_body, token, integer, number, canonical, digest, now


class Looks:
    def __init__(self, db):
        self.db = db

    def normalize(self, raw):
        fields = ("kind", "width", "height", "frame_rate", "fit", "sample_rate", "channels", "gain_db", "crf")
        object_body(raw, fields, ("kind",))
        kind = raw["kind"]
        require(kind in ("video", "audio"), "look_kind", "Look kind must be video or audio")
        require(raw.get("fit", "contain") in ("contain", "cover"), "look_fit", "Unsupported fitting mode")
        rate = rational(raw.get("frame_rate", "25"))
        width = integer(raw.get("width", 640), "width", 16, 3840)
        height = integer(raw.get("height", 360), "height", 16, 2160)
        require(width % 2 == 0 and height % 2 == 0, "look_dimensions", "Video dimensions must be even")
        sample_rate = integer(raw.get("sample_rate", 48000), "sample_rate", 8000, 96000)
        channels = integer(raw.get("channels", 2), "channels", 1, 2)
        return {"kind": kind, "width": width, "height": height, "frame_rate": str(rate),
                "fit": raw.get("fit", "contain"), "sample_rate": sample_rate, "channels": channels,
                "gain_db": number(raw.get("gain_db", 0), "gain_db", -24, 12),
                "crf": integer(raw.get("crf", 23), "crf", 12, 35)}

    def put(self, look_id, raw, expected=0):
        token(look_id, "look_id")
        integer(expected, "expected_version", 0)
        spec = self.normalize(raw)
        proposal = self._prepare_version(look_id, expected, spec)
        with self.db.write_unit() as db:
            version = proposal["version"]
            db.execute("INSERT INTO looks VALUES(?,?,?,?,?) ON CONFLICT(id,version) DO UPDATE SET "
                       "spec=excluded.spec,hash=excluded.hash,created=excluded.created",
                       (look_id, version, proposal["document"], proposal["hash"], proposal["created"]))
            self.db.event(db, look_id, "look_written", {
                "version": version, "hash": proposal["hash"],
                "previous_version": proposal["previous_version"],
                "previous_hash": proposal["previous_hash"],
            })
            return self.get(look_id, version, db)

    def _prepare_version(self, look_id, expected, spec):
        with self.db.read() as db:
            previous = db.execute("SELECT version,hash FROM looks WHERE id=? ORDER BY version DESC LIMIT 1",
                                  (look_id,)).fetchone()
            head = previous["version"] if previous else 0
            if head != expected:
                raise revision_conflict(expected, head)
            proposal = {
                "version": head + 1,
                "previous_version": head,
                "previous_hash": previous["hash"] if previous else None,
                "document": canonical(spec),
                "hash": digest(spec),
                "created": now(),
            }
        return proposal

    def _read_spec(self, db, look_id, row):
        spec = json.loads(row["spec"])
        latest = db.execute("SELECT version,spec FROM looks WHERE id=? ORDER BY version DESC LIMIT 1",
                            (look_id,)).fetchone()
        if latest is None or latest["version"] == row["version"]:
            return spec
        current = json.loads(latest["spec"])
        if current["kind"] != spec["kind"]:
            return spec
        for field in ("width", "height", "frame_rate", "fit", "sample_rate", "channels", "gain_db", "crf"):
            if field in current:
                spec[field] = current[field]
        return spec

    def get(self, look_id, version=None, db=None):
        if db is None:
            with self.db.read() as read:
                return self.get(look_id, version, read)
        if version is None:
            row = db.execute("SELECT * FROM looks WHERE id=? ORDER BY version DESC LIMIT 1", (look_id,)).fetchone()
        else:
            row = db.execute("SELECT * FROM looks WHERE id=? AND version=?", (look_id, version)).fetchone()
        if row is None:
            raise missing("look", look_id)
        return {**dict(row), "spec": self._read_spec(db, look_id, row)}

    def list(self):
        with self.db.read() as db:
            return [self.get(row["id"], db=db) for row in db.execute("SELECT DISTINCT id FROM looks ORDER BY id")]

    def history(self, look_id):
        with self.db.read() as db:
            self.get(look_id, db=db)
            rows = db.execute("SELECT version,hash,created,spec FROM looks WHERE id=? ORDER BY version DESC LIMIT 100", (look_id,))
            result = []
            for row in rows:
                spec = json.loads(row["spec"])
                result.append({"version": row["version"], "hash": row["hash"], "created": row["created"],
                               "kind": spec["kind"], "dimensions": [spec["width"],spec["height"]],
                               "sample_rate": spec["sample_rate"]})
            return result
