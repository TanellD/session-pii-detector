"""Локальный индекс ГИС ЖКХ: только ID дома, тип, квартиры, состояние и регион."""
import argparse
from contextlib import closing
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import zipfile


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=Path("data/external/annotation-size-statistics/data_gisgkh_157_v20260917_parquet.zip"))
    parser.add_argument("--output", type=Path, default=Path("data/reference/gisgkh-address-index.sqlite"))
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Индекс уже существует; укажите новый путь, прежний индекс не переписывается")
    import pyarrow.parquet as pq
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".building.sqlite")
    if temporary.exists():
        parser.error("Промежуточный файл уже существует; проверьте предыдущий запуск")
    count = 0
    with zipfile.ZipFile(args.input) as archive, closing(sqlite3.connect(temporary)) as db:
        member = next(n for n in archive.namelist() if n.endswith(".parquet") and not n.startswith("__"))
        source = pq.ParquetFile(io.BytesIO(archive.read(member)))
        db.execute("CREATE TABLE houses (house_id TEXT, house_type TEXT, flats INTEGER, state TEXT, region TEXT)")
        db.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)")
        for batch in source.iter_batches(batch_size=20000, columns=["fias_id", "house_type", "flats", "life_cycle_stage", "region_name"]):
            values = [(r["fias_id"].casefold(), r["house_type"], r["flats"], r["life_cycle_stage"], r["region_name"])
                      for r in batch.to_pylist() if r["fias_id"]]
            db.executemany("INSERT INTO houses VALUES (?,?,?,?,?)", values)
            count += len(values)
        db.execute("CREATE INDEX house_id_lookup ON houses (house_id)")
        metadata = {"source_url": "https://tochno.st/datasets/gisgkh", "reference_period": "2026-08",
                    "dataset_version": "v20260917", "archive_sha256": hashlib.sha256(args.input.read_bytes()).hexdigest()}
        db.executemany("INSERT INTO metadata VALUES (?,?)", list(metadata.items()))
        db.commit()
    temporary.replace(args.output)
    print(json.dumps({"status": "built", "rows": count, "output": str(args.output), "source": metadata}))


if __name__ == "__main__":
    main()
