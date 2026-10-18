import os
import queue
import re
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
import subprocess
from debugger import Gdb, quoted
from samples import open_samples


class DebugFeatures:
    def setup_debug(self, right):
        self.debugger = None
        self.debug_temp = None
        self.debug_state = 'idle'
        self.debug_generation = 0
        self.pause_revision = 0
        self.debug_events = queue.Queue()
        self.gdb_path = self.config.get('gdb', '')
        if self.gdb_path and not Path(self.gdb_path).is_absolute():
            self.gdb_path = str(Path(__file__).resolve().parent / self.gdb_path)
        bundled_gdb = Path(__file__).resolve().parent / 'toolchain' / 'mingw64' / 'bin' / 'gdb.exe'
        if bundled_gdb.is_file():
            self.gdb_path = str(bundled_gdb)
        # A widget packed into a descendant still keeps its original Tk parent.
        # Recreate it under its tab so the notebook cannot cover its native window.
        self.output.destroy()
        book = ttk.Notebook(right)
        book.pack(fill='both', expand=True)
        out_tab, errors_tab, debug_tab = ttk.Frame(book), ttk.Frame(book), ttk.Frame(book)
        book.add(out_tab, text='输出')
        book.add(errors_tab, text='错误 / 警告')
        book.add(debug_tab, text='调试：局部变量 / 调用栈')
        self.output_book, self.output_tab = book, out_tab
        self.output = tk.Text(out_tab, height=10, font=self.io_font, wrap='word', state='disabled')
        self.output.pack(fill='both', expand=True)
        self.output.bind('<Double-Button-1>', self.goto_error)
        self.diagnostics = ttk.Treeview(errors_tab, columns=('kind', 'line', 'message'), show='headings', height=6)
        for name, title, width in [('kind', '级别', 65), ('line', '行:列', 65), ('message', '信息', 550)]:
            self.diagnostics.heading(name, text=title)
            self.diagnostics.column(name, width=width, stretch=name == 'message')
        self.diagnostics.pack(fill='both', expand=True)
        self.diagnostics.bind('<<TreeviewSelect>>', self.select_diagnostic)
        self.locals_view = ttk.Treeview(debug_tab, columns=('name', 'value'), show='headings', height=4)
        self.locals_view.heading('name', text='局部变量')
        self.locals_view.heading('value', text='值（暂停时刷新）')
        self.locals_view.column('name', width=140)
        self.locals_view.column('value', width=350)
        self.locals_view.pack(fill='both', expand=True)
        self.stack_view = tk.Listbox(debug_tab, height=3, font=self.small_font)
        self.stack_view.pack(fill='x')
        row = ttk.Frame(self.root, padding=(8, 3))
        row.pack(side='bottom', fill='x')
        self.debug_buttons = {}
        for label, action in [('开始调试 F6', self.start_debug), ('断点 F9', self.toggle_breakpoint), ('继续 F8', lambda: self.debug_command('-exec-continue')), ('逐过程 F10', lambda: self.debug_command('-exec-next')), ('进入 F11', lambda: self.debug_command('-exec-step')), ('跳出 Shift+F11', lambda: self.debug_command('-exec-finish')), ('设置 GDB', self.set_gdb), ('样例测试 Ctrl+Enter', lambda: open_samples(self))]:
            button = ttk.Button(row, text=label, command=action)
            button.pack(side='left', padx=2)
            self.debug_buttons[label] = button
        self.wrap_toolbar(row)
        self.debug_status = tk.StringVar(value='调试就绪 · 点击行号设断点；黄色行表示下一条将执行的语句')
        ttk.Label(self.root, textvariable=self.debug_status, padding=(8, 2)).pack(side='bottom', fill='x')
        self.root.bind('<F6>', lambda e: self.start_debug())
        self.root.bind('<F8>', lambda e: self.debug_command('-exec-continue'))
        self.root.bind('<F9>', lambda e: self.toggle_breakpoint())
        self.root.bind('<F10>', lambda e: self.debug_command('-exec-next'))
        self.root.bind('<F11>', lambda e: self.debug_command('-exec-step'))
        self.root.bind('<Shift-F11>', lambda e: self.debug_command('-exec-finish'))
        self.root.after(50, self.poll_debug)

    def reset_diagnostics(self):
        self.editor.tag_remove('compile_error', '1.0', 'end')
        self.editor.tag_remove('compile_warning', '1.0', 'end')
        self.diagnostics.delete(*self.diagnostics.get_children())
        if self._doc:
            self._doc.diagnostics = []
            self._doc.diagnostic_source = None

    def render_diagnostics(self):
        """Show the active tab's diagnostics after switching documents."""
        self.diagnostics.delete(*self.diagnostics.get_children())
        for kind, location, message in self._doc.diagnostics:
            self.diagnostics.insert('', 'end', values=(kind, location, message))

    def run_or_continue(self):
        if self.debug_state == 'paused':
            self.output_book.select(self.output_tab)
            self.debug_command('-exec-continue')
        elif self.debug_state == 'idle':
            self.build(True)
        else:
            self.debug_status.set('正在编译或调试运行中；需要中止时点击“停止”')

    def show_diagnostics(self, text, source, cpp):
        if source != self.editor.get('1.0', 'end-1c'):
            return
        self.reset_diagnostics()
        records = []
        for line in text.splitlines():
            match = re.match(r'(.+?):(\d+):(\d+):\s*(fatal error|error|warning):\s*(.*)', line)
            if not match:
                continue
            file, row, col, kind, message = match.groups()
            # Header diagnostics must not mark unrelated rows in the current file.
            if os.path.normcase(os.path.normpath(file)) != os.path.normcase(os.path.normpath(str(cpp))):
                continue
            row, col = int(row), int(col)
            tag = 'compile_warning' if kind == 'warning' else 'compile_error'
            self.editor.tag_add(tag, f'{row}.0', f'{row}.end+1c')
            records.append(('警告' if kind == 'warning' else '错误', f'{row}:{col}', message))
        self._doc.diagnostics = records
        self._doc.diagnostic_source = source
        self.render_diagnostics()
        self.lines()

    def select_diagnostic(self, event=None):
        selected = self.diagnostics.selection()
        if selected:
            _, location, _ = self.diagnostics.item(selected[0], 'values')
            row, col = map(int, location.split(':'))
            self.editor.mark_set('insert', f'{row}.{max(0, col-1)}')
            self.editor.see(f'{row}.0')
            self.editor.focus_set()

    def breakpoint_lines(self):
        return sorted({int(self.editor.index(name).split('.')[0]) for name in self.breakpoints})

    def gutter_breakpoint(self, event):
        self.toggle_breakpoint(int(self.editor.index(f'@0,{event.y}').split('.')[0]))

    def toggle_breakpoint(self, line=None):
        if self.debug_state != 'idle' or self.running:
            self.debug_status.set('请结束本次调试后修改断点')
            return 'break'
        line = line or int(self.editor.index('insert').split('.')[0])
        matches = [name for name in self.breakpoints if int(self.editor.index(name).split('.')[0]) == line]
        if matches:
            for name in matches:
                self.editor.mark_unset(name)
                self.breakpoints.remove(name)
        else:
            name = 'bp_' + str(max([int(n[3:]) for n in self.breakpoints] + [0])+1)
            self.editor.mark_set(name, f'{line}.0')
            self.editor.mark_gravity(name, 'right')
            self.breakpoints.add(name)
        self.lines()
        return 'break'

    def clear_breakpoints(self):
        for name in self.breakpoints:
            self.editor.mark_unset(name)
        self.breakpoints.clear()
        self.reset_diagnostics()

    def set_gdb(self):
        path = filedialog.askopenfilename(title='选择 gdb.exe', filetypes=[('GDB', 'gdb.exe'), ('程序', '*.exe')])
        if path:
            self.gdb_path = path
            self.persist()

    def debug_ui_state(self, state):
        self.debug_state = state
        self.editor.configure(state='normal' if state == 'idle' else 'disabled')
        for label, button in self.debug_buttons.items():
            enabled = (state == 'idle') if label in ('开始调试 F6', '断点 F9', '设置 GDB', '样例测试 Ctrl+Enter') else state == 'paused'
            button.configure(state='normal' if enabled else 'disabled')
        if state != 'paused':
            self.editor.tag_remove('debug_line', '1.0', 'end')

    def start_debug(self):
        if self.running or self.debug_state != 'idle':
            return
        gdb = self.gdb_path or str(Path(self.compiler).with_name('gdb.exe'))
        if not Path(gdb).is_file():
            messagebox.showinfo('需要 GDB', '没有找到 gdb.exe，请点击“设置 GDB”选择与编译器匹配的 GDB。')
            return
        if not Path(self.compiler).is_file():
            messagebox.showinfo('需要编译器', '请先设置 C++ 编译器。')
            return
        self.gdb_path = gdb
        self.persist()
        self.hide_popup()
        self.reset_diagnostics()
        self.locals_view.delete(*self.locals_view.get_children())
        self.stack_view.delete(0, 'end')
        self.debug_generation += 1
        generation = self.debug_generation
        source = self.editor.get('1.0', 'end-1c')
        stdin = self.stdin.get('1.0', 'end-1c')
        standard, compiler, origin = self.standard.get(), self.compiler, self.path
        breaks = self.breakpoint_lines()
        self.debug_ui_state('building')
        self.cancelled.clear()
        self.debug_status.set('正在编译调试版本（-g -O0）…')
        self.log('正在编译调试版本…\n', True)

        def worker():
            temp = tempfile.TemporaryDirectory(prefix='algocpp_debug_')
            folder = Path(temp.name)
            try:
                cpp, exe = folder/'main.cpp', folder/'main.exe'
                cpp.write_text(source, encoding='utf-8')
                (folder/'stdin.txt').write_text(stdin, encoding='utf-8')
                env = os.environ.copy()
                env['PATH'] = str(Path(compiler).parent) + os.pathsep + env.get('PATH', '')
                env['LC_ALL'] = 'C'
                command = [compiler, str(cpp), '-o', str(exe), f'-std={standard}', '-g', '-O0', '-Wall', '-Wextra', '-fdiagnostics-color=never']
                if origin:
                    command += ['-I', str(origin.parent)]
                self.proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                try:
                    out, err = self.proc.communicate(timeout=60)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
                    self.proc.communicate()
                    raise RuntimeError('调试编译超过 60 秒')
                text = (out+err).decode('utf-8', errors='replace')
                self.debug_events.put((generation, 'compiled', (text, source, cpp)))
                if self.proc.returncode or self.cancelled.is_set():
                    raise RuntimeError('已停止' if self.cancelled.is_set() else '调试编译失败，请修正标红行')
                session = Gdb(gdb, exe, str(origin.parent) if origin else str(folder), env,
                              lambda kind, name, data: self.debug_events.put((generation, 'gdb', (kind, name, data))))
                self.debugger = session
                self.debug_temp = temp
                temp = None
                session.command('-gdb-set pagination off')
                session.command('-gdb-set confirm off')
                session.command('-enable-pretty-printing')
                session.command('-break-insert -t main')
                for row in breaks:
                    info = session.command('-break-insert ' + quoted(cpp.as_posix()+':'+str(row)))
                    actual = info.get('bkpt', {}).get('line', str(row))
                    self.debug_events.put((generation, 'log', f'断点：第 {row} 行 → 可执行行 {actual}\n'))
                if self.cancelled.is_set():
                    raise RuntimeError('已停止')
                self.debug_events.put((generation, 'running', None))
                run = 'run < "' + (folder/'stdin.txt').as_posix() + '" > "' + (folder/'stdout.txt').as_posix() + '" 2> "' + (folder/'stderr.txt').as_posix() + '"'
                session.command('-interpreter-exec console ' + quoted(run))
            except Exception as exc:
                self.debug_events.put((generation, 'failure', str(exc)))
            finally:
                self.proc = None
                if temp:
                    temp.cleanup()
        threading.Thread(target=worker, daemon=True).start()

    def debug_command(self, command):
        if self.debug_state != 'paused' or not self.debugger:
            return 'break'
        session = self.debugger
        generation = self.debug_generation
        self.pause_revision += 1
        self.debug_ui_state('running')
        self.debug_status.set('调试运行中…')
        def worker():
            try:
                session.command(command)
            except Exception as exc:
                self.debug_events.put((generation, 'command_error', str(exc)))
        threading.Thread(target=worker, daemon=True).start()
        return 'break'

    def refresh_debug(self, generation, session):
        revision = self.pause_revision
        def worker():
            try:
                local = session.command('-stack-list-variables --simple-values')
                stack = session.command('-stack-list-frames 0 15')
                self.debug_events.put((generation, 'inspection', (revision, local, stack)))
            except Exception as exc:
                self.debug_events.put((generation, 'log', f'变量读取：{exc}\n'))
        threading.Thread(target=worker, daemon=True).start()

    def debug_output(self):
        if self.debug_temp:
            for filename in ('stdout.txt', 'stderr.txt'):
                path = Path(self.debug_temp.name)/filename
                if path.exists():
                    try:
                        self.log(path.read_text(encoding='utf-8', errors='replace'))
                    except OSError as exc:
                        self.log(f'读取调试输出失败：{exc}\n')

    def finish_debug(self, message):
        self.debug_generation += 1  # Discard queued events from the finished session.
        session, temp = self.debugger, self.debug_temp
        self.debugger = self.debug_temp = None
        self.debug_ui_state('idle')
        self.debug_status.set(message)
        self.output_book.select(self.output_tab)
        self.log('\n' + message + '\n')
        def cleanup():
            if session:
                session.stop()
                try:
                    session.proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            if temp:
                try:
                    temp.cleanup()
                except OSError:
                    pass
        # Finish terminating GDB before Python cleans up its temporary directory.
        threading.Thread(target=cleanup, daemon=False).start()

    def poll_debug(self):
        try:
            while True:
                generation, event, data = self.debug_events.get_nowait()
                if generation != self.debug_generation:
                    continue
                if event == 'compiled':
                    text, source, cpp = data
                    self.log(text)
                    self.show_diagnostics(text, source, cpp)
                elif event == 'log':
                    self.log(data)
                elif event == 'running':
                    self.debug_ui_state('running')
                    self.debug_status.set('调试运行中…')
                elif event == 'failure':
                    self.debug_output()
                    self.finish_debug(data)
                elif event == 'command_error':
                    self.debug_ui_state('paused')
                    self.debug_status.set(data)
                    self.log(data+'\n')
                elif event == 'inspection' and self.debug_state == 'paused':
                    revision, local, stack = data
                    if revision != self.pause_revision:
                        continue
                    self.locals_view.delete(*self.locals_view.get_children())
                    for item in local.get('variables', []):
                        self.locals_view.insert('', 'end', values=(item.get('name', ''), item.get('value', item.get('type', '')+'（复合类型）')))
                    self.stack_view.delete(0, 'end')
                    for item in stack.get('stack', []):
                        frame = item.get('frame', item)
                        self.stack_view.insert('end', f'#{frame.get("level", "")} {frame.get("func", "?")}  {frame.get("file", "")}:{frame.get("line", "")}')
                elif event == 'gdb':
                    kind, name, fields = data
                    if kind in ('~', '&', '@'):
                        self.log(fields)
                    elif kind == '*' and name == 'stopped':
                        reason = fields.get('reason', '')
                        if reason.startswith('exited'):
                            self.debug_output()
                            self.finish_debug('调试结束 · ' + reason + ' ' + fields.get('exit-code', ''))
                            continue
                        self.debug_ui_state('paused')
                        self.pause_revision += 1
                        frame = fields.get('frame', {})
                        row = frame.get('line')
                        filename = frame.get('fullname', frame.get('file', ''))
                        is_main = self.debug_temp and os.path.normcase(os.path.normpath(filename)) == os.path.normcase(os.path.normpath(str(Path(self.debug_temp.name)/'main.cpp')))
                        if row and is_main:
                            self.editor.tag_add('debug_line', f'{row}.0', f'{row}.end+1c')
                            self.editor.see(f'{row}.0')
                        self.debug_status.set(f'已暂停 · {reason} · {frame.get("func", "")} · {frame.get("file", "")}:{row or "?"}' + (' · 当前在外部代码' if not is_main else ''))
                        self.refresh_debug(generation, self.debugger)
                    elif kind == 'closed':
                        self.debug_output()
                        self.finish_debug('GDB 已退出')
        except queue.Empty:
            pass
        self.root.after(50, self.poll_debug)
