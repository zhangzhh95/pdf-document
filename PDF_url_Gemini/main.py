import sys
import os
import shutil
import urllib.parse
import datetime
import subprocess
import platform
import tempfile
import ctypes
import time
import json
from ctypes import wintypes
from git import Repo, GitCommandError
import pyperclip

# PyQt6 基础库 (注意：此处仅导入基础UI组件，绝对不要导入 WebEngine 或 ActiveX)
from PyQt6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QPushButton, QLabel, QTreeView, 
                             QLineEdit, QMessageBox, QMenu, QInputDialog,
                             QSplitter, QFrame, QProgressBar, QDialog, QDialogButtonBox,
                             QListWidget, QListWidgetItem, QAbstractItemView, QStyledItemDelegate,
                             QSizePolicy, QFormLayout, QStackedWidget, QPlainTextEdit)
from PyQt6.QtCore import Qt, QDir, QSize, QRectF, QThread, pyqtSignal, QByteArray, QBuffer, QFile, QIODevice, QFileInfo, QMimeData, QSortFilterProxyModel, QTimer, QUrl
from PyQt6.QtGui import QAction, QIcon, QFileSystemModel, QKeySequence, QFont, QShortcut, QColor, QPainter, QPixmap, QPen

# --- 配置文件路径 ---
CONFIG_FILE = "app_config.json"
DEFAULT_CONFIG = {
    "repo_path": r"D:\Github\pdf-document",
    "base_url": "https://zhangzhh95.github.io/pdf-document/"
}

# --- Ghostscript 路径配置 ---
GS_CUSTOM_PATH = r"D:\Program Files\gs\gs10.06.0\bin\gswin64c.exe"
if os.path.exists(GS_CUSTOM_PATH):
    GS_CMD = GS_CUSTOM_PATH
else:
    GS_CMD = 'gswin64c' if platform.system() == 'Windows' else 'gs'

def _resource_path(relative_path):
    base_path = getattr(sys, "_MEIPASS", os.path.abspath(os.path.dirname(__file__)))
    return os.path.join(base_path, relative_path)

def _get_app_icon():
    icon_path = _resource_path(os.path.join("assets", "logo.ico"))
    if os.path.exists(icon_path):
        return QIcon(icon_path)
    return QIcon()

def _read_text_file_best_effort(file_path, max_bytes=2 * 1024 * 1024):
    try:
        size = os.path.getsize(file_path)
        if size > max_bytes:
            return "文件过大，暂不预览（>2MB）"

        with open(file_path, "rb") as f:
            data = f.read()

        if data.startswith(b"\xff\xfe"):
            return data.decode("utf-16-le", errors="replace")
        if data.startswith(b"\xfe\xff"):
            return data.decode("utf-16-be", errors="replace")
        if data.startswith(b"\xef\xbb\xbf"):
            return data.decode("utf-8-sig", errors="replace")

        head = data[:2048]
        if head:
            nul_ratio = head.count(b"\x00") / len(head)
            if nul_ratio >= 0.1:
                for enc in ("utf-16-le", "utf-16-be", "utf-16"):
                    try:
                        return data.decode(enc, errors="replace")
                    except Exception:
                        pass

        non_ascii = sum(1 for b in data if b >= 0x80)
        if non_ascii == 0:
            return data.decode("utf-8", errors="replace")

        candidates = []
        for enc in (
            "utf-8",
            "gb18030",
            "gbk",
            "big5",
            "shift_jis",
            "cp1252",
        ):
            try:
                text = data.decode(enc, errors="replace")
            except Exception:
                continue
            candidates.append((enc, text))

        if not candidates:
            return data.decode("utf-8", errors="replace")

        def score(text):
            bad = text.count("\ufffd")
            control = sum(1 for ch in text if (ord(ch) < 9) or (13 < ord(ch) < 32))
            cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
            cjk_ratio = cjk / max(1, len(text))
            cjk_bonus = cjk if cjk_ratio >= 0.02 else 0
            return bad * 1000 + control * 10 - cjk_bonus

        best = min(candidates, key=lambda x: score(x[1]))
        return best[1]
    except Exception:
        return ""

def _hide_lonely_console_window_on_windows():
    if platform.system() != "Windows":
        return
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return

        def _get_parent_process_name_windows():
            TH32CS_SNAPPROCESS = 0x00000002

            class PROCESSENTRY32W(ctypes.Structure):
                _fields_ = [
                    ("dwSize", wintypes.DWORD),
                    ("cntUsage", wintypes.DWORD),
                    ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wintypes.DWORD),
                    ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD),
                    ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD),
                    ("szExeFile", wintypes.WCHAR * 260),
                ]

            pid = ctypes.windll.kernel32.GetCurrentProcessId()
            snapshot = ctypes.windll.kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
            if snapshot == ctypes.c_void_p(-1).value:
                return None
            try:
                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if not ctypes.windll.kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                    return None

                parent_pid = None
                while True:
                    if entry.th32ProcessID == pid:
                        parent_pid = entry.th32ParentProcessID
                        break
                    if not ctypes.windll.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
                if not parent_pid:
                    return None

                entry = PROCESSENTRY32W()
                entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
                if not ctypes.windll.kernel32.Process32FirstW(snapshot, ctypes.byref(entry)):
                    return None
                while True:
                    if entry.th32ProcessID == parent_pid:
                        return (entry.szExeFile or "").lower()
                    if not ctypes.windll.kernel32.Process32NextW(snapshot, ctypes.byref(entry)):
                        break
                return None
            finally:
                ctypes.windll.kernel32.CloseHandle(snapshot)

        parent = _get_parent_process_name_windows()
        if not parent:
            return

        console_parents = {
            "cmd.exe",
            "powershell.exe",
            "pwsh.exe",
            "wt.exe",
            "windowsterminal.exe",
            "conhost.exe",
            "bash.exe",
            "git-bash.exe",
        }
        if parent not in console_parents:
            ctypes.windll.user32.ShowWindow(hwnd, 0)  # SW_HIDE
    except Exception:
        return

def _cleanup_stray_startup_windows(app, main_window):
    try:
        for w in app.topLevelWidgets():
            if w is None or w is main_window:
                continue
            if not getattr(w, "isVisible", lambda: False)():
                continue
            title = (getattr(w, "windowTitle", lambda: "")() or "").strip()
            size = getattr(w, "size", lambda: None)()
            width = getattr(size, "width", lambda: 0)() if size else 0
            height = getattr(size, "height", lambda: 0)() if size else 0
            if title == "" and width <= 600 and height <= 400:
                try:
                    w.hide()
                    w.close()
                except Exception:
                    pass
    except Exception:
        return

def _make_windows_explorer_preview_icon():
    pix = QPixmap(18, 18)
    pix.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    border = QPen(QColor("#cfcfcf"))
    border.setWidthF(1.4)
    painter.setPen(border)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    outer = QRectF(2.2, 3.0, 13.6, 11.6)
    painter.drawRoundedRect(outer, 2.2, 2.2)

    painter.fillRect(QRectF(3.3, 4.2, 3.0, 9.2), QColor("#0078d4"))
    painter.end()

    return QIcon(pix)

def _get_windows_inet_proxy():
    if platform.system() != "Windows":
        return None
    try:
        import winreg
        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return None
            proxy, _ = winreg.QueryValueEx(key, "ProxyServer")

        if not proxy or not isinstance(proxy, str):
            return None
        proxy = proxy.strip()
        if not proxy:
            return None

        if "=" in proxy:
            parts = {}
            for seg in proxy.split(";"):
                if "=" not in seg:
                    continue
                k, v = seg.split("=", 1)
                parts[k.strip().lower()] = v.strip()
            proxy = (parts.get("https") or parts.get("http") or "").strip()

        if not proxy:
            return None
        if "://" not in proxy:
            proxy = "http://" + proxy
        return proxy
    except Exception:
        pass

    try:
        startupinfo = None
        if platform.system() == "Windows":
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        r = subprocess.run(
            ["netsh", "winhttp", "show", "proxy"],
            capture_output=True,
            text=True,
            startupinfo=startupinfo,
        )
        out = (r.stdout or "") + "\n" + (r.stderr or "")
        if "Direct access" in out:
            return None

        for line in out.splitlines():
            if "Proxy Server" in line:
                _, val = line.split(":", 1)
                val = val.strip()
                if not val:
                    continue
                if "://" not in val:
                    val = "http://" + val
                return val
        return None
    except Exception:
        return None

def _run_git_cli(repo_path, git_args, env_overrides=None, timeout_sec=90):
    cmd = ["git", "-C", repo_path] + list(git_args)
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("GCM_INTERACTIVE", "Never")
    if env_overrides:
        env.update(env_overrides)

    startupinfo = None
    if platform.system() == "Windows":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            errors="replace",
            env=env,
            startupinfo=startupinfo,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        raise GitCommandError(cmd, status="timeout", stderr=f"git timeout after {timeout_sec}s")

    if r.returncode != 0:
        raise GitCommandError(cmd, status=r.returncode, stderr=r.stderr, stdout=r.stdout)
    return r.stdout, r.stderr

def _git_push_with_timeout(repo_path, proxy=None, timeout_sec=180):
    git_args = [
        "-c", "http.connectTimeout=10",
        "-c", "http.lowSpeedLimit=1",
        "-c", "http.lowSpeedTime=20",
        "push", "--porcelain", "origin",
    ]
    env_overrides = None
    if proxy:
        env_overrides = {
            "HTTP_PROXY": proxy,
            "HTTPS_PROXY": proxy,
            "http_proxy": proxy,
            "https_proxy": proxy,
        }
    _run_git_cli(repo_path, git_args, env_overrides=env_overrides, timeout_sec=timeout_sec)

class ConfigManager:
    @staticmethod
    def load():
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return DEFAULT_CONFIG
        return DEFAULT_CONFIG

    @staticmethod
    def save(config):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Config save failed: {e}")

class ConfigDialog(QDialog):
    def __init__(self, current_repo, current_url, parent=None):
        super().__init__(parent)
        self.setWindowTitle("⚙️ 设置")
        self.resize(500, 150)
        self.apply_styles()
        
        layout = QVBoxLayout(self)
        form = QFormLayout()
        
        self.repo_edit = QLineEdit(current_repo)
        self.url_edit = QLineEdit(current_url)
        
        form.addRow("Git 本地仓库路径:", self.repo_edit)
        form.addRow("GitHub Pages URL:", self.url_edit)
        
        layout.addLayout(form)
        
        btns = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        layout.addWidget(btns)

    def get_data(self):
        return self.repo_edit.text().strip(), self.url_edit.text().strip()

    def apply_styles(self):
        self.setStyleSheet("""
            QDialog { background-color: #2b2b2b; color: #fff; font-family: "Microsoft YaHei"; }
            QLabel { color: #ccc; font-size: 14px; }
            QLineEdit { background-color: #333; border: 1px solid #555; padding: 5px; color: #fff; border-radius: 4px; }
            QPushButton { background-color: #007acc; color: white; border: none; padding: 6px 15px; border-radius: 4px; }
            QPushButton:hover { background-color: #0062a3; }
        """)

class GitStatusWorker(QThread):
    result_signal = pyqtSignal(int, bool) 

    def __init__(self, repo_path):
        super().__init__()
        self.repo_path = repo_path

    def run(self):
        try:
            repo = Repo(self.repo_path)
            count = len(repo.index.diff(None)) + len(repo.index.diff("HEAD")) + len(repo.untracked_files)
            self.result_signal.emit(count, True)
        except Exception:
            self.result_signal.emit(0, False)

class GitWorker(QThread):
    status_signal = pyqtSignal(str) 
    finished_signal = pyqtSignal(bool, str)

    def __init__(self, repo_path):
        super().__init__()
        self.repo_path = repo_path

    def run(self):
        try:
            self.status_signal.emit("正在连接 Git 仓库...")
            repo = Repo(self.repo_path)
            
            gitignore_path = os.path.join(self.repo_path, ".gitignore")
            trash_ignore_rule = ".trash_bin/"
            need_write = True
            if os.path.exists(gitignore_path):
                with open(gitignore_path, 'r', encoding='utf-8') as f:
                    if trash_ignore_rule in f.read():
                        need_write = False
            
            if need_write:
                with open(gitignore_path, 'a', encoding='utf-8') as f:
                    f.write(f"\n{trash_ignore_rule}\n")

            self.status_signal.emit("正在添加文件变更 (git add)...")
            repo.git.add('--all')
            
            if repo.is_dirty() or repo.untracked_files:
                self.status_signal.emit("正在提交更改 (git commit)...")
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                repo.index.commit(f"Update: {timestamp}")
            else:
                self.status_signal.emit("本地无变更，检查远程推送...")

            self.status_signal.emit("正在同步至 GitHub (git push)...")
            proxy = _get_windows_inet_proxy()
            origin = repo.remote(name='origin')
            try:
                _git_push_with_timeout(self.repo_path, proxy=proxy)
            except GitCommandError as e:
                err_msg = str(e)
                needs_proxy_retry = (
                    platform.system() == "Windows"
                    and (
                        "Failed to connect to github.com port 443" in err_msg
                        or "Could not connect to server" in err_msg
                        or "Couldn't connect to server" in err_msg
                    )
                )
                if needs_proxy_retry:
                    proxy = proxy or _get_windows_inet_proxy()
                    if proxy:
                        self.status_signal.emit("æ£€æµ‹åˆ°ç³»ç»Ÿä»£ç†ï¼Œæ­£åœ¨å°è¯•ä»£ç†åŒæ­¥...")
                        _git_push_with_timeout(self.repo_path, proxy=proxy)
                    else:
                        raise
                else:
                    raise
            
            self.finished_signal.emit(True, "同步成功！所有变更已上传。")
            
        except GitCommandError as e:
            err_msg = str(e)
            if "Connection was reset" in err_msg or "Failed to connect" in err_msg or "128" in str(e.status):
                tip = (
                    "\n\n【排查建议】\n"
                    "1. 请确保您的 VPN/代理处于全局模式。\n"
                    "2. 尝试在 Git Bash 中运行: git config --global http.proxy http://127.0.0.1:7890\n"
                    "3. 或尝试: git config --global --unset http.proxy 取消代理。"
                )
                self.finished_signal.emit(False, f"网络同步失败: {err_msg}{tip}")
            else:
                self.finished_signal.emit(False, f"Git 操作失败: {err_msg}")
        except Exception as e:
            self.finished_signal.emit(False, f"未知错误: {str(e)}")

class FolderPriorityProxyModel(QSortFilterProxyModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hidden_dir_names = {"PDF_url_Gemini"}

    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        try:
            idx = model.index(source_row, 0, source_parent)
            if model.isDir(idx) and model.fileName(idx) in self._hidden_dir_names:
                return False
        except Exception:
            pass
        return super().filterAcceptsRow(source_row, source_parent)

    def lessThan(self, source_left, source_right):
        model = self.sourceModel()
        left_is_dir = model.isDir(source_left)
        right_is_dir = model.isDir(source_right)

        if left_is_dir and not right_is_dir:
            return self.sortOrder() == Qt.SortOrder.AscendingOrder
        if not left_is_dir and right_is_dir:
            return self.sortOrder() == Qt.SortOrder.DescendingOrder

        return super().lessThan(source_left, source_right)

class CustomFileSystemModel(QFileSystemModel):
    def data(self, index, role):
        if role == Qt.ItemDataRole.DisplayRole:
            if index.column() == 1: # Size
                size = self.size(index)
                if self.isDir(index):
                    return ""
                if size < 1024:
                    return f"{size} B"
                elif size < 1024 * 1024:
                    return f"{size / 1024:.1f} KB"
                else:
                    return f"{size / (1024 * 1024):.1f} MB"
            elif index.column() == 2: # Type
                if self.isDir(index):
                    return "文件夹"
                return QFileInfo(self.filePath(index)).suffix().lower()
        
        return super().data(index, role)

class CustomTreeView(QTreeView):
    releasePreviewSignal = pyqtSignal()

    def __init__(self, repo_path, parent=None):
        super().__init__(parent)
        self.repo_path = repo_path
        self.setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QTreeView.DragDropMode.DragDrop)
        self.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        
        self.setEditTriggers(QAbstractItemView.EditTrigger.EditKeyPressed | 
                             QAbstractItemView.EditTrigger.SelectedClicked)
        
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.open_context_menu)
        
        self._is_cut_operation = False 
        self.undo_stack = []
        self._is_undoing = False

    def update_repo_path(self, new_path):
        self.repo_path = new_path

    def add_undo_record(self, record):
        if self._is_undoing: return
        self.undo_stack.append(record)
        if len(self.undo_stack) > 100:
            self.undo_stack.pop(0)

    def perform_undo(self):
        if not self.undo_stack:
            QMessageBox.information(self, "提示", "没有可撤销的操作。")
            return

        self.releasePreviewSignal.emit()
        QApplication.processEvents()

        op = self.undo_stack.pop()
        self._is_undoing = True
        try:
            if op['type'] == 'move':
                src = op['src']
                current = op['dest']
                if os.path.exists(current):
                    self.safe_move(current, src)
                    print(f"Undo move: {current} -> {src}")
            
            elif op['type'] == 'copy':
                copied_file = op['dest']
                if os.path.exists(copied_file):
                    self.safe_delete_permanently(copied_file)
                    print(f"Undo copy: Deleted {copied_file}")

            elif op['type'] == 'new_folder':
                folder_path = op['path']
                if os.path.exists(folder_path):
                    os.rmdir(folder_path)
                    print(f"Undo new folder: Removed {folder_path}")

            elif op['type'] == 'soft_delete':
                trash_path = op['trash_path']
                original_path = op['original_path']
                if os.path.exists(trash_path):
                    os.makedirs(os.path.dirname(original_path), exist_ok=True)
                    self.safe_move(trash_path, original_path)
                    print(f"Undo delete: Restored {original_path}")
                else:
                    QMessageBox.warning(self, "失败", "回收站中找不到该文件，无法恢复。")

            elif op['type'] == 'rename':
                current_path = op['new_path']
                old_path = op['old_path']
                if os.path.exists(current_path):
                    os.rename(current_path, old_path)
                    print(f"Undo rename: {current_path} -> {old_path}")

        except Exception as e:
            QMessageBox.warning(self, "撤销失败", f"无法撤销操作: {e}")
        finally:
            self._is_undoing = False

    def safe_delete_permanently(self, path):
        try:
            if os.path.isdir(path):
                shutil.rmtree(path)
            else:
                os.remove(path)
        except:
            pass

    def get_target_dir(self, index=None):
        if index is None:
            index = self.currentIndex()
        
        if isinstance(self.model(), QSortFilterProxyModel):
            index = self.model().mapToSource(index)
            model = self.model().sourceModel()
        else:
            model = self.model()

        if not index.isValid():
            return self.repo_path
            
        file_path = model.filePath(index)
        if model.isDir(index):
            return file_path
        else:
            return os.path.dirname(file_path)

    def safe_move(self, src, dst):
        if os.path.abspath(src) == os.path.abspath(dst):
            return True
        try:
            shutil.move(src, dst)
            return True
        except OSError as e:
            time.sleep(0.5) 
            try:
                if os.path.isdir(src):
                    shutil.copytree(src, dst)
                    shutil.rmtree(src)
                else:
                    shutil.copy2(src, dst)
                    os.remove(src)
                return True
            except Exception as e2:
                raise Exception(f"操作失败: {e2}")

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                event.setDropAction(Qt.DropAction.CopyAction)
            else:
                event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                event.setDropAction(Qt.DropAction.CopyAction)
            else:
                event.setDropAction(Qt.DropAction.MoveAction)
            event.accept()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        target_dir = self.get_target_dir(index)

        urls = event.mimeData().urls()
        if not urls:
            return
        
        self.releasePreviewSignal.emit()
        QApplication.processEvents()

        is_copy_action = (event.modifiers() & Qt.KeyboardModifier.ControlModifier) or (event.dropAction() == Qt.DropAction.CopyAction)

        for url in urls:
            src_path = url.toLocalFile()
            if not os.path.exists(src_path):
                continue

            file_name = os.path.basename(src_path)
            if os.path.dirname(os.path.abspath(src_path)) == os.path.abspath(target_dir):
                continue

            final_src_path = src_path
            is_temp_file = False
            
            try:
                original_size = os.path.getsize(src_path)
                file_size_mb = original_size / (1024 * 1024)
                
                if file_size_mb > 50 and file_name.lower().endswith('.pdf'):
                    action_str = "复制" if is_copy_action else "移动"
                    reply = QMessageBox.question(
                        self, "大文件提示", 
                        f"正在{action_str}文件 '{file_name}' ({file_size_mb:.1f}MB)。\n是否压缩？(/screen 模式)",
                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                    )
                    
                    if reply == QMessageBox.StandardButton.Yes:
                        compressed_path = self.compress_pdf_ghostscript(src_path)
                        if compressed_path:
                            comp_size = os.path.getsize(compressed_path)
                            if comp_size >= original_size:
                                QMessageBox.warning(self, "压缩无效", "压缩后体积未减小，放弃压缩。")
                                os.remove(compressed_path)
                            else:
                                final_src_path = compressed_path
                                is_temp_file = True 
                                QMessageBox.showinfo("成功", "压缩完成")
            except Exception:
                pass

            dest_path = os.path.join(target_dir, file_name)
            
            if os.path.exists(dest_path):
                choice = self.show_conflict_dialog(os.path.basename(dest_path))
                if choice == "skip":
                    if is_temp_file: os.remove(final_src_path)
                    continue
                elif choice == "rename":
                    dest_path = self.get_unique_name(target_dir, file_name)
                elif choice == "overwrite":
                    self.action_soft_delete_path(dest_path)
                elif choice == "cancel":
                    if is_temp_file: os.remove(final_src_path)
                    break
            
            try:
                if is_copy_action:
                    if is_temp_file:
                        shutil.copy2(final_src_path, dest_path)
                    else:
                        if os.path.isdir(src_path):
                            shutil.copytree(src_path, dest_path)
                        else:
                            shutil.copy2(src_path, dest_path)
                    self.add_undo_record({'type': 'copy', 'dest': dest_path})
                else:
                    if is_temp_file:
                        self.safe_move(final_src_path, dest_path)
                        try: os.remove(src_path)
                        except: pass
                    else:
                        self.safe_move(src_path, dest_path)
                        self.add_undo_record({'type': 'move', 'src': src_path, 'dest': dest_path})

            except Exception as e:
                QMessageBox.critical(self, "错误", f"操作失败: {e}")
                if is_temp_file and os.path.exists(final_src_path):
                    try: os.remove(final_src_path)
                    except: pass

        event.accept()

    def compress_pdf_ghostscript(self, input_path):
        try:
            fd, temp_output = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            cmd = [
                GS_CMD, "-sDEVICE=pdfwrite", "-dCompatibilityLevel=1.4",
                "-dPDFSETTINGS=/screen", "-dNOPAUSE", "-dQUIET", "-dBATCH",
                f"-sOutputFile={temp_output}", input_path
            ]
            startupinfo = None
            if platform.system() == 'Windows':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            subprocess.run(cmd, startupinfo=startupinfo, check=True)
            QApplication.restoreOverrideCursor()
            return temp_output
        except Exception as e:
            QApplication.restoreOverrideCursor()
            return None

    def open_context_menu(self, position):
        menu = QMenu()
        selected_rows = self.selectionModel().selectedRows(0)
        has_selection = len(selected_rows) > 0
        single_selection = len(selected_rows) == 1
        
        menu.addAction(QAction("📂 新建文件夹", self, triggered=self.action_new_folder))
        menu.addSeparator()
        
        act_copy = QAction("复制 (Ctrl+C)", self, triggered=self.action_copy)
        act_copy.setEnabled(has_selection)
        menu.addAction(act_copy)

        act_cut = QAction("剪切 (Ctrl+X)", self, triggered=self.action_cut)
        act_cut.setEnabled(has_selection)
        menu.addAction(act_cut)

        act_paste = QAction("粘贴 (Ctrl+V)", self, triggered=self.action_paste)
        act_paste.setEnabled(QApplication.clipboard().mimeData().hasUrls())
        menu.addAction(act_paste)

        menu.addSeparator()
        
        act_undo = QAction("撤销 (Ctrl+Z)", self, triggered=self.perform_undo)
        act_undo.setEnabled(len(self.undo_stack) > 0)
        menu.addAction(act_undo)

        menu.addSeparator()

        act_rename = QAction("重命名 (F2)", self, triggered=self.action_rename)
        act_rename.setEnabled(single_selection)
        menu.addAction(act_rename)

        act_delete = QAction("删除 (Del)", self, triggered=self.action_soft_delete_selection)
        act_delete.setEnabled(has_selection)
        menu.addAction(act_delete)

        menu.exec(self.viewport().mapToGlobal(position))

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.StandardKey.Copy):
            self.action_copy()
        elif event.matches(QKeySequence.StandardKey.Paste):
            self.action_paste()
        elif event.matches(QKeySequence.StandardKey.Cut):
            self.action_cut()
        elif event.matches(QKeySequence.StandardKey.Delete):
            self.action_soft_delete_selection()
        elif event.matches(QKeySequence.StandardKey.Undo):
            self.perform_undo()
        elif event.key() == Qt.Key.Key_F2:
            self.action_rename()
        else:
            super().keyPressEvent(event)

    def get_selected_paths(self):
        paths = []
        proxy_indexes = self.selectionModel().selectedRows(0)
        source_model = self.model().sourceModel()
        for idx in proxy_indexes:
            source_idx = self.model().mapToSource(idx)
            paths.append(source_model.filePath(source_idx))
        return paths

    def action_new_folder(self):
        target_dir = self.get_target_dir()
        name, ok = QInputDialog.getText(self, "新建文件夹", "文件夹名称:")
        if ok and name:
            new_path = os.path.join(target_dir, name)
            try:
                if not os.path.exists(new_path): 
                    os.mkdir(new_path)
                    self.add_undo_record({'type': 'new_folder', 'path': new_path})
            except Exception as e:
                QMessageBox.critical(self, "错误", str(e))

    def action_copy(self):
        paths = self.get_selected_paths()
        if not paths: return
        data = QMimeData()
        from PyQt6.QtCore import QUrl
        qurls = [QUrl.fromLocalFile(p) for p in paths]
        data.setUrls(qurls)
        QApplication.clipboard().setMimeData(data)
        self._is_cut_operation = False

    def action_cut(self):
        self.action_copy()
        self._is_cut_operation = True

    def action_paste(self):
        clipboard = QApplication.clipboard()
        mime_data = clipboard.mimeData()
        if not mime_data.hasUrls(): return

        target_dir = self.get_target_dir()
        
        self.releasePreviewSignal.emit()
        QApplication.processEvents()

        for url in mime_data.urls():
            src_path = url.toLocalFile()
            if not os.path.exists(src_path): continue
            
            file_name = os.path.basename(src_path)
            dest_path = os.path.join(target_dir, file_name)
            
            if os.path.abspath(src_path) == os.path.abspath(dest_path):
                continue
            
            if os.path.exists(dest_path):
                 dest_path = self.get_unique_name(target_dir, file_name)

            try:
                if self._is_cut_operation:
                    self.safe_move(src_path, dest_path)
                    self.add_undo_record({'type': 'move', 'src': src_path, 'dest': dest_path})
                else:
                    if os.path.isdir(src_path):
                        shutil.copytree(src_path, dest_path)
                    else:
                        shutil.copy2(src_path, dest_path)
                    self.add_undo_record({'type': 'copy', 'dest': dest_path})
            except Exception as e:
                QMessageBox.critical(self, "错误", f"粘贴失败: {e}")

        if self._is_cut_operation:
            clipboard.clear()
            self._is_cut_operation = False

    def action_soft_delete_selection(self):
        paths = self.get_selected_paths()
        if not paths: return
        
        self.releasePreviewSignal.emit()
        QApplication.processEvents()

        for path in paths:
            self.action_soft_delete_path(path)

    def action_soft_delete_path(self, path):
        trash_dir = os.path.join(self.repo_path, ".trash_bin")
        if not os.path.exists(trash_dir):
            os.makedirs(trash_dir)
            if platform.system() == "Windows":
                ctypes.windll.kernel32.SetFileAttributesW(trash_dir, 2)

        file_name = os.path.basename(path)
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_")
        trash_path = os.path.join(trash_dir, timestamp + file_name)

        try:
            self.safe_move(path, trash_path)
            self.add_undo_record({
                'type': 'soft_delete', 
                'original_path': path, 
                'trash_path': trash_path
            })
        except Exception as e:
            QMessageBox.warning(self, "删除失败", f"无法删除 {path}: {e}")

    def action_rename(self):
        self.releasePreviewSignal.emit()
        QApplication.processEvents()
        
        index = self.currentIndex()
        if not index.isValid(): return
        self.edit(index)

    def show_conflict_dialog(self, filename):
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle("文件冲突")
        msg_box.setText(f"'{filename}' 已存在。")
        btn_rename = msg_box.addButton("自动重命名", QMessageBox.ButtonRole.ActionRole)
        btn_overwrite = msg_box.addButton("覆盖", QMessageBox.ButtonRole.ActionRole)
        btn_skip = msg_box.addButton("跳过", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg_box.exec()
        if msg_box.clickedButton() == btn_rename: return "rename"
        elif msg_box.clickedButton() == btn_overwrite: return "overwrite"
        elif msg_box.clickedButton() == btn_skip: return "skip"
        return "cancel"

    def get_unique_name(self, directory, filename):
        base, ext = os.path.splitext(filename)
        counter = 1
        new_path = os.path.join(directory, f"{base}({counter}){ext}")
        while os.path.exists(new_path):
            counter += 1
            new_path = os.path.join(directory, f"{base}({counter}){ext}")
        return new_path

class GitHubManagerApp(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("GitHub PDF 文档管理系统")
        self.setWindowIcon(_get_app_icon())
        self.resize(1200, 650)
        self.center_window()
        
        self.config = ConfigManager.load()
        self.repo_path = self.config.get("repo_path", r"D:\Github\pdf-document")
        self.base_url = self.config.get("base_url", "https://zhangzhh95.github.io/pdf-document/")
        
        self.preview_mode = None 
        self.preview_widget = None
        self.preview_document = None
        self.preview_text_widget = None
        self.preview_pdf_device = None
        
        if not os.path.exists(self.repo_path):
            QMessageBox.critical(self, "路径错误", f"找不到仓库路径：{self.repo_path}\n请在设置中修改。")

        try:
            self.repo = Repo(self.repo_path)
        except Exception:
            self.repo = None

        self.setup_ui()
        self.apply_dark_theme()
        
        # 监听重命名信号
        self.source_model.fileRenamed.connect(self.on_file_renamed)
        
        self.status_worker = GitStatusWorker(self.repo_path)
        self.status_worker.result_signal.connect(self.on_git_status_result)
        
        self.status_timer = QTimer(self)
        self.status_timer.timeout.connect(self.check_git_status_loop)
        self.status_timer.start(3000)
        # 立即检查一次
        self.check_git_status_loop()

    def center_window(self):
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2 - 40
        self.move(x, y)

    def on_file_renamed(self, path, old_name, new_name):
        if self.tree._is_undoing: return
        new_path = os.path.join(os.path.dirname(path), new_name)
        old_path = os.path.join(os.path.dirname(path), old_name)
        self.tree.add_undo_record({'type': 'rename', 'old_path': old_path, 'new_path': new_path})

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # --- 左侧控制面板 ---
        left_panel = QFrame()
        left_panel.setObjectName("LeftPanel")
        left_panel.setFixedWidth(280)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(20, 10, 20, 20)
        left_layout.setSpacing(15)

        title_label = QLabel("📂 文档控制台")
        title_label.setObjectName("TitleLabel")
        left_layout.addWidget(title_label)
        
        search_layout = QHBoxLayout()
        search_layout.setSpacing(5)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("搜索文件...")
        self.search_input.returnPressed.connect(self.perform_search)
        search_layout.addWidget(self.search_input)

        search_btn = QPushButton("🔍")
        search_btn.setFixedWidth(40)
        search_btn.clicked.connect(self.perform_search)
        search_layout.addWidget(search_btn)
        left_layout.addLayout(search_layout)

        self.search_list = QListWidget()
        self.search_list.itemClicked.connect(self.on_search_result_clicked)
        self.search_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.search_list.setVisible(True) 
        left_layout.addWidget(self.search_list)

        btn_layout = QHBoxLayout()
        self.btn_sync = QPushButton("☁️ 同步")
        self.btn_sync.setObjectName("PrimaryButton")
        self.btn_sync.setFixedHeight(32)
        self.btn_sync.clicked.connect(self.start_sync)
        btn_layout.addWidget(self.btn_sync)

        self.btn_copy_url = QPushButton("🔗 复制 URL")
        self.btn_copy_url.setObjectName("GreenButton")
        self.btn_copy_url.setFixedHeight(32)
        self.btn_copy_url.clicked.connect(self.copy_selected_url)
        btn_layout.addWidget(self.btn_copy_url)
        
        left_layout.addLayout(btn_layout)

        status_frame = QFrame()
        status_frame.setObjectName("StatusFrame")
        status_layout = QVBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 10, 10, 10)
        status_layout.setSpacing(5)

        self.status_label = QLabel("就绪")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("StatusLabel")
        status_layout.addWidget(self.status_label)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedHeight(4)
        self.progress_bar.hide()
        status_layout.addWidget(self.progress_bar)

        self.git_status_indicator = QLabel("检测中...")
        self.git_status_indicator.setObjectName("GitStatus")
        status_layout.addWidget(self.git_status_indicator)

        left_layout.addWidget(status_frame)

        # --- 中间（树） ---
        tree_container = QWidget()
        tree_layout = QVBoxLayout(tree_container)
        tree_layout.setContentsMargins(0, 0, 0, 0)
        tree_layout.setSpacing(0)

        tree_toolbar = QWidget()
        tree_toolbar.setObjectName("TreeToolbar")
        toolbar_layout = QHBoxLayout(tree_toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)
        
        self.is_all_expanded = False
        self.btn_toggle_expand = QPushButton("∧ / ∨")
        self.btn_toggle_expand.setFixedWidth(80)
        self.btn_toggle_expand.clicked.connect(self.toggle_tree_expansion)
        
        self.btn_preview_toggle = QPushButton("预览")
        self.btn_preview_toggle.setObjectName("PreviewToggle")
        self.btn_preview_toggle.setIcon(_make_windows_explorer_preview_icon())
        self.btn_preview_toggle.setIconSize(QSize(18, 18))
        self.btn_preview_toggle.setFixedWidth(80)
        self.btn_preview_toggle.setCheckable(True)
        self.btn_preview_toggle.clicked.connect(self.toggle_preview_panel)

        self.btn_config = QPushButton("⚙️")
        self.btn_config.setFixedWidth(40)
        self.btn_config.clicked.connect(self.open_config)

        toolbar_layout.addWidget(self.btn_toggle_expand)
        toolbar_layout.addStretch() 
        toolbar_layout.addWidget(self.btn_preview_toggle)
        toolbar_layout.addWidget(self.btn_config)
        
        tree_layout.addWidget(tree_toolbar)

        self.source_model = CustomFileSystemModel()
        self.source_model.setRootPath(self.repo_path)
        self.source_model.setReadOnly(False) 

        self.proxy_model = FolderPriorityProxyModel()
        self.proxy_model.setSourceModel(self.source_model)
        
        self.tree = CustomTreeView(self.repo_path)
        self.tree.setModel(self.proxy_model)
        
        # 连接释放信号到清除预览
        self.tree.releasePreviewSignal.connect(self.clear_preview_lock)
        
        self.tree.clicked.connect(self.on_tree_clicked)
        self.tree.doubleClicked.connect(self.on_tree_double_click)

        root_index = self.source_model.index(self.repo_path)
        proxy_root_index = self.proxy_model.mapFromSource(root_index)
        self.tree.setRootIndex(proxy_root_index)
        self.tree.setAnimated(True)
        self.tree.setIndentation(20)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)
        
        self.tree.setColumnWidth(0, 525)
        self.tree.setColumnWidth(1, 80)
        self.tree.setColumnWidth(2, 60)
        self.tree.setColumnWidth(3, 150)
        
        tree_layout.addWidget(self.tree)

        # --- 右侧预览区 ---
        self.preview_panel = QWidget()
        self.preview_panel.setVisible(False)
        self.preview_layout = QVBoxLayout(self.preview_panel)
        self.preview_layout.setContentsMargins(0, 0, 0, 0)
        
        self.preview_widget = None # 懒加载

        self.content_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.content_splitter.addWidget(tree_container)
        self.content_splitter.addWidget(self.preview_panel)
        self.content_splitter.setSizes([1000, 0])
        self.content_splitter.setCollapsible(1, True)

        main_splitter = QSplitter(Qt.Orientation.Horizontal)
        main_splitter.addWidget(left_panel)
        main_splitter.addWidget(self.content_splitter)
        
        main_layout.addWidget(main_splitter)

    # 优化2: 异步加载 + 严防死守启动白窗
    def toggle_preview_panel(self):
        is_visible = self.btn_preview_toggle.isChecked()
        self.preview_panel.setVisible(is_visible)
        
        if is_visible:
            sizes = self.content_splitter.sizes()
            if len(sizes) > 1 and sizes[1] < 50:
                total = sum(sizes)
                self.content_splitter.setSizes([int(total * 0.6), int(total * 0.4)])
            
            if self.preview_widget is None:
                QTimer.singleShot(10, self._lazy_load_preview_engine)
            else:
                self.update_preview()
        
    def _lazy_load_preview_engine(self):
        try:
            from PyQt6.QtPdf import QPdfDocument
            from PyQt6.QtPdfWidgets import QPdfView

            class _PdfViewWithZoom(QPdfView):
                def wheelEvent(self, event):
                    if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                        delta_y = event.angleDelta().y()
                        if delta_y == 0:
                            event.ignore()
                            return
                        step = 1.1 if delta_y > 0 else 1 / 1.1
                        new_factor = max(0.1, min(5.0, self.zoomFactor() * step))
                        self.setZoomMode(QPdfView.ZoomMode.Custom)
                        self.setZoomFactor(new_factor)
                        event.accept()
                        return
                    super().wheelEvent(event)

            self.preview_document = QPdfDocument(self)
            self.preview_widget = _PdfViewWithZoom(self.preview_panel)
            self.preview_widget.setDocument(self.preview_document)
            self.preview_widget.setPageMode(QPdfView.PageMode.MultiPage)
            self.preview_widget.setZoomMode(QPdfView.ZoomMode.FitToWidth)
            self.preview_layout.addWidget(self.preview_widget)

            self.preview_text_widget = QPlainTextEdit(self.preview_panel)
            self.preview_text_widget.setReadOnly(True)
            self.preview_text_widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
            self.preview_text_widget.setFont(QFont("Microsoft YaHei", 10))
            self.preview_text_widget.hide()
            self.preview_layout.addWidget(self.preview_text_widget)
            self.preview_mode = "QTPDF"
        except Exception:
            self.preview_mode = "NONE"
            lbl = QLabel("PDF 预览组件缺失\n需 PyQt6.QtPdf / PyQt6.QtPdfWidgets", self.preview_panel)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            lbl.setStyleSheet("color: #888;")
            self.preview_layout.addWidget(lbl)
        
        self.update_preview()

    def on_tree_clicked(self, index):
        if self.btn_preview_toggle.isChecked():
            self.update_preview(index)

    def clear_preview_lock(self):
        if self.preview_widget and self.preview_mode:
            if self.preview_mode == "QTPDF" and self.preview_document is not None:
                self.preview_document.close()
                if self.preview_pdf_device is not None:
                    try:
                        self.preview_pdf_device.close()
                    except Exception:
                        pass
                    self.preview_pdf_device = None
            elif self.preview_mode == "WEBENGINE":
                self.preview_widget.setUrl(QUrl("about:blank"))
            elif self.preview_mode == "ACTIVEX":
                self.preview_widget.dynamicCall("Navigate(const QString&)", "about:blank")
        if self.preview_text_widget is not None:
            self.preview_text_widget.setPlainText("")
            self.preview_text_widget.hide()

    def update_preview(self, index=None):
        if self.preview_widget is None: return
        
        if index is None:
            index = self.tree.currentIndex()
        if not index.isValid(): return

        source_index = self.proxy_model.mapToSource(index)
        file_path = self.source_model.filePath(source_index)
        
        if os.path.isfile(file_path):
            if self.preview_mode == "QTPDF" and self.preview_document is not None:
                lower = file_path.lower()
                if lower.endswith(".pdf"):
                    if self.preview_text_widget is not None:
                        self.preview_text_widget.hide()
                    self.preview_widget.show()
                    try:
                        from PyQt6.QtPdf import QPdfDocument
                        if self.preview_pdf_device is not None:
                            try:
                                self.preview_pdf_device.close()
                            except Exception:
                                pass
                        self.preview_pdf_device = None
                        err = self.preview_document.load(file_path)
                        if err == QPdfDocument.Error.InvalidFileFormat:
                            device = QFile(file_path)
                            if device.open(QIODevice.OpenModeFlag.ReadOnly):
                                self.preview_pdf_device = device
                                err = self.preview_document.load(device)
                            else:
                                try:
                                    device.close()
                                except Exception:
                                    pass
                        if err == QPdfDocument.Error.InvalidFileFormat:
                            try:
                                if os.path.getsize(file_path) <= 50 * 1024 * 1024:
                                    with open(file_path, "rb") as f:
                                        pdf_bytes = f.read()
                                    buf = QBuffer()
                                    buf.setData(QByteArray(pdf_bytes))
                                    buf.open(QIODevice.OpenModeFlag.ReadOnly)
                                    self.preview_pdf_device = buf
                                    err = self.preview_document.load(buf)
                            except Exception:
                                pass
                        if err != QPdfDocument.Error.None_:
                            try:
                                with open(file_path, "rb") as f:
                                    file_head = f.read(5)
                                if file_head != b"%PDF-":
                                    self.status_label.setText("PDF预览失败: 文件不是有效PDF")
                                else:
                                    self.status_label.setText(f"PDF预览失败:{err}")
                            except Exception:
                                self.status_label.setText(f"PDF预览失败:{err}")
                    except Exception:
                        self.preview_document.load(file_path)
                elif lower.endswith(".txt"):
                    try:
                        text = _read_text_file_best_effort(file_path)

                        if self.preview_text_widget is None:
                            self.preview_text_widget = QPlainTextEdit(self.preview_panel)
                            self.preview_text_widget.setReadOnly(True)
                            self.preview_text_widget.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
                            self.preview_text_widget.setFont(QFont("Microsoft YaHei", 10))
                            self.preview_layout.addWidget(self.preview_text_widget)
                        self.preview_text_widget.setPlainText(text)
                        self.preview_widget.hide()
                        self.preview_text_widget.show()
                    except Exception:
                        self.clear_preview_lock()
                else:
                    self.clear_preview_lock()
            elif self.preview_mode == "WEBENGINE":
                self.preview_widget.setUrl(QUrl.fromLocalFile(file_path))
            elif self.preview_mode == "ACTIVEX":
                self.preview_widget.dynamicCall("Navigate(const QString&)", file_path)
        else:
            self.clear_preview_lock()

    def open_config(self):
        dlg = ConfigDialog(self.repo_path, self.base_url, self)
        if dlg.exec():
            new_repo, new_url = dlg.get_data()
            if os.path.exists(new_repo):
                self.repo_path = new_repo
                self.base_url = new_url
                ConfigManager.save({"repo_path": self.repo_path, "base_url": self.base_url})
                
                self.source_model.setRootPath(self.repo_path)
                root_index = self.source_model.index(self.repo_path)
                proxy_root_index = self.proxy_model.mapFromSource(root_index)
                self.tree.setRootIndex(proxy_root_index)
                self.tree.update_repo_path(self.repo_path)
                self.status_worker.repo_path = self.repo_path
                try:
                    self.repo = Repo(self.repo_path)
                except:
                    self.repo = None
                QMessageBox.showinfo("设置保存", "配置已更新。")
            else:
                QMessageBox.warning(self, "路径无效", "所选路径不存在！")

    def on_tree_double_click(self, index):
        if not index.isValid(): return
        source_index = self.proxy_model.mapToSource(index)
        file_path = self.source_model.filePath(source_index)
        if os.path.isfile(file_path):
            try:
                os.startfile(file_path)
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法打开文件: {e}")

    def toggle_tree_expansion(self):
        if self.is_all_expanded:
            self.tree.collapseAll()
            self.is_all_expanded = False
        else:
            self.tree.expandAll()
            self.is_all_expanded = True

    def apply_dark_theme(self):
        font = QFont("Microsoft YaHei")
        font.setStyleHint(QFont.StyleHint.SansSerif)
        QApplication.setFont(font)
        style = """
        * { font-family: "Microsoft YaHei", "Segoe UI", sans-serif; font-size: 14px; }
        QMainWindow { background-color: #2b2b2b; }
        QWidget { color: #e0e0e0; }
        #LeftPanel { background-color: #1e1e1e; border-right: 1px solid #333; }
        #TitleLabel { font-size: 20px; font-weight: bold; color: #fff; margin-bottom: 15px; }
        #HLine { color: #444; }
        #StatusLabel { color: #ccc; font-size: 12px; }
        #GitStatus { font-weight: bold; font-size: 13px; border-top: 1px solid #555; padding-top: 8px; margin-top: 5px; }
        #StatusFrame { background-color: #252526; border: 1px solid #3e3e3e; border-radius: 6px; }
        QPushButton { background-color: #333; border: 1px solid #555; border-radius: 4px; padding: 4px; color: #eee; }
        QPushButton:hover { background-color: #444; border-color: #666; }
        QPushButton:pressed { background-color: #222; }
        #PreviewToggle { padding: 4px 10px; text-align: left; }
        #PreviewToggle:checked { background-color: #2f2f2f; border-color: #0078d4; }
        #PrimaryButton { background-color: #007acc; border: none; font-weight: bold; }
        #PrimaryButton:hover { background-color: #0062a3; }
        #GreenButton { background-color: #28a745; border: none; font-weight: bold; }
        #GreenButton:hover { background-color: #218838; }
        #TreeToolbar { background-color: #252526; border-bottom: 1px solid #333; }
        QLineEdit { background-color: #252526; border: 1px solid #3e3e3e; border-radius: 4px; padding: 6px; color: #fff; }
        QTreeView, QListWidget { background-color: #252526; border: none; color: #ddd; outline: 0; font-size: 12px; }
        QTreeView::item, QListWidget::item { padding: 2px; border: none; }
        QTreeView::item:hover, QListWidget::item:hover { background-color: #2a2d2e; }
        QTreeView::item:selected, QListWidget::item:selected { background-color: #37373d; color: #fff; border: none; }
        QTreeView::item:focus { outline: none; border: none; }
        QTreeView QLineEdit { padding: 0px; margin: 0px; border: 1px solid #007acc; background-color: #252526; color: #ffffff; font-size: 12px; }
        QHeaderView::section { background-color: #1e1e1e; color: #aaa; padding: 2px; font-weight: bold; border: none; border-right: 1px solid #333; border-bottom: 1px solid #333; }
        QMenu { background-color: #2b2b2b; border: 1px solid #555; }
        QMenu::item { padding: 5px 20px; }
        QMenu::item:selected { background-color: #007acc; color: white; }
        QMessageBox { background-color: #2b2b2b; color: #e0e0e0; }
        QSplitter::handle { background-color: #3e3e3e; }
        """
        self.setStyleSheet(style)

    def check_git_status_loop(self):
        if not self.status_worker.isRunning():
            self.status_worker.start()

    def on_git_status_result(self, count, success):
        if not success:
            self.git_status_indicator.setText("❌ 仓库无效")
            self.git_status_indicator.setStyleSheet("color: #FF5252;")
            return
        if count > 0:
            self.git_status_indicator.setText(f"⚠️ {count}个变更待上传")
            self.git_status_indicator.setStyleSheet("color: #FFD700;")
        else:
            self.git_status_indicator.setText("✔ 0个变更待上传")
            self.git_status_indicator.setStyleSheet("color: #4CAF50;")

    def start_sync(self):
        self.btn_sync.setEnabled(False)
        self.progress_bar.show()
        self.status_label.setText("正在准备同步...")
        self.status_timer.stop()
        self.git_worker = GitWorker(self.repo_path)
        self.git_worker.status_signal.connect(self.update_status)
        self.git_worker.finished_signal.connect(self.sync_finished)
        self.git_worker.start()

    def update_status(self, text):
        self.status_label.setText(text)

    def sync_finished(self, success, message):
        self.btn_sync.setEnabled(True)
        self.progress_bar.hide()
        self.status_label.setText(message)
        self.status_timer.start(3000)
        if success:
            QMessageBox.information(self, "同步成功", "文件已成功推送到 GitHub！")
            self.check_git_status_loop()
        else:
            QMessageBox.warning(self, "同步失败", message)

    def copy_selected_url(self):
        proxy_indexes = self.tree.selectionModel().selectedRows(0)
        if not proxy_indexes:
            self.status_label.setText("请先选择文件")
            return
        urls = []
        for p_idx in proxy_indexes:
            src_idx = self.proxy_model.mapToSource(p_idx)
            file_path = self.source_model.filePath(src_idx)
            rel_path = os.path.relpath(file_path, self.repo_path)
            rel_path_web = rel_path.replace("\\", "/")
            encoded_path = urllib.parse.quote(rel_path_web)
            full_url = self.base_url + encoded_path
            urls.append(full_url)
        if urls:
            final_clip_text = "\n".join(urls)
            pyperclip.copy(final_clip_text)
            self.status_label.setText(f"已复制 {len(urls)} 个链接")
        else:
            self.status_label.setText("未生成有效链接")

    def perform_search(self):
        text = self.search_input.text().strip().lower()
        self.search_list.clear()
        if not text: return
        source_root_index = self.source_model.index(self.repo_path)
        count = self.search_recursive_populate(source_root_index, text)
        if count > 0:
            self.status_label.setText(f"找到 {count} 个匹配项")
        else:
            self.status_label.setText(f"未找到: {text}")

    def search_recursive_populate(self, parent_index, text):
        rows = self.source_model.rowCount(parent_index)
        count = 0
        for i in range(rows):
            idx = self.source_model.index(i, 0, parent_index)
            if self.source_model.isDir(idx) and self.source_model.fileName(idx) == "PDF_url_Gemini":
                continue
            name = self.source_model.fileName(idx).lower()
            file_path = self.source_model.filePath(idx)
            if text in name:
                item = QListWidgetItem(name)
                item.setData(Qt.ItemDataRole.UserRole, file_path)
                item.setIcon(QIcon(self.source_model.fileIcon(idx)))
                self.search_list.addItem(item)
                count += 1
            if self.source_model.isDir(idx):
                count += self.search_recursive_populate(idx, text)
        return count

    def on_search_result_clicked(self, item):
        file_path = item.data(Qt.ItemDataRole.UserRole)
        if not file_path: return
        idx = self.source_model.index(file_path)
        if idx.isValid():
            proxy_idx = self.proxy_model.mapFromSource(idx)
            if proxy_idx.isValid():
                parent = proxy_idx.parent()
                while parent.isValid():
                    self.tree.setExpanded(parent, True)
                    parent = parent.parent()
                self.tree.selectionModel().setCurrentIndex(proxy_idx, self.tree.selectionModel().SelectionFlag.ClearAndSelect | self.tree.selectionModel().SelectionFlag.Rows)
                self.tree.scrollTo(proxy_idx)

if __name__ == "__main__":
    _hide_lonely_console_window_on_windows()
    try:
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseSoftwareOpenGL, True)
    except Exception:
        pass
    app = QApplication(sys.argv)
    app.setApplicationName("Git Cloud")
    app.setApplicationDisplayName("Git Cloud")
    app.setWindowIcon(_get_app_icon())
    window = GitHubManagerApp()
    window.show()
    try:
        window._startup_cleanup_count = 0
        window._startup_cleanup_timer = QTimer(window)

        def _startup_cleanup_tick():
            _cleanup_stray_startup_windows(app, window)
            window._startup_cleanup_count += 1
            if window._startup_cleanup_count >= 20:
                window._startup_cleanup_timer.stop()

        window._startup_cleanup_timer.timeout.connect(_startup_cleanup_tick)
        window._startup_cleanup_timer.start(100)
    except Exception:
        QTimer.singleShot(150, lambda: _cleanup_stray_startup_windows(app, window))
    sys.exit(app.exec())
