"""
1.4.3 - Chat Completions API 采样参数演示
对比不同temperature值对模型输出的影响。
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

client = OpenAI()
MODEL = os.getenv("OPENAI_MODEL_NAME", "deepseek-v4-flash")

prompt = "用一句话描述春天。"


def generate(temperature: float, n: int = 3) -> list[str]:
    """用指定temperature生成n个回复"""
    results = []
    for _ in range(n):
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=100,
        )
        results.append(response.choices[0].message.content)
    return results


print(f"提示词: {prompt}\n")

# temperature=0: 几乎确定性输出，每次结果高度一致
print("--- temperature=0 (确定性) ---")
for i, text in enumerate(generate(0.0), 1):
    print(f"  [{i}] {text}")

# temperature=1.0: 更加多样化和创造性
print("\n--- temperature=1.0 (多样性) ---")
for i, text in enumerate(generate(1.0), 1):
    print(f"  [{i}] {text}")

# temperature=1.5: 高度随机
print("\n--- temperature=1.5 (高随机性) ---")
for i, text in enumerate(generate(1.5), 1):
    print(f"  [{i}] {text}")
