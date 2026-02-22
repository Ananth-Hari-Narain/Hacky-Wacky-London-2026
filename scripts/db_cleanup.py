"""
MongoDB inspection and cleanup utility.

Usage:
    python -m scripts.db_cleanup list-dbs
    python -m scripts.db_cleanup list-collections [--db question_bank]
    python -m scripts.db_cleanup count [--db question_bank] [--collection questions]
    python -m scripts.db_cleanup preview [--db question_bank] [--collection questions]
    python -m scripts.db_cleanup drop-collection [--db question_bank] [--collection questions]
    python -m scripts.db_cleanup drop-db [--db learning_engine]
    python -m scripts.db_cleanup clear [--db question_bank] [--collection questions]
"""
import argparse
import json

from models.db import get_client


def cmd_list_dbs():
    for db_name in sorted(get_client().list_database_names()):
        print(f"  {db_name}")


def cmd_list_collections(db_name: str):
    db = get_client()[db_name]
    collections = sorted(db.list_collection_names())
    if not collections:
        print(f"  (no collections in {db_name})")
        return
    for name in collections:
        count = db[name].estimated_document_count()
        print(f"  {name} ({count} docs)")


def cmd_count(db_name: str, collection_name: str):
    coll = get_client()[db_name][collection_name]
    print(coll.count_documents({}))


def cmd_preview(db_name: str, collection_name: str, limit: int):
    coll = get_client()[db_name][collection_name]
    for doc in coll.find({}, {"embedding": 0, "topic_vector": 0}).limit(limit):
        doc["_id"] = str(doc["_id"])
        if "updated_at" in doc:
            doc["updated_at"] = str(doc["updated_at"])
        print(json.dumps(doc, indent=2, default=str))
        print()


def cmd_clear(db_name: str, collection_name: str, filter_str: str | None):
    coll = get_client()[db_name][collection_name]
    filt = json.loads(filter_str) if filter_str else {}

    count = coll.count_documents(filt)
    if count == 0:
        print("No matching documents found.")
        return

    desc = f"all {count} documents" if not filter_str else f"{count} documents matching {filter_str}"
    confirm = input(f"Delete {desc} from {db_name}.{collection_name}? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return

    result = coll.delete_many(filt)
    print(f"Deleted {result.deleted_count} document(s)")


def cmd_drop_collection(db_name: str, collection_name: str):
    confirm = input(f"Drop collection {db_name}.{collection_name}? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return
    get_client()[db_name].drop_collection(collection_name)
    print(f"Dropped {db_name}.{collection_name}")


def cmd_drop_db(db_name: str):
    confirm = input(f"Drop entire database '{db_name}'? [y/N] ")
    if confirm.lower() != "y":
        print("Aborted.")
        return
    get_client().drop_database(db_name)
    print(f"Dropped database '{db_name}'")


def main():
    parser = argparse.ArgumentParser(description="MongoDB cleanup helpers")
    parser.add_argument("command", choices=[
        "list-dbs", "list-collections", "count", "preview",
        "clear", "drop-collection", "drop-db",
    ])
    parser.add_argument("--db", default="question_bank")
    parser.add_argument("--collection", default="questions")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--filter", default=None)
    args = parser.parse_args()

    match args.command:
        case "list-dbs":
            cmd_list_dbs()
        case "list-collections":
            cmd_list_collections(args.db)
        case "count":
            cmd_count(args.db, args.collection)
        case "preview":
            cmd_preview(args.db, args.collection, args.limit)
        case "clear":
            cmd_clear(args.db, args.collection, args.filter)
        case "drop-collection":
            cmd_drop_collection(args.db, args.collection)
        case "drop-db":
            cmd_drop_db(args.db)


if __name__ == "__main__":
    main()
