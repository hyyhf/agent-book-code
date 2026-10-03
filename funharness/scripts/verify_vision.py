"""Opt-in real DeepSeek vision regression (makes billable API calls).

Run: python -m funharness.scripts.verify_vision
Requires DEEPSEEK_API_KEY; --key-stdin accepts a key over stdin instead.
No credentials, fixtures, payloads, or session files survive the temporary run.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont

from funharness.src.agent import FunHarnessAgent
from funharness.src.core.llm import call_with_retry, sanitize_messages_for_api
from funharness.src.core.session import Session, SessionManager
from funharness.src.core.subagent import SubAgent
from funharness.src.core.tools import ToolRegistry, registry, tool_read_file


def make_fixture(path: Path, color: str) -> str:
    code = secrets.token_hex(4).upper()
    image = Image.new("RGB", (960, 520), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 68)
    except OSError:
        font = ImageFont.load_default(size=68)
    draw.text((60, 55), code, fill="black", font=font)
    draw.rectangle((80, 230, 290, 420), fill=color)
    draw.ellipse((600, 230, 790, 420), fill="black")
    image.save(path)
    return code


def limited_registry(*names):
    selected = ToolRegistry()
    selected._tools = {name: registry.list_tools()[name] for name in names}
    return selected


def check_answer(answer: str, code: str, color: str):
    assert code in answer.upper(), "Model did not read the random code from pixels"
    assert color in answer.lower(), "Model did not identify the rectangle color"


def last_answer(messages):
    return next(m["content"] for m in reversed(messages) if m["role"] == "assistant" and m.get("content"))


def run_agent(client, tool_name, path, *, attached=False, detail=None):
    names = ("tool_list_attachments", "tool_read_attachment") if attached else (tool_name,)
    agent = FunHarnessAgent(mode="auto", model="deepseek-flash", llm_client=client,
                            tool_registry=limited_registry(*names))
    try:
        target = "the attached image"
        if attached:
            agent.attachments.add(path)
        else:
            target = f"the image at {path.name} using {tool_name}"
        detail_instruction = f" Pass detail='{detail}' to the reading tool." if detail else ""
        agent.run(
            f"Inspect {target}. You must read its pixels with a tool. "
            "Reply in English with the exact code printed at the top, the rectangle's color, "
            "and a one-sentence description of the other shape. Do not infer from filenames."
            + detail_instruction
        )
        assert any(m["role"] == "tool" and isinstance(m["content"], list)
                   and any(b.get("type") == "image" for b in m["content"]) for m in agent.messages)
        if detail:
            assert any(m["role"] == "tool" and isinstance(m["content"], list)
                       and any(b.get("type") == "image" and b.get("detail") == detail for b in m["content"]) for m in agent.messages)
        return last_answer(agent.messages), agent.messages, agent.cost_tracker.total_tokens
    finally:
        agent.scheduler.stop()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--key-stdin", action="store_true")
    args = parser.parse_args()
    api_key = sys.stdin.readline().strip() if args.key_stdin else os.environ.get("DEEPSEEK_API_KEY", "")
    if not api_key:
        raise SystemExit("DEEPSEEK_API_KEY or --key-stdin is required")
    previous = Path.cwd()
    report = {"model": "deepseek-flash", "checks": []}
    with OpenAI(api_key=api_key, base_url="https://api.deepseek.com", timeout=90, max_retries=1) as client:
        with tempfile.TemporaryDirectory(prefix="funharness-vision-") as tmp:
            try:
                os.chdir(tmp)
                for case, tool_name, color, attached, detail in (
                    ("file_image", "tool_read_file", "red", False, None),
                    ("file_image_high", "tool_read_file", "blue", False, "high"),
                    ("attachment_image", "tool_read_attachment", "green", True, None),
                ):
                    path = Path("sample.png")
                    code = make_fixture(path, color)
                    answer, messages, tokens = run_agent(client, tool_name, path, attached=attached, detail=detail)
                    check_answer(answer, code, color)
                    report["checks"].append({"case": case, "passed": True, "tokens": tokens, "answer": answer})
                    print(json.dumps(report["checks"][-1], ensure_ascii=False), flush=True)

                # Replay with the final answer removed: the API must read the
                # original snapshot even after the source image is overwritten.
                session = Session(messages=messages[:-1])
                manager = SessionManager()
                manager.save(session)
                make_fixture(path, "purple")
                restored = manager.load(session.id)
                restored.messages.append({"role": "user", "content": (
                    "Using the image pixels already returned above, report the exact code and rectangle color in English. "
                    "Do not call tools or re-read the file; describe the earlier visual observation."
                )})
                wire = sanitize_messages_for_api(restored.messages)
                assert any(isinstance(m.get("content"), list) and any(b["type"] == "image_url" for b in m["content"]) for m in wire)
                response = call_with_retry(restored.messages, [], model="deepseek-flash", llm_client=client)
                answer = response.choices[0].message.content or ""
                check_answer(answer, code, "green")
                report["checks"].append({"case": "session_replay_after_overwrite", "passed": True, "answer": answer})
                print(json.dumps(report["checks"][-1], ensure_ascii=False), flush=True)

                code = make_fixture(path, "orange")
                subagent = SubAgent("visual reviewer", model="deepseek-flash", llm_client=client,
                                    tool_registry=limited_registry("tool_read_file"))
                answer = subagent.run("Use tool_read_file to inspect sample.png. Reply in English with the exact code and rectangle color.")
                check_answer(answer, code, "orange")
                assert any(m["role"] == "tool" and isinstance(m["content"], list) for m in subagent.messages)
                report["checks"].append({"case": "subagent", "passed": True, "answer": answer})
                print(json.dumps(report["checks"][-1], ensure_ascii=False), flush=True)

                first = Path("first.png")
                second = Path("second.png")
                first_code = make_fixture(first, "red")
                second_code = make_fixture(second, "blue")
                calls = [{"id": f"visual_{i}", "type": "function", "function": {
                    "name": "tool_read_file", "arguments": json.dumps({"path": str(p)}),
                }} for i, p in enumerate((first, second))]
                parallel = [
                    {"role": "user", "content": "Read both images and report each exact code and rectangle color in English. Once both tool results arrive, answer without further tools."},
                    {"role": "assistant", "content": None, "reasoning_content": "I will inspect both images.", "tool_calls": calls},
                    *[{"role": "tool", "tool_call_id": tc["id"], "content": tool_read_file(str(p)).content}
                      for tc, p in zip(calls, (first, second))],
                ]
                response = call_with_retry(parallel, [], model="deepseek-flash", llm_client=client)
                answer = response.choices[0].message.content or ""
                check_answer(answer, first_code, "red")
                check_answer(answer, second_code, "blue")
                report["checks"].append({"case": "parallel_tool_images", "passed": True, "answer": answer})
                print(json.dumps(report["checks"][-1], ensure_ascii=False), flush=True)
            finally:
                os.chdir(previous)
    print(json.dumps({"model": report["model"], "passed": len(report["checks"]), "status": "PASS"}), flush=True)


if __name__ == "__main__":
    main()
