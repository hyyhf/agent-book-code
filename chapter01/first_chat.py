"""
1.4.4 - 你的第一个对话程序
演示Chat Completions API的基本用法和采样参数。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

# 加载 .env 文件中的 API 配置
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-chat")

response = client.chat.completions.create(
    model=MODEL,
    messages=[
        {"role": "system", "content": "你是一位友好的AI助手。"},
        {"role": "user", "content": "请用一句话介绍什么是AI智能体。"},
    ],
    temperature=0.7,
    max_tokens=200,
)

message = response.choices[0].message
print(f"助手回复: {message.content}")
print(f"Token用量: 输入={response.usage.prompt_tokens}, "
      f"输出={response.usage.completion_tokens}, "
      f"总计={response.usage.total_tokens}")
