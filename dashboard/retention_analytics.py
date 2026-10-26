"""Read a versioned cadence artifact and current RFM labels, with no database writes."""
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

from django.db import connection

from .retention_build import APPLICATION_ID, VERSION, artifact_path


class ArtifactUnavailable(Exception):
    pass


SORTS = {"customer":"c.user_id", "orders":"c.source_orders DESC, c.user_id",
         "mean":"c.mean_gap DESC, c.user_id",
         "latest":"c.latest_gap DESC, c.user_id"}
COMPARISONS = {
    "longer_recorded_gap":"Longer recorded latest gap", "shorter_recorded_gap":"Shorter recorded latest gap",
    "same_recorded_gap":"Same recorded latest gap", "capped_comparison":"Comparison limited by 30+ cap",
    "insufficient_history":"Insufficient comparison history", "latest_gap_unavailable":"Latest gap missing or invalid",
    "invalid_sequence":"Invalid source sequence",
}


@contextmanager
def open_artifact():
    store = None
    try:
        path = artifact_path().resolve()
        store = sqlite3.connect(path.as_uri()+"?mode=ro",uri=True)
        store.row_factory = sqlite3.Row
        if store.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID or store.execute("PRAGMA user_version").fetchone()[0] != VERSION:
            raise ArtifactUnavailable("Unsupported cadence artifact version. Rebuild using build_retention.")
        database = str(connection.settings_dict["NAME"])
        if not database.startswith("file:"):
            database = Path(database).resolve().as_uri()+"?mode=ro"
        # URI read-only for the application database; query_only also protects
        # shared-memory regression-test databases from accidental writes.
        store.execute("ATTACH DATABASE ? AS platform", (database,))
        store.execute("PRAGMA query_only=ON")
        yield store
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise ArtifactUnavailable("Cadence data is unavailable. Build or rebuild the retention artifact.") from exc
    finally:
        if store is not None:
            store.close()


def load_summary(store):
    row = store.execute("SELECT value FROM metadata WHERE key='summary'").fetchone()
    try:
        summary = json.loads(row[0])
        required={"format_version","valid_gaps","source_customers","mean_gap","median_gap","share7","share14","source_name","built_at","eval_sets"}
        if not isinstance(summary,dict) or not required.issubset(summary) or summary["format_version"] != VERSION:
            raise ValueError
        if any(not isinstance(summary[key],int) or summary[key] < 0 for key in ("valid_gaps","source_customers")):
            raise ValueError
        return SimpleNamespace(**summary)
    except (TypeError, KeyError, ValueError) as exc:
        raise ArtifactUnavailable("Cadence artifact metadata is invalid. Rebuild the artifact.") from exc


def distribution(store, total):
    counts = dict(store.execute("SELECT gap,count FROM gap_histogram ORDER BY gap"))
    buckets = []
    for lower,upper,label in ((0,0,"Same day"),(1,7,"1-7 days"),(8,14,"8-14 days"),(15,21,"15-21 days"),(22,29,"22-29 days"),(30,30,"30+ days")):
        count = sum(counts.get(gap,0) for gap in range(lower,upper+1))
        buckets.append({"label":label,"count":count,"share":count*100/total if total else 0})
    maximum = max((bucket["count"] for bucket in buckets),default=0)
    for bucket in buckets:
        bucket["bar_width"] = bucket["count"]*100/maximum if maximum else 0
    return buckets


class CadenceRecords:
    """Django Paginator-compatible, SQL-counted and sliced read-only artifact query."""
    def __init__(self,store,cleaned):
        self.store = store
        clauses=[]
        self.params=[]
        for key,sql in (("user_id","c.user_id = ?"),("segment","r.segment = ?"),("comparison","c.comparison = ?")):
            if cleaned.get(key):
                clauses.append(sql)
                self.params.append(cleaned[key])
        self.where = " WHERE "+" AND ".join(clauses) if clauses else ""
        self.join = " FROM cadence c LEFT JOIN platform.customer_rfm r ON r.user_id=c.user_id"
        self.order = SORTS.get(cleaned.get("sort"),SORTS["customer"])

    def count(self):
        return self.store.execute("SELECT COUNT(*)"+self.join+self.where,self.params).fetchone()[0]

    def __getitem__(self,key):
        if not isinstance(key,slice) or key.step is not None:
            raise TypeError("CadenceRecords supports only simple pagination slices")
        start=key.start or 0
        sql="SELECT c.*, r.segment"+self.join+self.where+" ORDER BY "+self.order+" LIMIT ? OFFSET ?"
        records=[]
        for row in self.store.execute(sql,(*self.params,key.stop-start,start)):
            record=SimpleNamespace(**dict(row))
            record.comparison_label=COMPARISONS.get(record.comparison,"Unavailable")
            record.share7=record.within7*100/record.gap_n if record.gap_n else None
            record.share14=record.within14*100/record.gap_n if record.gap_n else None
            records.append(record)
        return records


def coverage(store):
    joined=" FROM cadence c LEFT JOIN platform.customer_rfm r ON r.user_id=c.user_id"
    matched=store.execute("SELECT COUNT(*)"+joined+" WHERE r.user_id IS NOT NULL").fetchone()[0]
    unmatched=store.execute("SELECT COUNT(*)"+joined+" WHERE r.user_id IS NULL").fetchone()[0]
    rfm_only=store.execute("SELECT COUNT(*) FROM platform.customer_rfm r LEFT JOIN cadence c ON c.user_id=r.user_id WHERE c.user_id IS NULL").fetchone()[0]
    return {"matched_customers":matched,"source_without_rfm":unmatched,"rfm_without_source":rfm_only}
