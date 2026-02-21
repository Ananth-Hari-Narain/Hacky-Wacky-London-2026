import os
from dotenv import load_dotenv, find_dotenv
from openai import OpenAI

load_dotenv(find_dotenv())  # reliably finds .env from project root

api_key = os.getenv("OPENAI_API_KEY")
if not api_key:
    raise RuntimeError("OPENAI_API_KEY not found. Put it in .env at the project root.")

client = OpenAI(api_key=api_key)

def call_model(messages, temperature=0.1) -> str:
    # Request JSON output to reduce parsing failures
    resp = client.chat.completions.create(
        model="gpt-4o",
        messages=messages,
        temperature=temperature,
        response_format={"type": "json_object"},
    )
    return resp.choices[0].message.content