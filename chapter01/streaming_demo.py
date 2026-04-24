"""
1.4.3 - 流式输出演示
展示Stream模式下逐Token输出的效果。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")

print("--- 流式输出演示 ---\n")

stream = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "user", "content": "请简要介绍Python语言的三个特点。"},
    ],
    stream=True,
)

for chunk in stream:
    delta = chunk.choices[0].delta
    if delta.content:
        print(delta.content, end="", flush=True)

print("\n\n--- 输出完成 ---")
