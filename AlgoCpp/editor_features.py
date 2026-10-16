"""C++ lexical bracket matching and editor commands (no Python dependencies)."""
import re
from bisect import bisect_right
import shutil
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from tkinter import messagebox, StringVar, ttk, TclError

PAIRS = {'(': ')', '[': ']', '{': '}'}
QUOTES = {'"': '"', "'": "'"}
INDENT = ' ' * 4
STYLE = ('{BasedOnStyle: LLVM, IndentWidth: 4, TabWidth: 4, UseTab: Never, '
         'ColumnLimit: 100, BreakBeforeBraces: Attach, '
         'AllowShortFunctionsOnASingleLine: None, AllowShortBlocksOnASingleLine: Never, '
         'AllowShortIfStatementsOnASingleLine: Never, AllowShortLoopsOnASingleLine: false, '
         'SortIncludes: Never, FixNamespaceComments: false}')


def scan(source):
    """Return code-only text and ignored ranges; retain offsets and newlines."""
    chars = list(source)
    ignored = []
    i = 0
    while i < len(source):
        start = i
        closed = True
        if source.startswith('//', i):
            end = source.find('\n', i)
            i = len(source) if end < 0 else end
            closed = False
        elif source.startswith('/*', i):
            end = source.find('*/', i + 2)
            closed = end >= 0
            i = end + 2 if closed else len(source)
        elif source.startswith('R"', i) and (raw := re.match(r'R"([^\s()\\]{0,16})\(', source[i:])):
            ending = ')' + raw[1] + '"'
            end = source.find(ending, i + raw.end())
            closed = end >= 0
            i = end + len(ending) if closed else len(source)
        elif source[i].isdigit() and (i == 0 or not (source[i-1].isalnum() or source[i-1] == '_')):
            # Apostrophes inside numeric literals (1'000, 0xff'ff) are separators.
            token = re.match(r"[0-9][\w'.]*", source[i:])
            i += token.end()
            continue
        elif source[i] in QUOTES:
            quote = source[i]
            i += 1
            closed = False
            while i < len(source):
                if source[i] == '\\':
                    i = min(i + 2, len(source))
                elif source[i] == quote:
                    i += 1
                    closed = True
                    break
                elif source[i] == '\n':
                    break
                else:
                    i += 1
        else:
            i += 1
            continue
        ignored.append((start, i, closed))
        chars[start:i] = ['\n' if c == '\n' else ' ' for c in source[start:i]]
    return ''.join(chars), ignored


def bracket_data(source):
    code, ignored = scan(source)
    stack, matches, depths, bad = [], {}, {}, set()
    for i, char in enumerate(code):
        if char in PAIRS:
            depths[i] = len(stack)
            stack.append((i, char))
        elif char in PAIRS.values():
            if stack and PAIRS[stack[-1][1]] == char:
                start, _ = stack.pop()
                matches[start] = i
                matches[i] = start
                depths[i] = depths[start]
            else:
                bad.add(i)
    bad.update(i for i, _ in stack)
    if any(code[i] in '{}' for i in bad):
        recover_braces(code, matches, depths, bad)
    return code, ignored, matches, depths, bad


def recover_braces(code, matches, depths, bad):
    """Recover malformed blocks using indentation, leaving ambiguous braces red.

    Indentation is a recovery hint only: a lexically balanced block is never
    reinterpreted, since indentation is not part of C++ grammar.
    """
    line_starts = [0] + [m.end() for m in re.finditer('\n', code)]
    lines = code.split('\n')
    indents = [len(re.match(r'[ \t]*', line)[0].expandtabs(4)) for line in lines]

    def location(pos):
        line = bisect_right(line_starts, pos) - 1
        return line, indents[line]

    # A missing inner closer may leave the outermost opener on the lexical
    # stack. Revisit its entire brace component, rather than coloring that
    # outer opener alone and reporting false matches in the inner blocks.
    components, current, level = [], [], 0
    for pos, char in enumerate(code):
        if char not in '{}':
            continue
        current.append(pos)
        level += 1 if char == '{' else -1
        if level <= 0:
            components.append(current)
            current, level = [], 0
    if current:
        components.append(current)
    for component in components:
        if not any(pos in bad for pos in component):
            continue
        # Remove tentative stack matches before building only confident pairs.
        for pos in component:
            other = matches.pop(pos, None)
            if other is not None:
                matches.pop(other, None)
        pending = []
        for pos in component:
            bad.add(pos)
            if code[pos] == '{':
                pending.append(pos)
                continue
            line, indent = location(pos)
            leading = not code[line_starts[line]:pos].strip()
            candidates = [n for n, start in enumerate(pending)
                          if location(start)[0] < line and location(start)[1] == indent]
            if leading and len(candidates) == 1:
                n = candidates[0]
                # An outer closing brace must not be consumed by a deeper if.
                # Only recover when the intervening blocks are indented more.
                if all(location(start)[1] > indent for start in pending[n+1:]):
                    start = pending[n]
                    del pending[n:]
                    matches[start], matches[pos] = pos, start
                    depths[pos] = depths[start]
                    bad.discard(start)
                    bad.discard(pos)
                    continue
            if pending:
                start = pending.pop()
                start_line, _ = location(start)
                # An isolated inline block is still unambiguous even if an
                # enclosing block is incomplete on a different line.
                if start_line == line and lines[line].count('{') == lines[line].count('}') == 1:
                    matches[start], matches[pos] = pos, start
                    depths[pos] = depths[start]
                    bad.discard(start)
                    bad.discard(pos)


class EditorFeatures:
    def setup_editor_features(self):
        self._bracket_source = None
        self._selection_drag = None
        self._drag_scroll_timer = None
        self.bracket_status = StringVar()
        ttk.Label(self.root, textvariable=self.bracket_status, foreground='#b33a46',
                  padding=(8, 0)).pack(side='bottom', fill='x')
        for n, color in enumerate(('#ffd166', '#80cbc4', '#b39ddb', '#82aaff', '#f6a6cf', '#b5db7b')):
            self.editor.tag_configure(f'bracket_{n}', foreground=color)
        self.editor.tag_configure('bracket_match', background='#365e78', foreground='#ffffff', underline=True)
        self.editor.tag_configure('bracket_bad', background='#723442', foreground='#ffffff')
        self.editor.tag_raise('sel')
        self.editor.bind('<Shift-Tab>', self.unindent)
        self.editor.bind('<Control-Shift-F>', self.format_code)
        self.editor.bind('<Control-Shift-f>', self.format_code)
        self.editor.bind('<ButtonPress-1>', self.selection_press)
        self.editor.bind('<B1-Motion>', self.selection_motion)
        self.editor.bind('<Control-z>', self.undo_edit)
        self.editor.bind('<Control-y>', self.redo_edit)
        self.editor.bind('<Control-Shift-Z>', self.redo_edit)
        self.editor.bind('<Control-Shift-z>', self.redo_edit)

    def offset(self, index='insert'):
        return len(self.editor.get('1.0', index))

    def index_at(self, offset):
        # Tk indices count UTF-16 units on Windows; derive line/column for Unicode.
        source = self.editor.get('1.0', 'end-1c')[:offset]
        line = source.count('\n') + 1
        column = len(source.rsplit('\n', 1)[-1].encode('utf-16-le')) // 2
        return f'{line}.{column}'

    def update_brackets(self):
        source = self.editor.get('1.0', 'end-1c')
        if source != self._bracket_source:
            self._bracket_source = source
            self._brackets = bracket_data(source)
            for tag in [f'bracket_{n}' for n in range(6)] + ['bracket_bad']:
                self.editor.tag_remove(tag, '1.0', 'end')
            _, _, matches, depths, bad = self._brackets
            for offset, depth in depths.items():
                if offset in matches:
                    index = self.index_at(offset)
                    self.editor.tag_add(f'bracket_{depth % 6}', index, index + '+1c')
            for offset in bad:
                index = self.index_at(offset)
                self.editor.tag_add('bracket_bad', index, index + '+1c')
        code, _, matches, _, bad = self._brackets
        self.editor.tag_remove('bracket_match', '1.0', 'end')
        pos = self.offset()
        selection = self.editor.tag_ranges('sel')
        single_selection = selection and len(self.editor.get(*selection)) == 1
        if single_selection:
            pos = self.offset(selection[0])
        nearby = [p for p in ((pos,) if single_selection else (pos, pos - 1))
                  if 0 <= p < len(code) and code[p] in '()[]{}']
        # Clicking an unmatched brace must not highlight a nearby successful
        # pair or its enclosing block as if the selected brace were matched.
        active = nearby[0] if nearby else None
        candidate = active if active in matches else None
        if candidate is None and not nearby and not selection:
            # Inside a block, show the nearest enclosing pair.
            candidate = max((a for a, b in matches.items() if a < pos < b), default=None)
        if candidate is not None:
            for p in (candidate, matches[candidate]):
                index = self.index_at(p)
                self.editor.tag_add('bracket_match', index, index + '+1c')
        if hasattr(self, 'bracket_status'):
            if active in bad:
                self.bracket_status.set('此括号未匹配或归属不明确；请检查缺失的括号。')
            elif bad:
                self.bracket_status.set(f'括号结构不完整：{len(bad)} 个括号未匹配或归属不明确。')
            else:
                self.bracket_status.set('')

    def editable(self):
        return str(self.editor.cget('state')) == 'normal'

    def selection_press(self, event):
        self.cancel_selection_drag()
        selection = self.editor.tag_ranges('sel')
        if self.editable() and selection and not event.state & 5:
            target = self.editor.index(f'@{event.x},{event.y}')
            if self.editor.compare(selection[0], '<=', target) and self.editor.compare(target, '<', selection[1]):
                self.hide_popup()
                self.editor.focus_set()
                self.editor.mark_set('_drag_start', selection[0])
                self.editor.mark_set('_drag_end', selection[1])
                self._selection_drag = {'x': event.x, 'y': event.y, 'active': False,
                                        'target': target, 'click': target,
                                        'cursor': self.editor.cget('cursor')}
                return 'break'

    def selection_motion(self, event):
        drag = self._selection_drag
        if not drag:
            return
        drag['last_x'], drag['last_y'] = event.x, event.y
        if abs(event.x - drag['x']) + abs(event.y - drag['y']) >= 5:
            drag['active'] = True
            self.editor.configure(cursor='fleur')
        if drag['active']:
            self.update_drag_target(event.x, event.y)
            if self._drag_scroll_timer is None:
                self.scroll_selection_drag()
        return 'break'

    def update_drag_target(self, x, y):
        if self._selection_drag:
            target = self.editor.index(f'@{x},{y}')
            self._selection_drag['target'] = target
            self.editor.mark_set('insert', target)
            self.status.set('移动选区：松开鼠标放到光标处；Esc 取消。')

    def scroll_selection_drag(self):
        self._drag_scroll_timer = None
        drag = self._selection_drag
        if not drag or not drag['active']:
            return
        y = drag.get('last_y', drag['y'])
        if y < 12 or y > self.editor.winfo_height() - 12:
            self.editor.yview_scroll(-1 if y < 12 else 1, 'units')
            self.update_drag_target(drag.get('last_x', drag['x']), y)
        self._drag_scroll_timer = self.root.after(60, self.scroll_selection_drag)

    def cancel_selection_drag(self):
        if self._drag_scroll_timer is not None:
            self.root.after_cancel(self._drag_scroll_timer)
            self._drag_scroll_timer = None
        if self._selection_drag:
            self.editor.configure(cursor=self._selection_drag['cursor'])
            self._selection_drag = None
        for name in ('_drag_start', '_drag_end'):
            if name in self.editor.mark_names():
                self.editor.mark_unset(name)

    def selection_release(self, event):
        drag = self._selection_drag
        if not drag:
            self.changed(event)
            return
        try:
            if not drag['active']:
                self.editor.tag_remove('sel', '1.0', 'end')
                self.editor.mark_set('insert', drag['click'])
            elif self.editable():
                start, end = self.editor.index('_drag_start'), self.editor.index('_drag_end')
                target = self.editor.index(f'@{event.x},{event.y}')
                inside = self.editor.compare(start, '<=', target) and self.editor.compare(target, '<=', end)
                if not inside:
                    text = self.editor.get(start, end)
                    self.editor.mark_set('_drag_drop', target)
                    self.editor.mark_gravity('_drag_drop', 'left')
                    self.editor.edit_separator()
                    self.editor.configure(autoseparators=False)
                    try:
                        self.editor.delete(start, end)
                        self.editor.mark_set('_drag_finish', '_drag_drop')
                        self.editor.mark_gravity('_drag_finish', 'right')
                        self.editor.insert('_drag_drop', text)
                        start = self.editor.index('_drag_drop')
                        # Marks track the exact inserted range, including emoji.
                        end = self.editor.index('_drag_finish')
                        self.editor.tag_remove('sel', '1.0', 'end')
                        self.editor.tag_add('sel', start, end)
                        self.editor.mark_set('insert', end)
                        self.editor.see('insert')
                    finally:
                        self.editor.configure(autoseparators=True)
                        self.editor.edit_separator()
                        self.editor.mark_unset('_drag_drop')
                        self.editor.mark_unset('_drag_finish')
        finally:
            self.cancel_selection_drag()
            self.highlight()
        return 'break'

    def escape_editor(self, event=None):
        self.cancel_selection_drag()
        self.hide_popup()
        self.highlight()
        return 'break'

    def undo_edit(self, event=None):
        return self.edit_history(False)

    def redo_edit(self, event=None):
        return self.edit_history(True)

    def edit_history(self, redo):
        if not self.editable():
            return 'break'
        self.cancel_selection_drag()
        self.hide_popup()
        try:
            if redo:
                self.editor.edit_redo()
            else:
                self.editor.edit_undo()
            self.editor.see('insert')
            self.highlight()
        except TclError:
            self.status.set('没有可以回退的操作。' if redo else '没有可以撤销的操作。')
        self.editor.focus_set()
        return 'break'

    def key(self, event):
        if event.state & 4 or not self.editable():
            return
        ch = event.char
        if not ch:
            return
        selection = self.editor.tag_ranges('sel')
        source = self.editor.get('1.0', 'end-1c')
        pos = self.offset()
        code, ignored = scan(source)
        in_literal = any(a < pos < b or (pos == b and not closed) for a, b, closed in ignored)
        # Skip a closing quote only if it closes a complete string at the caret.
        quote_end = any(closed and pos == b - 1 for a, b, closed in ignored)
        if not selection and ch in QUOTES and quote_end and source[pos:pos+1] == ch:
            self.editor.mark_set('insert', 'insert+1c')
            return 'break'
        if in_literal:
            return
        if ch == '}' and not selection and not self.editor.get('insert linestart', 'insert').strip():
            stack = []
            for i, c in enumerate(code[:pos]):
                if c == '{':
                    stack.append(i)
                elif c == '}' and stack:
                    stack.pop()
            if stack:
                line = source[:stack[-1]].rsplit('\n', 1)[-1]
                indent = re.match(r'[ \t]*', line)[0]
                self.editor.delete('insert linestart', 'insert')
                self.editor.insert('insert', indent)
                if self.editor.get('insert', 'insert+1c') == '}':
                    self.editor.mark_set('insert', 'insert+1c')
                else:
                    self.editor.insert('insert', '}')
                return 'break'
        if not selection and ch in PAIRS.values() and source[pos:pos+1] == ch:
            self.editor.mark_set('insert', 'insert+1c')
            return 'break'
        pairs = {**PAIRS, **QUOTES}
        if ch in pairs:
            # C++ digit separator; do not insert a second apostrophe.
            if ch == "'" and pos and source[pos-1].isalnum() and re.search(r"\b\d[\w']*$", source[:pos]):
                return
            if selection:
                text = self.editor.get(*selection)
                start = self.editor.index(selection[0])
                self.editor.delete(*selection)
                self.editor.insert(start, ch + text + pairs[ch])
                self.editor.mark_set('insert', start + f'+{len(ch + text + pairs[ch])}c')
            else:
                self.editor.insert('insert', ch + pairs[ch])
                self.editor.mark_set('insert', 'insert-1c')
            return 'break'

    def backspace(self, event=None):
        if not self.editable() or self.editor.tag_ranges('sel'):
            return
        if self.editor.get('insert-1c', 'insert+1c') in ('()', '[]', '{}', '""', "''"):
            self.editor.delete('insert-1c', 'insert+1c')
            return 'break'
        before = self.editor.get('insert linestart', 'insert')
        if before and not before.strip() and '\t' not in before:
            count = (len(before) - 1) % 4 + 1
            self.editor.delete(f'insert-{count}c', 'insert')
            return 'break'

    def enter(self, event=None):
        if not self.editable():
            return 'break'
        if self.popup:
            self.accept()
            return 'break'
        selection = self.editor.tag_ranges('sel')
        if selection:
            self.editor.mark_set('insert', selection[0])
            self.editor.delete(*selection)
        before = self.editor.get('insert linestart', 'insert')
        indent = re.match(r'[ \t]*', before)[0].expandtabs(4)
        source = self.editor.get('1.0', 'end-1c')
        pos = self.offset()
        code, ignored = scan(source)
        if any(a < pos < b or (pos == b and not closed) for a, b, closed in ignored):
            self.editor.insert('insert', '\n' + indent)
            return 'break'
        left = code[:pos].rstrip()
        opens = left[-1:] in ('{', '(', '[')
        closing = PAIRS.get(left[-1:])
        if opens and source[pos:pos+1] == closing:
            self.editor.insert('insert', '\n' + indent + INDENT + '\n' + indent)
            self.editor.mark_set('insert', 'insert-1line lineend')
        else:
            self.editor.insert('insert', '\n' + indent + (INDENT if opens else ''))
        return 'break'

    def indent_selection(self, reverse=False):
        if not self.editable():
            return 'break'
        selection = self.editor.tag_ranges('sel')
        if not selection and not reverse:
            self.editor.insert('insert', ' ' * (4 - int(self.editor.index('insert').split('.')[1]) % 4))
            return 'break'
        first = int(self.editor.index(selection[0] if selection else 'insert').split('.')[0])
        last = int(self.editor.index(selection[1] if selection else 'insert').split('.')[0])
        if selection and str(selection[1]).endswith('.0'):
            last -= 1
        self.editor.edit_separator()
        self.editor.configure(autoseparators=False)
        try:
            for line in range(first, last + 1):
                if reverse:
                    text = self.editor.get(f'{line}.0', f'{line}.end')
                    count = 1 if text.startswith('\t') else min(4, len(text) - len(text.lstrip(' ')))
                    self.editor.delete(f'{line}.0', f'{line}.{count}')
                else:
                    self.editor.insert(f'{line}.0', INDENT)
        finally:
            self.editor.configure(autoseparators=True)
            self.editor.edit_separator()
        self.changed()
        return 'break'

    def tab(self, event=None):
        if self.popup:
            self.accept()
            return 'break'
        return self.indent_selection()

    def unindent(self, event=None):
        return self.indent_selection(reverse=True)

    def format_code(self, event=None):
        if not self.editable() or self.running or self.debug_state != 'idle':
            self.status.set('请结束运行或调试后再格式化代码。')
            return 'break'
        self.hide_popup()
        home = Path(__file__).resolve().parent
        bundled = home / 'toolchain' / 'formatter' / 'clang-format.exe'
        formatter = str(bundled) if bundled.is_file() else shutil.which('clang-format')
        if not formatter:
            messagebox.showerror('缺少格式化工具', '请将 clang-format.exe 放到 toolchain/formatter/，或安装 clang-format 并加入 PATH。')
            return 'break'
        source = self.editor.get('1.0', 'end-1c')
        data = source.encode('utf-8')
        cursor = len(source[:self.offset()].encode('utf-8'))
        try:
            if bracket_data(source)[4]:
                raise ValueError('存在未匹配的括号，请先修正红色括号后重试。')
            result = subprocess.run([formatter, '--assume-filename=main.cpp', '--style=' + STYLE,
                                     '--output-replacements-xml', f'--cursor={cursor}'],
                                    input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    timeout=5, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if result.returncode:
                raise ValueError(result.stderr.decode('utf-8', errors='replace'))
            document = ET.fromstring(result.stdout)
            if document.attrib.get('incomplete_format') == 'true':
                raise ValueError('代码结构不完整，请先修正缺失的括号或语法后重试。')
            edits = [(int(r.attrib['offset']), int(r.attrib['length']), r.text or '')
                     for r in document.findall('replacement')]
            # All offsets from clang-format are UTF-8 byte offsets, including cursor.
            converted = [(len(data[:start].decode('utf-8')), len(data[:start+length].decode('utf-8')), text)
                         for start, length, text in edits]
            if not edits:
                self.status.set('代码已经符合排版规范。')
                return 'break'
            self.editor.edit_separator()
            self.editor.configure(autoseparators=False)
            try:
                for start, end, text in reversed(converted):
                    index = self.index_at(start)
                    self.editor.delete(index, self.index_at(end))
                    self.editor.insert(index, text)
            finally:
                self.editor.configure(autoseparators=True)
                self.editor.edit_separator()
            new_data = self.editor.get('1.0', 'end-1c').encode('utf-8')
            new_cursor = int(document.findtext('cursor', str(cursor)))
            self.editor.mark_set('insert', self.index_at(len(new_data[:new_cursor].decode('utf-8', errors='ignore'))))
            self.editor.see('insert')
            self.highlight()
            self.status.set('已格式化：四空格缩进、规范空格与换行。Ctrl+Z 可撤销。')
        except (OSError, ValueError, subprocess.TimeoutExpired, ET.ParseError) as exc:
            messagebox.showerror('格式化失败', str(exc))
        return 'break'
