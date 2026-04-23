"""
1.2 - 多轮对话程序
手动管理对话历史，实现一个简单的多轮对话。
展示应用层如何弥补LLM无状态性的不足。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

messages = [
    {"role": "system", "content": "你是一位简洁有力的AI助手，回答控制在50字以内。"},
]

print("多轮对话演示 (输入 quit 退出)")
print("-" * 40)

while True:
    user_input = input("\n你: ")
    if user_input.strip().lower() == "quit":
        print("再见!")
        break

    messages.append({"role": "user", "content": user_input})

    response = client.chat.completions.create(
        model=MODEL,
        messages=messages,
    )

    assistant_reply = response.choices[0].message.content
    messages.append({"role": "assistant", "content": assistant_reply})

    print(f"助手: {assistant_reply}")
    print(f"  [当前对话历史: {len(messages)} 条消息, "
          f"Token: {response.usage.total_tokens}]")
