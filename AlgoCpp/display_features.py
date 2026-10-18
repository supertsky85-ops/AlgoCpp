"""High DPI startup, screen-sized windows, and shared scalable editor fonts."""
import sys
import tkinter.font as tkfont


def enable_high_dpi():
    # Must run before Tk creates its first HWND. System awareness is supported
    # by the bundled Tk 8.6 without requiring per-monitor WM_DPICHANGED logic.
    if sys.platform == 'win32':
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except (AttributeError, OSError):
                pass


def fit_window(window, width=1600, height=1000):
    scale = max(1.0, window.winfo_fpixels('1i') / 96)
    screen_w, screen_h = window.winfo_screenwidth(), window.winfo_screenheight()
    width = min(round(width * scale), round(screen_w * .94))
    height = min(round(height * scale), round(screen_h * .90))
    window.geometry(f'{width}x{height}+{max(0, (screen_w-width)//2)}+{max(0, (screen_h-height)//3)}')
    window.minsize(min(round(850*scale), width), min(round(600*scale), height))


class DisplayFeatures:
    def setup_display(self):
        from tkinter import StringVar, ttk
        # The first DPI-unaware release was 1200x820 logical pixels: Windows
        # enlarged it with display scaling. Match that on-screen footprint now
        # that Tk draws at native resolution instead of using bitmap scaling.
        screen_w, screen_h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        scale = max(1.0, round(self.root.winfo_fpixels('1i')) / 96)
        width = min(round(1200 * scale), int(screen_w*.94))
        height = min(round(820 * scale), int(screen_h*.90))
        self.root.geometry(f'{width}x{height}+{max(0, (screen_w-width)//2)}+{max(0, (screen_h-height)//3)}')
        self.root.minsize(min(640, width), min(480, height))
        self.root.resizable(True, True)
        ttk.Style(self.root).configure('Treeview', rowheight=max(24, tkfont.nametofont('TkDefaultFont').metrics('linespace') + 8))
        try:
            self.font_size = max(9, min(32, int(self.config.get('font_size', 14))))
        except (ValueError, TypeError):
            self.font_size = 14
        self.code_font = tkfont.Font(root=self.root, family='Consolas', size=self.font_size)
        self.io_font = tkfont.Font(root=self.root, family='Consolas', size=max(9, self.font_size-2))
        self.small_font = tkfont.Font(root=self.root, family='Consolas', size=max(9, self.font_size-2))
        self.zoom_label = StringVar(value=f'{round(self.font_size/14*100)}%')
        # Topmost is a per-session choice: every launch starts with it off.
        self.root.attributes('-topmost', False)
        self.topmost_label = StringVar(value='窗口置顶：开' if self.root.attributes('-topmost') else '窗口置顶：关')
        self.root.bind('<Control-plus>', lambda e: self.zoom_font(1))
        self.root.bind('<Control-equal>', lambda e: self.zoom_font(1))
        self.root.bind('<Control-minus>', lambda e: self.zoom_font(-1))
        self.root.bind('<Control-0>', lambda e: self.zoom_font(14-self.font_size))

    def wrap_toolbar(self, frame):
        """Wrap controls on narrow split-screen windows without hiding buttons."""
        children = frame.winfo_children()
        for child in children:
            child.pack_forget()
        frame.pack_propagate(False)
        state = {'pending': False, 'layout': None}

        def layout():
            state['pending'] = False
            if not frame.winfo_exists():
                return
            available = max(1, frame.winfo_width() - 20)
            used, top, row_height, placements = 0, 3, 0, []
            for child in children:
                width = child.winfo_reqwidth() + 6
                if used and used + width > available:
                    top += row_height + 4
                    used, row_height = 0, 0
                placements.append((used+10, top, child.winfo_reqwidth(), child.winfo_reqheight()))
                used += width
                row_height = max(row_height, child.winfo_reqheight())
            if placements != state['layout']:
                for child, (x, y, width, height) in zip(children, placements):
                    child.place(x=x, y=y, width=width, height=height)
                frame.configure(height=top+row_height+5)
                state['layout'] = placements

        def schedule(event=None):
            if not state['pending']:
                state['pending'] = True
                frame.after_idle(layout)
        frame.bind('<Configure>', schedule, add='+')
        schedule()

    def bind_zoom(self, widget):
        widget.bind('<Control-MouseWheel>', lambda e: self.zoom_font(1 if e.delta > 0 else -1) if e.delta else 'break')

    def update_gutter_width(self):
        self.gutter_width = max(53, self.small_font.measure('00000') + 20)
        for doc in getattr(self, 'docs', ()):
            doc.gutter.configure(width=self.gutter_width)

    def zoom_font(self, delta):
        self.font_size = max(9, min(32, self.font_size + delta))
        self.code_font.configure(size=self.font_size)
        self.io_font.configure(size=max(9, self.font_size-2))
        self.small_font.configure(size=max(9, self.font_size-2))
        self.zoom_label.set(f'{round(self.font_size/14*100)}%')
        self.update_gutter_width()
        self.root.after_idle(self.lines)
        return 'break'

    def toggle_maximize(self):
        self.root.state('normal' if self.root.state() == 'zoomed' else 'zoomed')

    def toggle_topmost(self):
        enabled = not bool(self.root.attributes('-topmost'))
        self.root.attributes('-topmost', enabled)
        self.topmost_label.set('窗口置顶：开' if enabled else '窗口置顶：关')
        samples = getattr(self, 'samples_window', None)
        if samples and samples.window.winfo_exists():
            samples.window.attributes('-topmost', enabled)
        popup = getattr(self, 'popup', None)
        if popup and popup.winfo_exists():
            popup.attributes('-topmost', enabled)
