
import json
import subprocess
import time

import uiautomation as auto
from ollama import Client

MODEL = "qwen3.5:4b"
client = Client(host="http://127.0.0.1:11434")

# For now, this agent can operate Calculator only.
# No shell commands, filesystem access, or admin privileges.

def calculator():
    window = auto.WindowControl(
        searchDepth=1, Name="Calculator"
    )
    if not window.Exists(2):
        subprocess.Popen(["calc.exe"])
        time.sleep(2)
    if not window.Exists(3):
        raise RuntimeError("Calculator not found")
    window.SetActive()
    return window


def inspect():
    window = calculator()
    buttons = []
    text = []

    for control, depth in auto.WalkControl(
        window, maxDepth=12
    ):
        name = control.Name.strip()
        if not name:
            continue
        if control.ControlTypeName == "ButtonControl":
            buttons.append(name)
        elif control.ControlTypeName == "TextControl":
            text.append(name)

    return {
        "buttons": sorted(set(buttons)),
        "display_text": text[-20:],
    }


def click_button(name):
    window = calculator()

    # Only click a named button actually inside Calculator.
    for control, depth in auto.WalkControl(
        window, maxDepth=12
    ):
        if (
            control.ControlTypeName == "ButtonControl"
            and control.Name == name
        ):
            control.Click()
            time.sleep(0.3)
            return {
                "clicked": name,
                "display": inspect()["display_text"],
            }

    return {"error": f"Button not found: {name}"}


tools = [
    {
        "type": "function",
        "function": {
            "name": "inspect_calculator",
            "description": "Read Calculator buttons and display",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click_calculator_button",
            "description": "Click a Calculator button by exact name",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}
                },
                "required": ["name"],
            },
        },
    },
]


def run_agent(task):
    messages = [
        {
            "role": "system",
            "content": (
                "You are a Windows Calculator agent. "
                "Use tools to complete the user's calculation. "
                "First inspect the calculator controls. "
                "Click buttons by their exact accessible names. "
                "Read the resulting display to verify the answer. "
                "Never claim to have clicked a button unless "
                "the tool confirms it. "
                "Finish with a concise verified result."
            ),
        },
        {"role": "user", "content": task},
    ]

    for step in range(25):
        response = client.chat(
            model=MODEL,
            messages=messages,
            tools=tools,
            options={"num_ctx": 8192, "temperature": 0},
        )

        msg = response.message
        messages.append(msg.model_dump(exclude_none=True))
        calls = msg.tool_calls or []

        if not calls:
            print("\nAGENT:", msg.content)
            return

        for call in calls:
            function = call.function.name
            args = call.function.arguments or {}

            if function == "inspect_calculator":
                result = inspect()
            elif function == "click_calculator_button":
                result = click_button(args.get("name", ""))
            else:
                result = {"error": "Unknown tool"}

            print(f"[Step {step + 1}] {function}")
            print(json.dumps(result, ensure_ascii=False)[:700])

            messages.append({
                "role": "tool",
                "tool_name": function,
                "content": json.dumps(result),
            })

    print("Stopped: step limit reached.")


if __name__ == "__main__":
    task = input("What should I do? ")
    run_agent(task)
