import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from debug_ui import DebugFeatures
from samples import open_samples
from editor_features import EditorFeatures
from display_features import DisplayFeatures, enable_high_dpi

enable_high_dpi()

HOME = Path(__file__).resolve().parent
CONFIG = HOME / 'settings.json'
TEMPLATE = '''#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    int a, b;
    cin >> a >> b;
    cout << a + b << '\\n';
    return 0;
}
'''
WORDS = '''alignas auto bool break case catch char class const constexpr continue default delete do double else enum explicit extern false float for friend if inline int long namespace new noexcept nullptr operator private protected public return short signed sizeof static struct switch template this throw true try typedef typename union unsigned using virtual void volatile while include define std cin cout cerr endl string vector array deque list forward_list set multiset map multimap unordered_set unordered_map stack queue priority_queue pair tuple bitset iterator sort stable_sort partial_sort nth_element reverse rotate unique lower_bound upper_bound binary_search equal_range min max minmax swap find find_if count count_if accumulate iota gcd lcm next_permutation prev_permutation make_pair make_tuple tie greater less begin end rbegin rend size empty push_back emplace_back pop_back push emplace pop top front back insert erase clear resize reserve capacity at data substr c_str length append compare find rfind replace assign count lower_bound upper_bound first second make_heap push_heap pop_heap sort_heap distance advance next prev numeric_limits to_string stoi stoll stod move fill memset memcpy'''.split()
SNIPPETS = {
    'fori': 'for (int i = 0; i < n; ++i) {\n    \n}',
    'forr': 'for (auto &x : container) {\n    \n}',
    'while': 'while (condition) {\n    \n}',
    'if': 'if (condition) {\n    \n}',
    'vector': 'vector<int> ',
    'priority_queue': 'priority_queue<int> ',
    'lower_bound': 'lower_bound(a.begin(), a.end(), value)',
    'upper_bound': 'upper_bound(a.begin(), a.end(), value)',
    'sort': 'sort(a.begin(), a.end())',
}


class App(DisplayFeatures, EditorFeatures, DebugFeatures):
    def __init__(self, root):
        self.root = root
        self.path = None
        self.proc = None
        self.running = False
        self.cancelled = threading.Event()
        self.popup = None
        self.config = {}
        try:
            self.config = json.loads(CONFIG.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
        self.compiler = self.config.get('compiler', '')
        if self.compiler and not Path(self.compiler).is_absolute():
            self.compiler = str(HOME / self.compiler)
        bundled = HOME / 'toolchain' / 'mingw64' / 'bin' / 'g++.exe'
        if bundled.is_file():
            self.compiler = str(bundled)
        if not Path(self.compiler).is_file():
            self.compiler = shutil.which('g++') or shutil.which('clang++') or ''
            for candidate in [r'C:\msys64\ucrt64\bin\g++.exe', r'C:\msys64\mingw64\bin\g++.exe', r'C:\mingw64\bin\g++.exe']:
                if not self.compiler and Path(candidate).is_file():
                    self.compiler = candidate
        root.title('AlgoCpp · 本地 C++ 算法练习')
        self.setup_display()
        bar = ttk.Frame(root, padding=8)
        bar.pack(fill='x')
        for label, action in [('新建', self.new), ('打开', self.open), ('保存 Ctrl+S', self.save), ('编译 F7', lambda: self.build(False)), ('运行 F5', self.run_or_continue), ('停止', self.stop), ('设置编译器', self.settings)]:
            ttk.Button(bar, text=label, command=action).pack(side='left', padx=3)
        self.standard = tk.StringVar(value=self.config.get('standard', 'c++17'))
        ttk.Combobox(bar, textvariable=self.standard, values=['c++11', 'c++14', 'c++17', 'c++20', 'c++23'], state='readonly', width=8).pack(side='right')
        ttk.Label(bar, text='补全 Ctrl+Space / Tab    ').pack(side='right')
        self.wrap_toolbar(bar)
        editbar = ttk.Frame(root, padding=(8, 0, 8, 5))
        editbar.pack(fill='x')
        for label, action in [('撤销 Ctrl+Z', self.undo_edit), ('回退 Ctrl+Y', self.redo_edit), ('格式化 Ctrl+Shift+F', self.format_code)]:
            ttk.Button(editbar, text=label, command=action).pack(side='left', padx=3)
        ttk.Button(editbar, text='A−', width=3, command=lambda: self.zoom_font(-1)).pack(side='left', padx=(12, 2))
        ttk.Label(editbar, textvariable=self.zoom_label, width=5, anchor='center').pack(side='left')
        ttk.Button(editbar, text='A+', width=3, command=lambda: self.zoom_font(1)).pack(side='left', padx=2)
        ttk.Button(editbar, text='最大化 / 还原', command=self.toggle_maximize).pack(side='left', padx=8)
        ttk.Button(editbar, textvariable=self.topmost_label, command=self.toggle_topmost).pack(side='left', padx=3)
        self.wrap_toolbar(editbar)
        panes = tk.PanedWindow(root, orient='vertical', sashwidth=5, showhandle=False,
                               sashrelief='flat', bg='#d9dde3', borderwidth=0, opaqueresize=True)
        self.main_panes = panes
        panes.pack(fill='both', expand=True, padx=8)
        editframe = ttk.Frame(panes)
        panes.add(editframe, minsize=120, height=340, stretch='always')
        self.gutter_width = 53
        self.numbers = tk.Canvas(editframe, width=53, bg='#202430', highlightthickness=0)
        self.numbers.pack(side='left', fill='y')
        self.editor = tk.Text(editframe, undo=True, wrap='none', font=self.code_font, bg='#171b24', fg='#dbe4f0', insertbackground='white', selectbackground='#384f70', padx=10, pady=8, tabs=('4c',))
        scroll = ttk.Scrollbar(editframe, command=self.scroll)
        scroll.pack(side='right', fill='y')
        self.editor.pack(fill='both', expand=True)
        self.editor.configure(yscrollcommand=lambda a, b: (scroll.set(a, b), self.lines()))
        bottom = tk.PanedWindow(panes, orient='horizontal', sashwidth=5, showhandle=False,
                                sashrelief='flat', bg='#d9dde3', borderwidth=0, opaqueresize=True)
        self.io_panes = bottom
        panes.add(bottom, minsize=100, height=180, stretch='always')
        left, right = ttk.Frame(bottom), ttk.Frame(bottom)
        bottom.add(left, minsize=100, width=320, stretch='always')
        bottom.add(right, minsize=150, width=640, stretch='always')
        ttk.Label(left, text='标准输入 stdin（运行前填好）').pack(anchor='w')
        self.stdin = tk.Text(left, height=6, width=24, font=self.io_font, wrap='none')
        self.stdin.pack(fill='both', expand=True)
        self.stdin.insert('1.0', '3 5\n')
        ttk.Label(right, text='编译信息 / 标准输出（双击错误行定位）').pack(anchor='w')
        self.output = tk.Text(right, height=10, font=self.io_font, wrap='word', state='disabled')
        self.output.pack(fill='both', expand=True)
        self.output.bind('<Double-Button-1>', self.goto_error)
        self.status = tk.StringVar()
        self.status_label = ttk.Label(root, textvariable=self.status, padding=5)
        self.status_label.pack(side='bottom', fill='x')
        self.setup_debug(right)
        for tag, color in [('keyword', '#c792ea'), ('string', '#c3e88d'), ('comment', '#718096'), ('number', '#f78c6c'), ('stl', '#82aaff')]:
            self.editor.tag_configure(tag, foreground=color)
        self.setup_editor_features()
        # Reserve toolbars/status first; let the central panes use the rest.
        panes.pack_forget()
        panes.pack(fill='both', expand=True, padx=8)
        self.update_gutter_width()
        for widget in (self.editor, self.stdin, self.output, self.stack_view):
            self.bind_zoom(widget)
        self.editor.bind('<KeyPress>', self.key)
        self.editor.bind('<KeyRelease>', self.changed)
        self.editor.bind('<ButtonRelease-1>', self.selection_release)
        self.editor.bind('<Control-space>', self.complete)
        self.editor.bind('<Tab>', self.tab)
        self.editor.bind('<Return>', self.enter)
        self.editor.bind('<BackSpace>', self.backspace)
        self.editor.bind('<Escape>', self.escape_editor)
        self.editor.bind('<Down>', lambda e: self.popup_move(1))
        self.editor.bind('<Up>', lambda e: self.popup_move(-1))
        root.bind('<Control-s>', lambda e: self.save())
        root.bind('<Control-o>', lambda e: self.open())
        root.bind('<Control-n>', lambda e: self.new())
        root.bind('<F5>', lambda e: self.run_or_continue())
        root.bind('<F7>', lambda e: self.build(False))
        root.bind('<Control-Return>', lambda e: open_samples(self))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.editor.insert('1.0', TEMPLATE)
        self.editor.edit_modified(False)
        self.editor.focus_set()
        self.highlight()

    def scroll(self, *args):
        self.editor.yview(*args)
        self.lines()

    def lines(self):
        self.numbers.delete('all')
        index = self.editor.index('@0,0')
        while True:
            info = self.editor.dlineinfo(index)
            if not info:
                break
            self.numbers.create_text(self.gutter_width-10, info[1] + info[3] / 2, text=index.split('.')[0], fill='#778399', anchor='e', font=self.small_font)
            if hasattr(self, 'breakpoints') and int(index.split('.')[0]) in self.breakpoint_lines():
                y = info[1] + info[3] / 2
                self.numbers.create_oval(3, y-5, 13, y+5, fill='#f05b66', outline='')
            index = self.editor.index(index + '+1line')

    def changed(self, event=None):
        self.update_brackets()
        if hasattr(self, 'timer'):
            self.root.after_cancel(self.timer)
        self.timer = self.root.after(130, self.highlight)
        if event and event.keysym not in ('Up', 'Down', 'Return', 'Tab', 'Escape'):
            if event.char and (event.char.isalnum() or event.char in '_.'):
                self.complete(auto=True)
            else:
                self.hide_popup()

    def highlight(self):
        src = self.editor.get('1.0', 'end-1c')
        if self.diagnostic_source is not None and src != self.diagnostic_source:
            self.reset_diagnostics()
        for tag in ('keyword', 'string', 'comment', 'number', 'stl'):
            self.editor.tag_remove(tag, '1.0', 'end')
        pattern = r'//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27|\b\d+(?:\.\d+)?\b|\b[A-Za-z_]\w*\b'
        for m in re.finditer(pattern, src):
            s = m.group()
            tag = 'comment' if s.startswith(('//', '/*')) else 'string' if s[0] in '\"\x27' else 'number' if s[0].isdigit() else 'keyword' if s in WORDS[:WORDS.index('std')] else 'stl' if s in WORDS else None
            if tag:
                self.editor.tag_add(tag, f'1.0+{m.start()}c', f'1.0+{m.end()}c')
        self.update_brackets()
        self.lines()
        pos = self.editor.index('insert')
        self.status.set(f'{self.path or "未命名.cpp"}    行列 {pos}    编译器：{self.compiler or "未配置，请选择 g++.exe 或 clang++.exe"}')

    def hide_popup(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None

    def complete(self, event=None, auto=False):
        text = self.editor.get('1.0', 'insert')
        m = re.search(r'[A-Za-z_]\w*$', text)
        prefix = m.group() if m else ''
        self.hide_popup()
        if auto and len(prefix) < 2:
            return
        identifiers = re.findall(r'\b[A-Za-z_]\w*\b', self.editor.get('1.0', 'end-1c'))
        matches = sorted(x for x in set(WORDS + list(SNIPPETS) + identifiers) if x.startswith(prefix) and x != prefix)
        if not auto and prefix in SNIPPETS:
            matches.insert(0, prefix)
        if not matches:
            return 'break'
        box = self.editor.bbox('insert')
        if not box:
            return 'break'
        self.prefix = prefix
        self.popup = tk.Toplevel(self.root)
        self.popup.overrideredirect(True)
        x, y, w, h = box
        self.popup.geometry(f'+{self.editor.winfo_rootx()+x}+{self.editor.winfo_rooty()+y+h}')
        self.choices = tk.Listbox(self.popup, height=min(8, len(matches)), width=35, font=self.io_font, exportselection=False)
        self.choices.pack()
        self.bind_zoom(self.choices)
        for word in matches:
            self.choices.insert('end', word)
        self.choices.selection_set(0)
        self.choices.bind('<ButtonRelease-1>', lambda e: self.accept())
        return 'break'

    def popup_move(self, delta):
        if self.popup:
            selected = self.choices.curselection()
            n = max(0, min(self.choices.size()-1, (selected[0] if selected else 0)+delta))
            self.choices.selection_clear(0, 'end')
            self.choices.selection_set(n)
            self.choices.see(n)
            return 'break'

    def accept(self):
        selected = self.choices.curselection()
        if selected:
            word = self.choices.get(selected[0])
            self.editor.delete(f'insert-{len(self.prefix)}c', 'insert')
            self.editor.insert('insert', SNIPPETS.get(word, word))
        self.hide_popup()
        self.editor.focus_set()
        self.changed()

    def confirm_discard(self):
        if not self.editor.edit_modified():
            return True
        answer = messagebox.askyesnocancel('保存代码', '是否保存当前代码？')
        return False if answer is None else self.save() if answer else True

    def new(self):
        if self.debug_state != 'idle':
            return
        if self.confirm_discard():
            self.clear_breakpoints()
            self.path = None
            self.editor.delete('1.0', 'end')
            self.editor.insert('1.0', TEMPLATE)
            self.editor.edit_reset()
            self.editor.edit_modified(False)
            self.highlight()

    def open(self):
        if self.debug_state != 'idle':
            return
        if not self.confirm_discard():
            return
        path = filedialog.askopenfilename(filetypes=[('C++ 文件', '*.cpp *.cc *.cxx *.h *.hpp'), ('所有文件', '*.*')])
        if path:
            try:
                text = Path(path).read_text(encoding='utf-8-sig')
                self.clear_breakpoints()
                self.editor.delete('1.0', 'end')
                self.editor.insert('1.0', text)
                self.path = Path(path)
                self.editor.edit_reset()
                self.editor.edit_modified(False)
                self.highlight()
            except (OSError, UnicodeError) as exc:
                messagebox.showerror('打开失败', str(exc))

    def save(self):
        path = self.path or filedialog.asksaveasfilename(defaultextension='.cpp', filetypes=[('C++ 文件', '*.cpp')])
        if not path:
            return False
        try:
            Path(path).write_text(self.editor.get('1.0', 'end-1c'), encoding='utf-8')
            self.path = Path(path)
            self.editor.edit_modified(False)
            self.highlight()
            return True
        except OSError as exc:
            messagebox.showerror('保存失败', str(exc))
            return False

    def settings(self):
        path = filedialog.askopenfilename(title='选择 g++.exe 或 clang++.exe', filetypes=[('编译器', '*.exe'), ('所有文件', '*.*')])
        if path:
            self.compiler = path
            self.persist()
            self.highlight()

    def persist(self):
        def portable(path):
            if not path:
                return ''
            try:
                return str(Path(path).relative_to(HOME))
            except ValueError:
                return path
        try:
            CONFIG.write_text(json.dumps({'compiler': portable(self.compiler), 'standard': self.standard.get(), 'gdb': portable(getattr(self, 'gdb_path', '')), 'font_size': self.font_size, 'always_on_top': bool(self.root.attributes('-topmost'))}, ensure_ascii=False, indent=2), encoding='utf-8')
        except OSError:
            pass

    def log(self, text, reset=False):
        self.output.configure(state='normal')
        if reset:
            self.output_book.select(self.output_tab)
            self.output.delete('1.0', 'end')
        self.output.insert('end', text.replace('\r\n', '\n'))
        self.output.see('end')
        self.output.configure(state='disabled')

    def build(self, run):
        if self.running or self.debug_state != 'idle':
            return
        if not self.compiler or not Path(self.compiler).is_file():
            messagebox.showinfo('需要 C++ 编译器', '请先安装 MinGW-w64 / MSYS2 的 GCC，然后点击“设置编译器”选择 g++.exe。也支持配置好 C++ 标准库的 clang++.exe。')
            return
        source = self.editor.get('1.0', 'end-1c')
        stdin = self.stdin.get('1.0', 'end-1c')
        standard = self.standard.get()
        origin = self.path
        compiler = self.compiler
        self.persist()
        self.running = True
        self.cancelled.clear()
        self.log('正在编译…\n', True)
        self.reset_diagnostics()
        def worker():
            def emit(s):
                self.root.after(0, self.log, s)
            try:
                with tempfile.TemporaryDirectory(prefix='algocpp_') as folder:
                    cpp, exe = Path(folder)/'main.cpp', Path(folder)/'main.exe'
                    cpp.write_text(source, encoding='utf-8')
                    env = os.environ.copy()
                    env['PATH'] = str(Path(compiler).parent) + os.pathsep + env.get('PATH', '')
                    env['LC_ALL'] = 'C'
                    command = [compiler, str(cpp), '-o', str(exe), f'-std={standard}', '-O2', '-Wall', '-Wextra', '-fdiagnostics-color=never']
                    if origin:
                        command.extend(['-I', str(origin.parent)])
                    steps = [(command, None, 60)]
                    if run:
                        steps.append(([str(exe)], stdin, 5))
                    for i, (args, data, timeout) in enumerate(steps):
                        if self.cancelled.is_set():
                            break
                        self.proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(origin.parent) if origin else folder, env=env, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                        if self.cancelled.is_set():
                            self.proc.kill()
                        try:
                            out, err = self.proc.communicate(input=data.encode('utf-8') if data is not None else None, timeout=timeout)
                        except subprocess.TimeoutExpired:
                            self.proc.kill()
                            out, err = self.proc.communicate()
                            emit(out.decode('utf-8', errors='replace') + err.decode('utf-8', errors='replace'))
                            emit(f'\n超时：{"编译" if i == 0 else "运行"}超过 {timeout} 秒，已停止。\n')
                            break
                        text = out.decode('utf-8', errors='replace') + err.decode('utf-8', errors='replace')
                        emit(text)
                        if i == 0:
                            self.root.after(0, self.show_diagnostics, text, source, cpp)
                        if self.cancelled.is_set():
                            emit('\n已停止。\n')
                            break
                        if self.proc.returncode:
                            emit(f'\n{"编译失败" if i == 0 else "程序退出"}，退出码 {self.proc.returncode}\n')
                            break
                        emit('\n编译成功。\n' if i == 0 else '\n程序结束，退出码 0。\n')
            except Exception as exc:
                emit(f'\n执行失败：{exc}\n')
            finally:
                self.proc = None
                self.running = False
        threading.Thread(target=worker, daemon=True).start()

    def stop(self):
        self.cancelled.set()
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
        if self.debug_state in ('paused', 'running'):
            self.debug_output()
            self.finish_debug('调试已停止')

    def goto_error(self, event):
        line = self.output.get('insert linestart', 'insert lineend')
        match = re.search(r':(\d+):(\d+):', line)
        if match:
            position = f'{match[1]}.{max(0, int(match[2])-1)}'
            self.editor.mark_set('insert', position)
            self.editor.see(position)
            self.editor.focus_set()

    def close(self):
        if self.confirm_discard():
            samples = getattr(self, 'samples_window', None)
            if samples and not samples.running:
                samples.close()
                if self.samples_window is not None:
                    return
            self.stop()
            self.persist()
            self.root.destroy()


if __name__ == '__main__':
    App(tk.Tk()).root.mainloop()
