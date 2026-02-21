import os
from dotenv import load_dotenv, find_dotenv
from pymongo import MongoClient

load_dotenv(find_dotenv())

mongo_uri = os.getenv("MONGO_URI")
if not mongo_uri:
    raise RuntimeError("MONGO_URI not found. Put it in .env at the project root.")

client = MongoClient(mongo_uri)
db = client["grader"]
submissions = db["submissions"]

def save_submission(data: dict):
    submissions.insert_one(data)