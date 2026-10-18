import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from debug_ui import DebugFeatures
from samples import open_samples
from editor_features import EditorFeatures, scan
from display_features import DisplayFeatures, enable_high_dpi
from intellisense import MEMBERS, analyze, member_snippet

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


class Document:
    """One editing tab: its own text widget, gutter, undo history and marks."""

    def __init__(self, app, content, path, number):
        self.app = app
        self.path = Path(path) if path else None
        self.number = number
        self.breakpoints = set()
        self.diagnostics = []
        self.diagnostic_source = None
        self.frame = ttk.Frame(app.editbody)
        self.gutter = tk.Canvas(self.frame, width=app.gutter_width, bg='#202430', highlightthickness=0)
        self.gutter.pack(side='left', fill='y')
        self.editor = tk.Text(self.frame, undo=True, wrap='none', font=app.code_font, bg='#171b24', fg='#dbe4f0', insertbackground='white', selectbackground='#384f70', padx=10, pady=8, tabs=('4c',))
        self.scroll = ttk.Scrollbar(self.frame, command=self.scroll_view)
        self.scroll.pack(side='right', fill='y')
        self.editor.pack(fill='both', expand=True)
        self.editor.configure(yscrollcommand=self.on_scroll)
        self.editor.insert('1.0', content)
        self.editor.edit_reset()
        self.editor.edit_modified(False)
        self.tab = tk.Frame(app.tabbar, bg='#e9edf4', padx=7, pady=3, cursor='hand2')
        self.tab.pack(side='left', padx=(0, 2))
        self.label = tk.Label(self.tab, text='', bg='#e9edf4', fg='#5b6574', cursor='hand2')
        self.label.pack(side='left')
        self.close_button = tk.Label(self.tab, text='×', bg='#e9edf4', fg='#8a94a3', cursor='hand2')
        self.close_button.pack(side='left', padx=(7, 0))
        self.tab.bind('<Button-1>', lambda event: app.activate_doc(self))
        self.label.bind('<Button-1>', lambda event: app.activate_doc(self))
        self.close_button.bind('<Button-1>', lambda event: app.close_document(self))
        self.tab.bind('<Button-2>', lambda event: app.close_document(self))
        self.label.bind('<Button-2>', lambda event: app.close_document(self))
        self.update_tab()

    @property
    def name(self):
        return self.path.name if self.path else f'未命名 {self.number}'

    def scroll_view(self, *args):
        self.editor.yview(*args)
        if self.app.doc is self:
            self.app.lines()

    def on_scroll(self, first, last):
        self.scroll.set(first, last)
        if self.app.doc is self:
            self.app.lines()

    def update_tab(self):
        active = self.app.doc is self
        background = '#ffffff' if active else '#e9edf4'
        foreground = '#1c2430' if active else '#5b6574'
        dirty = '● ' if self.editor.edit_modified() else ''
        self.label.configure(text=dirty + self.name, bg=background, fg=foreground)
        self.tab.configure(bg=background)
        self.close_button.configure(bg=background)


class App(DisplayFeatures, EditorFeatures, DebugFeatures):
    def __init__(self, root):
        self.root = root
        self._doc = None
        self.docs = []
        self.proc = None
        self.running = False
        self.cancelled = threading.Event()
        self.popup = None
        self.member_completion = False
        self.popup_auto_empty = False
        self.prefix = ''
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
        for label, action in [('新建 Ctrl+N', self.new), ('打开 Ctrl+O', self.open), ('保存 Ctrl+S', self.save), ('编译 F7', lambda: self.build(False)), ('运行 F5', self.run_or_continue), ('停止', self.stop), ('设置编译器', self.settings)]:
            ttk.Button(bar, text=label, command=action).pack(side='left', padx=3)
        self.standard = tk.StringVar(value=self.config.get('standard', 'c++17'))
        ttk.Combobox(bar, textvariable=self.standard, values=['c++11', 'c++14', 'c++17', 'c++20', 'c++23'], state='readonly', width=8).pack(side='right')
        ttk.Label(bar, text='补全 Ctrl+Space / Tab    ').pack(side='right')
        self.wrap_toolbar(bar)
        editbar = ttk.Frame(root, padding=(8, 0, 8, 5))
        editbar.pack(fill='x')
        for label, action in [('撤销 Ctrl+Z', self.undo_edit), ('回退 Ctrl+Y', self.redo_edit), ('格式化 Ctrl+Shift+F', self.format_code), ('关闭标签 Ctrl+W', self.close_document)]:
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
        self.tabbar = tk.Frame(editframe, bg='#dfe5ee')
        self.tabbar.pack(side='top', fill='x')
        self.new_tab_button = tk.Label(self.tabbar, text='+ 新建', bg='#dfe5ee', fg='#4a5566',
                                       cursor='hand2', padx=8, pady=3)
        self.new_tab_button.pack(side='right')
        self.new_tab_button.bind('<Button-1>', lambda event: self.new())
        self.editbody = ttk.Frame(editframe)
        self.editbody.pack(side='top', fill='both', expand=True)
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
        self.setup_editor_features()
        self.setup_debug(right)
        # Reserve toolbars/status first; let the central panes use the rest.
        panes.pack_forget()
        panes.pack(fill='both', expand=True, padx=8)
        # Create the first tab once the pane layout is in place.
        self.add_document(TEMPLATE)
        self.update_gutter_width()
        for widget in (self.stdin, self.output, self.stack_view):
            self.bind_zoom(widget)
        root.bind('<Control-s>', lambda e: self.save())
        root.bind('<Control-o>', lambda e: self.open())
        root.bind('<Control-n>', lambda e: self.new())
        root.bind('<Control-Shift-N>', lambda e: self.new(switch=True))
        root.bind('<Control-Shift-n>', lambda e: self.new(switch=True))
        root.bind('<Control-w>', self.close_tab_event)
        root.bind('<F5>', lambda e: self.run_or_continue())
        root.bind('<F7>', lambda e: self.build(False))
        root.bind('<Control-Return>', lambda e: open_samples(self))
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.editor.focus_set()
        self.highlight()
        # Let the wrapped toolbars and panes take their real sizes before the
        # first frame is shown; otherwise the editor stays short for a moment.
        self.root.update()

    # ------------------------------------------------------------------ tabs
    @property
    def doc(self):
        return self._doc

    @property
    def editor(self):
        return self._doc.editor

    @property
    def numbers(self):
        return self._doc.gutter

    @property
    def path(self):
        return self._doc.path if self._doc else None

    @path.setter
    def path(self, value):
        if self._doc:
            self._doc.path = Path(value) if value else None

    @property
    def breakpoints(self):
        return self._doc.breakpoints if self._doc else set()

    @property
    def diagnostic_source(self):
        return self._doc.diagnostic_source if self._doc else None

    @diagnostic_source.setter
    def diagnostic_source(self, value):
        if self._doc:
            self._doc.diagnostic_source = value

    def configure_editor(self, editor):
        for tag, color in [('keyword', '#c792ea'), ('string', '#c3e88d'), ('comment', '#718096'), ('number', '#f78c6c'), ('stl', '#82aaff')]:
            editor.tag_configure(tag, foreground=color)
        for n, color in enumerate(('#ffd166', '#80cbc4', '#b39ddb', '#82aaff', '#f6a6cf', '#b5db7b')):
            editor.tag_configure(f'bracket_{n}', foreground=color)
        editor.tag_configure('bracket_match', background='#365e78', foreground='#ffffff', underline=True)
        editor.tag_configure('bracket_bad', background='#723442', foreground='#ffffff')
        editor.tag_configure('compile_error', background='#602c36')
        editor.tag_configure('compile_warning', background='#4d4126')
        editor.tag_configure('debug_line', background='#665523')
        editor.tag_raise('sel')

    def next_untitled_number(self):
        """Reuse the smallest number freed by closing an untitled tab."""
        used = {doc.number for doc in self.docs if doc.path is None}
        number = 1
        while number in used:
            number += 1
        return number

    def add_document(self, content='', path=None, select=True):
        doc = Document(self, content, path, self.next_untitled_number())
        self.docs.append(doc)
        self.configure_editor(doc.editor)
        self.bind_editor(doc.editor)
        self.bind_zoom(doc.editor)
        doc.gutter.bind('<Button-1>', self.gutter_breakpoint)
        if select or self._doc is None:
            self.activate_doc(doc, force=True)
        return doc

    def activate_doc(self, doc, force=False):
        if doc not in self.docs:
            return
        if not force and (self.running or getattr(self, 'debug_state', 'idle') != 'idle'):
            self.status.set('运行或调试期间不能切换标签页。')
            return
        previous = self._doc
        if previous is doc:
            return
        self.hide_popup()
        if previous is not None:
            self.cancel_selection_drag()
            previous.frame.pack_forget()
            previous.update_tab()
        self._doc = doc
        doc.frame.pack(fill='both', expand=True)
        doc.update_tab()
        self._bracket_source = None
        if hasattr(self, 'diagnostics'):
            self.render_diagnostics()
        self.highlight()
        doc.editor.focus_set()

    def remove_document(self, doc):
        self.hide_popup()
        index = self.docs.index(doc)
        was_active = self._doc is doc
        doc.tab.destroy()
        doc.frame.destroy()
        self.docs.remove(doc)
        if was_active:
            self._doc = None
            if self.docs:
                self.activate_doc(self.docs[min(index, len(self.docs) - 1)], force=True)
            else:
                self.add_document(TEMPLATE)
        self.highlight()

    def close_document(self, doc=None):
        if self.busy():
            self.status.set('请先结束运行或调试，再关闭标签页。')
            return
        self.hide_popup()
        doc = doc or self._doc
        if doc is None:
            return
        if doc.editor.edit_modified():
            self.activate_doc(doc, force=True)
            answer = messagebox.askyesnocancel('关闭标签页', f'{doc.name} 有未保存的修改，是否保存？')
            if answer is None:
                return
            if answer and not self.save():
                return
        self.remove_document(doc)

    def close_tab_event(self, event=None):
        self.close_document()
        return 'break'

    # -------------------------------------------------------------- editing
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
            if event.char and (event.char.isalnum() or event.char in '_.>'):
                self.complete(auto=True)
            else:
                self.hide_popup()
        if self._doc:
            self._doc.update_tab()

    def highlight(self):
        if self._doc is None:
            return
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
        self._doc.update_tab()
        self.root.title(f'{self._doc.name} · AlgoCpp')
        pos = self.editor.index('insert')
        saved = '' if self.path else '未保存（可直接运行）    '
        self.status.set(f'{self._doc.name}    {saved}行列 {pos}    编译器：{self.compiler or "未配置，请选择 g++.exe 或 clang++.exe"}')

    def hide_popup(self):
        if self.popup:
            self.popup.destroy()
            self.popup = None

    def complete(self, event=None, auto=False):
        text = self.editor.get('1.0', 'insert')
        if re.search(r'[A-Za-z_]\w*[ \t]*(?:\.|->)[ \t]*(?:[A-Za-z_]\w*)?\Z', text[-200:]):
            info = analyze(scan(self.editor.get('1.0', 'end-1c'))[0], len(text))
        else:
            info = {'receiver': None, 'type': None, 'prefix': '', 'types': {}}
        self.hide_popup()
        member_mode = bool(info['receiver'] and info['type'] in MEMBERS)
        self.member_completion = member_mode
        if member_mode:
            prefix = info['prefix']
            self.prefix = prefix
            members = MEMBERS[info['type']]
            matches = [word for word in members if word.startswith(prefix) and word != prefix]
        else:
            m = re.search(r'[A-Za-z_]\w*$', text)
            prefix = m.group() if m else ''
            if auto and len(prefix) < 2:
                return
            self.prefix = prefix
            identifiers = re.findall(r'\b[A-Za-z_]\w*\b', self.editor.get('1.0', 'end-1c'))
            matches = sorted(x for x in set(WORDS + list(SNIPPETS) + identifiers) if x.startswith(prefix) and x != prefix)
            if not auto and prefix in SNIPPETS:
                matches.insert(0, prefix)
        if not matches:
            return 'break'
        self.popup_auto_empty = bool(member_mode and auto and not prefix)
        box = self.editor.bbox('insert')
        if not box:
            return 'break'
        self.popup = tk.Toplevel(self.root)
        self.popup.overrideredirect(True)
        # A topmost main window would otherwise hide the popup behind it.
        self.popup.attributes('-topmost', bool(self.root.attributes('-topmost')))
        x, y, w, h = box
        self.popup.geometry(f'+{self.editor.winfo_rootx()+x}+{self.editor.winfo_rooty()+y+h}')
        self.choices = tk.Listbox(self.popup, height=min(8, len(matches)), width=40, font=self.io_font, exportselection=False)
        self.choices.pack()
        self.bind_zoom(self.choices)
        for word in matches:
            self.choices.insert('end', word)
        self.choices.selection_set(0)
        self.choices.bind('<ButtonRelease-1>', lambda e: self.accept())
        self.popup.lift()
        return 'break'

    def popup_move(self, delta):
        if self.popup:
            self.popup_auto_empty = False
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
            self.editor.insert('insert', member_snippet(word) if self.member_completion else SNIPPETS.get(word, word))
        self.hide_popup()
        self.editor.focus_set()
        self.changed()

    # ----------------------------------------------------------------- files
    def busy(self):
        return self.running or getattr(self, 'debug_state', 'idle') != 'idle'

    def reset_document(self, doc):
        """Drop breakpoints and stale diagnostics when a tab gets new content."""
        for name in list(doc.breakpoints):
            doc.editor.mark_unset(name)
        doc.breakpoints.clear()
        doc.diagnostics = []
        doc.diagnostic_source = None
        for tag in ('compile_error', 'compile_warning', 'debug_line'):
            doc.editor.tag_remove(tag, '1.0', 'end')
        if hasattr(self, 'diagnostics'):
            self.diagnostics.delete(*self.diagnostics.get_children())

    def new(self, switch=False):
        if self.busy():
            self.status.set('请先结束运行或调试，再新建标签页。')
            return
        self.hide_popup()
        doc = self.add_document(TEMPLATE, select=switch)
        if switch:
            self.status.set(f'已新建 {doc.name}；未保存也能直接按 F5 运行。')
        else:
            self.status.set(f'已在后台新建 {doc.name}（点击标签切换；Ctrl+Shift+N 新建并切换）。')

    def open(self):
        if self.busy():
            self.status.set('请先结束运行或调试，再打开文件。')
            return
        self.hide_popup()
        path = filedialog.askopenfilename(filetypes=[('C++ 文件', '*.cpp *.cc *.cxx *.h *.hpp'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding='utf-8-sig')
        except (OSError, UnicodeError) as exc:
            messagebox.showerror('打开失败', str(exc))
            return
        current = self._doc
        if current and current.path is None and not current.editor.edit_modified():
            # Reuse the pristine start tab instead of leaving an empty one behind.
            self.reset_document(current)
            current.editor.delete('1.0', 'end')
            current.editor.insert('1.0', text)
            current.path = Path(path)
            current.editor.edit_reset()
            current.editor.edit_modified(False)
            current.update_tab()
            self.highlight()
        else:
            self.add_document(text, Path(path))
        self.status.set(f'已打开 {Path(path).name}')

    def save(self):
        if self._doc is None:
            return False
        path = self.path or filedialog.asksaveasfilename(defaultextension='.cpp', filetypes=[('C++ 文件', '*.cpp')])
        if not path:
            return False
        try:
            Path(path).write_text(self.editor.get('1.0', 'end-1c'), encoding='utf-8')
            self.path = Path(path)
            self.editor.edit_modified(False)
            self._doc.update_tab()
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
            CONFIG.write_text(json.dumps({'compiler': portable(self.compiler), 'standard': self.standard.get(), 'gdb': portable(getattr(self, 'gdb_path', '')), 'font_size': self.font_size}, ensure_ascii=False, indent=2), encoding='utf-8')
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
        self.hide_popup()
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
        self.hide_popup()
        if self.running or getattr(self, 'debug_state', 'idle') != 'idle':
            if not messagebox.askyesno('退出', '程序正在运行或调试，退出会停止它。确定退出？'):
                return
            self.stop()
        for doc in list(self.docs):
            if doc.editor.edit_modified():
                self.activate_doc(doc, force=True)
                answer = messagebox.askyesnocancel('保存代码', f'{doc.name} 有未保存的修改，是否保存？')
                if answer is None:
                    return
                if answer and not self.save():
                    return
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
