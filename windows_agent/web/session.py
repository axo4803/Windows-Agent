"""One desktop task at a time, with cancellable browser approvals."""
from collections import deque
from dataclasses import dataclass, field
import json
import secrets
import threading
import time
from types import SimpleNamespace

from windows_agent import PROJECT_ROOT


ACTIVE = {"starting", "running", "awaiting_approval", "stopping"}


@dataclass
class Job:
    task: str
    options: dict
    id: str = field(default_factory=lambda: secrets.token_urlsafe(12))
    status: str = "starting"
    answer: str = ""
    error: str = ""
    approval: dict | None = None
    decision: bool | None = None
    agent: object = None
    cancel: threading.Event = field(default_factory=threading.Event)
    events: deque = field(default_factory=lambda: deque(maxlen=500))
    sequence: int = 0
    completed: bool = False
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None


def validate_options(payload):
    task = payload.get("task")
    if not isinstance(task, str) or not task.strip() or len(task) > 12000:
        raise ValueError("Enter a task between 1 and 12,000 characters.")
    model = payload.get("model", "qwen3.5:4b")
    if not isinstance(model, str) or not model.strip() or len(model) > 200:
        raise ValueError("Choose an installed Ollama model.")
    options = {"model": model.strip()}
    for name, default in (("read_only", False), ("strict_approval", False), ("offline", False)):
        value = payload.get(name, default)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be true or false.")
        options[name] = value
    for name, default, maximum in (("max_tools", 40, 200), ("max_turns", 30, 200), ("max_seconds", 600, 3600)):
        value = payload.get(name, default)
        if type(value) is not int or not 1 <= value <= maximum:
            raise ValueError(f"{name} must be between 1 and {maximum}.")
        options[name] = value
    return task.strip(), options


def make_agent(args, manager, job):
    # Import only when a task starts; opening the page does not initialize desktop tools.
    from desktop_agent import DesktopAgent

    class BrowserAgent(DesktopAgent):
        def log_event(self, event, **fields):
            super().log_event(event, **fields)
            manager.agent_event(job, event, fields)

        def show_response(self, text):
            manager.answer(job, text)

        def approve(self, description):
            manager.await_approval(job, description, self.check)

    return BrowserAgent(args)


def desktop_context():
    import uiautomation as auto
    return auto.UIAutomationInitializerInThread()


class Session:
    def __init__(self, agent_factory=make_agent, thread_context=desktop_context, initial_messages=None):
        self.condition = threading.Condition(threading.RLock())
        self.agent_factory = agent_factory
        self.thread_context = thread_context
        self.jobs = deque(maxlen=20)
        self.worker = None
        self.initial_messages = initial_messages or []

    def _emit(self, job, kind, **data):
        if "details" in data:
            encoded = json.dumps(data["details"], ensure_ascii=False, default=str)
            if len(encoded) > 16000:
                data["details"] = encoded[:16000] + "\n[Preview shortened; full result is in the local log.]"
        job.sequence += 1
        job.events.append({"id": job.sequence, "kind": kind, "time": time.time(), **data})

    def start(self, payload):
        task, options = validate_options(payload)
        with self.condition:
            if self.jobs and self.jobs[-1].status in ACTIVE:
                raise RuntimeError("A task is already running. Stop it before starting another.")
            job = Job(task, options)
            self.jobs.append(job)
            self._emit(job, "status", label="Starting local agent")
            self.worker = threading.Thread(target=self._run, args=(job,), daemon=True)
            self.worker.start()
            return job.id

    def _run(self, job):
        args = SimpleNamespace(**job.options, online_docs=not job.options["offline"],
                               fast_review=False, dynamic_routing=True,
                               allow_app=["settings", "taskmgr"], stop_file=str(PROJECT_ROOT / "desktop_agent.stop"))
        agent = None
        try:
            with self.thread_context():
                agent = self.agent_factory(args, self, job)
                with self.condition:
                    job.agent = agent
                    if job.cancel.is_set():
                        agent.stop.set()
                    else:
                        job.status = "running"
                    self.condition.notify_all()
                agent.check()
                agent.run(job.task)
        except KeyboardInterrupt:
            with self.condition:
                self._emit(job, "status", label="Task stopped")
        except Exception as exc:
            with self.condition:
                job.error = str(exc)[:2000] or type(exc).__name__
                self._emit(job, "error", label=job.error)
        finally:
            if agent is not None:
                agent.stop.set()
            with self.condition:
                if job.cancel.is_set():
                    job.status = "stopped"
                elif job.error:
                    job.status = "error"
                elif job.completed:
                    job.status = "completed"
                else:
                    job.status = "stopped"
                    self._emit(job, "status", label="Task ended without a verified final answer. Check the activity and local log.")
                job.approval = None
                job.finished_at = time.time()
                self.condition.notify_all()

    def agent_event(self, job, event, fields):
        with self.condition:
            if event == "run_started":
                self._emit(job, "status", label="Thinking with " + job.options["model"])
            elif event == "model_response":
                calls = fields.get("tool_calls") or []
                for call in calls:
                    self._emit(job, "tool_start", label=call["name"], details=call.get("arguments", {}))
                if not calls:
                    self._emit(job, "status", label="Checking the answer against your request")
            elif event in {"tool_result", "batch_step"}:
                # DesktopAgent has already stripped image data from these events.
                self._emit(job, "tool_result", label=fields.get("tool", "tool"),
                           details=fields.get("result", {}), call=fields.get("call"), step=fields.get("step"))
            elif event in {"tool_exception", "ollama_error"}:
                self._emit(job, "error", label=fields.get("error", "Tool failed"))
                if event == "ollama_error":
                    job.error = "Local Ollama request failed. " + fields.get("error", "")[:1500]
            elif event == "run_stopped":
                self._emit(job, "status", label=fields.get("reason", "Stopped"))
            elif event == "run_completed":
                job.completed = True
                self._emit(job, "status", label="Finished", calls=fields.get("tool_calls", 0))

    def answer(self, job, text):
        with self.condition:
            job.answer = str(text)[:24000]
            self._emit(job, "answer", label="Answer ready")

    def await_approval(self, job, description, check):
        with self.condition:
            check()
            if job.cancel.is_set():
                raise KeyboardInterrupt()
            job.approval = {"id": secrets.token_urlsafe(16), "description": str(description)[:4000]}
            job.decision = None
            job.status = "awaiting_approval"
            self._emit(job, "approval", label=job.approval["description"])
            try:
                while job.decision is None:
                    check()
                    if job.cancel.is_set():
                        raise KeyboardInterrupt()
                    self.condition.wait(timeout=0.1)
                check()
                if job.cancel.is_set():
                    raise KeyboardInterrupt()
                if not job.decision:
                    raise PermissionError("User denied this action")
            finally:
                job.approval = None
                job.status = "stopping" if job.cancel.is_set() else "running"

    def _current(self, identifier):
        if not self.jobs or self.jobs[-1].id != identifier:
            raise ValueError("This task is no longer current.")
        return self.jobs[-1]

    def decide(self, identifier, approval_id, approved):
        if type(approved) is not bool:
            raise ValueError("Approval must be true or false.")
        with self.condition:
            job = self._current(identifier)
            if job.cancel.is_set() or not job.approval or job.approval["id"] != approval_id or job.decision is not None:
                raise ValueError("This approval has expired.")
            job.decision = approved
            self._emit(job, "status", label="Action approved" if approved else "Action denied")
            self.condition.notify_all()

    def stop(self, identifier):
        with self.condition:
            job = self._current(identifier)
            if job.status in ACTIVE:
                job.cancel.set()
                job.status = "stopping"
                job.approval = None
                if job.agent:
                    job.agent.stop.set()
                self._emit(job, "status", label="Stopping; an action already dispatched may finish")
                self.condition.notify_all()

    def reset(self):
        with self.condition:
            if self.jobs and self.jobs[-1].status in ACTIVE:
                raise RuntimeError("Wait for the running task to finish before starting a new chat.")
            from windows_agent import context
            context.system_profile.save(context.HISTORY, {"exchanges": []})
            self.jobs.clear()
            self.initial_messages = []

    def snapshot(self, cursor=0, identifier=""):
        with self.condition:
            current = self.jobs[-1] if self.jobs else None
            same = current and current.id == identifier
            jobs = [{"id": j.id, "task": j.task, "answer": j.answer, "status": j.status,
                     "error": j.error, "model": j.options["model"], "options": j.options, "started_at": j.started_at,
                     "finished_at": j.finished_at} for j in self.jobs]
            return {"messages": self.initial_messages, "jobs": jobs,
                    "current": current.id if current else None,
                    "approval": current.approval if current else None,
                    "cursor": current.sequence if current else 0,
                    "events": [e for e in current.events if e["id"] > (cursor if same else 0)] if current else [],
                    "events_truncated": bool(current and current.events and (cursor if same else 0) < current.events[0]["id"] - 1)}

    def close(self):
        with self.condition:
            if self.jobs:
                self.stop(self.jobs[-1].id)
        if self.worker:
            self.worker.join(timeout=2)
