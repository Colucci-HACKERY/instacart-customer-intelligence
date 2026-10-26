"""Stream original orders into a separate, atomically published cadence artifact."""
import csv
import hashlib
import json
import math
import os
import sqlite3
import tempfile
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.core.management.base import CommandError

VERSION = 1
APPLICATION_ID = 0x49524331


def artifact_path():
    return Path(getattr(settings, "RETENTION_ARTIFACT_PATH", settings.BASE_DIR / "artifacts" / "retention-v1.sqlite3"))


def histogram_median(counts):
    total = sum(counts)
    if not total:
        return None
    positions = ((total - 1) // 2, total // 2)
    selected = []
    cumulative = 0
    for value, count in enumerate(counts):
        for position in positions:
            if cumulative <= position < cumulative + count:
                selected.append(value)
        cumulative += count
    return sum(selected) / 2


def positive_integer(value):
    if not value or not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 2147483647:
        raise ValueError("expected a positive whole-number identifier")
    return int(value)


def recorded_gap(value):
    if value == "":
        return None, "missing"
    try:
        number = float(value)
        if not math.isfinite(number) or number < 0 or number > 30 or not number.is_integer():
            raise ValueError
        return int(number), "valid"
    except (TypeError, ValueError):
        return None, "invalid"


def summarize_customer(user_id, orders):
    """Sort by order_number only. Invalid sequences never contribute gap metrics."""
    base = {"user_id":user_id, "source_orders":len(orders), "sequence_valid":0,
            "gap_n":0, "missing_gaps":0, "invalid_gaps":0, "capped_gaps":0,
            "mean_gap":None, "median_gap":None, "within7":0, "within14":0,
            "latest_gap":None, "earlier_mean":None, "earlier_n":0,
            "comparison":"invalid_sequence", "latest_number":None,
            "latest_eval_set":None, "unexpected_first_gap":0}
    sequences = [row["number"] for row in orders]
    valid_sequence = (
        all(number is not None for number in sequences)
        and sorted(sequences) == list(range(1, len(orders)+1))
        and len({row["order_id"] for row in orders}) == len(orders)
    )
    if not valid_sequence:
        return base, [0]*31
    ordered = sorted(orders, key=lambda row:row["number"])
    base["sequence_valid"] = 1
    base["latest_number"] = ordered[-1]["number"]
    base["latest_eval_set"] = ordered[-1]["eval_set"]
    base["unexpected_first_gap"] = int(ordered[0]["raw_gap"] != "")
    counts = [0]*31
    earlier = []
    for index, row in enumerate(ordered[1:], start=1):
        gap, status = recorded_gap(row["raw_gap"])
        if status != "valid":
            base[status+"_gaps"] += 1
            continue
        counts[gap] += 1
        if index == len(ordered)-1:
            base["latest_gap"] = gap
        else:
            earlier.append(gap)
    n = sum(counts)
    base.update(gap_n=n, capped_gaps=counts[30], mean_gap=sum(value*count for value,count in enumerate(counts))/n if n else None,
                median_gap=histogram_median(counts), within7=sum(counts[:8]), within14=sum(counts[:15]),
                earlier_n=len(earlier), earlier_mean=sum(earlier)/len(earlier) if earlier else None)
    if len(ordered) < 2:
        base["comparison"] = "insufficient_history"
    elif base["latest_gap"] is None:
        base["comparison"] = "latest_gap_unavailable"
    elif len(earlier) < 2:
        base["comparison"] = "insufficient_history"
    elif 30 in earlier or base["latest_gap"] == 30:
        base["comparison"] = "capped_comparison"
    else:
        # Missing earlier gaps are excluded and the baseline sample size is shown.
        difference = base["latest_gap"] * len(earlier) - sum(earlier)
        base["comparison"] = "longer_recorded_gap" if difference > 0 else ("shorter_recorded_gap" if difference < 0 else "same_recorded_gap")
    return base, counts


def build_retention(orders_csv, output=None, progress=lambda message:None):
    source = Path(orders_csv).resolve()
    target = Path(output or artifact_path()).resolve()
    protected = {Path(settings.BASE_DIR / "db.sqlite3").resolve(), source}
    database = str(settings.DATABASES["default"]["NAME"])
    if database and not database.startswith("file:") and database != ":memory:":
        protected.add(Path(database).resolve())
    if target in protected:
        raise CommandError("The cadence artifact cannot overwrite the application database or source CSV.")
    if target.exists():
        try:
            with closing(sqlite3.connect(target.as_uri()+"?mode=ro", uri=True)) as existing:
                if existing.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                    raise ValueError("not a retention artifact")
        except (sqlite3.Error, ValueError) as exc:
            raise CommandError("Refusing to overwrite an unrelated file.") from exc
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=".retention-", suffix=".sqlite3", dir=target.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    store = None
    try:
        store = sqlite3.connect(temporary)
        store.execute(f"PRAGMA application_id={APPLICATION_ID}")
        store.execute(f"PRAGMA user_version={VERSION}")
        store.execute("PRAGMA cache_size=-16384")
        store.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        store.execute("""CREATE TABLE cadence (
            user_id INTEGER PRIMARY KEY, source_orders INTEGER, sequence_valid INTEGER,
            gap_n INTEGER, missing_gaps INTEGER, invalid_gaps INTEGER, capped_gaps INTEGER,
            mean_gap REAL, median_gap REAL, within7 INTEGER, within14 INTEGER,
            latest_gap INTEGER, earlier_mean REAL, earlier_n INTEGER, comparison TEXT,
            latest_number INTEGER, latest_eval_set TEXT, unexpected_first_gap INTEGER)""")
        store.execute("CREATE TABLE gap_histogram (gap INTEGER PRIMARY KEY, count INTEGER NOT NULL)")
        global_histogram = [0]*31
        coverage = Counter()
        eval_sets = Counter()
        comparisons = Counter()
        digest = hashlib.sha256()
        pending = []
        current_user = None
        block = []
        def finish():
            if current_user is None:
                return
            record, histogram = summarize_customer(current_user, block)
            coverage["source_customers"] += 1
            coverage["valid_sequence_customers"] += record["sequence_valid"]
            coverage["invalid_sequence_customers"] += not record["sequence_valid"]
            coverage["excluded_sequence_rows"] += len(block) if not record["sequence_valid"] else 0
            coverage["customers_with_gaps"] += record["gap_n"] > 0
            coverage["missing_gaps"] += record["missing_gaps"]
            coverage["invalid_gaps"] += record["invalid_gaps"]
            coverage["unexpected_first_gaps"] += record["unexpected_first_gap"]
            coverage["first_orders_excluded"] += record["sequence_valid"]
            coverage["eligible_gap_slots"] += len(block)-1 if record["sequence_valid"] else 0
            comparisons[record["comparison"]] += 1
            for gap, count in enumerate(histogram):
                global_histogram[gap] += count
            pending.append(tuple(record.values()))
            if len(pending) >= 1000:
                store.executemany("INSERT INTO cadence VALUES ("+",".join(["?"]*18)+")", pending)
                pending.clear()
            if coverage["source_customers"] % 25000 == 0:
                progress(f"Summarized {coverage['source_customers']:,} customers...")
        source_before = source.stat()
        with source.open("rb") as raw:
            def lines():
                for index, line in enumerate(raw):
                    digest.update(line)
                    yield line.decode("utf-8-sig" if index == 0 else "utf-8")
            reader = csv.DictReader(lines())
            required = {"order_id","user_id","eval_set","order_number","order_dow","order_hour_of_day","days_since_prior_order"}
            if not required.issubset(reader.fieldnames or []):
                raise CommandError("orders.csv is missing required original timing/sequence columns.")
            for line, row in enumerate(reader, start=2):
                if None in row or any(value is None for value in row.values()):
                    raise CommandError(f"Malformed CSV row {line}; previous artifact preserved.")
                try:
                    user_id = positive_integer(row["user_id"])
                    order_id = positive_integer(row["order_id"])
                except ValueError as exc:
                    raise CommandError(f"Invalid customer/order identifier at row {line}.") from exc
                if current_user is not None and user_id < current_user:
                    raise CommandError("Source must be grouped in ascending user_id; previous artifact preserved.")
                if user_id != current_user:
                    finish()
                    current_user = user_id
                    block = []
                try:
                    number = positive_integer(row["order_number"])
                except ValueError:
                    number = None
                if row["eval_set"] not in ("prior","train","test"):
                    raise CommandError(f"Unknown eval_set at row {line}.")
                block.append({"order_id":order_id,"number":number,"raw_gap":row["days_since_prior_order"],"eval_set":row["eval_set"]})
                if len(block) > 10000:
                    raise CommandError("Customer block exceeds the safety limit of 10,000 orders.")
                coverage["source_rows"] += 1
                eval_sets[row["eval_set"]] += 1
            finish()
        if not coverage["source_rows"]:
            raise CommandError("Source has no orders; previous artifact preserved.")
        if pending:
            store.executemany("INSERT INTO cadence VALUES ("+",".join(["?"]*18)+")", pending)
        source_after = source.stat()
        if (source_before.st_size, source_before.st_mtime_ns) != (source_after.st_size, source_after.st_mtime_ns):
            raise CommandError("Source changed during build; previous artifact preserved.")
        store.executemany("INSERT INTO gap_histogram VALUES (?,?)", enumerate(global_histogram))
        summary = dict(coverage)
        summary.update(format_version=VERSION, built_at=datetime.now(timezone.utc).isoformat(),
            source_name=source.name, source_path=str(source), source_sha256=digest.hexdigest(),
            source_size=source_after.st_size, source_mtime_ns=source_after.st_mtime_ns,
            eval_sets=dict(eval_sets), comparison_counts=dict(comparisons),
            valid_gaps=sum(global_histogram), capped_gaps=global_histogram[30],
            mean_gap=sum(gap*count for gap,count in enumerate(global_histogram))/sum(global_histogram) if sum(global_histogram) else None,
            median_gap=histogram_median(global_histogram), within7=sum(global_histogram[:8]), within14=sum(global_histogram[:15]))
        summary["share7"] = summary["within7"]*100/summary["valid_gaps"] if summary["valid_gaps"] else None
        summary["share14"] = summary["within14"]*100/summary["valid_gaps"] if summary["valid_gaps"] else None
        store.execute("INSERT INTO metadata VALUES ('summary',?)", (json.dumps(summary),))
        for field in ("source_orders", "mean_gap", "latest_gap", "comparison"):
            direction = "ASC" if field == "comparison" else "DESC"
            store.execute(f"CREATE INDEX cadence_{field}_idx ON cadence ({field} {direction},user_id)")
        store.commit()
        store.close()
        store = None
        os.replace(temporary, target)
        return summary
    except (OSError, UnicodeError, csv.Error, sqlite3.Error) as exc:
        raise CommandError(f"Cadence build failed; previous artifact preserved: {exc}") from exc
    finally:
        if store is not None:
            store.close()
        if temporary.exists():
            temporary.unlink()
