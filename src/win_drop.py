"""Windows 原生文件拖拽支持（WM_DROPFILES），无需第三方依赖。

同时挂接顶层窗口与全部子窗口，确保把文件拖到文件列表、原文、日志、
按钮等任意子控件上都能收到拖放。
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

if os.name != "nt":
    raise ImportError("win_drop is only available on Windows")

WM_DROPFILES = 0x0233
GWL_WNDPROC = -4

LRESULT = ctypes.c_ssize_t
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)
ENUM_CHILD_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

_user32 = ctypes.windll.user32
_shell32 = ctypes.windll.shell32

if ctypes.sizeof(ctypes.c_void_p) == 8:
    _get_window_long = _user32.GetWindowLongPtrW
    _set_window_long = _user32.SetWindowLongPtrW
else:
    _get_window_long = _user32.GetWindowLongW
    _set_window_long = _user32.SetWindowLongW

_get_window_long.argtypes = [wintypes.HWND, ctypes.c_int]
_get_window_long.restype = ctypes.c_void_p
_set_window_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
_set_window_long.restype = ctypes.c_void_p

_call_window_proc = _user32.CallWindowProcW
_call_window_proc.argtypes = [
    ctypes.c_void_p,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_call_window_proc.restype = LRESULT

_def_window_proc = _user32.DefWindowProcW
_def_window_proc.argtypes = [
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
]
_def_window_proc.restype = LRESULT

_get_parent = _user32.GetParent
_get_parent.argtypes = [wintypes.HWND]
_get_parent.restype = wintypes.HWND

_enum_child_windows = _user32.EnumChildWindows
_enum_child_windows.argtypes = [wintypes.HWND, ENUM_CHILD_PROC, wintypes.LPARAM]
_enum_child_windows.restype = wintypes.BOOL

_drag_accept_files = _shell32.DragAcceptFiles
_drag_accept_files.argtypes = [wintypes.HWND, wintypes.BOOL]
_drag_accept_files.restype = None

_drag_query_file = _shell32.DragQueryFileW
_drag_query_file.argtypes = [
    wintypes.HANDLE,
    wintypes.UINT,
    wintypes.LPWSTR,
    wintypes.UINT,
]
_drag_query_file.restype = wintypes.UINT

_drag_finish = _shell32.DragFinish
_drag_finish.argtypes = [wintypes.HANDLE]
_drag_finish.restype = None


def _as_handle_int(value) -> int:
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    try:
        return int(value)
    except (TypeError, ValueError):
        pass
    try:
        return int(getattr(value, "value", 0) or 0)
    except (TypeError, ValueError):
        return 0


class WinDropTarget:
    """把 Tk 顶层窗口及其子控件注册为 Windows 文件拖放目标。"""

    def __init__(self, root, callback):
        self.root = root
        self.callback = callback
        self._new_proc = WNDPROC(self._window_proc)
        self._old_procs = {}
        self._hwnds = []

        root.update_idletasks()
        frame = self._frame_handle()
        client = root.winfo_id()

        candidates = []
        seen = set()

        def add(hwnd) -> bool:
            key = _as_handle_int(hwnd)
            if key and key not in seen:
                seen.add(key)
                candidates.append(key)
            return True

        enum_callback = ENUM_CHILD_PROC(lambda hwnd, _lp: add(hwnd))
        if frame:
            add(frame)
            _enum_child_windows(frame, enum_callback, 0)
        add(client)
        _enum_child_windows(client, enum_callback, 0)

        def collect_widget(widget) -> None:
            try:
                add(widget.winfo_id())
            except Exception:
                pass
            try:
                for child in widget.winfo_children():
                    collect_widget(child)
            except Exception:
                pass

        collect_widget(root)

        new_pointer = ctypes.cast(self._new_proc, ctypes.c_void_p)
        for hwnd in candidates:
            try:
                _drag_accept_files(hwnd, True)
                old = _get_window_long(hwnd, GWL_WNDPROC)
                if not old:
                    continue
                _set_window_long(hwnd, GWL_WNDPROC, new_pointer)
                self._old_procs[hwnd] = _as_handle_int(old)
                self._hwnds.append(hwnd)
            except Exception:
                continue

        if not self._hwnds:
            raise OSError("未能安装任何拖拽窗口过程")

    @property
    def window_count(self) -> int:
        return len(self._hwnds)

    def _frame_handle(self):
        try:
            frame = self.root.wm_frame()
            if isinstance(frame, str):
                value = int(frame, 16) if frame.lower().startswith("0x") else int(frame)
            else:
                value = int(frame)
            if value:
                return value
        except Exception:
            pass
        try:
            parent = _get_parent(self.root.winfo_id())
            return int(parent) if parent else self.root.winfo_id()
        except Exception:
            return self.root.winfo_id()

    def _window_proc(self, hwnd, msg, wparam, lparam):
        if msg == WM_DROPFILES:
            paths = []
            try:
                count = _drag_query_file(wparam, 0xFFFFFFFF, None, 0)
                for index in range(count):
                    length = _drag_query_file(wparam, index, None, 0)
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    _drag_query_file(wparam, index, buffer, length + 1)
                    if buffer.value:
                        paths.append(buffer.value)
            finally:
                _drag_finish(wparam)
            if paths:
                self.root.after(0, self.callback, paths)
            return 0

        key = _as_handle_int(hwnd)
        old = self._old_procs.get(key)
        if old:
            return _call_window_proc(
                ctypes.c_void_p(old),
                hwnd,
                msg,
                wparam,
                lparam,
            )
        return _def_window_proc(hwnd, msg, wparam, lparam)
