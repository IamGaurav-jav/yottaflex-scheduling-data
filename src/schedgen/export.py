"""CSV / JSON / SQL writers, a typed CSV loader (so validation can run on exported files),
and the checksum manifest used to demonstrate reproducibility."""
import csv
import hashlib
import json
import os

from .schema import BY_NAME, GROUND_TRUTH, TABLE_ORDER

SQL_TYPES = {"str": "TEXT", "date": "TEXT", "ts": "TEXT", "int": "INTEGER", "float": "REAL", "bool": "INTEGER"}


def _csv_val(v):
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    return v


def write_csv(path, tab, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(tab.cols())
        for r in rows:
            w.writerow([_csv_val(r[c]) for c in tab.cols()])


def write_json(path, tab, rows):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[\n" + ",\n".join(json.dumps({c: r[c] for c in tab.cols()}, ensure_ascii=False)
                                    for r in rows) + "\n]\n")


def _parse(f, s):
    if s == "":
        return None
    if f.type == "int":
        return int(s)
    if f.type == "float":
        return float(s)
    if f.type == "bool":
        return s == "true"
    return s


def load_csv_dir(csv_dir):
    t = {}
    for name in TABLE_ORDER:
        tab = BY_NAME[name]
        with open(os.path.join(csv_dir, f"{name}.csv"), newline="", encoding="utf-8") as fh:
            rd = csv.DictReader(fh)
            fmap = {f.name: f for f in tab.fields}
            t[name] = [{c: _parse(fmap[c], row[c]) for c in rd.fieldnames} for row in rd]
    return t


def load_ground_truth(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------------------- SQL
def _sql_lit(v):
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "1" if v else "0"
    if isinstance(v, (int, float)):
        return repr(v)
    return "'" + str(v).replace("'", "''") + "'"


def ddl(tab):
    lines = []
    for f in tab.fields:
        s = f"  {f.name} {SQL_TYPES[f.type]}{' NOT NULL' if f.required else ''}"
        if f.enum:
            s += f" CHECK ({f.name} IS NULL OR {f.name} IN ({', '.join(_sql_lit(x) for x in f.enum)}))"
        if f.type in ("int", "float") and (f.min is not None or f.max is not None):
            conds = []
            if f.min is not None:
                conds.append(f"{f.name} >= {f.min}")
            if f.max is not None:
                conds.append(f"{f.name} <= {f.max}")
            s += f" CHECK ({f.name} IS NULL OR ({' AND '.join(conds)}))"
        lines.append(s)
    lines.append(f"  PRIMARY KEY ({', '.join(tab.pk)})")
    for f in tab.fields:
        if f.unique and (f.name,) != tab.pk:
            lines.append(f"  UNIQUE ({f.name})")
    for cols in tab.uniques:
        lines.append(f"  UNIQUE ({', '.join(cols)})")
    for cols, ref, rcols, _ in tab.fks:
        lines.append(f"  FOREIGN KEY ({', '.join(cols)}) REFERENCES {ref} ({', '.join(rcols)})")
    return f"CREATE TABLE {tab.name} (\n" + ",\n".join(lines) + "\n);\n"


def write_sql(out_dir, t):
    with open(os.path.join(out_dir, "schema.sql"), "w", encoding="utf-8") as fh:
        fh.write("-- Generated from schedgen.schema; SQLite dialect. Dates/timestamps are ISO-8601 TEXT.\n")
        fh.write("PRAGMA foreign_keys = ON;\n\n")
        for name in TABLE_ORDER:
            fh.write(ddl(BY_NAME[name]) + "\n")
    with open(os.path.join(out_dir, "seed.sql"), "w", encoding="utf-8") as fh:
        fh.write("-- Seed data. Load schema.sql first.\nPRAGMA foreign_keys = ON;\nBEGIN;\n")
        for name in TABLE_ORDER:
            tab = BY_NAME[name]
            cols = ", ".join(tab.cols())
            for r in t[name]:
                vals = ", ".join(_sql_lit(r[c]) for c in tab.cols())
                fh.write(f"INSERT INTO {name} ({cols}) VALUES ({vals});\n")
        fh.write("COMMIT;\n")


def write_dataset(out_dir, t, ground_truth):
    for sub in ("csv", "json", "sql"):
        os.makedirs(os.path.join(out_dir, sub), exist_ok=True)
    for name in TABLE_ORDER:
        write_csv(os.path.join(out_dir, "csv", f"{name}.csv"), BY_NAME[name], t[name])
        write_json(os.path.join(out_dir, "json", f"{name}.json"), BY_NAME[name], t[name])
    write_sql(os.path.join(out_dir, "sql"), t)
    write_csv(os.path.join(out_dir, "ground_truth.csv"), GROUND_TRUTH, ground_truth)
    write_json(os.path.join(out_dir, "ground_truth.json"), GROUND_TRUTH, ground_truth)


def checksums(out_dir):
    """sha256 of every file under out_dir except manifest/reports written afterwards."""
    skip = {"manifest.json", "reproducibility_report.json"}
    out = {}
    for root, _, files in os.walk(out_dir):
        for fn in sorted(files):
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, out_dir).replace(os.sep, "/")
            if rel in skip:
                continue
            h = hashlib.sha256()
            with open(p, "rb") as fh:
                h.update(fh.read())
            out[rel] = h.hexdigest()
    return dict(sorted(out.items()))
