from openai import OpenAI

from config.settings import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)


def call_model(messages, temperature=0.1, model="gpt-4o",
               response_format=None) -> str:
    kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": response_format or {"type": "json_object"},
    }
    resp = client.chat.completions.create(**kwargs)
    return resp.choices[0].message.content
