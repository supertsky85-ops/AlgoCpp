"""GDB/MI transport. No shell is used for debugger commands."""
import json
import os
import re
import subprocess
import threading


def mi_value(text):
    """Parse MI strings, tuples, lists and result pairs (including repeated keys)."""
    pos = 0

    def value():
        nonlocal pos
        if text[pos] == '"':
            start = pos
            pos += 1
            while pos < len(text):
                if text[pos] == '\\':
                    pos += 2
                elif text[pos] == '"':
                    pos += 1
                    raw = text[start:pos]
                    try:
                        return json.loads(raw)
                    except ValueError:
                        return raw[1:-1]
                else:
                    pos += 1
            return text[start:]
        if text[pos] in '[{':
            closing = ']' if text[pos] == '[' else '}'
            is_list = closing == ']'
            pos += 1
            result = [] if is_list else {}
            while pos < len(text) and text[pos] != closing:
                key = re.match(r'[\w-]+=', text[pos:])
                if key:
                    name = key.group()[:-1]
                    pos += len(key.group())
                    item = value()
                    if is_list:
                        result.append({name: item})
                    else:
                        result[name] = item
                else:
                    item = value()
                    if is_list:
                        result.append(item)
                if pos < len(text) and text[pos] == ',':
                    pos += 1
            pos += 1
            return result
        start = pos
        while pos < len(text) and text[pos] not in ',]}':
            pos += 1
        return text[start:pos]

    return value() if text else {}


def record(line):
    match = re.match(r'(\d*)([\^*=~@&])(.*)', line.strip())
    if not match:
        return None
    token, kind, body = match.groups()
    if kind in '~@&':
        return token, kind, '', mi_value(body)
    name, _, fields = body.partition(',')
    return token, kind, name, mi_value('{' + fields + '}')


class Gdb:
    def __init__(self, binary, exe, cwd, env, callback):
        self.callback = callback
        self.pending = {}
        self.lock = threading.Lock()
        self.serial = 0
        self.closed = False
        self.proc = subprocess.Popen(
            [binary, '--nx', '--quiet', '--interpreter=mi2', str(exe)], cwd=cwd,
            env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace',
            bufsize=1, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            for line in self.proc.stdout:
                item = record(line)
                if not item:
                    continue
                token, kind, name, data = item
                if kind == '^':
                    with self.lock:
                        waiter = self.pending.get(token)
                    if waiter:
                        waiter[1].append((name, data))
                        waiter[0].set()
                else:
                    self.callback(kind, name, data)
        finally:
            self.closed = True
            with self.lock:
                for event, result in self.pending.values():
                    if not result:
                        result.append(('error', {'msg': 'GDB 已退出'}))
                    event.set()
            self.callback('closed', '', {})

    def command(self, command, timeout=15):
        event, result = threading.Event(), []
        with self.lock:
            if self.closed:
                raise RuntimeError('GDB 已退出')
            self.serial += 1
            token = str(self.serial)
            self.pending[token] = (event, result)
            self.proc.stdin.write(token + command + '\n')
            self.proc.stdin.flush()
        try:
            if not event.wait(timeout):
                raise RuntimeError('GDB 响应超时，请停止后重试')
            name, data = result[0]
            if name == 'error':
                raise RuntimeError(data.get('msg', '调试命令失败'))
            return data
        finally:
            with self.lock:
                self.pending.pop(token, None)

    def stop(self):
        # Windows taskkill /T also terminates the debugged child process.
        if self.proc.poll() is None:
            subprocess.run(['taskkill', '/PID', str(self.proc.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def quoted(value):
    return json.dumps(str(value), ensure_ascii=True)
