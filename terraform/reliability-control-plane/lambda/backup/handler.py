import gzip
import os
import shutil
import sqlite3
import tempfile
import time
import zipfile

import boto3

s3 = boto3.client("s3")
cloudwatch = boto3.client("cloudwatch")


def validate_backup():
    bucket = os.environ["BACKUP_BUCKET"]
    prefix = os.environ.get("BACKUP_PREFIX", "")
    objects = s3.list_objects_v2(Bucket=bucket, Prefix=prefix).get("Contents", [])
    objects = [item for item in objects if not item["Key"].endswith("/")]
    if not objects:
        return False
    newest = max(objects, key=lambda item: item["LastModified"])
    if time.time() - newest["LastModified"].timestamp() > float(os.environ["BACKUP_MAX_AGE_SECONDS"]):
        return False

    with tempfile.TemporaryDirectory() as directory:
        source = os.path.join(directory, "backup")
        target = os.path.join(directory, "database.sqlite")
        s3.download_file(bucket, newest["Key"], source)
        key = newest["Key"].lower()
        if key.endswith(".gz"):
            with gzip.open(source, "rb") as compressed, open(target, "wb") as database:
                shutil.copyfileobj(compressed, database)
        elif key.endswith(".zip"):
            with zipfile.ZipFile(source) as archive:
                members = [name for name in archive.namelist() if not name.endswith("/")]
                if not members:
                    return False
                with archive.open(members[0]) as compressed, open(target, "wb") as database:
                    shutil.copyfileobj(compressed, database)
        else:
            target = source
        with sqlite3.connect(f"file:{target}?mode=ro", uri=True) as connection:
            return connection.execute("PRAGMA quick_check").fetchone()[0].lower() == "ok"


def lambda_handler(event, context):
    healthy = False
    try:
        healthy = validate_backup()
    except Exception as error:
        print(f"backup validation failed: {error}")
    cloudwatch.put_metric_data(
        Namespace=os.environ["METRIC_NAMESPACE"],
        MetricData=[{"MetricName": "BackupHealthy", "Value": int(healthy), "Unit": "Count"}],
    )
    return {"backup_healthy": healthy}
