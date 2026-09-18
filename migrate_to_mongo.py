# One-time script: copies the chunks currently sitting in the local
# chunks_store.json file into MongoDB, so you don't have to re-upload
# admissions.txt etc. after switching to the database.
#
# Run this ONCE, after you've filled in MONGO_URI in
# .streamlit/secrets.toml, with:
#     python migrate_to_mongo.py

import json
import tomllib
import pymongo

with open(".streamlit/secrets.toml", "rb") as f:
    secrets = tomllib.load(f)

mongo_client = pymongo.MongoClient(secrets["MONGO_URI"])
collection = mongo_client["rcl_knowledge_assistant"]["chunks"]

with open("chunks_store.json", "r", encoding="utf-8") as f:
    chunks = json.load(f)

if len(chunks) == 0:
    print("chunks_store.json is empty - nothing to migrate.")
else:
    collection.insert_many(chunks)
    print("Moved " + str(len(chunks)) + " chunks into MongoDB.")

    names = sorted(set(c["source"] for c in chunks))
    for name in names:
        print("  - " + name)

    print("\nDone! You can now delete chunks_store.json if you like - ")
    print("the app no longer reads from it.")
