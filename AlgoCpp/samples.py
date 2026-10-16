"""Local sample runner: compile once, execute each case in a fresh process."""
from display_features import fit_window
import difflib
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

LIMIT = 1024 * 1024


def compare_output(expected, actual, mode):
    expected = expected.replace('\r\n', '\n')
    actual = actual.replace('\r\n', '\n')
    if mode == '忽略空白':
        a, b = expected.split(), actual.split()
        if a == b:
            return True, '通过：非空白内容一致。'
        for i in range(max(len(a), len(b))):
            x = a[i] if i < len(a) else '<缺少>'
            y = b[i] if i < len(b) else '<缺少>'
            if x != y:
                return False, f'第 {i+1} 个输出项不同\n期望：{x!r}\n实际：{y!r}\n期望共 {len(a)} 项，实际共 {len(b)} 项。'
    if expected == actual:
        return True, '通过：内容完全一致（已统一 Windows 换行）。'
    difference = ''.join(difflib.unified_diff(expected.splitlines(keepends=True), actual.splitlines(keepends=True), fromfile='期望', tofile='实际'))
    for i in range(max(len(expected), len(actual))):
        x = expected[i:i+1]
        y = actual[i:i+1]
        if x != y:
            row = expected[:i].count('\n')+1
            col = i - expected.rfind('\n', 0, i)
            return False, f'首个差异：第 {row} 行，第 {col} 列\n期望字符：{x!r}，实际字符：{y!r}\n（空字符串表示已到末尾）\n\n{difference}'


def execute_case(exe, data, cwd, env, timeout, cancel, set_process):
    """File-backed streams avoid deadlocks and unbounded output in memory."""
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix='algocpp_case_') as folder:
        base = Path(folder)
        (base/'input').write_bytes(data.encode('utf-8'))
        with (base/'input').open('rb') as inp, (base/'out').open('wb') as out, (base/'err').open('wb') as err:
            proc = subprocess.Popen([str(exe)], stdin=inp, stdout=out, stderr=err, cwd=cwd, env=env,
                                    creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            set_process(proc)
            verdict = None
            while True:
                if cancel.is_set():
                    verdict = '已停止'
                elif (base/'out').stat().st_size + (base/'err').stat().st_size > LIMIT:
                    verdict = 'OLE'
                elif time.monotonic()-start >= timeout:
                    verdict = 'TLE'
                if verdict:
                    if proc.poll() is None:
                        proc.kill()
                    proc.wait()
                    break
                try:
                    proc.wait(timeout=0.02)
                    break
                except subprocess.TimeoutExpired:
                    pass
            set_process(None)
        if (base/'out').stat().st_size + (base/'err').stat().st_size > LIMIT and verdict != '已停止':
            verdict = 'OLE'
        def read(name):
            with (base/name).open('rb') as stream:
                return stream.read(LIMIT).decode('utf-8', errors='replace').replace('\r\n', '\n')
        return {'verdict': verdict or ('RE' if proc.returncode else ''), 'actual': read('out'),
                'stderr': read('err'), 'exit_code': proc.returncode,
                'ms': (time.monotonic()-start)*1000}


class Samples:
    def __init__(self, app):
        self.app = app
        self.home = Path(__file__).resolve().parent
        self.file = self.home/'samples.json'
        self.rows = []
        self.results = {}
        self.current = None
        self.running = False
        self.events = queue.Queue()
        self.window = tk.Toplevel(app.root)
        self.window.title('AlgoCpp · 多组样例测试')
        self.window.transient(app.root)
        self.window.attributes('-topmost', bool(app.root.attributes('-topmost')))
        fit_window(self.window, 1400, 900)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        bar = ttk.Frame(self.window, padding=6)
        bar.pack(fill='x')
        self.edit_buttons = []
        for text, action in [('新增', self.add), ('删除', self.delete), ('导入样例集', self.load), ('导出样例集', self.save_as)]:
            b = ttk.Button(bar, text=text, command=action)
            b.pack(side='left', padx=2)
            self.edit_buttons.append(b)
        self.run_button = ttk.Button(bar, text='测试全部 Ctrl+Enter', command=self.run)
        self.run_button.pack(side='left', padx=5)
        ttk.Button(bar, text='停止', command=app.stop).pack(side='left')
        self.mode = tk.StringVar(value='忽略空白')
        self.mode_box = ttk.Combobox(bar, textvariable=self.mode, values=['忽略空白', '严格比较'], state='readonly', width=10)
        self.mode_box.pack(side='right', padx=3)
        self.timeout = tk.StringVar(value='5')
        self.timeout_box = ttk.Spinbox(bar, from_=0.1, to=60, increment=0.5, textvariable=self.timeout, width=5)
        self.timeout_box.pack(side='right')
        ttk.Label(bar, text='每组限时（秒）').pack(side='right')
        panes = ttk.Panedwindow(self.window, orient='horizontal')
        panes.pack(fill='both', expand=True, padx=6)
        left, right = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(left, weight=1)
        panes.add(right, weight=4)
        self.tree = ttk.Treeview(left, columns=('name', 'verdict', 'ms'), show='headings', selectmode='browse')
        for col, title, width in [('name', '样例', 115), ('verdict', '结果', 65), ('ms', '毫秒', 65)]:
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, stretch=col == 'name')
        for tag, color in [('AC', '#147a35'), ('WA', '#ba2636'), ('TLE', '#ad6500'), ('RE', '#ba2636'), ('OLE', '#ad6500')]:
            self.tree.tag_configure(tag, foreground=color)
        self.tree.pack(fill='both', expand=True)
        self.tree.bind('<<TreeviewSelect>>', self.select)
        namebar = ttk.Frame(right)
        namebar.pack(fill='x')
        ttk.Label(namebar, text='样例名称：').pack(side='left')
        self.name = tk.StringVar()
        self.name_entry = ttk.Entry(namebar, textvariable=self.name)
        self.name_entry.pack(side='left', fill='x', expand=True)
        editors = ttk.Panedwindow(right, orient='horizontal')
        editors.pack(fill='both', expand=True)
        self.input = self.text_panel(editors, '输入 stdin')
        self.expected = self.text_panel(editors, '期望输出')
        details = ttk.Notebook(right)
        details.pack(fill='both', expand=True)
        self.actual = self.text_tab(details, '实际输出')
        self.diff = self.text_tab(details, '差异 / 错误信息')
        self.summary = tk.StringVar(value='新增或导入样例，填写期望输出后点击“测试全部”。每组会独立启动程序。')
        ttk.Label(self.window, textvariable=self.summary, padding=6, wraplength=1050).pack(fill='x')
        self.window.bind('<Control-Return>', lambda e: self.run())
        if self.file.exists():
            try:
                self.import_file(self.file)
            except (ValueError, OSError, TypeError) as exc:
                self.summary.set(f'样例文件读取失败：{exc}')
        if not self.rows:
            self.add('样例 1', '3 5\n', '8\n')
            self.add('样例 2', '-2 7\n', '5\n')
            self.tree.selection_set('0')
            self.select()
        self.window.after(50, self.poll)

    def text_panel(self, panes, title):
        frame = ttk.Frame(panes)
        panes.add(frame, weight=1)
        ttk.Label(frame, text=title).pack(anchor='w')
        text = tk.Text(frame, font=self.app.io_font, height=10, width=28, undo=True, wrap='none')
        self.app.bind_zoom(text)
        text.pack(fill='both', expand=True)
        return text

    def text_tab(self, notebook, title):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text=title)
        text = tk.Text(frame, font=self.app.io_font, height=12, wrap='word', state='disabled')
        scroll = ttk.Scrollbar(frame, command=text.yview)
        scroll.pack(side='right', fill='y')
        text.configure(yscrollcommand=scroll.set)
        self.app.bind_zoom(text)
        text.pack(fill='both', expand=True)
        return text

    def commit(self):
        if self.current is not None and not self.running:
            row = self.rows[self.current]
            updated = {'name': self.name.get() or f'样例 {self.current+1}', 'input': self.input.get('1.0','end-1c'), 'expected': self.expected.get('1.0','end-1c')}
            if row != updated:
                self.results.pop(self.current, None)
            self.rows[self.current] = updated
            self.update_row(self.current)

    def persist(self):
        data = {'version': 1, 'mode': self.mode.get(), 'timeout': self.timeout.get(), 'cases': self.rows}
        # Atomic replacement avoids corrupting saved cases on interrupted writes.
        temp = self.file.with_name(self.file.name+'.tmp')
        temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        temp.replace(self.file)

    def update_row(self, i):
        result = self.results.get(i, {})
        values = (self.rows[i]['name'], result.get('verdict', '未测试'), f'{result["ms"]:.1f}' if 'ms' in result else '')
        if self.tree.exists(str(i)):
            self.tree.item(str(i), values=values, tags=(result.get('verdict', ''),))
        else:
            self.tree.insert('', 'end', iid=str(i), values=values)

    def select(self, event=None):
        selected = self.tree.selection()
        if not selected:
            return
        self.commit()
        self.current = int(selected[0])
        row = self.rows[self.current]
        self.name.set(row['name'])
        for widget, value in [(self.input, row['input']), (self.expected, row['expected'])]:
            widget.configure(state='normal')
            widget.delete('1.0','end')
            widget.insert('1.0', value)
            widget.configure(state='disabled' if self.running else 'normal')
        self.show_result()

    def show_result(self):
        result = self.results.get(self.current, {})
        for widget, value in [(self.actual, result.get('actual', '')), (self.diff, result.get('detail', '尚未测试。') + ('\n\n标准错误：\n'+result['stderr'] if result.get('stderr') else ''))]:
            widget.configure(state='normal')
            widget.delete('1.0','end')
            widget.insert('1.0', value)
            widget.configure(state='disabled')

    def add(self, name=None, data='', expected=''):
        if self.running:
            return
        self.commit()
        i = len(self.rows)
        self.rows.append({'name': name or f'样例 {i+1}', 'input': data, 'expected': expected})
        self.update_row(i)
        self.tree.selection_set(str(i))
        self.select()

    def delete(self):
        if self.running or self.current is None:
            return
        self.rows.pop(self.current)
        self.current = None
        self.results.clear()
        self.tree.delete(*self.tree.get_children())
        for i in range(len(self.rows)):
            self.update_row(i)
        if self.rows:
            self.tree.selection_set('0')
            self.select()
        else:
            self.add()

    def import_file(self, path):
        data = json.loads(Path(path).read_text(encoding='utf-8-sig'))
        cases = data.get('cases') if isinstance(data, dict) else None
        if not isinstance(cases, list) or not cases:
            raise ValueError('样例集需要包含非空 cases 数组')
        if len(cases) > 1000:
            raise ValueError('最多支持 1000 组样例')
        clean = []
        for i, case in enumerate(cases):
            if not isinstance(case, dict) or not all(isinstance(case.get(k), str) for k in ('input','expected')):
                raise ValueError(f'第 {i+1} 组必须包含字符串 input 和 expected')
            if not isinstance(case.get('name', ''), str):
                raise ValueError('样例名称必须为字符串')
            clean.append({'name':case.get('name') or f'样例 {i+1}', 'input':case['input'], 'expected':case['expected']})
        self.rows = clean
        self.current = None
        self.results.clear()
        self.tree.delete(*self.tree.get_children())
        self.mode.set(data.get('mode') if data.get('mode') in ('忽略空白', '严格比较') else '忽略空白')
        self.timeout.set(str(data.get('timeout', 5)))
        for i in range(len(self.rows)):
            self.update_row(i)
        self.tree.selection_set('0')
        self.select()

    def load(self):
        if self.running:
            return
        path = filedialog.askopenfilename(parent=self.window, filetypes=[('样例集 JSON', '*.json')])
        if path:
            try:
                self.import_file(path)
            except (OSError, ValueError, TypeError) as exc:
                messagebox.showerror('导入失败', str(exc), parent=self.window)

    def save_as(self):
        self.commit()
        path = filedialog.asksaveasfilename(parent=self.window, defaultextension='.json', filetypes=[('样例集 JSON', '*.json')])
        if path:
            old = self.file
            try:
                self.file = Path(path)
                self.persist()
            except OSError as exc:
                messagebox.showerror('保存失败', str(exc), parent=self.window)
            finally:
                self.file = old

    def set_busy(self, busy):
        self.running = busy
        for b in self.edit_buttons+[self.run_button, self.name_entry, self.timeout_box]:
            b.configure(state='disabled' if busy else 'normal')
        self.mode_box.configure(state='disabled' if busy else 'readonly')
        for widget in (self.input, self.expected):
            widget.configure(state='disabled' if busy else 'normal')

    def run(self):
        app = self.app
        if self.running or app.running or app.debug_state != 'idle':
            self.summary.set('请先结束当前运行或调试，再开始样例测试。')
            return
        self.commit()
        try:
            timeout = float(self.timeout.get())
            if not 0.1 <= timeout <= 60:
                raise ValueError()
        except ValueError:
            messagebox.showinfo('限时无效', '每组限时请输入 0.1～60 秒。', parent=self.window)
            return
        if not Path(app.compiler).is_file():
            messagebox.showinfo('需要编译器', '请先在主窗口设置编译器。', parent=self.window)
            return
        try:
            self.persist()
        except OSError as exc:
            messagebox.showerror('样例保存失败', str(exc), parent=self.window)
            return
        cases = [dict(row) for row in self.rows]
        source = app.editor.get('1.0','end-1c')
        compiler, standard, origin, mode = app.compiler, app.standard.get(), app.path, self.mode.get()
        self.results.clear()
        for i in range(len(cases)):
            self.results[i] = {'verdict':'等待'}
            self.update_row(i)
        self.show_result()
        app.cancelled.clear()
        app.running = True
        app.reset_diagnostics()
        self.set_busy(True)
        self.summary.set('正在编译…（只编译一次，每组独立运行）')
        def worker():
            def process(p):
                app.proc = p
                if p and app.cancelled.is_set() and p.poll() is None:
                    p.kill()
            try:
                with tempfile.TemporaryDirectory(prefix='algocpp_samples_') as folder:
                    base = Path(folder)
                    cpp, exe = base/'main.cpp', base/'main.exe'
                    cpp.write_text(source, encoding='utf-8')
                    env = os.environ.copy()
                    env['PATH'] = str(Path(compiler).parent)+os.pathsep+env.get('PATH','')
                    env['LC_ALL'] = 'C'
                    args = [compiler, str(cpp), '-o', str(exe), f'-std={standard}', '-O2', '-Wall', '-Wextra', '-fdiagnostics-color=never']
                    if origin:
                        args += ['-I', str(origin.parent)]
                    p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                    process(p)
                    try:
                        out, err = p.communicate(timeout=60)
                    except subprocess.TimeoutExpired:
                        p.kill()
                        p.communicate()
                        raise RuntimeError('编译超过 60 秒，已停止')
                    text = (out+err).decode('utf-8', errors='replace')
                    self.events.put(('compile', (text, source, cpp)))
                    if app.cancelled.is_set():
                        raise RuntimeError('样例测试已停止')
                    if p.returncode:
                        self.events.put(('ce', text))
                        return
                    for i, case in enumerate(cases):
                        if app.cancelled.is_set():
                            break
                        self.events.put(('progress', i))
                        result = execute_case(exe, case['input'], str(origin.parent) if origin else folder, env, timeout, app.cancelled, process)
                        if not result['verdict']:
                            passed, detail = compare_output(case['expected'], result['actual'], mode)
                            result['verdict'] = 'AC' if passed else 'WA'
                            result['detail'] = detail
                        else:
                            result['detail'] = {'TLE':f'超过 {timeout:g} 秒，已终止', 'OLE':'输出超过 1 MiB，已截断并停止', 'RE':f'程序异常退出，退出码 {result["exit_code"]}', '已停止':'用户停止了测试'}.get(result['verdict'], '')
                        self.events.put(('result',(i, result)))
            except Exception as exc:
                self.events.put(('failure', str(exc)))
            finally:
                app.proc = None
                self.events.put(('done', None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        try:
            while True:
                event, data = self.events.get_nowait()
                if event == 'compile':
                    text, source, cpp = data
                    self.app.log('样例测试编译信息：\n'+text, True)
                    self.app.show_diagnostics(text, source, cpp)
                elif event == 'ce':
                    for i in range(len(self.rows)):
                        self.results[i] = {'verdict':'CE', 'detail':data, 'actual':''}
                        self.update_row(i)
                    self.summary.set('编译失败：查看主窗口标红行或“差异 / 错误信息”。')
                elif event == 'progress':
                    self.summary.set(f'正在运行 {data+1}/{len(self.rows)}：{self.rows[data]["name"]}')
                elif event == 'result':
                    i, result = data
                    self.results[i] = result
                    self.update_row(i)
                    if i == self.current:
                        self.show_result()
                elif event == 'failure':
                    self.summary.set(data)
                    self.app.log(data+'\n')
                elif event == 'done':
                    for i, result in self.results.items():
                        if result.get('verdict') == '等待':
                            result.update(verdict='未运行', detail='本轮未执行（已停止或编译未完成）。')
                            self.update_row(i)
                    counts = {}
                    for result in self.results.values():
                        key = result.get('verdict','未运行')
                        counts[key] = counts.get(key,0)+1
                    summary = '测试结束 · '+' / '.join(f'{k} {v}' for k,v in counts.items())
                    self.summary.set(summary)
                    self.app.log(summary+'\n')
                    self.app.running = False
                    self.set_busy(False)
                    self.show_result()
        except queue.Empty:
            pass
        self.window.after(50, self.poll)

    def close(self):
        if self.running:
            self.summary.set('请先点击“停止”，测试结束后可关闭窗口。')
            return
        self.commit()
        try:
            self.persist()
        except OSError as exc:
            messagebox.showerror('保存失败', str(exc), parent=self.window)
            return
        self.app.samples_window = None
        self.window.destroy()


def open_samples(app):
    existing = getattr(app, 'samples_window', None)
    if existing and existing.window.winfo_exists():
        existing.window.deiconify()
        existing.window.lift()
        return
    app.samples_window = Samples(app)
