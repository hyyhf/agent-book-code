"""
1.1 - 无状态性演示
展示LLM的无状态特征：每次API调用彼此独立，模型不会"记住"之前的对话。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")


def chat(messages: list[dict]) -> str:
    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
    )
    return response.choices[0].message.content


# --- 演示1: 两次独立调用，模型无法记住上一轮 ---
print("=== 演示1: 独立调用 ===")
reply1 = chat([{"role": "user", "content": "我叫张三，我是一名软件工程师。"}])
print(f"第1次回复: {reply1}\n")

reply2 = chat([{"role": "user", "content": "我的职业是什么?"}])
print(f"第2次回复: {reply2}\n")

# --- 演示2: 把对话历史带上，模型就能"记住" ---
print("=== 演示2: 携带对话历史 ===")
reply3 = chat([
    {"role": "user", "content": "我叫张三，我是一名软件工程师。"},
    {"role": "assistant", "content": reply1},
    {"role": "user", "content": "我的职业是什么?"},
])
print(f"第3次回复: {reply3}")
