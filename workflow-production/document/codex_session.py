"""Private stdio Codex connection for a single serial worker. No thread resume."""
import json
import os
import re
import select
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path

SESSION_SECONDS = 300
MAX_RPC = 16 * 1024 * 1024
TRACE_LIMIT = 2 * 1024 * 1024
THREAD_ID = re.compile(r"[a-f0-9-]{36}")
DOC_CONFIG = {"features.shell_tool": False, "features.unified_exec": False,
              "features.apps": False, "features.plugins": False, "features.skill_search": False,
              "web_search": "disabled", "mcp_servers": {}}


class SessionError(Exception):
    pass


def executable_identity(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise SessionError("start_failed")
    st = path.stat()
    if not stat.S_ISREG(st.st_mode) or not os.access(path, os.X_OK):
        raise SessionError("start_failed")
    # Stat only detects a new installation. It doesn't select or pin a version.
    return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns)


def diagnostic(**values):
    # Only caller-supplied enums, measurements and counters. No prompt, paths or raw errors.
    print(json.dumps({"event": "codex_session", "utcMs": time.time_ns() // 1000000, **values}, separators=(",", ":")),
          file=__import__("sys").stderr, flush=True)


class CodexSession:
    def __init__(self, profile, stopped, *, ttl=SESSION_SECONDS):
        self.profile, self.stopped, self.ttl = profile, stopped, ttl
        self.child = self.trace = self.binary = self.binary_identity = None
        self.serial = self.seq = 0
        self.buffer = bytearray()
        self.born = 0
        self.last_finished = None

    def close(self):
        child = self.child
        if child is None:
            return
        if child.poll() is None:
            try:
                os.killpg(child.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        try:
            child.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=2)
        # Close any surviving descendants before admitting new work.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.stdin.close()
        child.stdout.close()
        if self.trace is not None:
            self.trace.close()
        self.child = self.trace = None
        self.buffer.clear()

    def expire_idle(self):
        if self.child is not None and time.monotonic() - self.born >= self.ttl:
            self.close()

    def _wait(self, deadline):
        if self.stopped.is_set():
            raise SessionError("cancelled")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise SessionError("timeout")
        return min(remaining, 0.1)

    def _send(self, value, deadline):
        fd = self.child.stdin.fileno()
        raw = memoryview((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode())
        if len(raw) > MAX_RPC:
            raise SessionError("input_limit")
        while raw:
            if not select.select([], [fd], [], self._wait(deadline))[1]:
                continue
            try:
                sent = os.write(fd, raw)
            except BlockingIOError:
                continue
            raw = raw[sent:]

    def _recv(self, deadline):
        fd = self.child.stdout.fileno()
        while True:
            self._wait(deadline)
            end = self.buffer.find(b"\n")
            if end >= 0:
                raw = self.buffer[:end]
                del self.buffer[:end+1]
                value = json.loads(raw)
                if type(value) is not dict:
                    raise SessionError("invalid_event")
                return value
            if len(self.buffer) >= MAX_RPC:
                raise SessionError("output_limit")
            if select.select([fd], [], [], self._wait(deadline))[0]:
                chunk = os.read(fd, 65536)
                if not chunk:
                    raise SessionError("disconnected")
                self.buffer.extend(chunk)

    def _request(self, method, params, deadline, on_event=None):
        self.seq += 1
        seq = self.seq
        self._send({"id": seq, "method": method, "params": params}, deadline)
        while True:
            msg = self._recv(deadline)
            if msg.get("id") == seq and "method" not in msg:
                if not isinstance(msg.get("result"), dict):
                    raise SessionError("rpc_error")
                return msg["result"]
            self._dispatch(msg, deadline, on_event)

    def _dispatch(self, msg, deadline, on_event):
        if "method" in msg and "id" in msg:
            # No approval prompts, side-effect requests or custom client tools.
            self._send({"id": msg["id"], "error": {"code": -32601, "message": "Worker does not grant client requests"}}, deadline)
            raise SessionError("unexpected_client_request")
        if on_event:
            on_event(msg)

    def _start(self, binary, identity, deadline):
        self.close()
        self.binary = binary
        self.binary_identity = identity
        flags = ["--disable", "apps"]
        env = {"HOME": str(Path.home()), "PATH": "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"}
        if self.profile == "document":
            env["TMPDIR"] = tempfile.gettempdir()
            for feature in ("shell_tool", "unified_exec", "plugins", "skill_search"):
                flags += ["--disable", feature]
            flags += ["-c", 'web_search="disabled"', "-c", "mcp_servers={}"]
        else:
            env["NO_COLOR"] = "1"
            env["RUST_LOG"] = "error,codex_otel.trace_safe=info,codex_otel.log_only=info"
            self.trace = tempfile.TemporaryFile()
        # A neutral working directory for the connection; each job gets its own cwd.
        try:
            self.child = subprocess.Popen([binary, "app-server", "--listen", "stdio://", *flags],
                                          cwd=env.get("TMPDIR", "/private/tmp"), env=env, stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE,
                                          stderr=self.trace if self.trace else subprocess.DEVNULL,
                                          start_new_session=True)
        except BaseException:
            if self.trace:
                self.trace.close()
                self.trace = None
            raise
        os.set_blocking(self.child.stdin.fileno(), False)
        self.born = time.monotonic()
        self.serial += 1
        self._request("initialize", {"clientInfo": {"name": "ngs_" + self.profile, "version": "1"},
                                    "capabilities": {}}, min(deadline, self.born + 15))
        self._send({"method": "initialized"}, deadline)

    def run(self, binary, cwd, prompt, schema, timeout, output_limit):
        started = time.monotonic()
        deadline = started + timeout
        reused = False
        thread_id = turn_id = None
        final = None
        terminal = None
        bad_item = False
        trace_start = 0
        attached = False
        image_item = False
        outcome = "unconfirmed"
        def event(msg):
            nonlocal terminal, final, bad_item, image_item
            params = msg.get("params") or {}
            if params.get("threadId") != thread_id:
                return
            if msg.get("method") == "item/completed" and (turn_id is None or params.get("turnId") == turn_id):
                item = params.get("item") or {}
                kind = item.get("type")
                if kind == "imageGeneration":
                    image_item = True
                if self.profile == "document" and kind not in ("agentMessage", "reasoning", "userMessage"):
                    bad_item = True
                if kind == "agentMessage" and item.get("phase") != "commentary":
                    final = item.get("text")
            if msg.get("method") == "turn/completed":
                terminal = params.get("turn")
        try:
            identity = executable_identity(binary)
            reused = (self.child is not None and self.child.poll() is None and self.binary == binary
                      and self.binary_identity == identity and started - self.born < self.ttl)
            if not reused:
                self._start(binary, identity, deadline)
            if executable_identity(binary) != identity:
                raise SessionError("start_failed")
            if self.trace:
                trace_start = os.fstat(self.trace.fileno()).st_size
            params = {"cwd": str(cwd), "approvalPolicy": "never", "sandbox": "read-only", "ephemeral": True}
            if self.profile == "document":
                params["config"] = DOC_CONFIG
            result = self._request("thread/start", params, min(deadline, time.monotonic() + 30))
            thread_id = result["thread"]["id"]
            if not isinstance(thread_id, str) or not THREAD_ID.fullmatch(thread_id):
                raise SessionError("invalid_thread")
            attached = True
            turn = self._request("turn/start", {"threadId": thread_id, "input": [
                {"type": "text", "text": prompt, "text_elements": []}], "outputSchema": schema}, deadline, event)
            turn_id = turn["turn"]["id"]
            while terminal is None:
                self._dispatch(self._recv(deadline), deadline, event)
            if terminal.get("id") != turn_id:
                raise SessionError("invalid_turn")
            status = terminal.get("status")
            if status not in ("completed", "failed", "interrupted"):
                raise SessionError("invalid_turn")
            result = self._request("thread/unsubscribe", {"threadId": thread_id}, time.monotonic() + 5)
            if result.get("status") != "unsubscribed":
                raise SessionError("unsubscribe_failed")
            attached = False
            trace = self._image_receipts(thread_id, trace_start) if self.trace else (b"", "not_applicable")
            if status != "completed":
                outcome = "turn_" + status
                raise SessionError(outcome)
            if bad_item or not isinstance(final, str):
                outcome = "invalid_output"
                raise SessionError(outcome)
            if len(final.encode()) > output_limit:
                outcome = "output_limit"
                raise SessionError(outcome)
            try:
                value = json.loads(final)
            except ValueError:
                outcome = "invalid_output"
                raise SessionError(outcome) from None
            outcome = ("response_" + (value["state"] if type(value) is dict and value.get("state") in ("generated", "blocked") else "invalid")
                       if self.profile == "image" else "response")
            return value, thread_id, trace[0]
        except BaseException as error:
            if outcome == "unconfirmed" and isinstance(error, SessionError):
                outcome = str(error)
            # No uncertain turn is replayed, and a server with unknown thread state is discarded.
            if attached or terminal is None or outcome in ("invalid_turn", "unsubscribe_failed"):
                self.close()
            if isinstance(error, SessionError):
                raise
            raise SessionError("disconnected") from None
        finally:
            finished = time.monotonic()
            diagnostic(worker=self.profile, sessionSerial=self.serial, reused=reused, outcome=outcome,
                       imageToolState=trace[1] if 'trace' in locals() else "unknown",
                       imageItemSeen=image_item,
                       durationMs=round((finished-started)*1000), gapMs=(None if self.last_finished is None
                           else round((started-self.last_finished)*1000)))
            self.last_finished = finished
            self.expire_idle()

    def _image_receipts(self, thread_id, offset):
        # App-server emits JSON or text traces. Normalize actual tool observations for
        # the pinned image verifier, never invent an event or accept model prose.
        size = os.fstat(self.trace.fileno()).st_size
        if size - offset > TRACE_LIMIT:
            return b"", "unknown"
        raw = os.pread(self.trace.fileno(), size-offset, offset)
        out = []
        status = "not_seen"
        valid_call = re.compile(r"(?:exec-|call_)[a-f0-9-]{10,80}")
        records = list(self._tool_records(raw))
        safe_calls = {f.get("call_id") for target, _, f in records if target == "codex_otel.trace_safe"
                      and f.get("conversation.id") == thread_id and f.get("tool_origin") == "builtin"
                      and f.get("mcp_tool") is False}
        for target, stamp, f in records:
            try:
                if f.get("event.name") != "codex.tool_result" or (f.get("conversation.id") != thread_id
                       and not (target == "codex_otel.log_only" and f.get("conversation.id") is None
                                and f.get("call_id") in safe_calls)):
                    continue
                key = f.get("tool_namespace"), f.get("tool_name")
                success, call = f.get("success"), f.get("call_id")
                if key not in (("image_gen", "imagegen"), ("functions", "exec")) or not valid_call.fullmatch(call or ""):
                    continue
                if key == ("image_gen", "imagegen") and success in ("true", "false"):
                    if success == "true" or status != "succeeded":
                        status = "succeeded" if success == "true" else "failed"
                if success != "true" or not isinstance(stamp, str) or not re.match(r"\d{4}-\d\d-\d\dT", stamp):
                    continue
                prefix = (f'{stamp} {target} event.name="codex.tool_result" tool_name={key[1]} tool_namespace={key[0]}'
                          f' success=true conversation.id={thread_id} call_id={call}')
                if target == "codex_otel.trace_safe" and f.get("tool_origin") == "builtin" and f.get("mcp_tool") is False:
                    out.append(prefix + " tool_origin=builtin mcp_tool=false")
                elif target == "codex_otel.log_only" and isinstance(f.get("arguments"), str):
                    # Output content is not used by the verifier. Avoid storing artifact/tool payloads.
                    out.append(prefix + " arguments=" + f["arguments"] + ' output=""')
            except (ValueError, TypeError, AttributeError):
                continue
        return ("\n".join(out) + "\n").encode(), status


    @staticmethod
    def _tool_records(raw):
        text = re.sub(r"\x1b\[[0-9;]*m", "", raw.decode("utf-8", "replace"))
        for line in text.splitlines():
            if not line.startswith("{") or "codex.tool_result" not in line:
                continue
            try:
                log = json.loads(line)
                yield log.get("target"), log.get("timestamp"), log.get("fields") or {}
            except (ValueError, AttributeError):
                pass
        line_start = r"(?m)^(?=\d{4}-\d\d-\d\dT[^ ]+ +(?:TRACE|DEBUG|INFO|WARN|ERROR) +)"
        plain = re.compile(r"(\d{4}-\d\d-\d\dT\S+) +INFO +(codex_otel\.(?:log_only|trace_safe)):\s+(.*)", re.S)
        field = re.compile(r'(?<![\w.])([\w.]+)=("[^"]*"|[^\s}:]+)')
        for record in re.split(line_start, text):
            m = plain.match(record)
            if not m:
                continue
            stamp, target, body = m.groups()
            header, sep, tail = body.partition(" arguments=")
            fields = {k: v.strip('"') for k, v in field.findall(header)}
            if fields.get("event.name") != "codex.tool_result":
                continue
            if "mcp_tool" in fields:
                fields["mcp_tool"] = {"true": True, "false": False}.get(fields["mcp_tool"])
            if sep and " output=" in tail:
                # Preserve exact arguments, including multiline code. Direct
                # image traces carry JSON; a wrapper's payload is raw JS.
                if fields.get("tool_namespace") == "image_gen":
                    try:
                        _, end = json.JSONDecoder().raw_decode(tail)
                        if tail[end:].lstrip().startswith("output="):
                            fields["arguments"] = tail[:end]
                    except (ValueError, TypeError):
                        pass
                else:
                    fields["arguments"] = tail.split(" output=", 1)[0]
            yield target, stamp, fields


def serve(profile, operation):
    """One private request and response at a time on inherited pipes."""
    import sys
    import threading
    stopped = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stopped.set())
    session = CodexSession(profile, stopped)
    try:
        while not stopped.is_set():
            if not select.select([sys.stdin.buffer], [], [], 0.25)[0]:
                session.expire_idle()
                continue
            raw = sys.stdin.buffer.readline(MAX_RPC * 2 + 1)
            if not raw:
                return
            if len(raw) > MAX_RPC * 2 or not raw.endswith(b"\n"):
                return
            try:
                answer = operation(session, json.loads(raw))
                result = {"ok": True, "value": answer}
            except SessionError as exc:
                result = {"ok": False, "reason": str(exc)}
            except Exception:
                result = {"ok": False, "reason": "invalid_request"}
            sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    finally:
        session.close()
