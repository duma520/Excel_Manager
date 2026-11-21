# [文件名]: Excel_Manager - 6.2 多线程优化版.py

import os
import sys
import sqlite3
import csv
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                            QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                            QMessageBox, QComboBox, QTabWidget, QFormLayout, QGroupBox,
                            QInputDialog, QListWidget, QListWidgetItem, QAbstractItemView, QHeaderView,
                            QDialog, QCheckBox, QFileDialog, QStatusBar, QMenu,
                            QToolBar, QSizePolicy, QSpacerItem, QSplitter, QTextEdit,
                            QScrollArea, QDateEdit, QSpinBox, QProgressDialog,
                            QRadioButton, QTreeWidget, QTreeWidgetItem)
from PySide6.QtGui import (QIcon, QPixmap, QImage, QFont, QTextDocument, QColor, QPainter, 
                           QAction, QKeySequence, QShortcut)
from PySide6.QtCore import Qt, QSize, QTimer, QRect, QDate, QThread, Signal, QObject, QThreadPool, QRunnable
# 以下重型模块改为延迟导入（函数内按需加载），大幅缩短启动时间
# from PySide6.QtPrintSupport import QPrinter, QPrintDialog  # -> print_item_detail()
# from barcode import EAN13, Code128  # -> generate_barcode()
# import qrcode  # -> generate_qrcode()
# from PIL import Image  # -> generate_barcode(), generate_qrcode()
import io
import json
import subprocess
from datetime import datetime
from collections import Counter
from functools import partial
from pypinyin import lazy_pinyin, Style
import chardet


def install_package(package):
    """自动安装缺失的包（工具函数，供按需调用）"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"成功安装 {package}")
        return True
    except subprocess.CalledProcessError:
        print(f"安装 {package} 失败，请手动安装: pip install {package}")
        return False


# ===================== 统一提示（状态栏，不弹窗）★ v6.8 =====================
# 用户要求：所有提示都改到状态栏，不要弹窗打断操作。
# 约定：
#   * 提示类（成功/失败/警告/信息）一律走 notify() → 状态栏；
#   * 需要用户“是/否”决定的确认框（QMessageBox.question）保留，不能改状态栏；
#   * 真正需要输入的表单对话框（列配置/编辑数据/选物品等）当然还是对话框；
#   * notify() 万一找不到状态栏（极少数独立窗口/启动早期）才退回弹窗，信息不会丢。
NOTIFY_ICONS = {'info': 'ℹ️', 'success': '✅', 'warn': '⚠️', 'error': '❌'}
NOTIFY_MSEC = {'info': 5000, 'success': 5000, 'warn': 8000, 'error': 12000}
NOTIFY_COLORS = {'info': '#333333', 'success': '#2E7D32',
                 'warn': '#E65100', 'error': '#C62828'}


def find_status_bar(obj):
    """沿 parent 链找一个可用的状态栏（QMainWindow.statusBar() 会自动创建）"""
    seen = set()
    cur = obj
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        sb = getattr(cur, 'status_bar', None)
        if sb is None and isinstance(cur, QMainWindow):
            try:
                sb = cur.statusBar()
            except Exception:
                sb = None
        if sb is not None:
            return sb
        try:
            cur = cur.parent() if hasattr(cur, 'parent') else None
        except Exception:
            cur = None
    try:
        win = QApplication.activeWindow()
    except Exception:
        win = None
    if win is not None:
        sb = getattr(win, 'status_bar', None)
        if sb is None and isinstance(win, QMainWindow):
            try:
                sb = win.statusBar()
            except Exception:
                sb = None
        if sb is not None:
            return sb
    return None


class NotifyLabel(QLabel):
    """状态栏右侧的“重要提示”标签（★ v6.8）

    警告/错误写在这里而不是 showMessage 的临时区域 —— 因为程序里有很多地方会
    用 showMessage 刷常规状态（“正在加载…”“用户: X | 总记录数: N”），临时区域
    会被它们盖掉、重要提示就看不见了。点一下即可清掉。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip('点击可清除此提示')

    def mousePressEvent(self, event):
        self.setText('')
        self.setToolTip('')
        self.hide()
        super().mousePressEvent(event)


def _ensure_notify_label(status_bar):
    """在状态栏右侧放一个常驻提示标签（每个状态栏一个）"""
    lbl = getattr(status_bar, '_notify_label', None)
    if lbl is None:
        lbl = NotifyLabel(status_bar)
        status_bar.addPermanentWidget(lbl)
        status_bar._notify_label = lbl
    return lbl


def _flash_dialog_title(dlg, level, msec):
    """对话框（模态时状态栏被挡住）用标题闪烁提示一下（★ v6.8）"""
    try:
        base = getattr(dlg, '_notify_base_title', None)
        if base is None:
            base = dlg.windowTitle()
            dlg._notify_base_title = base
        dlg.setWindowTitle(f"{NOTIFY_ICONS.get(level, '')} {base}".strip())

        def _restore():
            try:
                if getattr(dlg, '_notify_base_title', None) is not None:
                    dlg.setWindowTitle(dlg._notify_base_title)
            except Exception:
                pass

        QTimer.singleShot(msec or NOTIFY_MSEC.get(level, 5000), _restore)
    except Exception:
        pass


def _fallback_popup(owner, text, level):
    """兜底弹窗（★ v6.8）：找不到状态栏时才用。

    两个刻意的设计：
      1. 用 QMessageBox 的**对象形式**创建，不用 QMessageBox.information/warning/
         critical 三个静态方法 —— 否则批量改造脚本会把兜底本身也换成 notify()，
         导致无限递归（v6.8 改造时真的踩到了）；
      2. 用 show() 而不是 exec()：**绝不阻塞**（阻塞型弹窗正是这次要消灭的东西，
         而且会让自动化冒烟测试卡死）。
    """
    try:
        icons = {'error': QMessageBox.Critical, 'warn': QMessageBox.Warning,
                 'info': QMessageBox.Information}
        titles = {'error': '错误', 'warn': '警告', 'info': '提示'}
        box = QMessageBox(owner if isinstance(owner, QWidget) else None)
        box.setIcon(icons.get(level, QMessageBox.Information))
        box.setWindowTitle(titles.get(level, '提示'))
        box.setText(str(text))
        box.setStandardButtons(QMessageBox.Ok)
        box.setAttribute(Qt.WA_DeleteOnClose)
        box.show()
        _ACTIVE_POPUPS.append(box)
        try:
            box.destroyed.connect(lambda *_: _ACTIVE_POPUPS.remove(box)
                                  if box in _ACTIVE_POPUPS else None)
        except Exception:
            pass
    except Exception as e:
        print(f"[DEBUG] 兜底弹窗失败: {str(e)}")


_ACTIVE_POPUPS = []     # 保活：无父对象的兜底弹窗不能被 Python 提前回收


def notify(owner, message, level='info', msec=None):
    """统一提示（★ v6.8）：原先 QMessageBox.information/warning/critical 的弹窗
    改为状态栏提示，不打断操作。

    level: info / success / warn / error
      * info/success → 状态栏临时消息（过一会儿自动消失）
      * warn/error   → 状态栏右侧的常驻提示（不会被常规状态刷新盖掉，点击清除）
      * 有模态对话框弹着时，顺带闪一下它的标题（不然状态栏被挡住看不见）
    完整原文放进 tooltip（状态栏只有一行，太长的会截断）。
    返回 True 表示已写进状态栏，False 表示退回了弹窗（找不到状态栏时的兜底）。
    """
    text = str(message)
    flat = ' '.join(text.split())            # 状态栏只能一行，换行折叠成空格
    if len(flat) > 300:
        flat = flat[:300] + '…'
    icon = NOTIFY_ICONS.get(level, NOTIFY_ICONS['info'])
    color = NOTIFY_COLORS.get(level, NOTIFY_COLORS['info'])
    duration = msec or NOTIFY_MSEC.get(level, 5000)

    sb = find_status_bar(owner)
    if sb is None:
        # 兜底：真找不到状态栏就仍然弹窗（保证错误信息不会丢）
        print(f"[NOTIFY:{level}] {flat}")
        _fallback_popup(owner, text, level)
        return False

    try:
        if level in ('warn', 'error'):
            print(f"[{level.upper()}] {flat}")
            lbl = _ensure_notify_label(sb)
            lbl.setText(f"{icon} {flat}")
            lbl.setStyleSheet(f"color: {color};")
            lbl.setToolTip(text)
            lbl.show()
        else:
            lbl = getattr(sb, '_notify_label', None)
            if lbl is not None:
                lbl.setText('')
                lbl.setToolTip('')
                lbl.hide()
            sb.setStyleSheet(f"color: {color};")
            sb.showMessage(f"{icon} {flat}", duration)
            sb.setToolTip(text)
    except Exception as e:
        print(f"[DEBUG] 状态栏提示失败: {str(e)}")
        return False

    # 模态对话框挡着状态栏时，闪一下它的标题
    try:
        cur = owner
        while cur is not None and not isinstance(cur, QDialog):
            cur = cur.parent() if hasattr(cur, 'parent') else None
        if cur is not None:
            _flash_dialog_title(cur, level, duration)
    except Exception:
        pass
    return True


# ===================== 多线程工作器 =====================
class WorkerSignals(QObject):
    """工作线程信号定义"""
    finished = Signal(object)       # 完成信号，携带结果数据
    error = Signal(str)             # 错误信号，携带错误信息
    progress = Signal(int, int)     # 进度信号 (当前值, 最大值)
    status = Signal(str)            # 状态消息信号


class BaseWorker(QRunnable):
    """基于 QThreadPool 的基础工作器，适合一次性轻量任务"""
    def __init__(self, fn, *args, **kwargs):
        super().__init__()
        self.fn = fn
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        if self._is_cancelled:
            return
        try:
            result = self.fn(*self.args, **self.kwargs)
            if not self._is_cancelled:
                # ★ v6.4：信号宿主可能已被回收（线程池任务未保留引用时），
                #   不捕住会抛 RuntimeError: Signal source has been deleted
                try:
                    self.signals.finished.emit(result)
                except RuntimeError:
                    pass
        except Exception as e:
            if not self._is_cancelled:
                try:
                    self.signals.error.emit(str(e))
                except RuntimeError:
                    print(f"[DEBUG] 线程池任务异常（信号已失效）: {str(e)}")


class ThreadedWorker(QThread):
    """基于 QThread 的重型工作器，适合长时间运行的任务，支持进度报告"""
    finished = Signal(object)
    error = Signal(str)
    progress = Signal(int, int)
    status = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True
        self.requestInterruption()

    def is_cancelled(self):
        return self._is_cancelled or self.isInterruptionRequested()

    def release_db(self):
        """★ v6.3：工作线程用完自己的数据库连接后必须关闭

        只关 private_connection=True 的私有连接；共享的池化连接
        （主窗口自己的那种）绝不能在此关闭。
        """
        try:
            if getattr(self.user_db, 'private_connection', False):
                self.user_db.close()
        except Exception:
            pass


class LoadDataWorker(ThreadedWorker):
    """数据加载工作线程"""
    def __init__(self, user_db, columns_config, parent=None):
        super().__init__(parent)
        self.user_db = user_db
        self.columns_config = columns_config

    def run(self):
        try:
            self.status.emit("正在加载数据...")
            data = self.user_db.get_all_data()
            if self.is_cancelled():
                return
            # 返回原始数据，由主线程更新UI
            result = {
                'data': data,
                'columns_config': self.columns_config
            }
            self.finished.emit(result)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.release_db()


class ImportDataWorker(ThreadedWorker):
    """数据导入工作线程"""
    def __init__(self, import_config, main_window, parent=None):
        super().__init__(parent)
        self.import_config = import_config
        self.main_window = main_window

    def run(self):
        try:
            filename = self.import_config['filename']
            self.status.emit(f"正在读取文件: {os.path.basename(filename)}...")

            # 读取数据文件
            data = self.main_window.read_import_file(filename, self.import_config)
            if self.is_cancelled():
                return
            if not data:
                self.finished.emit({'success': 0, 'errors': 0, 'error_details': ['无法读取数据文件']})
                return

            total = len(data)
            self.progress.emit(0, total)
            self.status.emit(f"正在导入 {total} 条数据...")

            mapping = self.import_config['field_mapping']
            conflict_resolution = self.import_config['conflict_resolution']
            batch_size = self.import_config.get('batch_size', 1000)

            # 获取目标数据库连接
            target_user = self.import_config['target_user']
            db_file = self.main_window.user_manager.get_user_db_file(target_user)
            # ★ v6.3：导入线程用自己的连接，不与主线程共用同一个 Cursor
            user_db = UserDatabase(db_file, private=True)

            try:
                success_count = 0
                error_count = 0
                error_details = []

                for i in range(0, total, batch_size):
                    if self.is_cancelled():
                        break
                    batch = data[i:i + batch_size]
                    batch_success, batch_errors = self.main_window.process_import_batch(
                        batch, mapping, conflict_resolution, user_db, i + 1
                    )
                    success_count += batch_success
                    error_count += len(batch_errors)
                    error_details.extend(batch_errors)
                    self.progress.emit(min(i + batch_size, total), total)

                self.finished.emit({
                    'success': success_count,
                    'errors': error_count,
                    'error_details': error_details
                })
            finally:
                user_db.close()
        except Exception as e:
            self.error.emit(str(e))


class SearchDataWorker(ThreadedWorker):
    """数据搜索工作线程"""
    def __init__(self, user_db, keyword, parent=None):
        super().__init__(parent)
        self.user_db = user_db
        self.keyword = keyword

    def run(self):
        try:
            self.status.emit(f"正在搜索: {self.keyword}...")
            data = self.user_db.search_data_all_columns_enhanced(self.keyword)
            if self.is_cancelled():
                return
            self.finished.emit(data)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.release_db()


class ExportDataWorker(ThreadedWorker):
    """数据导出工作线程"""
    def __init__(self, user_db, filename, parent=None):
        super().__init__(parent)
        self.user_db = user_db
        self.filename = filename

    def run(self):
        try:
            self.status.emit("正在导出数据...")
            success = self.user_db.export_to_csv(self.filename)
            if self.is_cancelled():
                return
            self.finished.emit(success)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.release_db()


class BackupWorker(ThreadedWorker):
    """数据库备份工作线程"""
    def __init__(self, user_db, backup_type="manual", parent=None):
        super().__init__(parent)
        self.user_db = user_db
        self.backup_type = backup_type

    def run(self):
        try:
            self.status.emit("正在备份数据库...")
            backup_path = self.user_db.backup_database(backup_type=self.backup_type)
            if self.is_cancelled():
                return
            self.finished.emit(backup_path)
        except Exception as e:
            self.error.emit(str(e))
        finally:
            self.release_db()


class BarcodeGenerator(BaseWorker):
    """条形码/二维码生成器（使用线程池）"""
    def __init__(self, fn, *args, **kwargs):
        super().__init__(fn, *args, **kwargs)


# ===================== 多线程工作器结束 =====================


class ProjectInfo:
    """项目信息元数据（集中管理所有项目相关信息）"""
    VERSION = "6.22"
    BUILD_DATE = "2026-09-28"
    AUTHOR = "杜玛"
    LICENSE = "GNU Affero General Public License v3.0"
    COPYRIGHT = "© 永久 杜玛"
    URL = "https://github.com/duma520"
    MAINTAINER_EMAIL = "不提供"
    NAME = "数据管理系统"
    DESCRIPTION = "数据管理系统，支持用户管理、数据录入、条形码/二维码生成、数据统计等功能。"
    VERSION_HISTORY = {
        "1.0.0": "初始版本，支持基本数据录入和显示。",
        "1.1.0": "增加条形码和二维码生成，支持数据搜索功能。",
        "1.2.0": "添加用户管理功能，支持多用户登录。",
        "1.3.0": "优化界面布局，增加数据统计功能。",
        "1.4.0": "修复已知问题，优化性能和用户体验。",
        "4.11.0": "增加支持EAN13，改成弹窗修改删除等操作，列表马卡龙色系间隔显示。",
        "4.12.0": "优化代码结构，修复已知问题。",
        "4.15.0": "增加右键菜单功能。",
        "4.16.0": "增加json，csv文件导入功能。",
        "4.17.0": "增强数据导入功能，支持自动创建用户和数据结构识别。",
        "4.18.0": "增加专门麻将规则JSON文件导入功能。",
        "4.20.0": "增加修改用户名功能。",
        "4.21.0": "增加数据库备份恢复功能。",
        "4.30.0": "增加拼音首字搜索和持续优化功能。",
        "4.69.1": "升级到PySide6，修复兼容性问题。",
        "4.71.0": "全面采用多线程处理数据加载、导入、搜索、导出等操作，大幅提升响应速度，避免界面卡顿。",
        "5.0.0": "重大版本升级，全面多线程架构重构，性能与稳定性飞跃提升。",
        "5.1.0": "优化启动流程，修复程序启动卡顿问题，提升用户响应体验。",
        "5.1.1": "深度优化启动速度：重型模块(barcode/qrcode/PIL)改为延迟加载，启动时间从6秒降至2秒以内。",
        "6.0": "版本升级至 6.0，持续优化性能与稳定性。",
        "6.1": "新增全局设置实时自动保存+持久化：窗口几何/最大化、上次登录用户、统一导入配置均保存至 excel_manager_settings.json，下次启动自动沿用。",
        "6.2": "修复三处已知问题：①install_package 引用未导入 subprocess 且定义在调用之后；②重复数据清理对话框 selected_fields 作用域错误；③HELP_TEXT 死代码接入『帮助→使用说明』。",
        "6.3": "修复『添加资料点确定后程序自动退出』：①统计/详情面板清空布局改用 takeAt+deleteLater（原 setParent(None) 会在布局里留下悬空指针、踩坏堆内存）；②QThread 工作线程生命周期加固（取消+保活+延迟释放，避免 QThread 在线程未结束时被析构而 qFatal 退出）；③工作线程改用独立数据库连接，不再与主线程共用 sqlite3 Cursor；退出前等待所有线程结束。",
        "6.4": "①添加数据成功后不再弹窗，改为状态栏提示；②「修改列配置」不再重建（清空）数据库，改为迁移表结构：新增/删除/改类型列、按中文名自动识别改名列，已有数据全部保留，改前自动备份；③修复多列『唯一/必填』时写配置主键冲突（INSERT OR REPLACE + JSON 列表存储），并支持多列唯一/必填。",
        "6.5": "登录后也能修改列配置：主窗口「工具→🧩修改列配置」与「🛠️用户设置」窗口里的按钮均可修改当前用户列配置（与登录窗口入口共用同一套「备份+迁移表结构+保存配置」流程，数据保留），改完自动刷新表头/统计/详情。",
        "6.6": "添加数据后自动翻到最新行：新记录添加成功并刷新列表后，表格自动滚动到最后一行（最新记录）并选中它，无需再手动往下拖；仅「添加数据」触发，刷新/搜索/导入等其它刷新不受影响。",
        "6.7": "修复「增加列后全是 None、还以为数据没了，重开后添加不了数据」：①列配置改以数据库真实表结构为准（启动、录入前、打开列配置前自动校正，并把 users.settings 回写一致），不再因“设置与表结构分裂”而显示错位或报 table data has no column named xxx；②修改列配置的迁移不再静默清空 —— 旧版“一列都对不上就只保留记录条数”会把整表值清成 NULL（实测丢过 70 条），现改为位置兜底保住数据、真对不上就直接中止；③迁移后核对表结构是否真等于新配置；④确认框直接列出「保留数据的列/新增列/会被删除的列」；⑤表格取值改按列名、NULL 显示为空串（不再显示 None）；⑥列配置对话框不允许删到最后一列、新列默认名不再重复。",
        "6.8": "按用户要求把所有提示改到状态栏（不弹窗打断）：全文件 146 处 QMessageBox.information/warning/critical 统一改为 notify() —— 成功/信息走状态栏临时消息，警告/错误走状态栏右侧常驻提示（不会被常规状态刷新盖掉，点击可清除，完整原文在 tooltip，并按级别用绿/橙/红色）；弹着对话框时顺带闪一下标题；实在找不到状态栏才退回**非阻塞**弹窗。需要用户「是/否」决定的确认框（QMessageBox.question，删除/覆盖/退出等 11 处）仍然保留。",
        "6.9": "条形码编码格式与 Code128 子集可逐列设置：在「新建用户」和「工具→修改列配置」两处，都可为每个勾选「条形码」的列选择编码格式（自动/Code128/EAN13/EAN8/UPC-A/Code39）与 Code128 子集（自动/A/B/C）。格式与子集随列配置存进库（barcode_format_* / barcode_subset_*），表格显示、详情查看、图片导出、HTML 报告全部按该列的设置出码；数据不满足所选格式时自动回退 Code128 并给出提示，不会出现“条码空白”。",
        "6.10": "新增「👁️显示列设置」：数据列表可以选择只显示其中几列（工具菜单 / 用户设置窗口 / 表格右键 / 表头右键四个入口，表头右键还可一键隐藏当前列或恢复全部显示）。隐藏只影响显示——数据照常录入、保存、搜索、导出，详情/打印/复制依旧是完整数据；设置存在本用户库的 config 表里（hidden_columns），下次打开还是这套显示列，数据统计也只统计显示的列。",
        "6.11": "修复「登录窗口跑到屏幕外，连拖都拖不回来」：窗口恢复上次位置前先用 safe_window_geometry() 校验是否还在屏幕可见范围内（换了分辨率/拔了外接显示器/远程桌面都会导致旧坐标越界），越界或首次运行就居中到主屏幕；主窗口用同一套校验。",
        "6.12": "修复「程序窗口都顶出屏幕了」的真正根因——窗口位置每启动一次就往上顶 31px：Windows 上 setGeometry() 摆的是客户区坐标，而 x()/y()（存进设置里的值）是含标题栏的边框坐标，两者恒定差一个标题栏高度，于是“存→取→摆”每轮把窗口推进屏幕外一点（实测 main_geometry 已漂到 y=-31）。现改为 resize()+move() 摆放（与 x()/y() 同坐标系，零漂移）；存盘改用 window_geometry_for_save()：最大化时存 normalGeometry()（不再存 -8/-31 残影）并保证存的坐标一定看得见；窗口显示后再用 ensure_window_on_screen() 按真实边框兜一底（够不着就拉回屏幕中间）；窗口比屏幕还大时自动缩到屏幕内（整窗夹取）；新增「工具→🪟窗口位置重置（居中显示）」(Ctrl+Alt+C) 作为手动救回入口。",
        "6.13": "「显示列设置」里也能单独勾选条形码/二维码图列了（用户：「条形码，二维码 这些也要列进去，如果有的话」）：数据列表里那两列图片以前只能跟着来源列一起显示/隐藏，现在在清单里单独成一项（显示成「××条形码 (条形码图 · 来自「××」)」），键＝列名#barcode / 列名#qrcode，和数据列名一起存在用户库 config 的 hidden_columns 里；取消勾选只影响数据列表里那张图（数据列、详情、打印、复制、导出仍旧完整）；表头右键「隐藏这一列」对图列同样有效；对话框统计改为「共 N 项（X 列 + Y 张图）」；列改名/删列后残留的图列键会自动清理。",
        "6.14": "表头排序改成三态循环：升序 → 降序 → 默认（不排序）（用户：「点表头排序，除了正反排序，还要有默认排序，就是不排序」）。① 排序状态按【数据列名】记（不按列序号），隐藏列/增减列也不会排错列；② 排序改在【数据层】做（渲染前对整份数据排一遍），不再原地改单元格文本——老实现不搬 Qt.UserRole 里的 rowid，排完序后再编辑/删除会动错行（真 bug，已修）；③ 第三下回到默认排序＝数据库原始顺序，并清掉表头箭头（原来第三下的箭头一直留着，看着像没生效）；④ 数字列按数值比（1/2/10 而不是 1/10/2），空值永远排最后；⑤ 关掉 Qt 内置排序（否则点表头它自己也会排一次，和三态打架）；⑥ 排序状态在刷新/搜索后保持，搜索后点表头是在搜索结果里排（不再把搜索结果丢掉）。",
        "6.15": "关闭就是关闭，换人登录改用新增的「👤切换用户登录」（用户：「点程序关闭就关闭，不用返回到登录窗口。程序增加切换用户登录就可以了」）：① MainWindow.closeEvent 不再无条件把登录窗口又显示回来（原来点关闭只是回到登录窗口、程序不退出），现在保存设置/停线程/自动备份/关库之后直接退出程序（显式 app.quit()，避免登录窗口隐身后进程残留）；② 文件菜单：「🚪退出登录」→「👤切换用户登录」（switch_user()，旧名 logout() 保留为别名），「🚪退出」→「❌退出程序」；③ LoginWindow.showEvent 每次显示都刷新用户列表（切回来立刻看到最新用户）；④ 帮助文档同步。",
        "6.16": "「详细信息」页新增翻页（用户：「详细信息增加 向上和向下翻，就不用每次都回到列表点才能看详细，直接详细就可以直接下一个，或者上一个」）：详情页顶部常驻「⬆ 上一条 / ⬇ 下一条」+「第 N / M 条」位置提示 + 「📊 回到列表」；快捷键 Alt+↑ / Alt+↓（只在详情页生效）。翻页时**列表的选中行跟着同步**（回到列表位置对得上），到头时按钮置灰并在状态栏提醒（不绕回另一头）；show_item_detail() 现在支持 index=（双击路径）或 row=（翻页路径）两种调用，表未渲染/未选中/无数据时安全返回。",
        "6.17": "Code128 子集改成「装得进就按你选的出码，装不进才回退自动」（用户：「Code128，C，就是列设置是这种的，符合的都改成用户要求的，不符合的才改成自动」）：① 子集 C 不再要求偶数位 —— 纯数字一律用 C（偶数位全程 C；奇数位按 Code128 规范让最后一位转一次 B，否则那一位会被丢掉），只有含非数字字符才回退「自动」（首尾空格不参与判断）；② 只有 ASCII 的 0-9 才算「纯数字」（全角「１２３４」、上标「²²」以前被 str.isdigit() 当成数字，结果 Code128 编不出来变成空白条码）；③ 修掉上游 python-barcode 的一个真 bug —— 它的 _try_to_optimize() 会把「起始符后面的第一个码」当成切换码合并，而 99 在 C 里其实是数据对「99」，所以 **「99」开头的数字（如 9912345678）会被静默吃掉两位**；锁定子集时不再合并，auto 走纯数字时也用同一实现，一并修好；④ 同一对象重复出码结果不再变（出码前复位字符集/缓冲区）。",
        "6.18": "不符合所选 Code128 子集的值用【字体颜色】标出来（用户：「不符合 Code128 C 的，可以用颜色标记出来，因为背景色已经是固定的，改背景色会设计太多东西，所以应该是字体颜色不一样，而且要跟背景色有反差，否则连字都看不清」）：数据列表里凡是装不进该列所选 Code128 子集（A/B/C）的值，字体改成深红 #8B0000（对 5 种马卡龙行底色的 WCAG 对比度最低 6.41:1，普通文字是 8.09，都远高于 AA 要求的 4.5:1），鼠标悬停有 tooltip 说明原因（例如“Code128-C 只支持纯数字，这个值里还有「-ABC」这类非数字字符，出码时会自动回退为「自动」子集”）；**只改字体颜色、不碰背景色**（背景是固定的马卡龙交替行色）。子集选「自动」的列、EAN13/EAN8/UPC-A/Code39 列、空值都不标；数据列表、搜索结果、排序后重新渲染都会标（跟着值走，不跟着行号）。",
        "6.19": "把「这张条码实际是用什么出的」直接显示出来（回答用户问的「一维码 为什么没有实现 code128 C 子集方式？」—— 光看条码图根本看不出来）：① 新增 `barcode_display_text()` 把「非 ASCII → 拼音首字母_6位哈希」的转换从 generate_barcode 里抽出来做单一来源（说明与实际出码共用同一段）；② 新增 `describe_barcode_encoding()`：真的构建一次条码对象、看【起始符】来判断实际子集，输出如「Code128-C 全程 C 子集（起始符 105）」「Code128-C，中间切了一次 B（起始符 105）—— 奇数位数字，规范要求」「Code128 自动切换（起始符 104）：所选 C 子集装不下，已回退」「EAN13」；③ 表格/搜索结果/未展示的旧搜索路径的条码图上悬停可见，详细信息页在条码图下面直接写一行「编码：…」。这样“到底有没有按我选的 C 出码”不用再猜。",
        "6.20": "「不符合所选 Code128 子集」的**一维码图**改用浅红底标出来（用户：「一维码背景色不是白色的吗，如果改一维码 不符合Code128 编码的C子集，就用其他背景色」）：条码图本身是白底（行底色被图片完全盖住、用户根本看不见），所以不符合子集的那张图直接渲染成**浅红底 #FFCDD2**（黑条对比度≈14:1，扫描毫无影响），图片控件的底色也跟着换；**只影响屏幕上画的图，导出/打印仍是白底**（扫描最安全、也不会把颜色印到标签上）。新增模块级 `BARCODE_MISMATCH_BG_COLOR()` / `barcode_background_for()`，`generate_barcode()` 多一个 `background` 参数（None＝白底，旧调用不受影响）。",
        "6.21": "修好「Code128 C 扫不出来」（用户：「我有个程序需要扫 code128 C 才能识别…可以肯定是现在这个程序里面生成的 C 子集还是有问题」）：**编码本身没错**（新测试把出图逐模块比对标准码表，全对；起始符 105、校验符按规范算），真因是 **`generate_barcode()` 把图统一缩到高 100px** —— 10 位数字的 C 条码有 90 个模块，被压成 97x100px 后每根黑条只剩 **1px**、插值还糊出 37 个灰度像素、黑条从 **25 根掉到 21 根**，扫描器根本读不出来（同一值走 B 子集出图 402px 宽、条更稀，勉强能读 ⇒ 看起来就像「C 子集坏了」）。修法：**不再缩放位图，改成直接提高 dpi 出图**（dpi=300*scale，模块宽度=mm*dpi/25.4，条宽永远是整数像素、零灰度）：详情页/导出图片/批量导出/打印报告四个地方用 `BARCODE_RENDER_SCALE=2`（每模块 4.7px、10 位 C 图 545px 宽，屏幕直接对着扫就能读）；表格里保持旧观感用 `barcode_preview_pixmap()` 缩略图（高仍 100px、行高不变），**并且【双击那张条码图】会弹出放大窗口**（`BarcodeZoomLabel` + `BarcodeZoomDialog`，可另存为 PNG 交给别家程序读）。新增回归测试 `_scaffold\\smoke_test_barcode_size.py`（44 项，含逐模块解码 + 源码守卫）。",
        "6.22": "新增「🔎详细信息显示设置」（用户：「设置 增加 详细信息，哪些可以暂时不显示，就好像之前哪些列不显示一样的逻辑，因为有时候可能只需要显示一些信息，包括可能只需要显示二维码或者一维码 或者 哪些信息，可能不需要全部都显示。」）：详细信息页也像「显示列」一样挑着显示。**可以逐项勾选**：基本信息里的每个字段（键 `field:<列名>`）、每一列的条形码块（`barcode:<列名>`）、每一列的二维码块（`qrcode:<列名>`）；清单里三组各有父节点，勾一下＝整组开关；另有六个快捷按钮「✅全部显示 / 🚫全部隐藏 / 🔁反选 / 📋只看基本信息 / ▮只看一维码 / ▦只看二维码」。设置存本用户库 config 表的 `hidden_detail_items`（存**被隐藏**的键、空＝全部显示），与「显示列设置」同一套逻辑；**只影响「🔎详细信息」页画不画** —— 数据列表、录入编辑、搜索、数据统计、打印、复制、导出依旧完整；列改名/删列后残留的旧键会自动清理；一项都不勾不让确定（免得详情页整页空着）。入口三个：工具菜单「🔎详细信息显示设置」、用户设置窗口按钮、详细信息页顶部「👁️ 显示设置」按钮。"
    }
    HELP_TEXT = """
数据管理系统使用说明

1. 用户管理
   - 首次使用需要创建用户
   - 每个用户可以配置独立的数据库结构
   - 支持多用户同时使用
   - 换人登录: 文件 → 👤切换用户登录(回到登录窗口, 程序不退出)
   - 关闭程序: 点窗口右上角 ✕ 或 文件 → ❌退出程序(就是退出, 不再回登录窗口)

2. 数据操作
   - 添加数据: 点击工具栏"+"按钮或按Ctrl+N
   - 编辑数据: 双击表格行或右键选择"编辑"
   - 删除数据: 选中行后右键选择"删除"或按Delete键
   - 搜索数据: 在搜索框输入关键词后回车或点击搜索按钮
   - 详细信息翻页: 详情页顶部「⬆上一条 / ⬇下一条」(Alt+↑/Alt+↓), 看完直接下一条
   - 表头排序: 点表头循环 升序 → 降序 → 默认(不排序), 箭头表示当前方向

3. 条形码/二维码功能
   - 在列配置中勾选"条形码"或"二维码"选项
   - 条形码可逐列选择编码格式: 自动 / Code128 / EAN13 / EAN8 / UPC-A / Code39
   - Code128 还可指定子集: A(大写+数字+控制符) B(大小写+数字+符号) C(纯数字,任意位数)
   - 能装进所选子集就按你的设置出码, 装不进(如C里放字母)才回退为"自动"
   - 装不进的值: 文字用【深红字体】、它那张一维码图用【浅红底】(悬停看原因)
   - 只影响屏幕上的显示: 条码导出/打印仍是白底(扫描最安全), 数据底色也不变
   - 想看某张条码实际用的是哪种子集: 鼠标悬停条码图(详情页会直接写在图下面,
     例如“Code128-C 全程 C 子集（起始符 105）”)
   - 表格里的条码是缩略图(太小扫不出来): 【双击那张条码图】弹出放大窗口,
     放大后的图可以直接对着屏幕扫, 也能「另存为 PNG」交给别的程序读;
     详细信息页/导出的图片/打印报告用的也都是放大后的清晰图
   - 新建用户时和以后右键"修改列配置"时都能改这两项
   - 数据不符合所选格式时会自动回退为 Code128, 不会出现空白条码
   - 支持任意文本生成二维码

4. 显示设置（只想显示一部分时用）
   4.1 数据列表显示哪些列
   - 入口: 「工具→👁️显示列设置」、「用户设置」窗口里的按钮、表格右键、表头右键
   - 勾选要显示的列; 取消勾选的列只是不在数据列表里显示
   - 条形码/二维码 图列也在这个清单里(要是有的话), 可以单独勾选
   - 只影响显示: 数据照常录入、保存、搜索、导出, 详情/打印/复制仍是完整数据
   - 设置存在当前用户的库里, 下次打开还是这套显示列
   - 想恢复全部显示: 表头右键「✅ 全部显示」
   4.2 详细信息显示哪些项目
   - 入口: 「工具→🔎详细信息显示设置」、「用户设置」窗口里的按钮、
     详细信息页顶部「👁️ 显示设置」按钮
   - 可以逐项勾选: 基本信息里的每个字段、每一列的条形码、每一列的二维码
   - 常用场景有快捷按钮: 只看基本信息 / 只看一维码 / 只看二维码 / 全部显示 / 反选
   - 组名(基本信息/条形码/二维码)上点一下 = 整组一起开关
   - 只影响「详细信息」页显示: 数据列表、录入编辑、搜索、统计、打印、复制、导出都不变
   - 至少要勾一项才能确定(避免详情页整页空着); 设置同样存在当前用户的库里

5. 数据导出
   - 支持导出为CSV文件
   - 支持打印数据详情
   - 支持导出条形码/二维码为图片

6. 快捷键
   - Ctrl+N: 添加新数据
   - Ctrl+F: 搜索
   - Ctrl+E: 编辑选中数据
   - Delete: 删除选中数据
   - F5: 刷新数据

7. 技术支持
   - 作者: {cls.AUTHOR}
   - 版本: {cls.VERSION}
   - 项目地址: {cls.URL}
   - 问题反馈: 请通过GitHub提交issue

注意:
- 标记为*的字段为必填项
- 标记为🔑的字段值必须唯一
- EAN13条码必须为13位数字且校验位正确
"""

    @classmethod
    def get_metadata(cls) -> dict:
        """获取主要元数据字典"""
        return {
            'version': cls.VERSION,
            'author': cls.AUTHOR,
            'license': cls.LICENSE,
            'url': cls.URL
        }

    @classmethod
    def get_header(cls) -> str:
        """生成标准化的项目头信息"""
        return f"{cls.NAME} {cls.VERSION} | {cls.LICENSE} License | {cls.URL}"


# 马卡龙色系定义
class MacaronColors:
    # 粉色系
    SAKURA_PINK = '#FFB7CE'  # 樱花粉
    ROSE_PINK = '#FF9AA2'    # 玫瑰粉
    # 蓝色系
    SKY_BLUE = '#A2E1F6'     # 天空蓝
    LILAC_MIST = '#E6E6FA'   # 淡丁香
    # 绿色系
    MINT_GREEN = '#B5EAD7'   # 薄荷绿
    APPLE_GREEN = '#D4F1C7'  # 苹果绿
    # 黄色/橙色系
    LEMON_YELLOW = '#FFEAA5' # 柠檬黄
    BUTTER_CREAM = '#FFF8B8' # 奶油黄
    PEACH_ORANGE = '#FFDAC1' # 蜜桃橙
    # 紫色系
    LAVENDER = '#C7CEEA'     # 薰衣草紫
    TARO_PURPLE = '#D8BFD8'  # 香芋紫
    # 中性色
    CARAMEL_CREAM = '#F0E6DD' # 焦糖奶霜

    LIGHT_CORAL = '#F08080'  # 浅珊瑚色
    PALE_TURQUOISE = '#AFEEEE' # 浅绿松石色
    LIGHT_SALMON = '#FFA07A' # 浅鲑鱼色


# ===== 全局设置持久化（实时自动保存）=====
def settings_path():
    """全局设置文件路径（程序目录/打包后exe目录）"""
    try:
        if getattr(sys, 'frozen', False):
            base = os.path.dirname(sys.executable)
        else:
            base = os.path.dirname(os.path.abspath(__file__))
    except Exception:
        base = os.path.abspath('.')
    return os.path.join(base, 'excel_manager_settings.json')


def load_global_settings():
    """加载全局设置"""
    try:
        with open(settings_path(), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_global_settings(data):
    """原子写入全局设置（tmp + os.replace，防止写坏）"""
    try:
        path = settings_path()
        tmp = path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[DEBUG] 保存全局设置失败: {e}")
        return False


# ===== 窗口位置安全（★ v6.11 起；v6.12 修掉「每启动一次往上顶 31px」的漂移）=====
def _screen_available_rects():
    """当前所有屏幕的可用区域列表（拿不到屏幕信息时返回空列表）"""
    rects = []
    try:
        app = QApplication.instance()
        if app is None:
            return rects
        for screen in app.screens():
            try:
                rects.append(screen.availableGeometry())
            except Exception:
                continue
    except Exception as e:
        print(f"[DEBUG] 读取屏幕信息失败: {str(e)}")
    return rects


def safe_window_geometry(saved_geo, default_size=(600, 400), min_visible=120,
                         always_center=False):
    """算一个「一定看得见、抓得住」的窗口几何 (x, y, w, h)，拿不到屏幕信息时返回 None（★ v6.11）

    用户报过：**登录窗口跑到屏幕外，连拖都拖不回来**（鼠标抓不到标题栏，只能去删
    配置文件）。查了他真实的 `excel_manager_settings.json`：
        "login_geometry": [903, -64, 823, 400]
    —— y = **-64**，窗口上边缘（标题栏）跑到屏幕上边之外了，所以看也不是、拖也不是。
    根因是全局设置存的是上次的坐标，换分辨率 / 多显示器 / 远程桌面 / 缩放比例变化后，
    那组坐标就不在可见范围里，直接 setGeometry 就把窗口“送”出去。

    规则：
      * `always_center=True`（登录窗口用）→ 尺寸沿用保存的（合理时），**位置一律居中**；
      * 否则保存的坐标要**够得着**才沿用：窗口上边缘不能在屏幕上方之外（否则抓不到
        标题栏），且横向至少露出 `min_visible` 像素、纵向至少露出 40 像素；
      * 够不着 / 没保存 → 用保存的尺寸（不合理就用 default_size）**居中到主屏幕**。
      * ★ v6.12：够得着的坐标再做一次**整窗夹取** —— 窗口不能被摆到只露出一部分
        （右边/下边够不着也等于不能用），窗口比屏幕还大时就按屏幕可用尺寸缩下来。

    ★ 注意：这里算出来的是**窗口边框坐标**（与 `x()`/`y()`/`frameGeometry()` 同一
    坐标系）—— 摆回去**必须用 `place_window()`（resize + move）**，不能用
    `setGeometry()`（那是客户区坐标，会每次把窗口往上顶一个标题栏高度）。
    """
    try:
        rects = _screen_available_rects()
        width, height = int(default_size[0]), int(default_size[1])
        saved = None
        if saved_geo and len(saved_geo) == 4:
            try:
                x, y, w, h = (int(v) for v in saved_geo)
                saved = (x, y, w, h)
                if w >= 200 and h >= 150:
                    width, height = w, h
            except (TypeError, ValueError):
                saved = None

        if saved is not None and not always_center:
            x, y = saved[0], saved[1]
            if not rects:
                # 连屏幕信息都拿不到（启动极早期等），不折腾，保持原样
                return (x, y, width, height)
            for area in rects:
                # ① 标题栏必须够得着：窗口上边缘不能跑到屏幕上方之外
                if y < area.top():
                    continue
                if y > area.bottom() - 40:
                    continue
                # ② 横向至少露出 min_visible 像素（能点得到、拖得动）
                visible_w = min(x + width, area.right() + 1) - max(x, area.left())
                if visible_w < min_visible:
                    continue
                # ③ 纵向至少露出 40 像素（就是标题栏那一条）
                visible_h = min(y + height, area.bottom() + 1) - max(y, area.top())
                if visible_h < 40:
                    continue
                # ★ v6.12：够得着就沿用，但整窗要夹进这块屏幕 —— 窗口比屏幕还大时
                #   按屏幕尺寸缩下来，避免右边/下边永远够不着（也是“顶出屏幕”的一种）
                fit_w = min(width, area.width())
                fit_h = min(height, area.height())
                fit_x = min(max(x, area.left()), area.right() + 1 - fit_w)
                fit_y = min(max(y, area.top()), area.bottom() + 1 - fit_h)
                if (fit_x, fit_y, fit_w, fit_h) != (x, y, width, height):
                    print(f"[DEBUG] 窗口上次的位置/尺寸超出屏幕，已夹取 "
                          f"{(x, y, width, height)} -> {(fit_x, fit_y, fit_w, fit_h)}")
                return (fit_x, fit_y, fit_w, fit_h)
            print(f"[DEBUG] 窗口上次的位置够不着（{list(saved)}），改为居中显示")

        if not rects:
            return None
        area = None
        try:
            app = QApplication.instance()
            screen = app.primaryScreen() if app is not None else None
            area = screen.availableGeometry() if screen is not None else None
        except Exception:
            area = None
        if area is None or area.isEmpty():
            area = rects[0]
        width, height = min(width, area.width()), min(height, area.height())
        x = area.x() + max(0, (area.width() - width) // 2)
        y = area.y() + max(0, (area.height() - height) // 2)
        return (x, y, width, height)
    except Exception as e:
        print(f"[DEBUG] 计算安全窗口位置失败: {str(e)}")
        return None


def center_window_on_screen(window):
    """把窗口摆到所在屏幕可用区域的正中间（★ v6.11，返回是否成功）"""
    if window is None:
        return False
    try:
        app = QApplication.instance()
        if app is None:
            return False
        screen = window.screen() or app.primaryScreen()
        if screen is None:
            return False
        area = screen.availableGeometry()
        if area.isEmpty():
            return False
        frame = window.frameGeometry()
        frame.moveCenter(area.center())
        window.move(frame.topLeft())
        return True
    except Exception as e:
        print(f"[DEBUG] 窗口居中失败: {str(e)}")
        return False


def place_window(window, geo):
    """按 (x, y, w, h) 摆放窗口（★ v6.12，返回是否成功）

    ★★ 为什么必须用 resize + move，**不能用 setGeometry**（真机实测，勿改回）：
        在 Windows 上 setGeometry(x, y, w, h) 摆的是**客户区**，而
        `x()`/`y()`/`frameGeometry()`（以及本程序存进设置里的值）是**窗口边框**
        坐标，两者恒定相差一个标题栏高度。实测（本机 31px）：

            setGeometry(560, 120, 800, 800)  →  x()/y() == (560, 89)
            resize(800,800) + move(560, 120) →  x()/y() == (560, 120)   ✅

        于是「存 x()/y() → 下次 setGeometry 摆回」每启动一次就把窗口往上顶
        31px，跑几轮标题栏就跑到屏幕上边之外 —— 用户真实的
        `main_geometry=[396, -31, 800, 800]` 就是这么一步步顶上去的
        （y=-31 正好是一个标题栏的高度，v6.11 的居中每跑一次也会再顶 31px，
        所以那个版本看起来“修了又犯”）。

        `move()` 与 `x()`/`y()` 完全同坐标系（show 之前/之后都一样，已实测），
        所以「存 → 取 → 摆」零漂移。
    """
    if window is None or not geo:
        return False
    try:
        x, y, width, height = (int(v) for v in geo)
    except (TypeError, ValueError):
        return False
    try:
        window.resize(width, height)
        window.move(x, y)
        return True
    except Exception as e:
        print(f"[DEBUG] 摆放窗口失败: {str(e)}")
        return False


def window_geometry_for_save(window, default_size=(800, 800)):
    """取一个适合写进设置的窗口几何 [x, y, w, h]（★ v6.12）

    ① 最大化/全屏时改用 `normalGeometry()`：直接读 `x()`/`y()` 拿到的是 Windows
       给最大化窗口的边框残影（典型是 -8 / -31），存下去下次启动就成了
       「标题栏在屏幕上边之外」，必须避免；
    ② 最后过一遍 `safe_window_geometry()`：保证存进文件里的坐标**一定看得见**，
       下次启动不用靠兜底也能正常显示。

    返回 [x, y, w, h]；实在取不到就返回 None（调用方保持原值不写）。
    """
    try:
        cur = None
        try:
            if window.isMaximized() or window.isFullScreen():
                normal = window.normalGeometry()
                if normal.width() >= 200 and normal.height() >= 150:
                    cur = [normal.x(), normal.y(), normal.width(), normal.height()]
        except Exception:
            cur = None
        if cur is None:
            cur = [window.x(), window.y(), window.width(), window.height()]
        safe = safe_window_geometry(cur, default_size)
        return list(safe) if safe else list(cur)
    except Exception as e:
        print(f"[DEBUG] 读取窗口几何失败: {str(e)}")
        return None


def ensure_window_on_screen(window, force_center=False, min_visible=120):
    """窗口**显示之后**按真实边框再兜一次底：抓不住就摆回屏幕中间（★ v6.12，返回是否挪动过）

    为什么还要这一层：启动时是「先算坐标 → 再 show()」，而真正决定标题栏在哪的是
    窗口边框，且多屏/DPI 切换时窗口管理器可能自行微调落点。所以等窗口真出来之后，
    拿 `frameGeometry()` 再核对一次最稳（这是最后一道保险，正常的路径不会触发）。

    `force_center=True` 时无条件居中（登录窗口用：它的位置本来就该居中）。
    最大化/全屏窗口直接跳过 —— 它们本来就在屏幕内，别去打扰。
    """
    if window is None:
        return False
    try:
        if window.isFullScreen() or window.isMaximized():
            return False
    except Exception:
        pass
    try:
        rects = _screen_available_rects()
        if not rects:
            return False
        frame = window.frameGeometry()
        if not force_center:
            for area in rects:
                if frame.top() < area.top():
                    continue
                if frame.top() > area.bottom() - 40:
                    continue
                visible_w = min(frame.right(), area.right()) - max(frame.left(), area.left())
                if visible_w < min_visible:
                    continue
                return False  # 够得着，不折腾
            print(f"[DEBUG] 窗口真实位置够不着（{frame.getRect()}），摆回屏幕中间")
        return center_window_on_screen(window)
    except Exception as e:
        print(f"[DEBUG] 窗口位置兜底失败: {str(e)}")
        return False


class UserManager:
    def __init__(self):
        self.conn = sqlite3.connect('user_manager.db')
        self.cursor = self.conn.cursor()
        self._create_tables()
    
    def _create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                db_file TEXT NOT NULL,
                settings TEXT NOT NULL,
                created_at TEXT NOT NULL,
                last_login TEXT
            )
        ''')
        self.conn.commit()
    
    def add_user(self, username, db_file, settings):
        try:
            now = datetime.now().isoformat()
            self.cursor.execute('INSERT INTO users VALUES (?, ?, ?, ?, ?)', 
                              (username, db_file, settings, now, None))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def delete_user(self, username):
        self.cursor.execute('DELETE FROM users WHERE username=?', (username,))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def get_users(self):
        self.cursor.execute('SELECT username, created_at, last_login FROM users ORDER BY last_login DESC')
        return self.cursor.fetchall()
    
    def get_user_settings(self, username):
        self.cursor.execute('SELECT settings FROM users WHERE username=?', (username,))
        result = self.cursor.fetchone()
        return result[0] if result else None
    
    def get_user_db_file(self, username):
        self.cursor.execute('SELECT db_file FROM users WHERE username=?', (username,))
        result = self.cursor.fetchone()
        return result[0] if result else None
    
    def update_user_settings(self, username, settings):
        self.cursor.execute('UPDATE users SET settings=? WHERE username=?', 
                          (settings, username))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def update_last_login(self, username):
        now = datetime.now().isoformat()
        self.cursor.execute('UPDATE users SET last_login=? WHERE username=?', 
                          (now, username))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def update_username(self, old_username, new_username):
        """更新用户名"""
        try:
            # 检查新用户名是否已存在
            self.cursor.execute('SELECT username FROM users WHERE username=?', (new_username,))
            if self.cursor.fetchone():
                return False, "用户名已存在"
            
            # 获取用户当前信息
            self.cursor.execute('SELECT db_file, settings, created_at, last_login FROM users WHERE username=?', (old_username,))
            user_data = self.cursor.fetchone()
            if not user_data:
                return False, "用户不存在"
            
            # 更新用户名
            self.cursor.execute('''
                UPDATE users SET username=?, db_file=?
                WHERE username=?
            ''', (new_username, f'user_{new_username}.db', old_username))
            
            # 重命名数据库文件
            old_db_file = user_data[0]
            new_db_file = f'user_{new_username}.db'
            if os.path.exists(old_db_file):
                os.rename(old_db_file, new_db_file)
            
            self.conn.commit()
            return True, "用户名修改成功"
        except Exception as e:
            self.conn.rollback()
            return False, f"修改用户名失败: {str(e)}"    
    
    def close(self):
        self.conn.close()


class UserDatabase:
    def __init__(self, db_file, private=False):
        self.db_file = db_file
        # ★ v6.3：private=True 表示这条连接专供某个工作线程使用（不走连接池）。
        #   原因见 clone_for_thread()：sqlite3 的 Connection/Cursor 没有线程保护，
        #   主线程与工作线程共用同一个 Cursor 并发 execute 会破坏堆内存，
        #   症状就是程序随机、毫无提示地直接退出。
        self.private_connection = bool(private)
        if self.private_connection:
            self.conn = self._open_private_connection(db_file)
            self.cursor = self.conn.cursor()
            return

        self.conn = DatabaseConnectionManager.get_connection(db_file)
        
        # 验证连接是否有效
        try:
            self.cursor = self.conn.cursor()
            # 测试连接
            self.cursor.execute("SELECT 1")
        except (sqlite3.ProgrammingError, sqlite3.OperationalError):
            # 连接无效，重新获取
            DatabaseConnectionManager._connections.pop(db_file, None)
            self.conn = DatabaseConnectionManager.get_connection(db_file)
            self.cursor = self.conn.cursor()
        
        # 设置数据库优化参数
        self._optimize_database()
        self._create_tables()
        # 设置WAL模式
        self.cursor.execute("PRAGMA journal_mode=WAL")
        # 初始化拼音字段
        self._init_pinyin_columns()
        # 修复：只有在表存在时才创建索引
        self._safe_create_indexes()

    def _create_indexes(self):
        """创建必要的索引以提高查询性能（原方法，保留为兼容性）"""
        try:
            # 先检查表是否存在
            self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='data'")
            if not self.cursor.fetchone():
                print("[DEBUG] data表不存在，跳过索引创建")
                return
                
            # 为常用搜索列创建索引
            self.cursor.execute('PRAGMA table_info(data)')
            columns = [row[1] for row in self.cursor.fetchall()]
            
            for column in columns:
                # 为文本列创建索引（跳过拼音列和过长的列）
                if not column.endswith('_pinyin') and len(column) < 50:
                    index_name = f"idx_{column}"
                    try:
                        self.cursor.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON data({column})')
                    except:
                        pass  # 忽略索引创建失败
            
            # 注意：rowid是SQLite的隐含列，无法用CREATE INDEX创建索引，
            # 且rowid本身就是表的B-tree键，天然已索引，无需额外创建
            print(f"[DEBUG] 数据库索引创建完成")
        except Exception as e:
            print(f"[DEBUG] 创建索引失败: {str(e)}")

    def _safe_create_indexes(self):
        """安全地创建索引，避免表不存在错误"""
        try:
            # 先检查表是否存在
            self.cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='data'")
            if not self.cursor.fetchone():
                print("[DEBUG] data表不存在，跳过索引创建")
                return
            
            # 为常用搜索列创建索引
            self.cursor.execute('PRAGMA table_info(data)')
            columns = [row[1] for row in self.cursor.fetchall()]
            
            for column in columns:
                # 为文本列创建索引（跳过拼音列和过长的列）
                if not column.endswith('_pinyin') and len(column) < 50:
                    index_name = f"idx_{column}"
                    try:
                        self.cursor.execute(f'CREATE INDEX IF NOT EXISTS {index_name} ON data({column})')
                    except Exception as e:
                        print(f"[DEBUG] 创建索引 {index_name} 失败: {str(e)}")
                        pass  # 忽略索引创建失败
            
            # 注意：rowid是SQLite的隐含列，无法用CREATE INDEX创建索引，
            # 且rowid本身就是表的B-tree键，天然已索引，无需额外创建
            print(f"[DEBUG] 数据库索引创建完成")
        except Exception as e:
            print(f"[DEBUG] 创建索引失败: {str(e)}")

    def _optimize_database(self):
        """数据库性能优化设置"""
        try:
            # 设置缓存大小（16MB）
            self.cursor.execute("PRAGMA cache_size = -16000")
            # 设置页面大小
            self.cursor.execute("PRAGMA page_size = 4096")
            # 启用外键（可选）
            self.cursor.execute("PRAGMA foreign_keys = ON")
            # 设置临时存储位置为内存
            self.cursor.execute("PRAGMA temp_store = MEMORY")
            # 设置同步模式为NORMAL（性能与安全平衡）
            self.cursor.execute("PRAGMA synchronous = NORMAL")
            # 设置自动清理WAL文件
            self.cursor.execute("PRAGMA wal_autocheckpoint = 1000")
            
            print(f"[DEBUG] 数据库优化设置完成")
        except Exception as e:
            print(f"[DEBUG] 数据库优化设置失败: {str(e)}")
                
    def _create_tables(self):
        """创建必要的表 - 表的实际创建在 initialize_database 中完成"""
        pass
    
    def initialize_database(self, columns_config):
        self.cursor.execute('DROP TABLE IF EXISTS data')
        
        columns = []
        for col in columns_config:
            col_name = self.quote_identifier(col['name'])  # 使用引号保护列名
            # EAN13实际存储为TEXT类型
            col_type = 'TEXT' if col.get('type') == 'EAN13' else col.get('type', 'TEXT')
            columns.append(f"{col_name} {col_type}")
        
        create_table_sql = f"CREATE TABLE data ({', '.join(columns)})"
        self.cursor.execute(create_table_sql)
        
        self.cursor.execute('DROP TABLE IF EXISTS config')
        self.cursor.execute('''
            CREATE TABLE config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')

        self._save_columns_config_to_db(columns_config)
        self.conn.commit()

    def _save_columns_config_to_db(self, columns_config):
        """把列配置（标签/条码/二维码/唯一/必填）写进 config 表

        ★ v6.4：
          * 一律用 INSERT OR REPLACE —— 原来用 INSERT，一旦有 2 列以上勾选
            「唯一」或「必填」就会因 key 主键冲突报 IntegrityError；
          * 唯一/必填改为 JSON 列表（unique_columns / required_columns）存储，
            支持多列；旧的单值键 unique_column / required_column 会被清理；
          * 顺手清掉已删除列残留的标签/条码/二维码键。
        """
        keep_label_keys = set()
        keep_flag_keys = set()
        keep_barcode_opt_keys = set()
        for col in columns_config:
            name = col['name']
            keep_label_keys.add(f"col_{name}_label")
            self.cursor.execute('INSERT OR REPLACE INTO config VALUES (?, ?)',
                                (f"col_{name}_label", col.get('label', name)))
            for flag, prefix in (('is_barcode', 'barcode_column_'), ('is_qrcode', 'qrcode_column_')):
                key = f"{prefix}{name}"
                keep_flag_keys.add(key)
                if col.get(flag, False):
                    self.cursor.execute('INSERT OR REPLACE INTO config VALUES (?, ?)', (key, "1"))
                else:
                    self.cursor.execute('DELETE FROM config WHERE key=?', (key,))

            # ★ v6.9：条形码编码格式 / Code128 子集（跟着「条形码」开关一起存）
            fmt_key = f"barcode_format_{name}"
            sub_key = f"barcode_subset_{name}"
            keep_barcode_opt_keys.update((fmt_key, sub_key))
            if col.get('is_barcode', False):
                self.cursor.execute('INSERT OR REPLACE INTO config VALUES (?, ?)',
                                    (fmt_key, normalize_barcode_format(
                                        col.get('barcode_format', 'auto'))))
                self.cursor.execute('INSERT OR REPLACE INTO config VALUES (?, ?)',
                                    (sub_key, normalize_barcode_subset(
                                        col.get('barcode_subset', 'auto'))))
            else:
                self.cursor.execute('DELETE FROM config WHERE key IN (?, ?)',
                                    (fmt_key, sub_key))

        unique_names = [c['name'] for c in columns_config if c.get('is_unique', False)]
        required_names = [c['name'] for c in columns_config if c.get('is_required', False)]
        self.cursor.execute('INSERT OR REPLACE INTO config VALUES (?, ?)',
                            ('unique_columns', json.dumps(unique_names, ensure_ascii=False)))
        self.cursor.execute('INSERT OR REPLACE INTO config VALUES (?, ?)',
                            ('required_columns', json.dumps(required_names, ensure_ascii=False)))
        self.cursor.execute("DELETE FROM config WHERE key IN ('unique_column','required_column')")

        # 清理已删除列的残留配置键
        self.cursor.execute('SELECT key FROM config')
        for (key,) in self.cursor.fetchall():
            if key.startswith('col_') and key.endswith('_label') and key not in keep_label_keys:
                self.cursor.execute('DELETE FROM config WHERE key=?', (key,))
            elif key.startswith(('barcode_column_', 'qrcode_column_')) and key not in keep_flag_keys:
                self.cursor.execute('DELETE FROM config WHERE key=?', (key,))
            elif key.startswith(('barcode_format_', 'barcode_subset_')) and \
                    key not in keep_barcode_opt_keys:
                self.cursor.execute('DELETE FROM config WHERE key=?', (key,))

    def migrate_column_config(self, new_columns_config):
        """按新的列配置**迁移**表结构，尽量保留已有数据（★ v6.4）

        与 initialize_database() 的区别：不再 DROP 整张表，因此在
        「登录窗口 →修改列配置」时不会再把用户数据清空。

        规则：
          * 列名相同 → 直接沿用；
          * 列名变了但中文名(label)没变 → 视为重命名，自动改名并保留数据；
          * 新列 → 建列，旧行该列为空；
          * 删掉列 → 只丢这一列的数据；
          * 列类型变化 → 按新类型重建（SQLite 只能重建表才能改类型）。

        返回 (是否成功, 说明文字)
        """
        new_columns_config = [c for c in (new_columns_config or []) if c.get('name')]
        if not new_columns_config:
            return False, '列配置为空'

        # 表不存在：等同新建
        self.cursor.execute('SELECT name FROM sqlite_master WHERE type="table" AND name="data"')
        if not self.cursor.fetchone():
            self.initialize_database(new_columns_config)
            return True, '已按新配置创建数据表'

        # ---- 1. 读旧表结构与旧中文名 ----
        self.cursor.execute('PRAGMA table_info(data)')
        old_names = [row[1] for row in self.cursor.fetchall()]
        old_labels = {}
        self.cursor.execute('SELECT key, value FROM config WHERE key LIKE "col_%_label"')
        for key, value in self.cursor.fetchall():
            old_labels[key[4:-6]] = value          # col_<name>_label

        # ---- 2. 逐个新列决定数据来源（与「修改列配置」确认框的预演共用同一套逻辑）----
        mapping, renamed, added, removed = plan_column_migration(
            old_names, old_labels, new_columns_config)
        carried = [c['name'] for c in new_columns_config
                   if mapping.get(c['name']) and mapping[c['name']] in old_names]

        # ★ v6.7 关键防护：一列都对不上时【直接中止】，绝不把已有数据清成 NULL。
        #   旧实现走“只保留记录条数”的兜底，结果整表数据全变 NULL（用户会以为数据
        #   没了）—— 实测就这样丢过一次（定位器ID 的 70 条）。这里在任何 DDL 之前返回。
        self.cursor.execute('SELECT COUNT(*) FROM data')
        rowcount = self.cursor.fetchone()[0]
        if rowcount > 0 and not carried:
            return False, ('新列配置与现有表结构完全不匹配（列名与中文名都不一样），'
                           f'为避免清空已有 {rowcount} 条数据已中止；'
                           '请至少保留一个原有列的「列名」或「中文名」。')

        # ---- 3. 重命名列（能让旧数据原封不动地挂到新列名上）----
        for new_name, old_name in renamed.items():
            try:
                self.cursor.execute(
                    f'ALTER TABLE data RENAME COLUMN {self.quote_identifier(old_name)} '
                    f'TO {self.quote_identifier(new_name)}')
                # 改名成功后，旧列已经“就叫新名字”了，后续搬迁直接用新名
                mapping[new_name] = new_name
            except Exception as e:
                print(f"[DEBUG] 重命名列失败 {old_name}->{new_name}: {str(e)}")
                mapping[new_name] = None
                added.append(new_name)
        self.cursor.execute('PRAGMA table_info(data)')
        old_names = [row[1] for row in self.cursor.fetchall()]

        # ---- 4. 重建表并把旧数据搬过去 ----
        cols_sql = []
        for col in new_columns_config:
            col_type = 'TEXT' if col.get('type') == 'EAN13' else col.get('type', 'TEXT')
            cols_sql.append(f"{self.quote_identifier(col['name'])} {col_type}")
        self.cursor.execute('DROP TABLE IF EXISTS data__migrate')
        self.cursor.execute(f"CREATE TABLE data__migrate ({', '.join(cols_sql)})")

        if carried:
            new_clause = ', '.join(self.quote_identifier(n) for n in carried)
            old_clause = ', '.join(self.quote_identifier(mapping[n]) for n in carried)
            # 连 rowid 一起搬，保证记录标识不变
            self.cursor.execute(
                f"INSERT INTO data__migrate (rowid, {new_clause}) "
                f"SELECT rowid, {old_clause} FROM data")
        else:
            # ★ v6.7：正常已被土面的防护拦住（会直接 return False）。这里仅作最后兼底：
            #   保留记录条数，但绝不静默 —— 打日志 + 在返回说明里写明。
            print(f"[DEBUG] 迁移时没有任何列可搬（旧列={old_names}），"
                  f"仅保留 {rowcount} 条记录条数")
            for _ in range(rowcount):
                self.cursor.execute('INSERT INTO data__migrate DEFAULT VALUES')

        self.cursor.execute('DROP TABLE data')
        self.cursor.execute('ALTER TABLE data__migrate RENAME TO data')

        # ---- 5. 重写列配置 / 补拼音列 / 重建索引 ----
        self._save_columns_config_to_db(new_columns_config)
        # 拼音开关以库里的 config 为准（private 连接不会走 _init_pinyin_columns，
        # 否则重建表后拼音列不会补回来，拼音搜索会失效）
        pinyin_on = bool(getattr(self, 'pinyin_enabled', False))
        try:
            self.cursor.execute("SELECT value FROM config WHERE key='pinyin_enabled'")
            row = self.cursor.fetchone()
            if row is not None:
                pinyin_on = (row[0] == '1')
        except Exception as e:
            print(f"[DEBUG] 读取拼音开关失败: {str(e)}")
        if pinyin_on:
            try:
                self._enable_pinyin_system()
            except Exception as e:
                print(f"[DEBUG] 重建拼音列失败: {str(e)}")
        self._safe_create_indexes()
        self.conn.commit()

        detail = []
        if added:
            detail.append(f"新增列 {len(added)} 个")
        if renamed:
            detail.append(f"重命名 {len(renamed)} 个")
        if removed:
            detail.append(f"删除列 {len(removed)} 个（其数据已丢弃）")
        if rowcount and not carried:
            detail.append('⚠️ 没有任何列能与原表对应，各列数据已清空（仅保留了记录条数）')
        msg = f'已有 {rowcount} 条数据已保留'
        if detail:
            msg += '；' + '，'.join(detail)
        return True, msg
    
    def get_columns_config(self):
        self.cursor.execute('SELECT key, value FROM config WHERE key LIKE "col_%"')
        config = {}
        for key, value in self.cursor.fetchall():
            config[key] = value

        # ★ v6.7：这里**不再**在 config 表为空时 return None。
        #   列配置必须始终以 data 表的真实结构为准（名字来自 table_info），
        #   否则“users.settings 中的列”与“表里的列”一旦不一致，就会显示错位、
        #   添加数据报 table data has no column named xxx。

        # ★ v6.4：唯一/必填支持多列（JSON 列表），并兼容旧的单值键
        unique_names, required_names = set(), set()
        try:
            self.cursor.execute('SELECT key, value FROM config WHERE key IN '
                                '("unique_columns","required_columns","unique_column","required_column")')
            for key, value in self.cursor.fetchall():
                if not value:
                    continue
                if key == 'unique_column':
                    unique_names.add(value)
                elif key == 'required_column':
                    required_names.add(value)
                else:
                    try:
                        names = json.loads(value)
                    except Exception:
                        names = [value]
                    (unique_names if key == 'unique_columns' else required_names).update(names or [])
        except Exception as e:
            print(f"[DEBUG] 读取唯一/必填配置失败: {str(e)}")
        
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        columns_config = []
        for col in columns:
            col_config = {
                'name': col,
                'label': config.get(f'col_{col}_label', col),
                'type': 'TEXT'  # 默认类型
            }
            
            # 检查是否是条形码列
            col_config['is_barcode'] = False
            self.cursor.execute('SELECT value FROM config WHERE key=?', (f"barcode_column_{col}",))
            if self.cursor.fetchone():
                col_config['is_barcode'] = True
            
            # 检查是否是二维码列
            col_config['is_qrcode'] = False
            self.cursor.execute('SELECT value FROM config WHERE key=?', (f"qrcode_column_{col}",))
            if self.cursor.fetchone():
                col_config['is_qrcode'] = True

            # ★ v6.9：条形码编码格式 / Code128 子集（没配置过就是「自动」）
            col_config['barcode_format'] = 'auto'
            col_config['barcode_subset'] = 'auto'
            self.cursor.execute('SELECT key, value FROM config WHERE key IN (?, ?)',
                                (f"barcode_format_{col}", f"barcode_subset_{col}"))
            for opt_key, opt_value in self.cursor.fetchall():
                if opt_key.startswith('barcode_format_'):
                    col_config['barcode_format'] = normalize_barcode_format(opt_value)
                else:
                    col_config['barcode_subset'] = normalize_barcode_subset(opt_value)
            
            # 检查是否是唯一列
            col_config['is_unique'] = col in unique_names
            
            # 检查是否是必填列
            col_config['is_required'] = col in required_names
            
            # 获取列类型
            self.cursor.execute('PRAGMA table_info(data)')
            for col_info in self.cursor.fetchall():
                if col_info[1] == col:
                    col_config['type'] = col_info[2].upper()
                    break
            
            columns_config.append(col_config)
        
        return columns_config

    def get_table_columns(self):
        """返回 data 表的真实列名（不含拼音列）（★ v6.7）"""
        try:
            self.cursor.execute('PRAGMA table_info(data)')
            return [row[1] for row in self.cursor.fetchall()
                    if not row[1].endswith('_pinyin')]
        except Exception as e:
            print(f"[DEBUG] 读取表结构失败: {str(e)}")
            return []

    def get_column_labels(self):
        """返回 {列名: 中文名}（★ v6.7）"""
        labels = {}
        try:
            self.cursor.execute('SELECT key, value FROM config WHERE key LIKE "col_%_label"')
            for key, value in self.cursor.fetchall():
                labels[key[4:-6]] = value          # col_<name>_label
        except Exception as e:
            print(f"[DEBUG] 读取列中文名失败: {str(e)}")
        return labels

    def get_hidden_columns(self):
        """读取「数据列表」里被隐藏的列名（★ v6.10）

        存在 config 表（key/value），跟着用户库走；读不到就是空列表＝全部显示。
        键名用 HIDDEN_COLUMNS_CONFIG_KEY（**绝不能以 col_ 开头**，否则会被
        get_columns_config 的 `LIKE "col_%"` 当成列配置键扫进去）。
        """
        return self._load_config_list(HIDDEN_COLUMNS_CONFIG_KEY, '显示列设置')

    def set_hidden_columns(self, names):
        """保存「数据列表」里被隐藏的列名（★ v6.10，立即 commit）

        传空列表＝全部显示（键仍然写入，便于以后清空设置）。
        """
        return self._save_config_list(HIDDEN_COLUMNS_CONFIG_KEY, names, '显示列设置')

    def get_hidden_detail_items(self):
        """读取「详细信息」页里被隐藏的项目键（★ v6.22）

        键形如 field:<列名> / barcode:<列名> / qrcode:<列名>，与「显示列设置」同一套
        逻辑（存被隐藏的、空＝全部显示），跟着用户库走。
        """
        return self._load_config_list(HIDDEN_DETAIL_CONFIG_KEY, '详细信息显示设置')

    def set_hidden_detail_items(self, keys):
        """保存「详细信息」页里被隐藏的项目键（★ v6.22，立即 commit）"""
        return self._save_config_list(HIDDEN_DETAIL_CONFIG_KEY, keys, '详细信息显示设置')

    def _load_config_list(self, config_key, what):
        """从 config 表读一个「字符串列表」设置（★ v6.22，仅本文件内部复用）"""
        try:
            self.cursor.execute('SELECT value FROM config WHERE key=?', (config_key,))
            row = self.cursor.fetchone()
            if not row or not row[0]:
                return []
            names = json.loads(row[0])
            return [str(n) for n in names] if isinstance(names, list) else []
        except Exception as e:
            print(f"[DEBUG] 读取{what}失败: {str(e)}")
            return []

    def _save_config_list(self, config_key, names, what):
        """往 config 表写一个「字符串列表」设置（★ v6.22，仅本文件内部复用）"""
        try:
            self.cursor.execute(
                'CREATE TABLE IF NOT EXISTS config (key TEXT PRIMARY KEY, value TEXT)')
            self.cursor.execute(
                'INSERT OR REPLACE INTO config VALUES (?, ?)',
                (config_key,
                 json.dumps([str(n) for n in (names or [])], ensure_ascii=False)))
            self.conn.commit()
            return True
        except Exception as e:
            print(f"[DEBUG] 保存{what}失败: {str(e)}")
            return False

    def column_migration_preview(self, new_columns_config):
        """预演「修改列配置」的影响（★ v6.7）

        返回 (keep, added, removed)：
          keep    = ['旧列→新列' 或 '列名']，这些列的数据会保留
          added   = ['新列']，旧表里没有可对应的列
          removed = ['旧列']，新配置里不再有，其数据会丢
        """
        mapping, renamed, added, removed = plan_column_migration(
            self.get_table_columns(), self.get_column_labels(), new_columns_config)
        keep = []
        for name, old in mapping.items():
            if not old:
                continue
            keep.append(f"{old}→{name}" if old != name else name)
        return keep, added, removed

    def _init_pinyin_columns(self):
        """初始化拼音字段系统"""
        try:
            # 检查是否已启用拼音字段功能
            self.cursor.execute("SELECT value FROM config WHERE key='pinyin_enabled'")
            result = self.cursor.fetchone()
            self.pinyin_enabled = result and result[0] == '1'
            
            if not self.pinyin_enabled:
                # 自动评估是否启用拼音字段
                self._auto_enable_pinyin()
                
        except:
            self.pinyin_enabled = False
    
    def _auto_enable_pinyin(self):
        """自动评估并启用拼音字段功能"""
        try:
            # 检查数据量
            count = self.get_data_count()
            
            # 如果数据量较大(>100条)，启用拼音字段优化
            if count > 100:
                self._enable_pinyin_system()
                print(f"[DEBUG] 自动启用拼音字段优化，数据量: {count}")
        except:
            pass
    
    def _enable_pinyin_system(self):
        """启用拼音字段系统"""
        try:
            # 为所有文本列添加拼音字段
            self.cursor.execute('PRAGMA table_info(data)')
            columns = [row[1] for row in self.cursor.fetchall()]
            
            for col in columns:
                pinyin_col = f"{col}_pinyin"
                # ★ v6.4：拼音列本身不再派生拼音列（否则会生成 xxx_pinyin_pinyin 之类垃圾列）
                if col.endswith('_pinyin'):
                    continue
                # 检查是否已存在拼音列
                self.cursor.execute(f"PRAGMA table_info(data)")
                existing_columns = [row[1] for row in self.cursor.fetchall()]
                
                if pinyin_col not in existing_columns:
                    # 添加拼音列
                    self.cursor.execute(f"ALTER TABLE data ADD COLUMN {pinyin_col} TEXT")
            
            # 标记拼音系统已启用
            self.cursor.execute("INSERT OR REPLACE INTO config VALUES ('pinyin_enabled', '1')")
            self.conn.commit()
            self.pinyin_enabled = True
            
            # 为现有数据生成拼音
            self._generate_pinyin_for_existing_data()
            
        except Exception as e:
            print(f"[DEBUG] 启用拼音系统失败: {str(e)}")
            self.pinyin_enabled = False
    
    def _generate_pinyin_for_existing_data(self):
        """为现有数据生成拼音字段"""
        if not self.pinyin_enabled:
            return
            
        try:
            self.cursor.execute('PRAGMA table_info(data)')
            columns = [row[1] for row in self.cursor.fetchall()]
            
            # 获取所有数据
            self.cursor.execute('SELECT rowid, * FROM data')
            all_data = self.cursor.fetchall()
            
            for row_data in all_data:
                rowid = row_data[0]
                update_data = {}
                
                for i, value in enumerate(row_data[1:], 1):
                    if i-1 < len(columns):
                        col_name = columns[i-1]
                        pinyin_col = f"{col_name}_pinyin"
                        
                        if value and isinstance(value, str) and pinyin_col in columns:
                            # 生成拼音首字母
                            pinyin_initials = self._get_pinyin_initials(str(value))
                            update_data[pinyin_col] = pinyin_initials
                
                if update_data:
                    set_clause = ', '.join([f"{key}=?" for key in update_data.keys()])
                    sql = f"UPDATE data SET {set_clause} WHERE rowid=?"
                    self.cursor.execute(sql, tuple(update_data.values()) + (rowid,))
            
            self.conn.commit()
            print(f"[DEBUG] 已为 {len(all_data)} 条记录生成拼音字段")
            
        except Exception as e:
            print(f"[DEBUG] 生成拼音字段失败: {str(e)}")

    def insert_data(self, data):
        """插入数据，同时生成拼音字段"""
        # 添加拼音字段
        data_with_pinyin = self._add_pinyin_to_data(data)

        # 使用引号保护列名
        columns = ', '.join([self.quote_identifier(key) for key in data_with_pinyin.keys()])
        placeholders = ', '.join(['?'] * len(data_with_pinyin))
        
        sql = f"INSERT INTO data ({columns}) VALUES ({placeholders})"
        self.cursor.execute(sql, tuple(data_with_pinyin.values()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def update_data(self, rowid, data):
        """更新数据，同时更新拼音字段"""
        # 添加拼音字段
        data_with_pinyin = self._add_pinyin_to_data(data)
        
        # 使用引号保护列名
        set_clause = ', '.join([f"{self.quote_identifier(key)}=?" for key in data_with_pinyin.keys()])
        sql = f"UPDATE data SET {set_clause} WHERE rowid=?"
        
        self.cursor.execute(sql, tuple(data_with_pinyin.values()) + (rowid,))
        self.conn.commit()
        return self.cursor.rowcount > 0

    
    def _add_pinyin_to_data(self, data):
        """为数据添加拼音字段"""
        if not self.pinyin_enabled:
            return data
            
        data_with_pinyin = data.copy()
        
        # 获取现有列信息
        self.cursor.execute('PRAGMA table_info(data)')
        existing_columns = [row[1] for row in self.cursor.fetchall()]
        
        for key, value in data.items():
            if isinstance(value, str) and value.strip():
                pinyin_key = f"{key}_pinyin"
                # 只有拼音列存在时才添加
                if pinyin_key in existing_columns:
                    pinyin_initials = self._get_pinyin_initials(value)
                    data_with_pinyin[pinyin_key] = pinyin_initials
        
        return data_with_pinyin

    def delete_data(self, rowid):
        sql = "DELETE FROM data WHERE rowid=?"
        self.cursor.execute(sql, (rowid,))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def check_unique(self, column, value, exclude_rowid=None):
        # 使用引号保护列名
        quoted_column = self.quote_identifier(column)
        sql = f"SELECT COUNT(*) FROM data WHERE {quoted_column}=?"
        params = [value]
        
        if exclude_rowid:
            sql += " AND rowid!=?"
            params.append(exclude_rowid)
        
        self.cursor.execute(sql, tuple(params))
        return self.cursor.fetchone()[0] == 0

    
    def search_data(self, keyword):
        """基础搜索方法 - 使用SQLite引号保护"""
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        conditions = []
        params = []
        for col in columns:
            # 使用引号保护列名
            quoted_col = self.quote_identifier(col)
            conditions.append(f"{quoted_col} LIKE ?")
            params.append(f"%{keyword}%")
        
        sql = f"SELECT rowid, * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def search_data_enhanced(self, keyword):
        """增强搜索：支持中文、英文、拼音、拼音首字母模糊搜索 - 使用SQLite引号保护"""
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        # 生成拼音和拼音首字母
        from pypinyin import lazy_pinyin, Style
        
        # 中文转拼音
        pinyin_full = ''.join(lazy_pinyin(keyword))
        # 中文转拼音首字母
        pinyin_initials = ''.join(lazy_pinyin(keyword, style=Style.FIRST_LETTER))
        
        conditions = []
        params = []
        
        for col in columns:
            # 使用引号保护列名
            quoted_col = self.quote_identifier(col)
            
            # 原始关键词搜索
            conditions.append(f"{quoted_col} LIKE ?")
            params.append(f"%{keyword}%")
            
            # 拼音全拼搜索
            if pinyin_full and pinyin_full != keyword:
                conditions.append(f"{quoted_col} LIKE ?")
                params.append(f"%{pinyin_full}%")
            
            # 拼音首字母搜索
            if pinyin_initials and pinyin_initials != keyword:
                conditions.append(f"{quoted_col} LIKE ?")
                params.append(f"%{pinyin_initials}%")
        
        if not conditions:
            return []
        
        sql = f"SELECT DISTINCT rowid, * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def search_data_all_columns(self, keyword):
        """搜索所有列，包含完整数据 - 使用SQLite引号保护"""
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        if not columns:
            return []
        
        # 构建搜索条件，使用引号保护列名
        conditions = []
        params = []
        for col in columns:
            quoted_col = self.quote_identifier(col)
            conditions.append(f"{quoted_col} LIKE ?")
            params.append(f"%{keyword}%")
        
        sql = f"SELECT rowid, * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()



    def get_all_data(self):
        self.cursor.execute('SELECT rowid, * FROM data')
        return self.cursor.fetchall()
    
    def get_data_by_id(self, rowid):
        self.cursor.execute('SELECT rowid, * FROM data WHERE rowid=?', (rowid,))
        return self.cursor.fetchone()
    
    def get_data_count(self):
        self.cursor.execute('SELECT COUNT(*) FROM data')
        return self.cursor.fetchone()[0]
    
    def export_to_csv(self, filename):
        self.cursor.execute('SELECT * FROM data')
        data = self.cursor.fetchall()
        
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        with open(filename, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(columns)
            writer.writerows(data)
        
        return True

    def auto_detect_columns(self, data):
        """自动检测数据结构并返回列配置"""
        columns_config = []
        
        if isinstance(data, dict):  # 处理类似rules.json的结构
            for key, value in data.items():
                if isinstance(value, list) and value and isinstance(value[0], dict):
                    columns_config.append({
                        'name': key.replace(' ', '_').lower(),
                        'label': key,
                        'type': 'TEXT'
                    })
        elif isinstance(data, list) and data and isinstance(data[0], dict):  # 处理CSV或列表JSON
            sample_item = data[0]
            for key, value in sample_item.items():
                col_type = 'TEXT'
                if isinstance(value, (int, float)):
                    col_type = 'INTEGER' if isinstance(value, int) else 'REAL'
                columns_config.append({
                    'name': key.replace(' ', '_').lower(),
                    'label': key,
                    'type': col_type
                })
        
        return columns_config

    def get_cursor(self):
        """获取数据库游标"""
        return self.cursor

    @staticmethod
    def _open_private_connection(db_file):
        """为工作线程打开一条独立连接（不走连接池）"""
        conn = sqlite3.connect(db_file, timeout=30, check_same_thread=False)
        try:
            cur = conn.cursor()
            cur.execute("PRAGMA cache_size = -16000")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()
        except Exception:
            pass
        return conn

    def clone_for_thread(self):
        """★ v6.3：给工作线程用的独立连接副本

        主窗口的 self.user_db 走连接池（DatabaseConnectionManager 里
        check_same_thread=False），主线程与工作线程会共用同一个
        Connection / Cursor。sqlite3 层对 Cursor 没有任何线程保护，
        两端并发 execute 会破坏堆内存 —— 症状就是程序随机、无提示退出。
        """
        try:
            return UserDatabase(self.db_file, private=True)
        except Exception as e:
            print(f"[DEBUG] 创建工作线程数据库连接失败，退回共享连接: {str(e)}")
            return self

    def close(self):
        self.conn.close()

    def backup_database(self, backup_type="auto", max_backups=30):
        """备份当前数据库"""
        backups_dir = os.path.join(os.path.dirname(self.db_file), "backups")
        os.makedirs(backups_dir, exist_ok=True)
        
        # 创建带时间戳的备份文件名
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{os.path.splitext(os.path.basename(self.db_file))[0]}_{backup_type}_{timestamp}.db"
        backup_path = os.path.join(backups_dir, backup_name)
        
        try:
            # 使用WAL模式备份
            backup_conn = sqlite3.connect(backup_path)
            self.conn.backup(backup_conn)
            backup_conn.close()
            
            # 清理多余的备份文件
            self._cleanup_old_backups(backups_dir, max_backups)
            
            return backup_path
        except Exception as e:
            print(f"备份失败: {str(e)}")
            return None
    
    def _cleanup_old_backups(self, backups_dir, max_backups):
        """清理旧的备份文件"""
        backups = []
        for f in os.listdir(backups_dir):
            if f.endswith(".db"):
                full_path = os.path.join(backups_dir, f)
                backups.append((full_path, os.path.getmtime(full_path)))
        
        # 按修改时间排序(旧的在前面)
        backups.sort(key=lambda x: x[1])
        
        # 删除多余的备份文件
        while len(backups) > max_backups:
            old_backup = backups.pop(0)
            try:
                os.remove(old_backup[0])
            except:
                pass
    
    def get_backups_list(self):
        """获取所有备份文件列表"""
        backups_dir = os.path.join(os.path.dirname(self.db_file), "backups")
        if not os.path.exists(backups_dir):
            return []
        
        backups = []
        for f in os.listdir(backups_dir):
            if f.endswith(".db") and os.path.splitext(f)[0].startswith(os.path.splitext(os.path.basename(self.db_file))[0]):
                full_path = os.path.join(backups_dir, f)
                file_info = {
                    "path": full_path,
                    "name": f,
                    "size": os.path.getsize(full_path),
                    "mtime": os.path.getmtime(full_path),
                    "type": "auto" if "_auto_" in f else "manual" if "_manual_" in f else "rollback" if "_rollback_" in f else "unknown"
                }
                backups.append(file_info)
        
        # 按修改时间倒序排列(新的在前面)
        backups.sort(key=lambda x: x["mtime"], reverse=True)
        return backups
    
    def restore_from_backup(self, backup_path):
        """从备份恢复数据库"""
        try:
            # 先创建恢复前的备份
            self.backup_database(backup_type="rollback")
            
            # 关闭当前连接
            self.conn.close()
            
            # 删除原数据库文件
            if os.path.exists(self.db_file):
                os.remove(self.db_file)
            
            # 复制备份文件
            import shutil
            shutil.copy2(backup_path, self.db_file)
            
            # 重新连接
            self.conn = sqlite3.connect(self.db_file)
            self.cursor = self.conn.cursor()
            
            return True
        except Exception as e:
            print(f"恢复失败: {str(e)}")
            return False




    def search_data_all_columns_enhanced(self, keyword):
        """增强的全列搜索：智能选择搜索策略，使用SQLite引号保护机制"""
        # 检查数据量决定搜索策略
        count = self.get_data_count()
        
        # 小数据量使用方案3，大数据量使用方案2
        if count <= 100 or not self.pinyin_enabled:
            return self._search_with_python_filter(keyword)  # 方案3
        else:
            return self._search_with_pinyin_columns(keyword)  # 方案2

    def _search_with_pinyin_columns(self, keyword):
        """使用拼音字段进行高效搜索（方案2）- 使用SQLite引号保护"""
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        if not columns:
            return []
        
        # 检查是否为拼音缩写
        is_pinyin_initials = keyword.isalpha() and 2 <= len(keyword) <= 6
        
        conditions = []
        params = []
        
        for col in columns:
            # 跳过拼音列本身
            if col.endswith('_pinyin'):
                continue
                
            # 使用引号保护列名
            quoted_col = self.quote_identifier(col)
            
            # 原始关键词搜索
            conditions.append(f"{quoted_col} LIKE ?")
            params.append(f"%{keyword}%")
            
            # 如果是拼音缩写，搜索对应的拼音列
            if is_pinyin_initials:
                pinyin_col = f"{col}_pinyin"
                if pinyin_col in columns:
                    quoted_pinyin_col = self.quote_identifier(pinyin_col)
                    conditions.append(f"{quoted_pinyin_col} LIKE ?")
                    params.append(f"%{keyword}%")
        
        if not conditions:
            return []
        
        sql = f"SELECT DISTINCT rowid, * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    
    def _search_with_python_filter(self, keyword):
        """使用Python过滤进行搜索（方案3）- 使用SQLite引号保护"""
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        if not columns:
            return []
        
        # 生成拼音和拼音首字母
        pinyin_full = ''.join(lazy_pinyin(keyword))
        pinyin_initials = self._get_pinyin_initials(keyword)
        
        # 检查输入是否为纯字母（可能是拼音缩写）
        is_possible_initials = keyword.isalpha() and 2 <= len(keyword) <= 6
        
        conditions = []
        params = []
        
        for col in columns:
            # 跳过拼音列本身
            if col.endswith('_pinyin'):
                continue
                
            # 使用引号保护列名
            quoted_col = self.quote_identifier(col)
            
            # 原始关键词搜索
            conditions.append(f"{quoted_col} LIKE ?")
            params.append(f"%{keyword}%")
            
            # 拼音全拼搜索
            if pinyin_full and pinyin_full != keyword:
                conditions.append(f"{quoted_col} LIKE ?")
                params.append(f"%{pinyin_full}%")
            
            # 拼音首字母搜索（用户输入中文，搜索拼音首字母）
            if pinyin_initials and pinyin_initials != keyword:
                conditions.append(f"{quoted_col} LIKE ?")
                params.append(f"%{pinyin_initials}%")
        
        # 如果是可能的拼音缩写，获取所有数据并在Python中过滤
        if is_possible_initials and not any(char in keyword for char in 'aeiou'):
            # 获取所有数据
            sql_all = "SELECT rowid, * FROM data"
            self.cursor.execute(sql_all)
            all_data = self.cursor.fetchall()
            
            # 过滤匹配拼音缩写的数据
            filtered_data = []
            for row in all_data:
                if self._match_chinese_by_initials(row, keyword, columns):
                    filtered_data.append(row)
            
            return filtered_data
        
        if not conditions:
            return []
        
        sql = f"SELECT DISTINCT rowid, * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()


    def _match_chinese_by_initials(self, row_data, initials, columns):
        """检查行数据中的中文是否匹配拼音首字母缩写"""
        try:
            # 跳过rowid
            for i, value in enumerate(row_data[1:], 1):
                if i-1 < len(columns) and value and isinstance(value, str):
                    # 只处理可能包含中文的文本
                    if any('\u4e00' <= char <= '\u9fff' for char in str(value)):
                        # 生成该中文的拼音首字母
                        chinese_initials = self._get_pinyin_initials(str(value))
                        if initials.lower() in chinese_initials.lower():
                            return True
        except:
            pass
        return False

    def _get_pinyin_initials(self, keyword):
        """安全地获取拼音首字母，兼容不同版本的pypinyin"""
        try:
            # 方法1: 使用FIRST_LETTER常量
            try:
                return ''.join(lazy_pinyin(keyword, style=Style.FIRST_LETTER))
            except:
                pass
            
            # 方法2: 使用备选常量名
            try:
                return ''.join(lazy_pinyin(keyword, style=Style.INITIALS))
            except:
                pass
            
            # 方法3: 手动处理
            pinyin_list = lazy_pinyin(keyword)
            initials = ''.join([p[0] for p in pinyin_list if p])
            return initials
            
        except Exception as e:
            print(f"[DEBUG] 获取拼音首字母失败: {str(e)}")
            return ""

    def quote_identifier(self, identifier):
        """使用SQLite标准的双引号引用标识符，保护特殊字符"""
        # 移除可能引起SQL注入的特殊字符
        safe_identifier = identifier.replace('"', '').replace(';', '').replace(')', '').replace('(', '')
        return f'"{safe_identifier}"'


    def quote_value(self, value):
        """使用SQLite标准的单引号引用值，并转义特殊字符"""
        if value is None:
            return 'NULL'
        # 转义单引号（将 ' 替换为 ''）
        escaped_value = str(value).replace("'", "''")
        return f"'{escaped_value}'"

    def batch_insert_data(self, data_list):
        """批量插入数据，大幅提升性能"""
        if not data_list:
            return 0
        
        try:
            # 开始事务
            self.cursor.execute("BEGIN TRANSACTION")
            
            inserted_count = 0
            for i, data in enumerate(data_list):
                try:
                    # 检查数据是否为空
                    if not data:
                        print(f"[DEBUG] 第{i+1}条数据为空，跳过")
                        continue
                        
                    # 添加拼音字段
                    data_with_pinyin = self._add_pinyin_to_data(data)
                    
                    # 再次检查处理后的数据是否为空
                    if not data_with_pinyin:
                        print(f"[DEBUG] 第{i+1}条数据处理后为空，跳过")
                        continue
                    
                    # 使用引号保护列名
                    columns = ', '.join([self.quote_identifier(key) for key in data_with_pinyin.keys()])
                    placeholders = ', '.join(['?'] * len(data_with_pinyin))
                    
                    sql = f"INSERT INTO data ({columns}) VALUES ({placeholders})"
                    
                    # 调试：打印SQL语句
                    print(f"[DEBUG] 执行SQL: {sql}")
                    print(f"[DEBUG] 参数: {tuple(data_with_pinyin.values())}")
                    
                    self.cursor.execute(sql, tuple(data_with_pinyin.values()))
                    inserted_count += 1
                    
                except Exception as e:
                    print(f"[DEBUG] 插入第{i+1}条数据失败: {str(e)}")
                    print(f"[DEBUG] 失败数据: {data_with_pinyin}")
                    # 可以选择继续处理其他数据或抛出异常
                    continue
            
            # 提交事务
            self.conn.commit()
            return inserted_count
            
        except Exception as e:
            # 回滚事务
            self.conn.rollback()
            print(f"[DEBUG] 批量插入事务失败: {str(e)}")
            raise e


    def bulk_insert_data(self, data_list, batch_size=1000):
        """超大容量批量插入，分批处理避免内存溢出"""
        total_inserted = 0
        
        for i in range(0, len(data_list), batch_size):
            batch = data_list[i:i + batch_size]
            inserted = self.batch_insert_data(batch)
            total_inserted += inserted
            
            # 进度回调（可选）
            if hasattr(self, 'on_batch_progress'):
                self.on_batch_progress(i + len(batch), len(data_list))
        
        return total_inserted

    def archive_old_data(self, archive_condition, archive_table_name="data_archive"):
        """归档旧数据"""
        try:
            # 创建归档表（如果不存在）
            self.cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {archive_table_name} AS 
                SELECT * FROM data WHERE 1=0
            ''')
            
            # 将符合条件的数据移动到归档表
            self.cursor.execute(f'''
                INSERT INTO {archive_table_name} 
                SELECT * FROM data WHERE {archive_condition}
            ''')
            
            # 删除原表中的归档数据
            self.cursor.execute(f'DELETE FROM data WHERE {archive_condition}')
            
            self.conn.commit()
            
            # 返回归档的记录数
            return self.cursor.rowcount
            
        except Exception as e:
            self.conn.rollback()
            print(f"[DEBUG] 数据归档失败: {str(e)}")
            return 0

    def get_database_size(self):
        """获取数据库文件大小"""
        try:
            if os.path.exists(self.db_file):
                return os.path.getsize(self.db_file)
            return 0
        except:
            return 0


class DatabaseConnectionManager:
    """数据库连接管理器，避免频繁创建连接"""
    _connections = {}
    
    @classmethod
    def get_connection(cls, db_file):
        if db_file not in cls._connections:
            cls._connections[db_file] = sqlite3.connect(db_file, timeout=30, check_same_thread=False)
            # 应用优化设置
            cursor = cls._connections[db_file].cursor()
            cursor.execute("PRAGMA cache_size = -16000")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.close()
        else:
            # 检查连接是否有效
            try:
                # 尝试执行一个简单查询来测试连接
                cls._connections[db_file].cursor().execute("SELECT 1").close()
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                # 连接已关闭，重新创建
                try:
                    cls._connections[db_file].close()
                except:
                    pass
                cls._connections[db_file] = sqlite3.connect(db_file, timeout=30, check_same_thread=False)
                cursor = cls._connections[db_file].cursor()
                cursor.execute("PRAGMA cache_size = -16000")
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.close()
        
        return cls._connections[db_file]
    
    @classmethod
    def close_all(cls):
        for conn in cls._connections.values():
            try:
                conn.close()
            except:
                pass
        cls._connections.clear()


def plan_column_migration(old_names, old_labels, new_columns_config):
    """算出「新列配置」与「旧表列」的对应关系（★ v6.7）

    迁移与「修改列配置」确认框的预演共用本函数，避免两处逻辑不一致。

    匹配顺序（越靠前越可靠）：
      1. 列名完全相同 → 直接沿用；
      2. 列名变了但中文名(label)没变 → 视为重命名，数据跟着走；
      3. 位置兜底：**仅当前两轮一列都没配上时**才启用（即用户把列名和中文名一起
         换掉了）。此时按“同位置”配对，仍能保住数据，而不是把整表清成 NULL。
         如果已经有列配上了（说明用户是保留了部分列、另外新增），那就不再做位置
         兜底 —— 否则会把“被删列”的旧值串到“新增列”里。

    返回 (mapping, renamed, added, removed)
      mapping : {新列名: 旧列名|None}
      renamed : {新列名: 旧列名}（只含真的改了名的）
      added   : [新列名]（旧表里没有可对应的，确属新增）
      removed : [旧列名]（新配置里不再有，其数据会被丢弃）
    """
    old_data = [n for n in (old_names or []) if not n.endswith('_pinyin')]
    new_columns_config = [c for c in (new_columns_config or []) if c.get('name')]
    old_labels = old_labels or {}

    used = set()        # 已被占用的旧列
    paired = set()      # 已配上旧列的新列
    # 先给每个新列留一个 None（=新增列），后面配上旧列再覆盖
    mapping = {c['name']: None for c in new_columns_config}

    # 1) 列名相同
    for col in new_columns_config:
        name = col['name']
        if name in old_data:
            mapping[name] = name
            paired.add(name)
            used.add(name)

    # 2) 中文名相同 → 改名
    for col in new_columns_config:
        name = col['name']
        if name in paired:
            continue
        label = (col.get('label') or '').strip()
        if not label:
            continue
        for old in old_data:
            if old in used:
                continue
            if old_labels.get(old) == label:
                mapping[name] = old
                paired.add(name)
                used.add(old)
                break

    # 3) 位置兜底：只在前两轮一列都没配上时才做（用户把列名+中文名都换了的情况）。
    #    否则会把被删列的旧值串到新增列上（实测：新增 stock 会拿到被删 note 的值）。
    if not paired:
        for idx, col in enumerate(new_columns_config):
            name = col['name']
            if idx < len(old_data):
                old = old_data[idx]
                if old not in used:
                    mapping[name] = old
                    paired.add(name)
                    used.add(old)

    renamed = {n: o for n, o in mapping.items() if o and o != n}
    added = [n for n, o in mapping.items() if not o]
    removed = [o for o in old_data if o not in used]
    return mapping, renamed, added, removed


def duplicate_column_names(columns_config):
    """返回重复的列名（★ v6.7：重名列会导致建表失败/数据串列）"""
    seen, dup = set(), []
    for col in (columns_config or []):
        name = (col.get('name') or '').strip()
        if not name:
            continue
        if name in seen and name not in dup:
            dup.append(name)
        seen.add(name)
    return dup


def column_config_change_preview(db_file, new_columns_config):
    """生成「修改列配置」确认框里的影响说明（★ v6.7）

    把这些信息直接写出来，用户就不会在“明明只是加一列”时把整表数据换掉。
    """
    if not db_file or not os.path.exists(db_file):
        return ''
    db = None
    try:
        db = UserDatabase(db_file, private=True)
        keep, added, removed = db.column_migration_preview(new_columns_config)
    except Exception as e:
        print(f"[DEBUG] 预演列配置变更失败: {str(e)}")
        return ''
    finally:
        if db is not None:
            try:
                db.close()
            except Exception:
                pass
    return ('\n\n本次对已有数据的影响：\n'
            f'  • 保留数据的列：{"、".join(keep) if keep else "（无）"}\n'
            f'  • 新增列（历史记录为空）：{"、".join(added) if added else "（无）"}\n'
            f'  • 删除列（这些列的数据会丢）：{"、".join(removed) if removed else "（无）"}')


def apply_column_config_migration(db_file, username, user_manager, new_columns_config):
    """修改列配置的公共流程：备份 → 迁移表结构（保留数据）→ 保存用户配置

    ★ v6.5：登录窗口（选用户）与主窗口（当前用户）共用本函数，保证两边
    行为一致、都不会再清空数据。

    返回 (是否成功, 说明文字)
    """
    if not db_file:
        return False, '无法获取数据库文件'
    if not new_columns_config:
        return False, '列配置为空'

    try:
        # ★ v6.5 关键：必须用 private=True 的【独立连接】！
        #   否则 UserDatabase(db_file) 拿到的是连接池里与主窗口同一个连接对象，
        #   下面 close() 会把主窗口正在用的连接一并关掉，
        #   紧接着就报 sqlite3.ProgrammingError: Cannot operate on a closed database。
        user_db = UserDatabase(db_file, private=True)
        try:
            # 先备份，万一迁移不理想还能找回
            try:
                user_db.backup_database(backup_type="manual")
            except Exception as e:
                print(f"[DEBUG] 修改列配置前备份失败: {str(e)}")

            # 迁移表结构（不再 DROP 表，数据保留）
            ok, msg = user_db.migrate_column_config(new_columns_config)
            if ok:
                # ★ v6.7：迁移后核对「真实表结构 == 新配置」。
                #   否则会出现“配置改了、表没改”的分裂状态：表格显示错位，
                #   添加数据报 table data has no column named xxx（实测踩过）。
                real = list(user_db.get_table_columns() or [])
                want = [c['name'] for c in new_columns_config]
                if real != want:
                    ok = False
                    msg = (f'表结构未按新配置更新（实际：{"、".join(real) or "无"}；'
                           f'期望：{"、".join(want)}）')
        finally:
            user_db.close()
    except Exception as e:
        return False, f'迁移失败: {str(e)}'

    if not ok:
        return False, msg

    try:
        settings = json.dumps(new_columns_config, ensure_ascii=False)
        if not user_manager.update_user_settings(username, settings):
            return False, f'{msg}；但保存用户配置失败'
    except Exception as e:
        return False, f'{msg}；但保存用户配置失败: {str(e)}'

    return True, msg


class FastImportDialog(QDialog):
    def __init__(self, filename, parent=None):
        super().__init__(parent)
        self.filename = filename
        self.user_manager = parent.user_manager
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle('快速导入配置')
        self.resize(900, 700)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 文件信息
        file_info = QGroupBox('文件信息')
        file_layout = QFormLayout()
        
        self.file_path_label = QLabel(self.filename)
        self.file_type_label = QLabel(self.parent().detect_file_type(self.filename))
        file_layout.addRow('文件路径:', self.file_path_label)
        file_layout.addRow('文件类型:', self.file_type_label)
        
        # 文件大小
        try:
            file_size = os.path.getsize(self.filename)
            size_mb = file_size / (1024 * 1024)
            size_label = QLabel(f"{size_mb:.2f} MB")
            file_layout.addRow('文件大小:', size_label)
        except:
            pass
        
        file_info.setLayout(file_layout)
        layout.addWidget(file_info)
        
        # 导入配置
        config_group = QGroupBox('导入配置')
        config_layout = QFormLayout()
        
        # 目标用户选择
        self.user_combo = QComboBox()
        users = self.user_manager.get_users()
        for user in users:
            self.user_combo.addItem(user[0])
        self.user_combo.currentTextChanged.connect(self.on_user_changed)
        config_layout.addRow('目标用户:', self.user_combo)
        
        # Excel特定配置
        if self.filename.lower().endswith(('.xlsx', '.xls')):
            self.sheet_combo = QComboBox()
            try:
                import openpyxl
                workbook = openpyxl.load_workbook(self.filename, read_only=True)
                for sheet_name in workbook.sheetnames:
                    self.sheet_combo.addItem(sheet_name)
                workbook.close()
            except Exception as e:
                print(f"[DEBUG] 加载Excel工作表失败: {str(e)}")
            config_layout.addRow('工作表:', self.sheet_combo)
        
        # 跳过行数
        self.skip_rows_spin = QSpinBox()
        self.skip_rows_spin.setRange(0, 100)
        self.skip_rows_spin.setValue(0)
        self.skip_rows_spin.valueChanged.connect(self.on_config_changed)
        config_layout.addRow('跳过行数:', self.skip_rows_spin)
    
        # 新增：第0行作为表头选项
        self.first_row_as_header_check = QCheckBox('将第一行作为表头')
        self.first_row_as_header_check.setChecked(True)  # 默认选中
        self.first_row_as_header_check.stateChanged.connect(self.on_config_changed)
        config_layout.addRow('表头设置:', self.first_row_as_header_check)
        
        # 冲突处理
        self.conflict_combo = QComboBox()
        self.conflict_combo.addItems(['跳过重复记录', '更新重复记录', '允许重复记录'])
        config_layout.addRow('重复记录处理:', self.conflict_combo)
        
        # 数据预览行数
        self.preview_rows_spin = QSpinBox()
        self.preview_rows_spin.setRange(1, 50)
        self.preview_rows_spin.setValue(5)
        config_layout.addRow('预览行数:', self.preview_rows_spin)
        
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)
        
        # 字段映射
        self.setup_field_mapping(layout)
        
        # 统计信息
        self.stats_label = QLabel()
        self.stats_label.setStyleSheet("color: #666; font-size: 12px;")
        layout.addWidget(self.stats_label)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        preview_btn = QPushButton('📊 预览数据')
        preview_btn.clicked.connect(self.preview_data)
        preview_btn.setStyleSheet("QPushButton { padding: 8px; }")
        button_layout.addWidget(preview_btn)
        
        auto_map_btn = QPushButton('🔍 自动映射')
        auto_map_btn.clicked.connect(self.auto_map_fields)
        auto_map_btn.setStyleSheet("QPushButton { padding: 8px; }")
        button_layout.addWidget(auto_map_btn)
        
        button_layout.addStretch()
        
        self.ok_btn = QPushButton('🚀 开始导入')
        self.ok_btn.clicked.connect(self.validate_and_accept)
        self.ok_btn.setStyleSheet("QPushButton { padding: 8px; background-color: #4CAF50; color: white; }")
        button_layout.addWidget(self.ok_btn)
        
        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        cancel_btn.setStyleSheet("QPushButton { padding: 8px; }")
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
        
        # 初始加载
        self.on_user_changed()
    
    def setup_field_mapping(self, layout):
        """设置字段映射界面"""
        mapping_group = QGroupBox('字段映射')
        mapping_layout = QVBoxLayout()
        
        # 说明
        help_label = QLabel('请将源文件字段映射到目标数据库字段（未映射的字段将被忽略）')
        help_label.setStyleSheet('color: #666; font-style: italic; padding: 5px;')
        mapping_layout.addWidget(help_label)
        
        # 字段映射表格
        self.mapping_table = QTableWidget()
        self.mapping_table.setColumnCount(3)
        self.mapping_table.setHorizontalHeaderLabels(['源文件字段', '目标数据库字段', '字段类型'])
        self.mapping_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.mapping_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        mapping_layout.addWidget(self.mapping_table)
        
        # 映射操作按钮
        map_buttons_layout = QHBoxLayout()
        
        select_all_btn = QPushButton('全选字段')
        select_all_btn.clicked.connect(self.select_all_fields)
        map_buttons_layout.addWidget(select_all_btn)
        
        clear_all_btn = QPushButton('清除映射')
        clear_all_btn.clicked.connect(self.clear_all_mappings)
        map_buttons_layout.addWidget(clear_all_btn)
        
        map_buttons_layout.addStretch()
        mapping_layout.addLayout(map_buttons_layout)
        
        mapping_group.setLayout(mapping_layout)
        layout.addWidget(mapping_group)
    
    def on_user_changed(self):
        """当用户选择改变时更新字段映射"""
        self.load_field_mapping()
    
    def on_config_changed(self):
        """当配置改变时更新字段映射"""
        self.load_field_mapping()
    
    def load_field_mapping(self):
        """加载字段映射 - 修改为自动对应模式，添加重置逻辑"""
        try:
            # 新增：重置字段映射状态，防止多文件导入时字段对应错乱
            if hasattr(self, 'previous_filename'):
                if self.previous_filename != self.filename:
                    # 文件切换时清除之前的映射状态
                    self.field_mapping_cache = {}
            self.previous_filename = self.filename
            
            # 读取源文件字段
            source_fields = self.get_source_fields()
            print(f"[DEBUG] 读取到的源文件字段: {source_fields}")   
            if not source_fields:
                print("[DEBUG] 无法读取源文件字段信息")
                notify(self, '无法读取源文件的字段信息', 'warn')
                return
            
            # 获取目标数据库字段
            target_user = self.user_combo.currentText()
            print(f"[DEBUG] 选择的目标用户: {target_user}")
            db_file = self.user_manager.get_user_db_file(target_user)
            print(f"[DEBUG] 目标用户数据库文件: {db_file}")
            if db_file:
                user_db = UserDatabase(db_file)
                self.columns_config = user_db.get_columns_config()
                print(f"[DEBUG] 目标数据库列配置: {self.columns_config}")
                target_fields = [col['name'] for col in self.columns_config]
                print(f"[DEBUG] 目标数据库字段: {target_fields}")
                user_db.close()
            else:
                self.columns_config = []
                target_fields = []
            
            # 设置表格
            self.mapping_table.setRowCount(len(source_fields))
            print(f"[DEBUG] 设置映射表格行数: {len(source_fields)}")
            
            # 自动对应逻辑：原文件第n个字段对应目标数据库第n个字段
            for i, field in enumerate(source_fields):
                print(f"[DEBUG] 处理源字段: {field}")
                # 源字段
                source_item = QTableWidgetItem(field)
                source_item.setFlags(source_item.flags() & ~Qt.ItemIsEditable)
                self.mapping_table.setItem(i, 0, source_item)
                
                # 目标字段选择
                combo = QComboBox()
                combo.addItem('（不导入）', '')  # 空选项
                for target_field in target_fields:
                    combo.addItem(target_field, target_field)
                
                # 自动对应：原文件第i个字段对应目标数据库第i个字段
                if i < len(target_fields):
                    print(f"[DEBUG] 自动对应字段: 源字段 '{field}' -> 目标字段 '{target_fields[i]}'")
                    # 直接按位置对应
                    auto_match = target_fields[i]
                    print(f"[DEBUG] 自动匹配结果: {auto_match}")
                    combo.setCurrentText(auto_match)
                else:
                    # 如果源字段多于目标字段，尝试名称匹配
                    auto_match = self.auto_match_field(field, target_fields)
                    if auto_match:
                        combo.setCurrentText(auto_match)
                
                self.mapping_table.setCellWidget(i, 1, combo)
                
                # 字段类型信息
                type_item = QTableWidgetItem()
                if auto_match:
                    # 找到对应的字段配置
                    for col_config in self.columns_config:
                        if col_config['name'] == auto_match:
                            field_type = col_config.get('type', 'TEXT')
                            required = " *" if col_config.get('is_required', False) else ""
                            unique = " 🔑" if col_config.get('is_unique', False) else ""
                            type_item.setText(f"{field_type}{required}{unique}")
                            break
                type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
                self.mapping_table.setItem(i, 2, type_item)
                print(f"[DEBUG] 设置映射表格第{i}行: 源字段='{field}', 目标字段='{combo.currentText()}', 类型='{type_item.text()}'")    
            
            self.mapping_table.resizeColumnsToContents()
            print("[DEBUG] 字段映射加载完成")
            
            # 更新统计信息
            self.update_stats()
            
        except Exception as e:
            print(f"[DEBUG] 加载字段映射失败: {str(e)}")
            notify(self, f'加载字段映射失败: {str(e)}', 'warn')
    
    def get_source_fields(self):
        """获取源文件字段"""
        try:
            file_type = self.parent().detect_file_type(self.filename)
            print(f"[DEBUG] 源文件类型: {file_type}")
            first_row_as_header = self.first_row_as_header_check.isChecked()
            print(f"[DEBUG] 第一行作为表头: {first_row_as_header}")
            skip_rows = self.skip_rows_spin.value()
            print(f"[DEBUG] 跳过行数: {skip_rows}")
            
            print(f"[DEBUG] 读取源文件字段: 文件类型={file_type}, 跳过行数={skip_rows}, 第一行作为表头={first_row_as_header}")
            if file_type == 'csv':
                # 读取CSV表头
                encodings = ['utf-8', 'gbk', 'gb2312', 'utf-8-sig']
                
                for encoding in encodings:
                    try:
                        with open(self.filename, 'r', encoding=encoding) as f:
                            # 跳过指定行数
                            for _ in range(skip_rows):
                                f.readline()
                            
                            if first_row_as_header:
                                # 读取表头行
                                header_line = f.readline().strip()
                                if not header_line:
                                    continue
                                    
                                # 检测分隔符
                                delimiter = self.parent().detect_csv_delimiter(header_line)
                                headers = header_line.split(delimiter)
                                return [h.strip() for h in headers if h.strip()]
                            else:
                                # 如果没有表头，读取第一行数据来推断列数
                                first_data_line = f.readline().strip()
                                if not first_data_line:
                                    continue
                                    
                                delimiter = self.parent().detect_csv_delimiter(first_data_line)
                                columns_count = len(first_data_line.split(delimiter))
                                # 修复：返回实际的列名而不是空数据
                                return [f'Column_{i+1}' for i in range(columns_count)]
                                
                    except UnicodeDecodeError:
                        continue
                    except Exception as e:
                        print(f"[DEBUG] {encoding} 编码读取失败: {str(e)}")
                        continue
                            
            elif file_type == 'json':
                # 读取JSON字段（不受表头设置影响）
                with open(self.filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    if isinstance(data, list) and data:
                        return list(data[0].keys())
                    elif isinstance(data, dict):
                        # 如果是字典结构，取第一个键的值作为样本
                        first_key = next(iter(data))
                        if isinstance(data[first_key], list) and data[first_key]:
                            return list(data[first_key][0].keys())
                        return list(data.keys())
                            
            elif file_type in ['xlsx', 'xls']:
                # 读取Excel表头
                try:
                    import openpyxl
                    workbook = openpyxl.load_workbook(self.filename, read_only=True)
                    
                    sheet_name = self.sheet_combo.currentText() if hasattr(self, 'sheet_combo') else None
                    if sheet_name and sheet_name in workbook.sheetnames:
                        sheet = workbook[sheet_name]
                    else:
                        sheet = workbook.active
                    
                    # 跳过指定行数
                    for _ in range(skip_rows):
                        next(sheet.iter_rows())
                    
                    if first_row_as_header:
                        # 读取表头行
                        header_row = next(sheet.iter_rows(values_only=True))
                        headers = [str(h) if h is not None else f"Column_{i+1}" 
                                for i, h in enumerate(header_row)]
                        
                        workbook.close()
                        return [h for h in headers if h]
                    else:
                        # 如果没有表头，读取第一行数据来推断列数
                        first_data_row = next(sheet.iter_rows(values_only=True))
                        # 修复：过滤掉None值，确保列数正确
                        valid_columns = [cell for cell in first_data_row if cell is not None]
                        columns_count = len(valid_columns)
                        workbook.close()
                        return [f'Column_{i+1}' for i in range(columns_count)]
                        
                except Exception as e:
                    print(f"[DEBUG] 读取Excel表头失败: {str(e)}")
                    return []
                    
        except Exception as e:
            print(f"[DEBUG] 获取源字段失败: {str(e)}")
        
        return []

    
    def auto_match_field(self, source_field, target_fields):
        """自动匹配字段"""
        if not target_fields:
            return None
            
        source_clean = source_field.lower().replace(' ', '_').replace('-', '_')
        print(f"[DEBUG] 尝试自动匹配源字段: {source_field} -> 清理后: {source_clean}")
        
        # 精确匹配
        for target_field in target_fields:
            print(f"[DEBUG] 检查精确匹配: 目标字段 {target_field}")
            if source_clean == target_field.lower():
                print(f"[DEBUG] 精确匹配成功: {source_field} -> {target_field}")
                return target_field
        
        # 包含匹配
        for target_field in target_fields:
            target_clean = target_field.lower()
            if (source_clean in target_clean or target_clean in source_clean):
                return target_field
        
        # 相似度匹配
        best_match = None
        best_score = 0.6  # 相似度阈值
        
        for target_field in target_fields:
            score = self.similarity(source_clean, target_field.lower())
            print(f"[DEBUG] 相似度匹配: 源字段 {source_clean} 与 目标字段 {target_field} 的相似度为 {score:.2f}")
            if score > best_score:
                best_score = score
                best_match = target_field
        
        return best_match
    
    def similarity(self, s1, s2):
        """计算字符串相似度"""
        from difflib import SequenceMatcher
        return SequenceMatcher(None, s1, s2).ratio()
    
    def auto_map_fields(self):
        """自动映射所有字段 - 修改为按位置对应"""
        for row in range(self.mapping_table.rowCount()):
            source_item = self.mapping_table.item(row, 0)
            if source_item:
                combo = self.mapping_table.cellWidget(row, 1)
                
                if combo:
                    # 获取目标字段列表
                    target_fields = []
                    for i in range(combo.count()):
                        target_field = combo.itemData(i)
                        if target_field:
                            target_fields.append(target_field)
                    
                    # 按位置自动对应
                    if row < len(target_fields):
                        auto_match = target_fields[row]
                        combo.setCurrentText(auto_match)
        
        # 更新字段类型信息
        self.update_field_types()
        self.update_stats()
    
    def update_field_types(self):
        """更新字段类型信息"""
        for row in range(self.mapping_table.rowCount()):
            combo = self.mapping_table.cellWidget(row, 1)
            if combo:
                target_field = combo.currentData()
                type_item = self.mapping_table.item(row, 2)
                
                if target_field and type_item:
                    # 找到对应的字段配置
                    for col_config in self.columns_config:
                        if col_config['name'] == target_field:
                            field_type = col_config.get('type', 'TEXT')
                            required = " *" if col_config.get('is_required', False) else ""
                            unique = " 🔑" if col_config.get('is_unique', False) else ""
                            type_item.setText(f"{field_type}{required}{unique}")
                            break
                    else:
                        type_item.setText("")
                elif type_item:
                    type_item.setText("")
    
    def update_stats(self):
        """更新统计信息"""
        total_fields = self.mapping_table.rowCount()
        mapped_fields = 0
        required_fields_mapped = 0
        total_required_fields = 0
        
        # 统计必填字段
        for col_config in self.columns_config:
            if col_config.get('is_required', False):
                total_required_fields += 1
        
        # 统计映射情况
        for row in range(self.mapping_table.rowCount()):
            combo = self.mapping_table.cellWidget(row, 1)
            if combo and combo.currentData():
                mapped_fields += 1
                
                # 检查是否是必填字段
                target_field = combo.currentData()
                for col_config in self.columns_config:
                    if col_config['name'] == target_field and col_config.get('is_required', False):
                        required_fields_mapped += 1
        
        stats_text = f"字段统计: 总计 {total_fields} 个字段, 已映射 {mapped_fields} 个"
        if total_required_fields > 0:
            stats_text += f", 必填字段 {required_fields_mapped}/{total_required_fields}"
        
        # 颜色提示
        if mapped_fields == 0:
            stats_text += " ⚠️ 请配置字段映射"
        elif total_required_fields > 0 and required_fields_mapped < total_required_fields:
            stats_text += " ⚠️ 必填字段未完全映射"
        else:
            stats_text += " ✅ 就绪"
        
        self.stats_label.setText(stats_text)
    
    def select_all_fields(self):
        """选择所有字段进行映射"""
        for row in range(self.mapping_table.rowCount()):
            combo = self.mapping_table.cellWidget(row, 1)
            if combo and combo.count() > 1:
                # 选择第一个非空选项
                combo.setCurrentIndex(1)
        
        self.update_field_types()
        self.update_stats()
    
    def clear_all_mappings(self):
        """清除所有字段映射"""
        for row in range(self.mapping_table.rowCount()):
            combo = self.mapping_table.cellWidget(row, 1)
            if combo:
                combo.setCurrentIndex(0)
        
        self.update_field_types()
        self.update_stats()
    
    def preview_data(self):
        """预览数据"""
        try:
            import_config = self.get_import_config()
            data = self.parent().read_import_file(self.filename, import_config)
            
            if not data:
                notify(self, '没有找到可预览的数据', 'info')
                return
            
            preview_count = min(self.preview_rows_spin.value(), len(data))
            preview_data = data[:preview_count]
            
            # 显示预览对话框
            dialog = QDialog(self)
            dialog.setWindowTitle(f'数据预览 (前{preview_count}行)')
            dialog.resize(900, 600)
            
            layout = QVBoxLayout()
            dialog.setLayout(layout)
            
            # 预览信息
            info_label = QLabel(f"总共 {len(data)} 行数据，显示前 {preview_count} 行")
            info_label.setStyleSheet("color: #666; padding: 5px; font-weight: bold;")
            layout.addWidget(info_label)
            
            # 数据质量检查
            quality_info = self.check_data_quality(data)
            if quality_info:
                quality_label = QLabel(quality_info)
                quality_label.setStyleSheet("color: #FF6B35; padding: 5px; background-color: #FFF3E0; border: 1px solid #FFB74D; border-radius: 3px;")
                quality_label.setWordWrap(True)
                layout.addWidget(quality_label)
            
            table = QTableWidget()
            if preview_data:
                headers = list(preview_data[0].keys())
                table.setColumnCount(len(headers))
                table.setHorizontalHeaderLabels(headers)
                table.setRowCount(len(preview_data))
                
                # 设置表格样式
                table.setAlternatingRowColors(True)
                table.setStyleSheet("""
                    QTableWidget {
                        gridline-color: #E0E0E0;
                        font-size: 11px;
                    }
                    QHeaderView::section {
                        background-color: #F5F5F5;
                        padding: 5px;
                        border: 1px solid #E0E0E0;
                        font-weight: bold;
                    }
                """)
                
                for row, record in enumerate(preview_data):
                    for col, header in enumerate(headers):
                        value = record.get(header, '')
                        item = QTableWidgetItem(str(value) if value is not None else "")
                        
                        # 高亮空值
                        if not value:
                            item.setBackground(QColor('#FFF9C4'))  # 浅黄色背景
                        
                        # 设置文本对齐
                        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                        table.setItem(row, col, item)
                
                table.resizeColumnsToContents()
                table.setSortingEnabled(True)
            
            layout.addWidget(table)
            
            # 操作按钮
            button_layout = QHBoxLayout()
            
            # 导出预览按钮
            export_btn = QPushButton('导出预览数据')
            export_btn.clicked.connect(lambda: self.export_preview_data(preview_data))
            button_layout.addWidget(export_btn)
            
            button_layout.addStretch()
            
            close_btn = QPushButton('关闭')
            close_btn.clicked.connect(dialog.accept)
            button_layout.addWidget(close_btn)
            
            layout.addLayout(button_layout)
            
            dialog.exec()
            
        except Exception as e:
            notify(self, f'数据预览失败: {str(e)}', 'error')

    def check_data_quality(self, data):
        """检查数据质量"""
        if not data:
            return None
        
        issues = []
        
        # 检查空值
        empty_count = 0
        total_cells = 0
        
        for record in data:
            for key, value in record.items():
                total_cells += 1
                if value is None or str(value).strip() == '':
                    empty_count += 1
        
        if empty_count > 0:
            empty_percent = (empty_count / total_cells) * 100
            issues.append(f"空值率: {empty_percent:.1f}% ({empty_count}/{total_cells})")
        
        # 检查数据一致性
        if len(data) > 1:
            first_keys = set(data[0].keys())
            for i, record in enumerate(data[1:], 1):
                current_keys = set(record.keys())
                if first_keys != current_keys:
                    issues.append(f"第{i+1}行列结构与第一行不一致")
                    break
        
        # 检查数据类型
        numeric_fields = 0
        text_fields = 0
        
        if data:
            sample_record = data[0]
            for key, value in sample_record.items():
                if value is not None:
                    if isinstance(value, (int, float)):
                        numeric_fields += 1
                    else:
                        text_fields += 1
        
        if numeric_fields > 0:
            issues.append(f"数值字段: {numeric_fields}个")
        
        if issues:
            return "数据质量检查: " + " | ".join(issues)
        
        return None

    def export_preview_data(self, data):
        """导出预览数据"""
        if not data:
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, '导出预览数据', 'preview_data.csv', 
            '所有文件 (*.*);;CSV文件 (*.csv);;JSON文件 (*.json)'
        )
        
        if not filename:
            return
        
        try:
            if filename.endswith('.csv'):
                with open(filename, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.DictWriter(f, fieldnames=data[0].keys())
                    writer.writeheader()
                    writer.writerows(data)
            elif filename.endswith('.json'):
                with open(filename, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
            
            notify(self, f'预览数据已导出到: {filename}', 'info')
            
        except Exception as e:
            notify(self, f'导出失败: {str(e)}', 'error')

    def validate_and_accept(self):
        """验证配置并接受"""
        # 检查必填字段映射
        missing_required = self.check_required_fields()
        
        if missing_required:
            msg = "以下必填字段未映射:\n\n" + "\n".join(missing_required)
            msg += "\n\n是否继续导入？未映射的必填字段将导致数据插入失败。"
            
            reply = QMessageBox.question(
                self, '必填字段警告', msg,
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.No:
                return
        
        # 检查是否有任何字段被映射
        if not self.has_any_mapping():
            notify(self, '请至少映射一个字段', 'warn')
            return
        
        # 确认导入
        estimated_count = self.estimate_data_count()
        if estimated_count > 1000:
            reply = QMessageBox.question(
                self, '确认导入', 
                f'预计将导入约 {estimated_count} 条记录，这可能需要一些时间。\n\n确定要开始导入吗？',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.No:
                return
        
        super().accept()

    def check_required_fields(self):
        """检查必填字段是否已映射"""
        missing_required = []
        
        for col_config in self.columns_config:
            if col_config.get('is_required', False):
                field_name = col_config['name']
                is_mapped = False
                
                # 检查该字段是否被映射
                for row in range(self.mapping_table.rowCount()):
                    combo = self.mapping_table.cellWidget(row, 1)
                    if combo and combo.currentData() == field_name:
                        is_mapped = True
                        break
                
                if not is_mapped:
                    missing_required.append(f"{field_name} ({col_config.get('label', field_name)})")
        
        return missing_required

    def has_any_mapping(self):
        """检查是否有任何字段被映射"""
        for row in range(self.mapping_table.rowCount()):
            combo = self.mapping_table.cellWidget(row, 1)
            if combo and combo.currentData():
                return True
        return False

    def estimate_data_count(self):
        """估算数据记录数量"""
        try:
            import_config = self.get_import_config()
            data = self.parent().read_import_file(self.filename, import_config)
            return len(data) if data else 0
        except:
            return 0

    def get_import_config(self):
        """获取导入配置"""
        config = {
            'filename': self.filename,
            'target_user': self.user_combo.currentText(),
            'skip_rows': self.skip_rows_spin.value(),
            'first_row_as_header': self.first_row_as_header_check.isChecked(),
            'field_mapping': self.get_field_mapping(),
            'conflict_resolution': self.get_conflict_resolution(),
            'max_rows': 100000,  # 默认最大行数限制
        }
        
        print(f"[DEBUG] 导入配置: {config}")

        # Excel特定配置
        if self.filename.lower().endswith(('.xlsx', '.xls')) and hasattr(self, 'sheet_combo'):
            print(f"[DEBUG] 选择的工作表: {self.sheet_combo.currentText()}")
            config['sheet_name'] = self.sheet_combo.currentText()
            print(f"[DEBUG] Excel工作表配置: {config['sheet_name']}")   
        
        return config


    def get_field_mapping(self):
        """获取字段映射配置"""
        mapping = {}
        print("[DEBUG] 获取字段映射配置:")
        
        for row in range(self.mapping_table.rowCount()):
            source_item = self.mapping_table.item(row, 0)
            combo = self.mapping_table.cellWidget(row, 1)
            
            if source_item and combo:
                source_field = source_item.text()
                target_field = combo.currentData()
                
                if target_field:  # 只添加有映射的字段
                    mapping[source_field] = target_field
                    print(f"[DEBUG] 字段映射: {source_field} -> {target_field}")
        
        print(f"[DEBUG] 最终字段映射: {mapping}")
        return mapping

    def get_conflict_resolution(self):
        """获取冲突解决策略"""
        resolution_text = self.conflict_combo.currentText()
        
        if resolution_text == '跳过重复记录':
            print(f"[DEBUG] 冲突解决策略: skip")
            return 'skip'
        elif resolution_text == '更新重复记录':
            print(f"[DEBUG] 冲突解决策略: update")
            return 'update'
        else:  # '允许重复记录'
            print(f"[DEBUG] 冲突解决策略: allow")
            return 'allow'

    def get_import_config_for_execution(self):
        """获取用于执行的导入配置（包含完整信息）"""
        config = self.get_import_config()
        print(f"[DEBUG] 获取执行导入配置: {config}")
        
        # 添加字段类型信息
        config['field_types'] = {}
        for col_config in self.columns_config:
            config['field_types'][col_config['name']] = {
                'type': col_config.get('type', 'TEXT'),
                'is_required': col_config.get('is_required', False),
                'is_unique': col_config.get('is_unique', False)
            }
        
        return config


class UltraFastImportDialog(FastImportDialog):
    """超快速导入对话框"""
    
    def __init__(self, filename, parent=None, unattended_mode=False):
        super().__init__(filename, parent)
        self.unattended_mode = unattended_mode
        self.setWindowTitle('超快速导入配置')
        self.unified_config = {}  # 存储统一配置
        
        # 新增：初始化字段映射缓存
        self.field_mapping_cache = {}
        self.previous_filename = None
        
        # 新增：初始化 columns_config 属性
        self.columns_config = []

        # 新增：自动加载和应用统一配置
        self.auto_load_unified_config()

        # 新增：如果是无人值守模式，自动接受配置
        if self.unattended_mode:
            QTimer.singleShot(100, self.auto_accept_for_unattended)

    def auto_accept_for_unattended(self):
        """无人值守模式下的自动接受逻辑"""
        print("[DEBUG] 进入无人值守模式自动接受逻辑")
        try:
            # 检查配置是否有效
            if self.validate_unattended_config():
                print(f"[DEBUG] 无人值守模式：自动接受文件 {os.path.basename(self.filename)} 的导入配置")
                super().accept()
            else:
                print(f"[DEBUG] 无人值守模式：配置验证失败，跳过文件 {os.path.basename(self.filename)}")
                super().reject()
        except Exception as e:
            print(f"[DEBUG] 无人值守模式处理失败: {str(e)}")
            super().reject()
    
    def validate_unattended_config(self):
        """验证无人值守模式下的配置是否有效"""
        print("[DEBUG] 验证无人值守模式配置")   
        try:
            # 检查是否有字段映射
            if not self.has_any_mapping():
                print("[DEBUG] 无人值守模式：没有字段映射，跳过")
                return False
            
            # 检查必填字段（可选，根据需求调整严格程度）
            missing_required = self.check_required_fields()
            print(f"[DEBUG] 无人值守模式：必填字段检查结果: {missing_required}")

            if missing_required:
                print(f"[DEBUG] 无人值守模式：必填字段未映射: {missing_required}")
                # 这里可以选择是否严格检查必填字段
                # return False  # 严格模式：有必填字段未映射就跳过
                # 或者继续执行（宽松模式）
            
            return True
        except Exception as e:
            print(f"[DEBUG] 无人值守配置验证失败: {str(e)}")
            return False

    def init_ui(self):
        super().init_ui()
        
        # 在配置组中添加性能选项
        config_layout = self.findChild(QFormLayout)
        if config_layout:
            # 批量大小设置
            self.batch_size_spin = QSpinBox()
            self.batch_size_spin.setRange(100, 10000)
            self.batch_size_spin.setValue(1000)
            self.batch_size_spin.setToolTip("较大的批量大小可以提高导入速度，但会占用更多内存")
            config_layout.addRow('批量大小:', self.batch_size_spin)
        
            # 最大行数限制（针对大文件）
            self.max_rows_spin = QSpinBox()
            self.max_rows_spin.setRange(1000, 1000000)
            self.max_rows_spin.setValue(100000)
            self.max_rows_spin.setToolTip("限制大文件的最大读取行数，避免内存溢出")
            config_layout.addRow('最大行数:', self.max_rows_spin)
        
            # 性能模式
            self.performance_combo = QComboBox()
            self.performance_combo.addItems(['平衡模式', '速度优先', '内存优化'])
            config_layout.addRow('性能模式:', self.performance_combo)
            
            # 预处理选项
            self.preprocess_check = QCheckBox('启用数据预处理')
            self.preprocess_check.setChecked(True)
            self.preprocess_check.setToolTip("预处理可以提前验证数据格式，避免导入中途失败")
            config_layout.addRow(self.preprocess_check)
            
            # 新增：统一配置选项
            self.unified_config_check = QCheckBox('应用统一配置到所有文件')
            self.unified_config_check.setChecked(False)
            self.unified_config_check.setToolTip("启用后，当前配置将应用于批量导入的所有文件")
            config_layout.addRow(self.unified_config_check)
            
            # 新增：无人值守模式选项
            self.unattended_mode_check = QCheckBox('无人值守模式')
            self.unattended_mode_check.setChecked(True)  # 默认开启
            self.unattended_mode_check.setToolTip("启用后，批量导入时将自动处理所有文件，无需人工干预")
            config_layout.addRow('无人值守模式:', self.unattended_mode_check)
            
            # 保存统一配置按钮
            self.save_config_btn = QPushButton('💾 保存当前配置')
            self.save_config_btn.clicked.connect(self.save_unified_config)
            config_layout.addRow('统一配置:', self.save_config_btn)
            
            # 加载统一配置按钮
            self.load_config_btn = QPushButton('📂 加载配置')
            self.load_config_btn.clicked.connect(self.load_unified_config)
            config_layout.addRow('', self.load_config_btn)

    def save_unified_config(self):
        """保存当前配置作为统一配置"""
        try:
            self.unified_config = {
                'target_user': self.user_combo.currentText(),
                'skip_rows': self.skip_rows_spin.value(),
                'first_row_as_header': self.first_row_as_header_check.isChecked(),
                'conflict_resolution': self.get_conflict_resolution(),
                'batch_size': self.batch_size_spin.value(),
                'performance_mode': self.performance_combo.currentText(),
                'preprocess': self.preprocess_check.isChecked(),
                'field_mapping': self.get_field_mapping(),  # 保存字段映射
                'saved_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            print(f"[DEBUG] 保存的统一配置: {self.unified_config}")

            # 新增：保存到父窗口，使其全局可用
            if hasattr(self.parent(), 'global_unified_config'):
                self.parent().global_unified_config = self.unified_config
                print("[DEBUG] 统一配置已保存到父窗口的 global_unified_config 属性")
                # 持久化到全局设置文件（实时保存，保证下次启动沿用）
                try:
                    data = load_global_settings()
                    data['global_unified_config'] = self.unified_config
                    save_global_settings(data)
                except Exception:
                    pass
            
            
            notify(self, '统一配置已保存！\n配置时间: ' + self.unified_config['saved_time'], 'info')
            print(f"[DEBUG] 统一配置已保存: {self.unified_config}")
            
        except Exception as e:
            print(f"[DEBUG] 保存统一配置失败: {str(e)}")
            notify(self, f'保存配置失败: {str(e)}', 'warn')

            
    def load_unified_config(self):
        """加载统一配置到当前对话框"""
        print("[DEBUG] 加载统一配置")
        if not self.unified_config:
            print("[DEBUG] 无统一配置可加载")
            notify(self, '暂无保存的统一配置', 'info')
            return
        
        try:
            # 恢复配置到界面
            print(f"[DEBUG] 加载的统一配置内容: {self.unified_config}")
            if 'target_user' in self.unified_config:
                index = self.user_combo.findText(self.unified_config['target_user'])
                if index >= 0:
                    self.user_combo.setCurrentIndex(index)
            
            if 'skip_rows' in self.unified_config:
                self.skip_rows_spin.setValue(self.unified_config['skip_rows'])
            
            if 'first_row_as_header' in self.unified_config:
                self.first_row_as_header_check.setChecked(self.unified_config['first_row_as_header'])
            
            if 'conflict_resolution' in self.unified_config:
                resolution_map = {'skip': 0, 'update': 1, 'allow': 2}
                if self.unified_config['conflict_resolution'] in resolution_map:
                    self.conflict_combo.setCurrentIndex(resolution_map[self.unified_config['conflict_resolution']])
            
            if 'batch_size' in self.unified_config:
                self.batch_size_spin.setValue(self.unified_config['batch_size'])
            
            if 'performance_mode' in self.unified_config:
                index = self.performance_combo.findText(self.unified_config['performance_mode'])
                if index >= 0:
                    self.performance_combo.setCurrentIndex(index)
            
            if 'preprocess' in self.unified_config:
                self.preprocess_check.setChecked(self.unified_config['preprocess'])
            
            # 恢复字段映射
            if 'field_mapping' in self.unified_config:
                self.apply_field_mapping(self.unified_config['field_mapping'])
            
            notify(self, f'统一配置已加载！\n配置时间: {self.unified_config.get("saved_time", "未知")}', 'info')
            
        except Exception as e:
            notify(self, f'加载配置失败: {str(e)}', 'warn')

    def apply_field_mapping(self, field_mapping):
        """应用字段映射配置"""
        try:
            for row in range(self.mapping_table.rowCount()):
                source_item = self.mapping_table.item(row, 0)
                if source_item:
                    source_field = source_item.text()
                    combo = self.mapping_table.cellWidget(row, 1)
                    
                    if combo and source_field in field_mapping:
                        target_field = field_mapping[source_field]
                        # 在组合框中查找对应的目标字段
                        for i in range(combo.count()):
                            if combo.itemData(i) == target_field:
                                combo.setCurrentIndex(i)
                                break
            
            self.update_field_types()
            self.update_stats()
            
        except Exception as e:
            print(f"[DEBUG] 应用字段映射失败: {str(e)}")

    def get_import_config(self):
        config = super().get_import_config()
        config.update({
            'batch_size': self.batch_size_spin.value(),
            'max_rows': self.max_rows_spin.value(),  # 新增最大行数配置
            'performance_mode': self.performance_combo.currentText(),
            'preprocess': self.preprocess_check.isChecked(),
            'use_unified_config': self.unified_config_check.isChecked(),
            'unified_config': self.unified_config if self.unified_config_check.isChecked() else {}
        })
        return config


    def is_ansi_file(self, header, filename):
        """检测是否为 ANSI 编码文件"""
        try:
            # ANSI 通常是系统默认编码，在中文Windows上是gbk
            # 检查文件扩展名
            ext = os.path.splitext(filename)[1].lower()
            common_ansi_extensions = ['.txt', '.csv', '.log', '.ini', '.inf']
            
            if ext in common_ansi_extensions:
                return True
            
            # 检查文件内容特征
            # ANSI 文件通常不包含 BOM 头
            if header.startswith(b'\xFF\xFE') or header.startswith(b'\xFE\xFF') or header.startswith(b'\xEF\xBB\xBF'):
                return False  # 有 BOM 头，不是纯 ANSI
            
            # 尝试用常见 ANSI 编码解码
            ansi_encodings = ['gbk', 'cp936', 'windows-1252', 'latin-1']
            for encoding in ansi_encodings:
                try:
                    header.decode(encoding)
                    return True
                except UnicodeDecodeError:
                    continue
            
            # 检查是否主要是 ASCII 字符（ANSI 兼容 ASCII）
            ascii_count = sum(1 for byte in header if byte <= 127)
            if ascii_count / len(header) > 0.9:  # 90% 以上是 ASCII
                return True
                
        except Exception:
            pass
        
        return False

    def auto_load_unified_config(self):
        """自动加载和应用统一配置"""
        try:
            # 检查是否有保存的统一配置
            if hasattr(self.parent(), 'global_unified_config') and self.parent().global_unified_config:
                self.unified_config = self.parent().global_unified_config
                self.apply_unified_config_automatically()
                print(f"[DEBUG] 自动应用统一配置到文件: {os.path.basename(self.filename)}")
        except Exception as e:
            print(f"[DEBUG] 自动加载统一配置失败: {str(e)}")

    def apply_unified_config_automatically(self):
        """自动应用统一配置到当前对话框"""
        if not self.unified_config:
            return
            
        try:
            # 自动勾选统一配置选项
            self.unified_config_check.setChecked(True)
            
            # 恢复配置到界面
            if 'target_user' in self.unified_config:
                index = self.user_combo.findText(self.unified_config['target_user'])
                if index >= 0:
                    self.user_combo.setCurrentIndex(index)
            
            if 'skip_rows' in self.unified_config:
                self.skip_rows_spin.setValue(self.unified_config['skip_rows'])
            
            if 'first_row_as_header' in self.unified_config:
                self.first_row_as_header_check.setChecked(self.unified_config['first_row_as_header'])
            
            if 'conflict_resolution' in self.unified_config:
                resolution_map = {'skip': 0, 'update': 1, 'allow': 2}
                if self.unified_config['conflict_resolution'] in resolution_map:
                    self.conflict_combo.setCurrentIndex(resolution_map[self.unified_config['conflict_resolution']])
            
            if 'batch_size' in self.unified_config:
                self.batch_size_spin.setValue(self.unified_config['batch_size'])
            
            if 'performance_mode' in self.unified_config:
                index = self.performance_combo.findText(self.unified_config['performance_mode'])
                if index >= 0:
                    self.performance_combo.setCurrentIndex(index)
            
            if 'preprocess' in self.unified_config:
                self.preprocess_check.setChecked(self.unified_config['preprocess'])
            
            # 恢复字段映射
            if 'field_mapping' in self.unified_config:
                # 延迟应用字段映射，等待字段加载完成
                QTimer.singleShot(100, lambda: self.apply_field_mapping(self.unified_config['field_mapping']))
            
            print(f"[DEBUG] 统一配置已自动应用到当前文件")
            
        except Exception as e:
            print(f"[DEBUG] 自动应用统一配置失败: {str(e)}")

    def clear_dialog_state(self):
        """清除对话框状态"""
        try:
            # 清除字段映射缓存
            if hasattr(self, 'field_mapping_cache'):
                self.field_mapping_cache = {}
            
            # 清除之前的文件名记录
            if hasattr(self, 'previous_filename'):
                self.previous_filename = None
                
            # 清除表格数据
            if hasattr(self, 'mapping_table'):
                self.mapping_table.clearContents()
                self.mapping_table.setRowCount(0)
                
            print("[DEBUG] 对话框状态已清除")
        except Exception as e:
            print(f"[DEBUG] 清除对话框状态失败: {str(e)}")


class LoginWindow(QMainWindow):
    def __init__(self, user_manager):
        super().__init__()
        self.user_manager = user_manager
        # ===== 全局设置（实时自动保存 + 持久化）=====
        self._global_settings = load_global_settings()
        self.global_unified_config = self._global_settings.get('global_unified_config', None)
        self._start_maximized = bool(self._global_settings.get('login_maximized', False))
        self._settings_save_timer = QTimer(self)
        self._settings_save_timer.setSingleShot(True)
        self._settings_save_timer.setInterval(500)  # 500ms 防抖
        self._settings_save_timer.timeout.connect(self._save_settings_now)
    
        # 确保chardet可用
        try:
            import chardet
            self.chardet = chardet
        except ImportError:
            print("[DEBUG] chardet不可用，将使用基础编码检测")
            self.chardet = None
        
        self.init_ui()
        self._restore_window_state()
        # ★ v6.12：等窗口真显示出来之后，按【真实边框】再核一次位置（多屏/DPI 变化时
        #   窗口管理器可能自行改落点）。**故意不加 force_center** —— 位置在构造时已经
        #   居中好了，这里只负责“够不着就把窗口救回屏幕中间”；如果强制居中，用户刚把
        #   窗口拖走就会被这个延迟回调拽回来（实测过）。
        QTimer.singleShot(0, lambda: ensure_window_on_screen(self))
        
        icon_path = resource_path('icon.ico')
        if os.path.exists('icon.ico'):
            self.setWindowIcon(QIcon('icon.ico'))
    
    def init_ui(self):
        self.setWindowTitle('用户登录')
        self.resize(600, 400)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 标题
        title_label = QLabel('数据管理系统')
        title_label.setAlignment(Qt.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(18)
        title_font.setBold(True)
        title_label.setFont(title_font)
        main_layout.addWidget(title_label)
        
        # 用户列表区域
        user_group = QGroupBox('用户列表')
        user_layout = QVBoxLayout()
        
        self.user_list = QListWidget()
        self.user_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.user_list.itemDoubleClicked.connect(self.login)
        user_layout.addWidget(self.user_list)
        
        # 添加用户信息显示
        self.user_info_label = QLabel()
        self.user_info_label.setWordWrap(True)
        user_layout.addWidget(self.user_info_label)
        
        user_group.setLayout(user_layout)
        main_layout.addWidget(user_group)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.login_btn = QPushButton('👨‍💼登录')
        self.login_btn.clicked.connect(self.login)
        button_layout.addWidget(self.login_btn)
        
        self.add_user_btn = QPushButton('🧑🧡添加用户')
        self.add_user_btn.clicked.connect(self.add_user)
        button_layout.addWidget(self.add_user_btn)
        
        self.delete_user_btn = QPushButton('🧓💥删除用户')
        self.delete_user_btn.clicked.connect(self.delete_user)
        button_layout.addWidget(self.delete_user_btn)

        self.edit_user_btn = QPushButton('👻修改用户名')
        self.edit_user_btn.clicked.connect(self.edit_username)
        button_layout.addWidget(self.edit_user_btn)

        self.modify_config_btn = QPushButton('🛠️修改列配置')
        self.modify_config_btn.clicked.connect(self.modify_column_config)
        button_layout.addWidget(self.modify_config_btn)

        # 添加超快速导入按钮
        self.ultra_fast_import_btn = QPushButton('⚡📥超快速导入')
        self.ultra_fast_import_btn.clicked.connect(self.ultra_fast_import_data)
        button_layout.addWidget(self.ultra_fast_import_btn)


        self.import_rules_btn = QPushButton('📤🀄导入麻将规则')
        self.import_rules_btn.clicked.connect(self.import_rules_json)
        button_layout.addWidget(self.import_rules_btn)

        self.import_btn = QPushButton('📤🧑导入用户')
        self.import_btn.clicked.connect(self.import_users)
        button_layout.addWidget(self.import_btn)

        main_layout.addLayout(button_layout)
        
        # 加载用户列表
        self.load_users()
        
        # 连接选择变化信号
        self.user_list.itemSelectionChanged.connect(self.show_user_info)

    def edit_username(self):
        """修改用户名"""
        selected_items = self.user_list.selectedItems()
        if not selected_items:
            notify(self, '请选择一个用户', 'warn')
            return
        
        old_username = selected_items[0].text()
        new_username, ok = QInputDialog.getText(
            self, '修改用户名', 
            '输入新用户名:', 
            text=old_username
        )
        
        if not ok or not new_username:
            return
            
        if new_username == old_username:
            notify(self, '新用户名不能与原用户名相同', 'warn')
            return
            
        # 调用UserManager更新用户名
        success, message = self.user_manager.update_username(old_username, new_username)
        if success:
            notify(self, message, 'info')
            self.load_users()  # 刷新用户列表
        else:
            notify(self, message, 'warn')

    def modify_column_config(self):
        """修改用户的列配置"""
        selected_items = self.user_list.selectedItems()
        if not selected_items:
            notify(self, '请选择一个用户', 'warn')
            return
        
        username = selected_items[0].text()
        
        # 获取当前用户的配置
        settings = self.user_manager.get_user_settings(username)
        current_config = []
        if settings:
            try:
                current_config = json.loads(settings)
            except Exception:
                current_config = []
        if not current_config:
            # ★ v6.7：settings 拿不到就按数据库真实表结构预填。
            #   以前会直接弹一个空白对话框 —— 用户一填就相当于换了整张表（数据全清）。
            db_file0 = self.user_manager.get_user_db_file(username)
            if db_file0 and os.path.exists(db_file0):
                _db = None
                try:
                    _db = UserDatabase(db_file0, private=True)
                    current_config = _db.get_columns_config() or []
                except Exception as e:
                    print(f"[DEBUG] 读取真实列配置失败: {str(e)}")
                    current_config = []
                finally:
                    if _db is not None:
                        try:
                            _db.close()
                        except Exception:
                            pass
        if not current_config:
            notify(self, '无法获取用户配置', 'warn')
            return

        # ★ v6.4：不再重建数据库，改为迁移表结构（尽量保留已有数据）
        # ★ v6.7：确认框移到对话框之后 —— 先让用户改完，再拿“真实的新配置 vs 现表”
        #   的影响预览去确认，免得用户在没看清影响的情况下就换掉整表。
        # 显示列配置对话框，预填充当前配置
        col_dialog = ColumnConfigDialog()
        col_dialog.load_existing_config(current_config)
        
        if col_dialog.exec() == QDialog.Accepted:
            new_columns_config = col_dialog.get_columns_config()
            if not new_columns_config:
                notify(self, '必须至少配置一列', 'warn')
                return

            dup = duplicate_column_names(new_columns_config)
            if dup:
                notify(self, f'列名重复：{"、".join(dup)}\n请先改成不同的列名', 'warn')
                return
            
            db_file = self.user_manager.get_user_db_file(username)
            if not db_file:
                notify(self, '无法获取数据库文件', 'warn')
                return

            preview = column_config_change_preview(db_file, new_columns_config)
            reply = QMessageBox.question(
                self, '确认列配置变更',
                f'即将修改用户 "{username}" 的列配置：\n\n'
                '  • 新增列：已有记录该列为空\n'
                '  • 删除列：只丢该列的数据\n'
                '  • 列名改了但「中文名」没改：自动跟着改名，数据保留\n'
                + preview +
                '\n\n改前会自动备份数据库。确定要继续吗？',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if reply != QMessageBox.Yes:
                return
            
            # ★ v6.5：与主窗口共用同一套「备份 + 迁移表结构 + 保存配置」流程
            try:
                ok, msg = apply_column_config_migration(db_file, username,
                                                        self.user_manager, new_columns_config)
            except Exception as e:
                notify(self, f'修改列配置失败: {str(e)}', 'error')
                return
            if ok:
                notify(self, f'列配置修改成功\n\n{msg}\n（已自动备份数据库）', 'info')
            else:
                notify(self, f'列配置修改失败: {msg}', 'warn')


    # ===== 全局设置实时自动保存 =====
    def _restore_window_state(self):
        """恢复窗口几何/最大化（★ v6.11：登录窗口每次打开都居中显示）

        用户报过：「选择用户登录的窗口改成默认在屏幕中间，现在都走到屏幕外，先移动都
        移动不了」。他真实的存档是 `"login_geometry": [903, -64, 823, 400]` —— y 为
        负，标题栏在屏幕上边之外，抓不住也拖不动。

        所以登录窗口**不再沿用上次的位置**：每次打开都把位置放屏幕中间（只沿用上次的
        窗口**尺寸**，用户拉大过就保持拉大后的尺寸）；万一屏幕信息拿不到，就用
        `center_window_on_screen()` 兜底。

        ★ v6.12：摆放改用 `place_window()`（resize + move）—— 以前用的 `setGeometry()`
        是客户区坐标，比本函数算出的边框坐标低一个标题栏高度，结果每次启动都把窗口
        往上顶 31px，几轮之后标题栏又出屏（所以 v6.11 看起来“修了又犯”）。
        """
        try:
            geo = self._global_settings.get('login_geometry')
            safe = safe_window_geometry(geo, (600, 400), always_center=True)
            if safe and place_window(self, safe):
                print(f"[DEBUG] 登录窗口居中使用几何 {list(safe)}（上次 {geo}）")
            else:
                center_window_on_screen(self)
        except Exception as e:
            print(f"[DEBUG] 恢复登录窗口位置失败，改为居中: {str(e)}")
            center_window_on_screen(self)

    def _autosave_settings(self):
        """实时自动保存（500ms 防抖）"""
        if hasattr(self, '_settings_save_timer') and self._settings_save_timer is not None:
            self._settings_save_timer.start()

    def _save_settings_now(self):
        """立即保存全局设置（原子写入）"""
        try:
            data = load_global_settings()
            # ★ v6.12：最大化时不存 -8/-31 那种残影坐标（存下去下次启动标题栏就出屏了），
            #   并且一律过一遍 safe_window_geometry()，保证存的是“看得见”的位置
            geo = window_geometry_for_save(self, (600, 400))
            if geo:
                data['login_geometry'] = geo
            data['login_maximized'] = self.isMaximized()
            if self._global_settings.get('last_user'):
                data['last_user'] = self._global_settings['last_user']
            if self.global_unified_config is not None:
                data['global_unified_config'] = self.global_unified_config
            save_global_settings(data)
        except Exception as e:
            print(f"[DEBUG] 保存全局设置失败: {e}")

    def showEvent(self, event):
        """每次显示都刷新用户列表（★ v6.15：从「切换用户登录」回来时能立刻看到最新用户）"""
        super().showEvent(event)
        try:
            self.load_users()
        except Exception as e:
            print(f"[DEBUG] 刷新用户列表失败: {str(e)}")

    def closeEvent(self, event):
        """关闭时强制保存设置"""
        if hasattr(self, '_settings_save_timer') and self._settings_save_timer is not None:
            self._settings_save_timer.stop()
        self._save_settings_now()
        super().closeEvent(event)

    def load_users(self):
        self.user_list.clear()
        users = self.user_manager.get_users()
        for user in users:
            self.user_list.addItem(user[0])
        # 恢复上次登录用户选中
        try:
            last_user = self._global_settings.get('last_user')
            if last_user:
                items = self.user_list.findItems(last_user, Qt.MatchExactly)
                if items:
                    self.user_list.setCurrentItem(items[0])
                    self.show_user_info()
        except Exception:
            pass
    
    def show_user_info(self):
        selected_items = self.user_list.selectedItems()
        if not selected_items:
            self.user_info_label.setText("")
            return
        
        username = selected_items[0].text()
        users = self.user_manager.get_users()
        user_info = next((u for u in users if u[0] == username), None)
        
        if user_info:
            created_at = datetime.fromisoformat(user_info[1]).strftime('%Y-%m-%d %H:%M:%S')
            last_login = user_info[2] if user_info[2] else "从未登录"
            if last_login != "从未登录":
                last_login = datetime.fromisoformat(last_login).strftime('%Y-%m-%d %H:%M:%S')
            
            info_text = f"用户名: {username}\n创建时间: {created_at}\n最后登录: {last_login}"
            self.user_info_label.setText(info_text)
    
    def login(self):
        selected_items = self.user_list.selectedItems()
        if not selected_items:
            notify(self, '请选择一个用户', 'warn')
            return
        
        username = selected_items[0].text()
        db_file = self.user_manager.get_user_db_file(username)
        settings = self.user_manager.get_user_settings(username)
        
        # 更新最后登录时间
        self.user_manager.update_last_login(username)
        
        # 记录上次登录用户并实时保存
        self._global_settings['last_user'] = username
        self._autosave_settings()
        
        self.main_window = MainWindow(username, db_file, settings, self.user_manager, self)
        if getattr(self.main_window, '_start_maximized', False):
            self.main_window.showMaximized()
        else:
            self.main_window.show()
        self.hide()
    
    def add_user(self):
        username, ok = QInputDialog.getText(self, '添加用户', '输入用户名:')
        if not ok or not username:
            return
        
        if username in [self.user_list.item(i).text() for i in range(self.user_list.count())]:
            notify(self, '用户名已存在', 'warn')
            return
        
        col_dialog = ColumnConfigDialog()
        if col_dialog.exec() == QDialog.Accepted:
            columns_config = col_dialog.get_columns_config()
            if not columns_config:
                notify(self, '必须至少配置一列', 'warn')
                return
            
            db_file = f'user_{username}.db'
            # ★ v6.7：新建库必须用【独立连接】。以前拿的是池化连接，close() 会把
            #   主窗口正在用的共享连接一起关掉（v6.5 已踩过一次同类问题）。
            user_db = UserDatabase(db_file, private=True)
            try:
                user_db.initialize_database(columns_config)
            finally:
                user_db.close()
            
            settings = json.dumps(columns_config, ensure_ascii=False)
            if self.user_manager.add_user(username, db_file, settings):
                self.load_users()
            else:
                notify(self, '添加用户失败', 'warn')
    
    def delete_user(self):
        selected_items = self.user_list.selectedItems()
        if not selected_items:
            notify(self, '请选择一个用户', 'warn')
            return
        
        username = selected_items[0].text()
        reply = QMessageBox.question(self, '确认', f'确定要删除用户 {username} 吗?\n这将删除所有相关数据!', 
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            # ★ v6.7：先取真实库文件名（删用户后查不到了），避免删错文件
            db_file = self.user_manager.get_user_db_file(username) or f'user_{username}.db'
            if self.user_manager.delete_user(username):
                if os.path.exists(db_file):
                    os.remove(db_file)
                self.load_users()
            else:
                notify(self, '删除用户失败', 'warn')

    def import_users(self):
        """导入用户数据"""
        filename, _ = QFileDialog.getOpenFileName(
            self, '选择用户数据文件', '', 
            '所有文件 (*.*);;JSON文件 (*.json);;CSV文件 (*.csv)'
        )
        
        if not filename:
            return
        
        try:
            if filename.endswith('.json'):
                with open(filename, 'r', encoding='utf-8') as f:
                    users_data = json.load(f)
            elif filename.endswith('.csv'):
                # 增强的CSV文件读取，支持多种编码和错误处理
                data = None
                encodings_to_try = ['utf-8', 'gbk', 'gb2312', 'latin-1', 'cp936', 'utf-8-sig']
                
                for encoding in encodings_to_try:
                    try:
                        print(f"[DEBUG] 尝试使用 {encoding} 编码读取CSV文件")
                        with open(filename, 'r', encoding=encoding, errors='replace') as f:
                            # 先读取一行来检查文件内容
                            first_line = f.readline()
                            if not first_line.strip():
                                print("[DEBUG] 文件为空或第一行为空")
                                continue
                            
                            # 回到文件开头
                            f.seek(0)
                            reader = csv.DictReader(f)
                            data = list(reader)
                            
                        if data and len(data) > 0:
                            print(f"[DEBUG] 使用 {encoding} 编码成功读取 {len(data)} 行数据")
                            users_data = data
                            break
                        else:
                            print(f"[DEBUG] 使用 {encoding} 编码读取到空数据")
                            data = None
                            
                    except UnicodeDecodeError as e:
                        print(f"[DEBUG] {encoding} 编码解码失败: {str(e)}")
                        continue
                    except csv.Error as e:
                        print(f"[DEBUG] CSV解析错误 ({encoding}): {str(e)}")
                        continue
                    except Exception as e:
                        print(f"[DEBUG] 使用 {encoding} 编码时发生其他错误: {str(e)}")
                        continue
                
                if data is None:
                    # 如果自动检测都失败，让用户选择编码
                    encoding, ok = QInputDialog.getItem(
                        self, '选择文件编码', 
                        '自动检测编码失败，请手动选择文件编码:',
                        encodings_to_try, 0, False
                    )
                    if ok and encoding:
                        try:
                            with open(filename, 'r', encoding=encoding, errors='replace') as f:
                                reader = csv.DictReader(f)
                                users_data = list(reader)
                            print(f"[DEBUG] 用户选择 {encoding} 编码，成功读取 {len(users_data)} 行数据")
                        except Exception as e:
                            notify(self, f'使用 {encoding} 编码仍然失败: {str(e)}', 'error')
                            return
                    else:
                        notify(self, '无法读取CSV文件，请检查文件编码或格式', 'error')
                        return
            else:
                notify(self, '不支持的文件格式', 'warn')
                return
            
            # 显示导入选项对话框
            dialog = ImportUsersDialog(users_data, self)
            if dialog.exec() == QDialog.Accepted:
                imported_count = 0
                for user in dialog.get_selected_users():
                    username = user['username']
                    db_file = user.get('db_file', f'user_{username}.db')
                    settings = user.get('settings', '[]')
                    
                    # 检查用户是否已存在
                    if not self.user_manager.get_user_db_file(username):
                        # 创建用户数据库文件
                        user_db = UserDatabase(db_file)
                        try:
                            columns_config = json.loads(settings)
                            user_db.initialize_database(columns_config)
                        except:
                            # 使用默认配置
                            default_config = [{'name': 'id', 'label': 'ID', 'type': 'INTEGER'}]
                            user_db.initialize_database(default_config)
                        user_db.close()
                        
                        # 添加用户到管理器
                        if self.user_manager.add_user(username, db_file, settings):
                            imported_count += 1
                
                notify(self, f'成功导入 {imported_count} 个用户\n失败 {len(dialog.get_selected_users()) - imported_count} 个', 'info')
                self.load_users()
                
        except Exception as e:
            notify(self, f'导入用户失败: {str(e)}', 'error')




    def import_rules_json(self):
        """专门导入麻将规则的JSON文件，现在也支持其他格式"""
        filename, _ = QFileDialog.getOpenFileName(
            self, '选择麻将规则文件', '', 
            '所有文件 (*.*);;JSON文件 (*.json);;备份文件 (*.bak)'
        )
        
        if not filename:
            return
        
        try:
            # 智能识别文件类型
            file_type = self.detect_file_type(filename)
            
            if file_type == 'json':
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            elif file_type == 'backup':
                data = self.parse_backup_file(filename)
                if data is None:
                    notify(self, '无法解析备份文件', 'warn')
                    return
            else:
                notify(self, '请选择JSON格式的规则文件', 'warn')
                return
            
            if not isinstance(data, dict):
                notify(self, '不是有效的规则JSON格式', 'warn')
                return
            
            # 自动生成用户名
            base_name = os.path.splitext(os.path.basename(filename))[0]
            username = f"rules_{base_name}_{datetime.now().strftime('%H%M%S')}"
            
            # 创建适合规则数据的列配置
            columns_config = [
                {'name': 'category', 'label': '番种分类', 'type': 'TEXT'},
                {'name': 'name', 'label': '规则名称', 'type': 'TEXT'},
                {'name': 'fan', 'label': '番数', 'type': 'INTEGER'},
                {'name': 'exclude', 'label': '排除番种', 'type': 'TEXT'},
                {'name': 'condition', 'label': '规则条件', 'type': 'TEXT'}
            ]
            
            # 创建新用户和数据库
            db_file = f'user_{username}.db'
            user_db = UserDatabase(db_file)
            user_db.initialize_database(columns_config)
            
            # 转换数据结构并插入
            imported_count = 0
            for category, rules in data.items():
                if isinstance(rules, list):
                    for rule in rules:
                        if isinstance(rule, dict):
                            # 转换数据格式
                            record = {
                                'category': category,
                                'name': rule.get('name', ''),
                                'fan': rule.get('fan', 0),
                                'exclude': ', '.join(rule.get('exclude', [])),
                                'condition': rule.get('condition', '')
                            }
                            try:
                                user_db.insert_data(record)
                                imported_count += 1
                            except Exception as e:
                                print(f"[DEBUG] 插入规则数据失败: {str(e)}")
                                continue
            
            user_db.close()
            
            # 添加用户到管理器
            settings = json.dumps(columns_config, ensure_ascii=False)
            if self.user_manager.add_user(username, db_file, settings):
                notify(self, f'麻将规则导入成功\n已创建用户: {username}\n导入规则数: {imported_count}', 'info')
                self.load_users()
            else:
                notify(self, '创建用户失败', 'warn')
                
        except Exception as e:
            print(f"[DEBUG] 导入规则失败: {str(e)}")
            notify(self, f'导入规则失败: {str(e)}', 'error')


    def parse_text_file(self, filename):
        """
        自动识别文本文件的分隔符并解析数据
        支持的分隔符：制表符(tab)、逗号、分号、竖线、空格
        """
        try:
            # 自动检测文件编码
            with open(filename, 'rb') as f:
                raw_data = f.read()
                print(f"[DEBUG] 读取文件字节数: {len(raw_data)}")
            
            # 使用chardet检测编码
            try:
                encoding_result = chardet.detect(raw_data)
                file_encoding = encoding_result['encoding']
                confidence = encoding_result['confidence']
                
                # 如果置信度较低或检测失败，使用备选编码
                if not file_encoding or confidence < 0.6:
                    file_encoding = 'gbk'  # 中文环境常用编码
                    
                print(f"[DEBUG] 检测到文件编码: {file_encoding}, 置信度: {confidence}")
            except:
                # 如果chardet不可用，尝试常见编码
                file_encoding = 'gbk'
            
            # 使用检测到的编码读取文件
            try:
                content = raw_data.decode(file_encoding)
                lines = content.splitlines()
                print(f"[DEBUG] 使用检测编码成功: {file_encoding}")
            except UnicodeDecodeError:
                # 如果检测的编码失败，尝试其他常见编码
                for encoding in ['gbk', 'gb2312', 'utf-8', 'latin-1']:
                    try:
                        content = raw_data.decode(encoding)
                        lines = content.splitlines()
                        file_encoding = encoding
                        print(f"[DEBUG] 使用备选编码成功: {encoding}")
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    # 所有编码尝试都失败
                    print("[DEBUG] 无法解码文件，尝试所有编码都失败")
                    return None
            
            if not lines:
                return []
            
            # 常见分隔符列表，按优先级排序
            delimiters = ['\t', ',', ';', '|', '  ']  # 注意：两个空格用于识别多个连续空格
            
            # 分析前几行来确定分隔符
            sample_lines = lines[:min(10, len(lines))]
            best_delimiter = None
            max_columns = 0
            max_consistency = 0
            
            for delimiter in delimiters:
                column_counts = []
                for line in sample_lines:
                    line = line.strip()
                    if line:  # 跳过空行
                        if delimiter == '  ':  # 处理多个空格的情况
                            columns = [col for col in line.split(' ') if col.strip()]
                        else:
                            columns = line.split(delimiter)
                        column_counts.append(len(columns))
                
                if column_counts:
                    # 计算列数的一致性（相同列数的行数比例）
                    from collections import Counter
                    count_freq = Counter(column_counts)
                    most_common_count, most_common_freq = count_freq.most_common(1)[0]
                    consistency = most_common_freq / len(column_counts)
                    
                    # 优先选择一致性高且列数多的分隔符
                    if consistency > max_consistency or (consistency == max_consistency and most_common_count > max_columns):
                        best_delimiter = delimiter
                        max_columns = most_common_count
                        max_consistency = consistency
            
            # 如果自动识别失败，尝试使用制表符作为默认分隔符
            if not best_delimiter or max_columns < 2:
                best_delimiter = '\t'
                # 重新计算列数
                column_counts = []
                for line in sample_lines:
                    line = line.strip()
                    if line:
                        columns = line.split(best_delimiter)
                        column_counts.append(len(columns))
                if column_counts:
                    max_columns = max(column_counts)
            
            if max_columns < 2:
                return None
            
            # 解析数据
            data = []
            
            # 检查第一行是否可能是表头（不包含数字）
            first_line = lines[0].strip()
            if first_line:
                first_columns = first_line.split(best_delimiter) if best_delimiter != '  ' else [col for col in first_line.split(' ') if col.strip()]
                
                # 判断第一行是否包含数字，如果不包含则可能是表头
                has_numbers = any(any(char.isdigit() for char in col) for col in first_columns)
                
                if not has_numbers and len(first_columns) >= 2:
                    # 第一行是表头
                    headers = first_columns
                    start_index = 1
                else:
                    # 第一行是数据，使用默认列名
                    headers = [f'column_{i+1}' for i in range(max_columns)]
                    start_index = 0
                
                # 解析数据行
                for i in range(start_index, len(lines)):
                    line = lines[i].strip()
                    if not line:
                        continue
                        
                    if best_delimiter == '  ':
                        columns = [col.strip() for col in line.split(' ') if col.strip()]
                    else:
                        columns = [col.strip() for col in line.split(best_delimiter)]
                    
                    # 确保列数一致
                    while len(columns) < len(headers):
                        columns.append("")
                    if len(columns) > len(headers):
                        columns = columns[:len(headers)]
                    
                    row_data = {}
                    for j, header in enumerate(headers):
                        if j < len(columns):
                            row_data[header] = columns[j]
                        else:
                            row_data[header] = ""
                    data.append(row_data)
            
            print(f"[DEBUG] 成功解析文本文件: {len(data)} 行数据，编码: {file_encoding}")
            return data
            
        except Exception as e:
            print(f"[DEBUG] 解析文本文件失败: {str(e)}")
            return None


    def detect_file_type(self, filename):
        """
        通过文件头和扩展名智能识别文件类型
        返回: 'json', 'csv', 'text', 'backup', 'sqlserver', 'unknown'
        """
        try:
            # 读取文件头进行识别
            with open(filename, 'rb') as f:
                header = f.read(1024)  # 读取前1024字节
            
            # 检测文件编码
            try:
                encoding_result = chardet.detect(header)
                file_encoding = encoding_result['encoding']
                if not file_encoding or encoding_result['confidence'] < 0.6:
                    file_encoding = 'utf-8'
            except:
                file_encoding = 'utf-8'
            
            # 尝试解码文件头内容
            try:
                header_text = header.decode(file_encoding, errors='ignore')
            except:
                header_text = header.decode('utf-8', errors='ignore')
            
            # 1. 检查JSON文件特征
            header_trimmed = header_text.strip()
            if (header_trimmed.startswith('{') and header_trimmed.endswith('}')) or \
            (header_trimmed.startswith('[') and header_trimmed.endswith(']')):
                return 'json'
            
            # 2. 检查CSV文件特征
            lines = header_text.split('\n')[:10]  # 检查前10行
            if len(lines) >= 2:
                # 检查是否有明显的分隔符（逗号、分号、制表符）
                first_line = lines[0].strip()
                second_line = lines[1].strip() if len(lines) > 1 else ""
                
                # 常见CSV分隔符
                for delimiter in [',', ';', '\t']:
                    if delimiter in first_line and len(first_line.split(delimiter)) > 1:
                        # 检查第二行是否有相同数量的列
                        if second_line and len(second_line.split(delimiter)) == len(first_line.split(delimiter)):
                            return 'csv'
            
            # 3. 检查SQLite备份文件特征
            if header.startswith(b'SQLite format 3') or filename.lower().endswith('.bak'):
                return 'backup'
            
            # 4. 检查SQL Server文件特征
            if self.is_sqlserver_file(header, filename):
                return 'sqlserver'
            
            # 5. 检查文本文件特征
            # 如果包含可读文本且不是二进制文件
            readable_chars = sum(1 for byte in header if 32 <= byte <= 126 or byte in [9, 10, 13])
            if readable_chars / len(header) > 0.7:  # 70%以上是可读字符
                # 尝试检测 ANSI 编码特征
                if self.is_ansi_file(header, filename):
                    return 'ansi'
                return 'text'

            # 7. 检查 ANSI 文件
            if self.is_ansi_file(header, filename):
                return 'ansi'

            
            # 6. 根据扩展名判断
            ext = os.path.splitext(filename)[1].lower()
            if ext == '.json':
                return 'json'
            elif ext == '.csv':
                return 'csv'
            elif ext == '.txt':
                return 'ansi'  # 文本文件通常使用 ANSI 编码
            elif ext == '.log':
                return 'ansi'  # 日志文件通常使用 ANSI 编码
            elif ext in ['.ini', '.inf', '.cfg']:
                return 'ansi'  # 配置文件通常使用 ANSI 编码
            elif ext == '.bak':
                return 'backup'
            elif ext in ['.mdf', '.ldf']:
                return 'sqlserver'

            return 'unknown'

            
        except Exception as e:
            print(f"[DEBUG] 文件类型检测失败: {str(e)}")
            # 回退到扩展名检测
            ext = os.path.splitext(filename)[1].lower()
            if ext == '.json':
                return 'json'
            elif ext == '.csv':
                return 'csv'
            elif ext == '.txt':
                return 'text'
            elif ext == '.bak':
                return 'backup'
            elif ext in ['.mdf', '.ldf']:
                return 'sqlserver'
            return 'unknown'

    def parse_backup_file(self, filename):
        """
        解析备份文件，支持多种备份格式
        """
        try:
            # 方法1: 尝试作为SQLite数据库文件解析
            if self.is_sqlite_backup(filename):
                return self.parse_sqlite_backup(filename)
            
            # 方法2: 尝试作为JSON备份解析
            if self.is_json_backup(filename):
                return self.parse_json_backup(filename)
            
            # 方法3: 尝试作为CSV备份解析
            if self.is_csv_backup(filename):
                return self.parse_csv_backup(filename)
            
            # 方法4: 通用文本备份解析
            return self.parse_generic_backup(filename)
            
        except Exception as e:
            print(f"[DEBUG] 解析备份文件失败: {str(e)}")
            return None

    def is_sqlite_backup(self, filename):
        """检查是否为SQLite备份文件"""
        try:
            with open(filename, 'rb') as f:
                header = f.read(16)
                return header.startswith(b'SQLite format 3')
        except:
            return False

    def parse_sqlite_backup(self, filename):
        """解析SQLite备份文件"""
        try:
            # 创建临时数据库连接
            temp_db = sqlite3.connect(filename)
            cursor = temp_db.cursor()
            
            # 获取所有表名
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            
            data = {}
            for table in tables:
                if table == 'data':  # 主要数据表
                    cursor.execute(f"SELECT * FROM {table}")
                    columns = [description[0] for description in cursor.description]
                    rows = cursor.fetchall()
                    
                    # 转换为字典列表格式
                    table_data = []
                    for row in rows:
                        row_dict = {}
                        for i, col in enumerate(columns):
                            row_dict[col] = row[i]
                        table_data.append(row_dict)
                    
                    data[table] = table_data
            
            temp_db.close()
            return data if data else None
            
        except Exception as e:
            print(f"[DEBUG] 解析SQLite备份失败: {str(e)}")
            return None

    def is_json_backup(self, filename):
        """检查是否为JSON格式备份"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                content = f.read(100).strip()
                return content.startswith('{') or content.startswith('[')
        except:
            return False

    def parse_json_backup(self, filename):
        """解析JSON格式备份"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return None

    def is_csv_backup(self, filename):
        """检查是否为CSV格式备份"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                first_line = f.readline().strip()
                return ',' in first_line and len(first_line.split(',')) > 1
        except:
            return False

    def parse_csv_backup(self, filename):
        """解析CSV格式备份"""
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                return list(reader)
        except:
            return None

    def parse_generic_backup(self, filename):
        """解析通用备份文件"""
        try:
            # 尝试多种编码
            encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']
            
            for encoding in encodings:
                try:
                    with open(filename, 'r', encoding=encoding) as f:
                        content = f.read()
                    
                    # 尝试解析为JSON
                    if content.strip().startswith('{') or content.strip().startswith('['):
                        return json.loads(content)
                    
                    # 尝试按行解析为结构化数据
                    lines = content.splitlines()
                    if len(lines) >= 2:
                        # 检测分隔符
                        for delimiter in [',', '\t', ';', '|']:
                            if delimiter in lines[0] and len(lines[0].split(delimiter)) > 1:
                                # 作为CSV处理
                                import io
                                f_obj = io.StringIO(content)
                                reader = csv.DictReader(f_obj, delimiter=delimiter)
                                return list(reader)
                    
                    # 作为纯文本返回
                    return [{'content': line} for line in lines if line.strip()]
                    
                except UnicodeDecodeError:
                    continue
                except Exception:
                    continue
            
            return None
            
        except Exception as e:
            print(f"[DEBUG] 通用备份解析失败: {str(e)}")
            return None

    def is_sqlserver_file(self, header, filename):
        """检查是否为SQL Server文件"""
        # 检查扩展名
        ext = os.path.splitext(filename)[1].lower()
        if ext in ['.mdf', '.ldf']:
            return True
        
        # 检查文件头特征
        # SQL Server文件通常有特定的文件头签名
        if len(header) >= 512:
            # 检查可能的SQL Server文件头特征
            # 这里可以根据实际文件特征进行调整
            try:
                # 尝试解析文件头中的可读信息
                header_text = header.decode('utf-8', errors='ignore')
                if 'Microsoft SQL Server' in header_text:
                    return True
            except:
                pass
        
        return False

    def parse_sqlserver_file(self, filename):
        """
        解析SQL Server文件（MDF/LDF）
        由于SQL Server文件是二进制格式，这里主要提取可读的文本信息
        """
        try:
            data = []
            
            # 读取文件并提取可读文本
            with open(filename, 'rb') as f:
                content = f.read()
            
            # 尝试多种编码提取可读文本
            encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']
            
            for encoding in encodings:
                try:
                    # 将二进制内容转换为文本，忽略错误
                    text_content = content.decode(encoding, errors='ignore')
                    
                    # 提取包含可读字符的行
                    lines = text_content.splitlines()
                    readable_lines = []
                    
                    for line in lines:
                        line = line.strip()
                        if line and len(line) > 2:  # 过滤空行和过短的行
                            # 检查行是否包含足够多的可读字符
                            readable_chars = sum(1 for char in line if 32 <= ord(char) <= 126)
                            if readable_chars / len(line) > 0.3:  # 30%以上是可读字符
                                readable_lines.append(line)
                    
                    if readable_lines:
                        # 创建数据结构
                        file_info = {
                            'filename': os.path.basename(filename),
                            'filetype': 'SQL Server',
                            'size': len(content),
                            'readable_lines_count': len(readable_lines),
                            'sample_content': '\n'.join(readable_lines[:10])  # 前10行作为样本
                        }
                        
                        # 尝试提取更多结构化信息
                        table_data = self.extract_sqlserver_metadata(content, readable_lines)
                        if table_data:
                            data.extend(table_data)
                        else:
                            data.append(file_info)
                        
                        print(f"[DEBUG] 成功解析SQL Server文件: {len(readable_lines)} 行可读内容")
                        return data
                        
                except UnicodeDecodeError:
                    continue
            
            # 如果无法提取可读文本，返回基本信息
            data.append({
                'filename': os.path.basename(filename),
                'filetype': 'SQL Server Binary',
                'size': len(content),
                'note': '二进制文件，无法提取可读内容'
            })
            
            return data
            
        except Exception as e:
            print(f"[DEBUG] 解析SQL Server文件失败: {str(e)}")
            return None

    def extract_sqlserver_metadata(self, binary_content, readable_lines):
        """
        从SQL Server文件中提取元数据
        """
        try:
            metadata = []
            
            # 分析可读行，寻找表名、列名等结构化信息
            for line in readable_lines:
                # 寻找可能的表名（通常包含特定关键词）
                if any(keyword in line.upper() for keyword in ['TABLE', 'CREATE', 'ALTER', 'DROP']):
                    metadata.append({
                        'type': 'SQL Statement',
                        'content': line[:200]  # 限制长度
                    })
                
                # 寻找可能的表数据
                elif len(line) > 10 and ' ' not in line:  # 可能是纯数据行
                    metadata.append({
                        'type': 'Data Row',
                        'content': line[:100]
                    })
            
            # 如果找到结构化信息，返回它们
            if metadata:
                return metadata
            
            # 否则返回前几行可读内容作为样本
            sample_data = []
            for i, line in enumerate(readable_lines[:20]):  # 最多20行
                sample_data.append({
                    'row_number': i + 1,
                    'content': line[:150]  # 限制长度
                })
            
            return sample_data
            
        except Exception as e:
            print(f"[DEBUG] 提取SQL Server元数据失败: {str(e)}")
            return None




    def show_import_errors(self, error_details):
        """显示导入错误详情"""
        if not error_details:
            return
        
        # 只显示前20个错误
        display_errors = error_details[:20]
        
        dialog = QDialog(self)
        dialog.setWindowTitle('导入错误详情')
        dialog.resize(600, 400)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # 错误信息
        print(f"[DEBUG] 显示导入错误详情，共 {len(error_details)} 个错误，显示前 {len(display_errors)} 个")
        info_label = QLabel(f"共发现 {len(error_details)} 个错误，显示前 {len(display_errors)} 个:")
        info_label.setStyleSheet("color: #D32F2F; font-weight: bold; padding: 5px;")
        layout.addWidget(info_label)
        
        # 错误列表
        error_text = QTextEdit()
        error_text.setReadOnly(True)
        error_text.setPlainText("\n".join(display_errors))
        print(f"[DEBUG] 错误详情内容:\n{error_text.toPlainText()}")
        error_text.setStyleSheet("font-family: monospace; font-size: 10px;")
        layout.addWidget(error_text)
        
        # 导出错误报告按钮
        if len(error_details) > 20:
            export_btn = QPushButton('导出完整错误报告')
            export_btn.clicked.connect(lambda: self.export_error_report(error_details))
            layout.addWidget(export_btn)
        
        # 关闭按钮
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.exec()

    def export_error_report(self, error_details):
        """导出错误报告"""
        filename, _ = QFileDialog.getSaveFileName(
            self, '导出错误报告', 'import_errors.txt', 
            '所有文件 (*.*);;文本文件 (*.txt)'
        )
        
        if not filename:
            return
        
        try:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write("数据导入错误报告\n")
                f.write("=" * 50 + "\n\n")
                f.write(f"错误总数: {len(error_details)}\n")
                f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
                f.write("错误详情:\n")
                f.write("-" * 30 + "\n")
                for error in error_details:
                    f.write(error + "\n")
            
            notify(self, f'错误报告已导出到: {filename}', 'info')
            
        except Exception as e:
            notify(self, f'导出失败: {str(e)}', 'error')

    def read_import_file(self, filename, import_config):
        """读取导入文件，根据文件大小自动选择读取方法 - 移除行数限制"""
        file_type = self.detect_file_type(filename)
        
        if file_type in ['csv', 'ansi']:
            # 根据文件大小选择读取方式
            file_size = os.path.getsize(filename)
            
            # 阈值设置：50MB以上使用生成器方式，避免内存溢出
            if file_size > 50 * 1024 * 1024:  # 50MB
                print(f"[DEBUG] 检测到大文件 ({file_size/1024/1024:.2f}MB)，使用生成器方式读取")
                try:
                    # 使用生成器读取，完全移除行数限制
                    data = []
                    
                    # 直接收集生成器产生的所有记录，不限制行数
                    generator = self.read_large_csv_file_generator(filename, import_config)
                    for i, record in enumerate(generator):
                        data.append(record)
                        # 每处理10000行输出一次进度
                        if i > 0 and i % 10000 == 0:
                            print(f"[DEBUG] 已处理 {i} 行数据...")
                    
                    print(f"[DEBUG] 生成器方式成功读取 {len(data)} 行数据")
                    
                    if not data:
                        print("[DEBUG] 生成器返回空数据，尝试常规读取方法")
                        return self.read_csv_file(filename, import_config)
                    
                    return data
                        
                except Exception as e:
                    print(f"[DEBUG] 生成器读取失败: {str(e)}，尝试常规读取方法")
                    return self.read_csv_file(filename, import_config)
            else:
                print(f"[DEBUG] 检测到小文件 ({file_size/1024/1024:.2f}MB)，使用常规方式读取")
                return self.read_csv_file(filename, import_config)
        elif file_type == 'json':
            return self.read_json_file(filename)
        elif file_type in ['xlsx', 'xls']:
            return self.read_excel_file(filename, import_config)
        else:
            raise Exception(f"不支持的文件类型: {file_type}")


    def read_csv_file(self, filename, import_config):
        """读取CSV文件，增强编码检测和错误处理"""
        try:
            # 增强的编码检测列表
            encodings = [
                'utf-8', 'utf-8-sig',  # UTF-8 系列
                'gbk', 'gb2312', 'gb18030',  # 中文编码
                'big5', 'big5hkscs',  # 繁体中文
                'shift_jis', 'euc_jp', 'iso2022_jp',  # 日文编码
                'euc_kr', 'iso2022_kr',  # 韩文编码
                'windows-1250', 'windows-1251', 'windows-1252', 'windows-1253', 'windows-1254', 
                'windows-1255', 'windows-1256', 'windows-1257', 'windows-1258',  # Windows 代码页
                'iso-8859-1', 'iso-8859-2', 'iso-8859-3', 'iso-8859-4', 'iso-8859-5', 
                'iso-8859-6', 'iso-8859-7', 'iso-8859-8', 'iso-8859-9', 'iso-8859-10', 
                'iso-8859-11', 'iso-8859-13', 'iso-8859-14', 'iso-8859-15', 'iso-8859-16',  # ISO 拉丁系列
                'koi8-r', 'koi8-u',  # 俄语编码
                'mac_roman', 'mac_cyrillic', 'mac_greek', 'mac_turkish', 'mac_iceland',  # Mac 编码
                'cp437', 'cp850', 'cp852', 'cp855', 'cp857', 'cp860', 'cp861', 'cp862', 
                'cp863', 'cp864', 'cp865', 'cp866', 'cp869', 'cp874', 'cp932', 'cp936', 
                'cp949', 'cp950', 'cp1250', 'cp1251', 'cp1252', 'cp1253', 'cp1254', 
                'cp1255', 'cp1256', 'cp1257', 'cp1258',  # 代码页系列
                'ascii', 'latin-1'  # 基础编码
            ]

            # 首先尝试使用chardet自动检测编码
            try:
                with open(filename, 'rb') as f:
                    raw_data = f.read(10000)  # 读取前10KB用于检测
                    
                if hasattr(self, 'chardet') and self.chardet:
                    encoding_result = self.chardet.detect(raw_data)
                    detected_encoding = encoding_result.get('encoding')
                    confidence = encoding_result.get('confidence', 0)
                    
                    # 如果置信度较高，优先使用检测到的编码
                    if detected_encoding and confidence > 0.7:
                        print(f"[DEBUG] chardet检测到编码: {detected_encoding}, 置信度: {confidence}")
                        
                        # 标准化编码名称（处理别名）
                        encoding_aliases = {
                            'gb2312': 'gbk', 'cp936': 'gbk', 'windows-936': 'gbk',
                            'iso-8859-1': 'latin-1', 'iso8859-1': 'latin-1',
                            'cp1252': 'windows-1252', 'ms950': 'big5'
                        }
                        detected_encoding = encoding_aliases.get(detected_encoding.lower(), detected_encoding)
                        
                        # 如果检测到的编码不在列表中，添加到最前面
                        if detected_encoding.lower() not in [enc.lower() for enc in encodings]:
                            encodings.insert(0, detected_encoding)
                        else:
                            # 如果检测到的编码在列表中，移到最前面
                            for i, enc in enumerate(encodings):
                                if enc.lower() == detected_encoding.lower():
                                    encodings.pop(i)
                                    encodings.insert(0, detected_encoding)
                                    break
            except Exception as e:
                print(f"[DEBUG] chardet检测失败: {str(e)}")
            
            # 尝试各种编码
            for encoding in encodings:
                try:
                    print(f"[DEBUG] 尝试使用 {encoding} 编码读取CSV文件")
                    
                    with open(filename, 'r', encoding=encoding, errors='replace') as f:
                        # 跳过指定的行数
                        skip_rows = import_config.get('skip_rows', 0)
                        for _ in range(skip_rows):
                            line = f.readline()
                            if not line:
                                break
                        
                        # 读取第一行用于分隔符检测
                        first_line = f.readline().strip()
                        if not first_line:
                            continue
                        
                        # 回到文件开头（跳过已读的行）
                        f.seek(0)
                        for _ in range(skip_rows + 1):  # +1 因为已经读过第一行
                            f.readline()
                        
                        # 检测分隔符
                        delimiter = self.detect_csv_delimiter(first_line)
                        print(f"[DEBUG] 检测到分隔符: {repr(delimiter)}")
                        
                        # 验证分隔符是否有效
                        if not self.validate_delimiter(first_line, delimiter):
                            print(f"[DEBUG] 分隔符 {repr(delimiter)} 验证失败，尝试备选分隔符")
                            delimiter = self.fallback_delimiter_detection(first_line)
                            print(f"[DEBUG] 使用备选分隔符: {repr(delimiter)}")
                        
                        # 重新读取整个文件
                        f.seek(0)
                        for _ in range(skip_rows):
                            f.readline()
                        
                        # 修复：根据是否有表头创建不同的reader
                        first_row_as_header = import_config.get('first_row_as_header', True)
                        if first_row_as_header:
                            reader = csv.DictReader(f, delimiter=delimiter)
                            data = list(reader)
                        else:
                            # 没有表头时，使用列索引作为字段名
                            reader = csv.reader(f, delimiter=delimiter)
                            data = []
                            for row in reader:
                                if row:  # 跳过空行
                                    record = {f'Column_{i+1}': value for i, value in enumerate(row)}
                                    data.append(record)
                        
                        if data:
                            print(f"[DEBUG] 使用 {encoding} 编码成功读取 {len(data)} 行CSV数据")
                            # 添加编码统计信息
                            if len(data) > 0:
                                sample_record = data[0]
                                print(f"[DEBUG] 样本数据: {list(sample_record.keys())[:3]}...")  # 只显示前3个字段
                            return data
                        else:
                            print(f"[DEBUG] 使用 {encoding} 编码读取到空数据")
                            
                except UnicodeDecodeError as e:
                    print(f"[DEBUG] {encoding} 编码解码失败: {str(e)}")
                    continue
                except csv.Error as e:
                    print(f"[DEBUG] CSV解析错误 ({encoding}): {str(e)}")
                    continue
                except Exception as e:
                    print(f"[DEBUG] 使用 {encoding} 编码时发生其他错误: {str(e)}")
                    continue
            
            # 如果所有编码都失败，提供手动选择编码的选项
            encoding, ok = QInputDialog.getItem(
                self, '选择文件编码', 
                '自动检测编码失败，请手动选择文件编码:',
                encodings, 0, False
            )
            
            if ok and encoding:
                try:
                    with open(filename, 'r', encoding=encoding, errors='replace') as f:
                        skip_rows = import_config.get('skip_rows', 0)
                        for _ in range(skip_rows):
                            f.readline()
                        
                        # 检测分隔符
                        first_line = f.readline().strip()
                        delimiter = self.detect_csv_delimiter(first_line)
                        
                        # 验证分隔符
                        if not self.validate_delimiter(first_line, delimiter):
                            delimiter = self.fallback_delimiter_detection(first_line)
                        
                        f.seek(0)
                        for _ in range(skip_rows):
                            f.readline()
                        
                        # 修复：根据是否有表头创建不同的reader
                        first_row_as_header = import_config.get('first_row_as_header', True)
                        if first_row_as_header:
                            reader = csv.DictReader(f, delimiter=delimiter)
                            data = list(reader)
                        else:
                            # 没有表头时，使用列索引作为字段名
                            reader = csv.reader(f, delimiter=delimiter)
                            data = []
                            for row in reader:
                                if row:  # 跳过空行
                                    record = {f'Column_{i+1}': value for i, value in enumerate(row)}
                                    data.append(record)
                        
                        if data:
                            print(f"[DEBUG] 用户选择 {encoding} 编码，成功读取 {len(data)} 行数据")
                            return data
                except Exception as e:
                    raise Exception(f"使用 {encoding} 编码仍然失败: {str(e)}")
            
            raise Exception("无法读取CSV文件，请检查文件编码或格式")
            
        except Exception as e:
            raise Exception(f"读取CSV文件失败: {str(e)}")



    def validate_delimiter(self, line, delimiter):
        """验证分隔符是否有效"""
        if not line or not delimiter:
            return False
            
        if delimiter == '  ':  # 空格分隔符
            columns = [col for col in line.split(' ') if col.strip()]
        else:
            columns = line.split(delimiter)
        
        # 有效的分隔符应该产生至少2列，且不是所有列都为空
        valid = len(columns) >= 2 and any(col.strip() for col in columns)
        print(f"[DEBUG] 分隔符验证: {repr(delimiter)} -> {valid} (列数: {len(columns)})")
        return valid

    def fallback_delimiter_detection(self, line):
        """备选分隔符检测"""
        if not line:
            return ','
            
        common_delimiters = [',', '\t', ';', '|', ':', '~']
        
        for delim in common_delimiters:
            columns = line.split(delim)
            if len(columns) >= 2 and any(col.strip() for col in columns):
                print(f"[DEBUG] 使用备选分隔符: {repr(delim)} (产生 {len(columns)} 列)")
                return delim
        
        # 如果所有常见分隔符都失败，返回逗号
        print(f"[DEBUG] 所有备选分隔符都失败，使用默认逗号分隔符")
        return ','

    def normalize_encoding_name(self, encoding):
        """标准化编码名称"""
        encoding_map = {
            'gb2312': 'gbk',  # gb2312是gbk的子集
            'cp936': 'gbk',   # cp936就是gbk
            'windows-936': 'gbk',
            'ms936': 'gbk',
            'ansi_x3.4-1968': 'ascii',
            'us-ascii': 'ascii',
            'iso-8859-1': 'latin-1',
            'iso8859-1': 'latin-1',
            'cp1252': 'windows-1252',
            'ms950': 'big5',
            'csbig5': 'big5'
        }
        return encoding_map.get(encoding.lower(), encoding)

    def try_read_with_encoding(self, filename, import_config, encoding):
        """使用指定编码尝试读取文件"""
        with open(filename, 'r', encoding=encoding, errors='replace') as f:
            # 跳过指定的行数
            skip_rows = import_config.get('skip_rows', 0)
            for _ in range(skip_rows):
                line = f.readline()
                if not line:
                    break
            
            # 读取第一行用于分隔符检测
            first_line = f.readline().strip()
            if not first_line:
                return None
            
            # 回到文件开头（跳过已读的行）
            f.seek(0)
            for _ in range(skip_rows + 1):  # +1 因为已经读过第一行
                f.readline()
            
            # 检测分隔符
            delimiter = self.detect_csv_delimiter(first_line)
            print(f"[DEBUG] 检测到分隔符: {repr(delimiter)}")
            
            # 重新读取整个文件
            f.seek(0)
            for _ in range(skip_rows):
                f.readline()
            
            reader = csv.DictReader(f, delimiter=delimiter)
            data = list(reader)
            
            return data if data else None


    def read_excel_file(self, filename, import_config):
        """读取Excel文件"""
        try:
            # 检查是否安装了openpyxl
            try:
                import openpyxl
            except ImportError:
                # 尝试自动安装
                if install_package("openpyxl"):
                    import openpyxl
                else:
                    raise Exception("请手动安装 openpyxl: pip install openpyxl")
            
            workbook = openpyxl.load_workbook(filename, data_only=True)
            sheet_name = import_config.get('sheet_name')
            
            if sheet_name and sheet_name in workbook.sheetnames:
                sheet = workbook[sheet_name]
            else:
                sheet = workbook.active
            
            # 获取数据
            data = []
            headers = []
            
            # 跳过指定的行数
            skip_rows = import_config.get('skip_rows', 0)
            
            for i, row in enumerate(sheet.iter_rows(values_only=True)):
                if i < skip_rows:
                    continue
                    
                if i == skip_rows:
                    # 第一行作为表头
                    headers = [str(cell) if cell is not None else f"Column_{idx+1}" 
                            for idx, cell in enumerate(row)]
                else:
                    # 数据行
                    record = {}
                    for idx, cell in enumerate(row):
                        if idx < len(headers):
                            record[headers[idx]] = cell
                    data.append(record)
            
            print(f"[DEBUG] 成功读取 {len(data)} 行Excel数据")
            return data
            
        except Exception as e:
            raise Exception(f"读取Excel文件失败: {str(e)}")

    def detect_csv_delimiter(self, content):
        """增强的分隔符检测，处理各种CSV文件格式"""
        lines = content.split('\n')[:50]  # 增加检查行数到50行
        
        # 扩展分隔符列表，按优先级排序
        delimiters = [',', '\t', ';', '|', '  ', ':', '~', '#', '$']
        
        best_delimiter = ','
        max_consistency = 0
        max_columns = 0
        max_valid_lines = 0
        
        for delimiter in delimiters:
            column_counts = []
            valid_lines = 0
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                # 跳过明显不是数据行的内容（如注释、空行等）
                if line.startswith(('#', '//', '--', '/*')) or len(line) < 2:
                    continue
                    
                if delimiter == '  ':  # 处理多个空格的情况
                    columns = [col.strip() for col in line.split(' ') if col.strip()]
                else:
                    columns = [col.strip() for col in line.split(delimiter)]
                
                # 检查是否是有效的分隔
                if (columns and len(columns) > 1 and 
                    len([col for col in columns if col]) > 1):  # 至少有两个非空列
                    column_counts.append(len(columns))
                    valid_lines += 1
            
            if column_counts and valid_lines >= 2:  # 降低有效行数要求
                from collections import Counter
                count_freq = Counter(column_counts)
                most_common_count, most_common_freq = count_freq.most_common(1)[0]
                consistency = most_common_freq / len(column_counts)
                
                # 更智能的评分算法
                score = consistency * 0.6 + (most_common_count / 10) * 0.2 + (valid_lines / len(lines)) * 0.2
                
                # 优先选择评分高、一致性高且有效行数多的分隔符
                if (score > max_consistency or 
                    (score == max_consistency and most_common_count > max_columns) or
                    (score == max_consistency and most_common_count == max_columns and valid_lines > max_valid_lines)):
                    best_delimiter = delimiter
                    max_columns = most_common_count
                    max_consistency = score
                    max_valid_lines = valid_lines
        
        print(f"[DEBUG] 检测到最佳分隔符: {repr(best_delimiter)}, "
            f"评分: {max_consistency:.2f}, 列数: {max_columns}, 有效行: {max_valid_lines}")
        
        # 如果检测结果不理想，提供手动选择选项
        if max_consistency < 0.3 or max_valid_lines < 2:
            return self.manual_select_delimiter(content)
        
        return best_delimiter

    def manual_select_delimiter(self, content):
        """当自动检测失败时，手动选择分隔符"""
        lines = content.split('\n')[:10]  # 显示前10行用于参考
        
        dialog = QDialog()
        dialog.setWindowTitle('选择分隔符')
        dialog.resize(600, 400)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # 显示文件预览
        preview_label = QLabel('文件预览（前10行）:')
        layout.addWidget(preview_label)
        
        preview_text = QTextEdit()
        preview_text.setPlainText('\n'.join(lines))
        preview_text.setReadOnly(True)
        preview_text.setStyleSheet("font-family: 'Courier New'; font-size: 10px;")
        layout.addWidget(preview_text)
        
        # 分隔符选择
        delimiter_group = QGroupBox('选择分隔符')
        delimiter_layout = QVBoxLayout()
        
        delimiter_combo = QComboBox()
        delimiters = [
            ('逗号 (,)', ','),
            ('制表符 (\\t)', '\t'),
            ('分号 (;)', ';'),
            ('竖线 (|)', '|'),
            ('冒号 (:)', ':'),
            ('波浪号 (~)', '~'),
            ('井号 (#)', '#'),
            ('美元符号 ($)', '$'),
            ('多个空格', '  ')
        ]
        
        for name, delim in delimiters:
            delimiter_combo.addItem(name, delim)
        
        delimiter_layout.addWidget(QLabel('请根据文件预览选择正确的分隔符:'))
        delimiter_layout.addWidget(delimiter_combo)
        delimiter_group.setLayout(delimiter_layout)
        layout.addWidget(delimiter_group)
        
        # 预览效果
        preview_result = QLabel('')
        preview_result.setWordWrap(True)
        preview_result.setStyleSheet("background-color: #f0f0f0; padding: 5px; border: 1px solid #ccc;")
        layout.addWidget(preview_result)
        
        def update_preview():
            selected_delim = delimiter_combo.currentData()
            if lines:
                first_line = lines[0]
                if selected_delim == '  ':
                    columns = [col for col in first_line.split(' ') if col.strip()]
                else:
                    columns = first_line.split(selected_delim)
                preview_result.setText(f"第一行将被解析为 {len(columns)} 列: {columns}")
        
        delimiter_combo.currentIndexChanged.connect(update_preview)
        update_preview()  # 初始更新
        
        # 按钮区域
        button_layout = QHBoxLayout()
        ok_btn = QPushButton('确定')
        cancel_btn = QPushButton('取消')
        
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        result = {'delimiter': ','}  # 默认值
        
        def on_accept():
            result['delimiter'] = delimiter_combo.currentData()
            dialog.accept()
        
        def on_reject():
            result['delimiter'] = ','  # 使用默认逗号
            dialog.reject()
        
        ok_btn.clicked.connect(on_accept)
        cancel_btn.clicked.connect(on_reject)
        
        if dialog.exec() == QDialog.Accepted:
            print(f"[DEBUG] 用户手动选择分隔符: {repr(result['delimiter'])}")
            return result['delimiter']
        else:
            print(f"[DEBUG] 用户取消手动选择，使用默认分隔符: ,")
            return ','  # 默认使用逗号




    def validate_required_fields(self, data, user_db):
        """验证必填字段"""
        columns_config = user_db.get_columns_config()
        required_fields = [col['name'] for col in columns_config if col.get('is_required', False)]
        
        for field in required_fields:
            if field not in data or not data[field]:
                return False
        
        return True

    def has_conflicts(self, data, user_db):
        """检查数据冲突"""
        columns_config = user_db.get_columns_config()
        unique_fields = [col['name'] for col in columns_config if col.get('is_unique', False)]
        
        for field in unique_fields:
            if field in data and data[field]:
                if not user_db.check_unique(field, data[field]):
                    return True
        
        return False

    def update_existing_record(self, data, user_db):
        """更新现有记录"""
        try:
            # 这里需要根据唯一字段找到要更新的记录
            # 这是一个简化的实现，实际应用中可能需要更复杂的逻辑
            columns_config = user_db.get_columns_config()
            unique_fields = [col['name'] for col in columns_config if col.get('is_unique', False)]
            
            for field in unique_fields:
                if field in data and data[field]:
                    # 查找具有相同唯一字段值的记录
                    user_db.cursor.execute(f'SELECT rowid FROM data WHERE {field}=?', (data[field],))
                    result = user_db.cursor.fetchone()
                    if result:
                        return user_db.update_data(result[0], data)
            
            return False
        except Exception as e:
            print(f"[DEBUG] 更新记录失败: {str(e)}")
            return False

    def read_large_csv_file(self, filename, import_config):
        """优化的大文件CSV读取，支持流式处理和内存映射"""
        try:
            skip_rows = import_config.get('skip_rows', 0)
            max_rows = import_config.get('max_rows', 0)  # 0表示无限制
            
            # 检测文件编码
            with open(filename, 'rb') as f:
                raw_data = f.read(4096)  # 读取前4KB检测编码
                encoding_result = chardet.detect(raw_data)
                file_encoding = encoding_result.get('encoding', 'utf-8')
            
            data = []
            row_count = 0
            
            with open(filename, 'r', encoding=file_encoding, errors='replace') as f:
                # 跳过指定行数
                for _ in range(skip_rows):
                    f.readline()
                
                # 读取表头
                header_line = f.readline().strip()
                if not header_line:
                    return []
                    
                delimiter = self.detect_csv_delimiter(header_line)
                headers = [h.strip() for h in header_line.split(delimiter) if h.strip()]
                
                # 流式读取数据
                for line in f:
                    if max_rows > 0 and row_count >= max_rows:
                        break
                        
                    line = line.strip()
                    if not line:
                        continue
                    
                    values = line.split(delimiter)
                    # 确保列数一致
                    if len(values) > len(headers):
                        values = values[:len(headers)]
                    elif len(values) < len(headers):
                        values.extend([''] * (len(headers) - len(values)))
                    
                    record = dict(zip(headers, values))
                    data.append(record)
                    row_count += 1
                    
                    # 每1000行处理一次，避免内存占用过高
                    if len(data) >= 1000:
                        # 这里可以添加批量处理逻辑
                        # 或者直接返回生成器
                        pass
            
            return data
            
        except Exception as e:
            raise Exception(f"读取大文件失败: {str(e)}")

    def read_large_csv_file_generator(self, filename, import_config):
        """生成器版本，真正支持超大文件 - 增强数据质量检查"""
        try:
            skip_rows = import_config.get('skip_rows', 0)
            first_row_as_header = import_config.get('first_row_as_header', True)
            
            print(f"[DEBUG] 开始读取大文件: {filename}")
            print(f"[DEBUG] 跳过行数: {skip_rows}, 第一行作为表头: {first_row_as_header}")

            # 检测文件编码 - 使用更可靠的方法
            with open(filename, 'rb') as f:
                raw_data = f.read(8192)  # 读取更多字节提高检测准确性
                
            # 优先使用chardet检测编码
            file_encoding = 'utf-8'
            if hasattr(self, 'chardet') and self.chardet:
                encoding_result = self.chardet.detect(raw_data)
                detected_encoding = encoding_result.get('encoding')
                confidence = encoding_result.get('confidence', 0)
                
                if detected_encoding and confidence > 0.6:
                    file_encoding = detected_encoding
                    print(f"[DEBUG] chardet检测到编码: {file_encoding}, 置信度: {confidence}")
                else:
                    # 备选编码列表
                    encodings_to_try = ['utf-8', 'gbk', 'gb2312', 'latin-1', 'cp936', 'utf-8-sig']
                    for encoding in encodings_to_try:
                        try:
                            raw_data.decode(encoding)
                            file_encoding = encoding
                            print(f"[DEBUG] 备选编码成功: {file_encoding}")
                            break
                        except UnicodeDecodeError:
                            continue
            
            print(f"[DEBUG] 最终使用编码: {file_encoding}")

            with open(filename, 'r', encoding=file_encoding, errors='replace') as f:
                # 跳过指定行数
                for i in range(skip_rows):
                    skipped_line = f.readline()
                    print(f"[DEBUG] 跳过第{i+1}行: {skipped_line.strip()[:100]}...")
                
                # 读取表头或第一行数据
                if first_row_as_header:
                    header_line = f.readline().strip()
                    if not header_line:
                        print("[DEBUG] 表头行为空，无法继续读取")
                        return
                        
                    # 检测分隔符
                    delimiter = self.detect_csv_delimiter(header_line)
                    headers = [h.strip() for h in header_line.split(delimiter) if h.strip()]
                    print(f"[DEBUG] 检测到分隔符: {repr(delimiter)}")
                    print(f"[DEBUG] 表头: {headers}")
                    print(f"[DEBUG] 表头数量: {len(headers)}")
                else:
                    # 如果没有表头，读取第一行数据来推断列数
                    first_data_line = f.readline().strip()
                    if not first_data_line:
                        print("[DEBUG] 第一行数据为空，无法继续读取")
                        return
                        
                    delimiter = self.detect_csv_delimiter(first_data_line)
                    headers_count = len(first_data_line.split(delimiter))
                    headers = [f'Column_{i+1}' for i in range(headers_count)]
                    print(f"[DEBUG] 无表头模式，推断列数: {headers_count}")
                    # 重置文件指针到数据开始位置
                    f.seek(0)
                    for _ in range(skip_rows + 1):  # 重新跳过行数
                        f.readline()

                # 数据质量统计
                quality_stats = {
                    'total_rows': 0,
                    'empty_rows': 0,
                    'incomplete_rows': 0,
                    'valid_rows': 0,
                    'malformed_rows': 0
                }

                # 逐行生成数据
                line_count = 0
                empty_line_count = 0
                valid_record_count = 0
                
                for line in f:
                    line = line.strip()
                    if not line:
                        empty_line_count += 1
                        quality_stats['empty_rows'] += 1
                        continue
                    
                    values = line.split(delimiter)
                    quality_stats['total_rows'] += 1
                
                    # 检查数据完整性
                    if len(values) < len(headers):
                        quality_stats['incomplete_rows'] += 1
                        print(f"[DEBUG] 第{line_count+1}行列数不足 ({len(values)}/{len(headers)})，已填充空值")
                    
                    # 检查数据格式问题
                    if len(values) == 0 or (len(values) == 1 and not values[0].strip()):
                        quality_stats['malformed_rows'] += 1
                        print(f"[DEBUG] 第{line_count+1}行数据格式异常，跳过")
                        continue
                
                    # 确保列数一致
                    if len(values) > len(headers):
                        values = values[:len(headers)]
                        print(f"[DEBUG] 第{line_count+1}行列数过多，已截断")
                    elif len(values) < len(headers):
                        values.extend([''] * (len(headers) - len(values)))
                        print(f"[DEBUG] 第{line_count+1}行列数不足，已填充空值")
                    
                    record = dict(zip(headers, values))
                    line_count += 1
                    valid_record_count += 1
                    quality_stats['valid_rows'] += 1
                    
                    # 调试信息
                    if line_count <= 5:  # 只打印前5条记录的调试信息
                        print(f"[DEBUG] 第{line_count}行数据样本: {list(record.values())[:3]}...")
                    
                    if line_count % 10000 == 0:
                        print(f"[DEBUG] 已读取 {line_count} 行数据，有效记录: {valid_record_count}")
                    
                    yield record
            
                # 输出数据质量报告
                print(f"[DEBUG] 数据质量报告: 总计{quality_stats['total_rows']}行, "
                    f"有效{quality_stats['valid_rows']}行, "
                    f"空行{quality_stats['empty_rows']}行, "
                    f"不完整{quality_stats['incomplete_rows']}行, "
                    f"格式异常{quality_stats['malformed_rows']}行")
                
                print(f"[DEBUG] 大文件读取完成 - 总行数: {line_count}, 有效记录: {valid_record_count}, 空行: {empty_line_count}")
                
                if valid_record_count == 0:
                    print("[DEBUG] 警告: 没有读取到任何有效记录!")
                    # 打印文件前几行用于调试
                    with open(filename, 'r', encoding=file_encoding, errors='replace') as debug_f:
                        for i in range(10):
                            debug_line = debug_f.readline()
                            if not debug_line:
                                break
                            print(f"[DEBUG] 文件第{i+1}行: {debug_line.strip()}")
                        
        except Exception as e:
            print(f"[DEBUG] 读取大文件失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise Exception(f"读取大文件失败: {str(e)}")



    def ultra_fast_import_data(self):
        """超快速导入数据并创建新用户 - 支持多文件选择"""
        filenames, _ = QFileDialog.getOpenFileNames(
            self, '选择数据文件', '', 
            '所有文件 (*.*);;CSV文件 (*.csv);;JSON文件 (*.json);;Excel文件 (*.xlsx *.xls)'
        )
        
        if not filenames:
            return
        
        # 批量处理多个文件
        total_success = 0
        total_errors = 0
        
        for filename in filenames:
            try:
                # 显示超快速导入对话框
                dialog = UltraFastImportDialog(filename, self)
                if dialog.exec() == QDialog.Accepted:
                    import_config = dialog.get_import_config()
                    
                    # 执行超快速导入
                    success_count, error_count = self.execute_ultra_fast_import(import_config)
                    
                    total_success += success_count
                    total_errors += error_count
                    
            except Exception as e:
                print(f"[DEBUG] 导入文件 {filename} 失败: {str(e)}")
                notify(self, f'导入文件 {os.path.basename(filename)} 失败: {str(e)}', 'error')
        
        # 显示批量导入结果
        result_msg = f"⚡📥批量导入完成！\n\n总共处理 {len(filenames)} 个文件\n成功: {total_success} 条\n失败: {total_errors} 条"
        if total_errors > 0:
            result_msg += f"\n\n失败原因可能包括：\n- 数据格式不正确\n- 必填字段为空\n- 唯一约束冲突"
        
        notify(self, result_msg, 'info')
        self.load_users()




    def execute_ultra_fast_import(self, import_config):
        """执行超快速导入"""
        filename = import_config['filename']
        target_user = import_config['target_user']
        mapping = import_config['field_mapping']
    
        # 添加字段映射验证
        if not mapping:
            notify(self, '字段映射为空，请重新配置导入设置', 'warn')
            return 0, 0
        
        print(f"[DEBUG] 字段映射配置: {mapping}")
        
        conflict_resolution = import_config['conflict_resolution']
        batch_size = import_config.get('batch_size', 1000)
        
        # 获取目标用户数据库
        db_file = self.user_manager.get_user_db_file(target_user)
        if not db_file:
            raise Exception(f"用户 {target_user} 的数据库文件不存在")
        
        user_db = UserDatabase(db_file)
        
        try:
            # 读取数据文件
            data = self.read_import_file(filename, import_config)
            if not data:
                return 0, 0
            
            success_count = 0
            error_count = 0
            error_details = []
            
            # 创建进度对话框
            progress_dialog = QProgressDialog("正在快速导入数据...", "取消", 0, len(data), self)
            progress_dialog.setWindowTitle("超快速导入进度")
            progress_dialog.setWindowModality(Qt.WindowModal)
            progress_dialog.show()
            
            # 分批处理数据
            for i in range(0, len(data), batch_size):
                if progress_dialog.wasCanceled():
                    break
                    
                batch = data[i:i + batch_size]
                batch_success, batch_errors = self.process_import_batch(
                    batch, mapping, conflict_resolution, user_db, i + 1
                )
                
                success_count += batch_success
                error_count += len(batch_errors)
                error_details.extend(batch_errors)
                
                # 更新进度
                progress_dialog.setValue(min(i + batch_size, len(data)))
                QApplication.processEvents()
            
            progress_dialog.close()
            
            # 显示结果
            if error_details:
                self.show_import_errors(error_details)
            
            return success_count, error_count
            
        finally:
            # 确保在导入完成后清理状态
            self.clear_all_storage()
            print(f"[DEBUG] 导入完成，已清理存储状态")
            # user_db.close()
        
        return success_count, error_count
            

    def process_import_batch(self, batch, mapping, conflict_resolution, user_db, start_index):
        """处理单个数据批次 - 修复版本"""
        success_count = 0
        error_details = []
        
        try:
            # 数据预处理
            print(f"[DEBUG] 开始预处理批次数据，原始数量: {len(batch)}")
            processed_batch = self.preprocess_import_data(batch, mapping)
            print(f"[DEBUG] 预处理后数据量: {len(processed_batch)}")
            
            # 准备批量数据
            batch_data = []
            for i, record in enumerate(processed_batch):
                try:
                    mapped_data = {}
                    
                    # 调试：打印原始记录和映射关系
                    if i < 3:  # 只打印前3条记录的调试信息
                        print(f"[DEBUG] 第{start_index + i}行预处理后记录: {record}")
                        print(f"[DEBUG] 字段映射关系: {mapping}")
                    
                    # 修复字段映射逻辑
                    mapping_found = False
                    for source_field, target_field in mapping.items():
                        if source_field in record and target_field:
                            value = record[source_field]
                            if value is None or str(value).strip() == '':
                                mapped_data[target_field] = ""
                            else:
                                mapped_data[target_field] = str(value)
                            mapping_found = True
                            if i < 3:  # 只打印前3条记录的映射信息
                                print(f"[DEBUG] 映射字段: {source_field} -> {target_field} = {value}")
                    
                    # 如果没有找到任何映射，尝试使用列名直接映射
                    if not mapping_found and record:
                        print(f"[DEBUG] 第{start_index + i}行未找到显式映射，尝试直接映射")
                        for key, value in record.items():
                            # 尝试将源字段名直接映射到目标字段
                            if key in [col['name'] for col in self.columns_config]:
                                mapped_data[key] = str(value) if value is not None else ""
                                mapping_found = True
                                if i < 3:  # 只打印前3条记录的映射信息
                                    print(f"[DEBUG] 直接映射: {key} = {value}")
                    
                    # 调试：打印映射后的数据
                    if i < 3:  # 只打印前3条记录的调试信息
                        print(f"[DEBUG] 第{start_index + i}行映射数据: {mapped_data}")
                    
                    # 如果映射后数据为空，记录错误并跳过
                    if not mapped_data:
                        print(f"[DEBUG] 第{start_index + i}行映射后数据为空")
                        error_details.append(f"第{start_index + i}行: 字段映射失败，无有效数据")
                        continue
                    
                    # 验证必填字段
                    if not self.validate_required_fields(mapped_data):
                        error_details.append(f"第{start_index + i}行: 必填字段缺失")
                        continue
                    
                    # 处理冲突
                    if conflict_resolution == 'skip' and self.has_conflicts(mapped_data):
                        error_details.append(f"第{start_index + i}行: 唯一性约束冲突，已跳过")
                        continue
                    elif conflict_resolution == 'update' and self.has_conflicts(mapped_data):
                        # 更新现有记录
                        if self.update_existing_record(mapped_data):
                            success_count += 1
                            continue
                        else:
                            error_details.append(f"第{start_index + i}行: 更新现有记录失败")
                            continue
                    
                    batch_data.append(mapped_data)
                    
                except Exception as e:
                    error_details.append(f"第{start_index + i}行: {str(e)}")
                    print(f"[DEBUG] 处理第{start_index + i}行时出错: {str(e)}")
            
            # 批量插入新数据
            if batch_data:
                print(f"[DEBUG] 准备批量插入 {len(batch_data)} 条数据")
                try:
                    inserted_count = self.user_db.bulk_insert_data(batch_data)
                    success_count += inserted_count
                    print(f"[DEBUG] 成功插入 {inserted_count} 条数据")
                except Exception as e:
                    error_details.append(f"批量插入失败: {str(e)}")
                    print(f"[DEBUG] 批量插入失败: {str(e)}")
        
        except Exception as e:
            error_details.append(f"批次处理失败: {str(e)}")
            print(f"[DEBUG] 批次处理失败: {str(e)}")
        
        return success_count, error_details

    def is_ansi_file(self, header, filename):
        """检测是否为 ANSI 编码文件"""
        try:
            # ANSI 通常是系统默认编码，在中文Windows上是gbk
            # 检查文件扩展名
            ext = os.path.splitext(filename)[1].lower()
            common_ansi_extensions = ['.txt', '.csv', '.log', '.ini', '.inf']
            
            if ext in common_ansi_extensions:
                return True
            
            # 检查文件内容特征
            # ANSI 文件通常不包含 BOM 头
            if header.startswith(b'\xFF\xFE') or header.startswith(b'\xFE\xFF') or header.startswith(b'\xEF\xBB\xBF'):
                return False  # 有 BOM 头，不是纯 ANSI
            
            # 尝试用常见 ANSI 编码解码
            ansi_encodings = ['gbk', 'cp936', 'windows-1252', 'latin-1']
            for encoding in ansi_encodings:
                try:
                    header.decode(encoding)
                    return True
                except UnicodeDecodeError:
                    continue
            
            # 检查是否主要是 ASCII 字符（ANSI 兼容 ASCII）
            ascii_count = sum(1 for byte in header if byte <= 127)
            if ascii_count / len(header) > 0.9:  # 90% 以上是 ASCII
                return True
                
        except Exception:
            pass
        
        return False

    def batch_ultra_fast_import(self):
        """批量超快速导入 - 支持统一配置"""
        filenames, _ = QFileDialog.getOpenFileNames(
            self, '选择多个数据文件', '', 
            '所有文件 (*.*);;CSV文件 (*.csv);;JSON文件 (*.json);;Excel文件 (*.xlsx *.xls)'
        )
        
        if not filenames:
            return
        
        # 询问是否使用统一配置
        use_unified = False
        unified_config = {}
        
        if len(filenames) > 1:
            reply = QMessageBox.question(
                self, '统一配置', 
                f'检测到 {len(filenames)} 个文件，是否使用统一配置？\n\n'
                '是：第一个文件的配置将应用于所有文件\n'
                '否：每个文件单独配置',
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Cancel:
                return
            elif reply == QMessageBox.Yes:
                use_unified = True
        
        # 批量处理多个文件
        total_success = 0
        total_errors = 0
        processed_files = 0
        
        # 创建进度对话框
        progress_dialog = QProgressDialog("正在批量导入文件...", "取消", 0, len(filenames), self)
        progress_dialog.setWindowTitle("批量导入进度")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.show()
        
        first_file_config = None
        
        for i, filename in enumerate(filenames):
            if progress_dialog.wasCanceled():
                break
                
            try:
                # 显示超快速导入对话框
                dialog = UltraFastImportDialog(filename, self)
                
                # 如果是统一配置模式且不是第一个文件，应用第一个文件的配置
                if use_unified and i > 0 and first_file_config:
                    dialog.unified_config = first_file_config
                    dialog.unified_config_check.setChecked(True)
                    dialog.load_unified_config()
                    
                    # 直接使用第一个文件的配置，不显示对话框
                    import_config = dialog.get_import_config()
                else:
                    # 第一个文件或非统一配置模式，正常显示对话框
                    if dialog.exec() == QDialog.Accepted:
                        import_config = dialog.get_import_config()
                        
                        # 保存第一个文件的配置用于统一配置
                        if use_unified and i == 0:
                            first_file_config = import_config
                    else:
                        continue  # 用户取消了第一个文件的配置
                
                # 执行超快速导入
                success_count, error_count = self.execute_ultra_fast_import(import_config)
                
                total_success += success_count
                total_errors += error_count
                processed_files += 1
                
            except Exception as e:
                print(f"[DEBUG] 导入文件 {filename} 失败: {str(e)}")
                total_errors += 1  # 将文件导入失败也计入错误
            
            # 更新进度
            progress_dialog.setValue(i + 1)
            QApplication.processEvents()
        
        progress_dialog.close()
        
        # 显示批量导入结果
        result_msg = f"⚡📥批量导入完成！\n\n总共处理 {len(filenames)} 个文件\n成功导入 {processed_files} 个文件\n成功记录: {total_success} 条\n失败记录: {total_errors} 条"
        
        if use_unified and processed_files > 1:
            result_msg += f"\n\n✅ 已应用统一配置到所有文件"
        
        notify(self, result_msg, 'info')
        self.load_users()

    def batch_ultra_fast_import_enhanced(self):
        """增强的批量超快速导入 - 支持真正的无人值守模式"""
        filenames, _ = QFileDialog.getOpenFileNames(
            self, '选择多个数据文件', '', 
            '所有文件 (*.*);;CSV文件 (*.csv);;JSON文件 (*.json);;Excel文件 (*.xlsx *.xls)'
        )
        
        if not filenames:
            return
        
        # 询问导入模式
        mode_dialog = QDialog(self)
        mode_dialog.setWindowTitle('选择导入模式')
        mode_dialog.resize(400, 250)
        
        layout = QVBoxLayout()
        mode_dialog.setLayout(layout)
        
        # 模式选择
        mode_group = QGroupBox('导入模式')
        mode_layout = QVBoxLayout()
        
        interactive_radio = QRadioButton('交互模式（推荐）')
        interactive_radio.setChecked(True)
        interactive_radio.setToolTip('每个文件单独配置，提供最大灵活性')
        
        unattended_radio = QRadioButton('无人值守模式')
        unattended_radio.setToolTip('使用统一配置自动处理所有文件，适合批量处理相似文件')
        
        mode_layout.addWidget(interactive_radio)
        mode_layout.addWidget(unattended_radio)
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        
        # 统一配置选项（仅无人值守模式可用）
        config_group = QGroupBox('统一配置选项')
        config_layout = QVBoxLayout()
        
        use_existing_config = QCheckBox('使用已保存的统一配置')
        use_existing_config.setEnabled(False)
        
        save_as_default = QCheckBox('将本次配置保存为默认配置')
        save_as_default.setEnabled(False)
        
        # 新增：无人值守模式选项
        unattended_mode = QCheckBox('启用无人值守模式')
        unattended_mode.setChecked(True)
        unattended_mode.setEnabled(False)

        config_layout.addWidget(use_existing_config)
        config_layout.addWidget(save_as_default)
        config_layout.addWidget(unattended_mode)
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)
        
        # 连接信号
        def update_config_options():
            unattended = unattended_radio.isChecked()
            use_existing_config.setEnabled(unattended)
            save_as_default.setEnabled(unattended)
            unattended_mode.setEnabled(unattended)
        
        unattended_radio.toggled.connect(update_config_options)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        start_btn = QPushButton('开始导入')
        cancel_btn = QPushButton('取消')
        
        button_layout.addWidget(start_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        # 连接按钮信号
        start_btn.clicked.connect(mode_dialog.accept)
        cancel_btn.clicked.connect(mode_dialog.reject)
        
        if mode_dialog.exec() != QDialog.Accepted:
            return
        
        # 根据选择的模式执行导入
        if unattended_radio.isChecked():
            # 新增：获取无人值守模式设置并传递
            unattended_mode_setting = unattended_mode.isChecked()
            self.execute_unattended_batch_import_with_cleanup(
                filenames, 
                use_existing_config.isChecked(),
                save_as_default.isChecked(),
                unattended_mode_setting  # 传递无人值守模式设置
            )
        else:
            self.batch_ultra_fast_import_with_cleanup(filenames)
    
    def execute_unattended_batch_import(self, filenames, use_existing_config, save_as_default, unattended_mode=True):
        """执行无人值守批量导入"""
        try:
            # 获取或创建统一配置
            if use_existing_config and hasattr(self, 'global_unified_config') and self.global_unified_config:
                unified_config = self.global_unified_config
                print("[DEBUG] 使用已保存的统一配置")
            else:
                # 使用第一个文件创建配置
                first_dialog = UltraFastImportDialog(filenames[0], self)
                if first_dialog.exec() != QDialog.Accepted:
                    return
                
                unified_config = first_dialog.get_import_config()
                print("[DEBUG] 使用第一个文件的配置作为统一配置")
            
            # 保存为默认配置（如果需要）
            if save_as_default:
                self.global_unified_config = unified_config
                print("[DEBUG] 已保存为默认统一配置")
            
            # 执行批量导入
            total_success = 0
            total_errors = 0
            processed_files = 0
            
            # 创建进度对话框
            progress_dialog = QProgressDialog("正在无人值守批量导入...", "取消", 0, len(filenames), self)
            progress_dialog.setWindowTitle("无人值守批量导入进度")
            progress_dialog.setWindowModality(Qt.WindowModal)
            progress_dialog.show()
            
            for i, filename in enumerate(filenames):
                if progress_dialog.wasCanceled():
                    break
                
                try:
                    # 修复：直接使用统一配置，不创建对话框
                    if i == 0:  # 第一个文件已经配置过了
                        import_config = unified_config
                    else:
                        # 对于后续文件，直接应用统一配置
                        import_config = unified_config.copy()
                        import_config['filename'] = filename  # 更新文件名
                    
                    # 直接执行导入，不显示对话框
                    success_count, error_count = self.execute_ultra_fast_import(import_config)
                    
                    total_success += success_count
                    total_errors += error_count
                    processed_files += 1
                    print(f"[DEBUG] 无人值守处理文件 {os.path.basename(filename)}: 成功{success_count}条, 失败{error_count}条")
                    
                except Exception as e:
                    print(f"[DEBUG] 无人值守处理文件 {filename} 失败: {str(e)}")
                    total_errors += 1
                
                # 更新进度
                progress_dialog.setValue(i + 1)
                QApplication.processEvents()
            
            progress_dialog.close()
            
            # 显示结果
            result_msg = f"🤖 无人值守批量导入完成！\n\n" \
                        f"总共处理: {len(filenames)} 个文件\n" \
                        f"成功导入: {processed_files} 个文件\n" \
                        f"成功记录: {total_success} 条\n" \
                        f"失败/跳过: {total_errors} 条"
            
            if processed_files > 1:
                result_msg += f"\n\n✅ 已应用统一配置到所有文件"
            
            notify(self, result_msg, 'info')
            self.load_users()
            
        except Exception as e:
            notify(self, f'无人值守批量导入失败: {str(e)}', 'error')

    def batch_ultra_fast_import_original(self, filenames):
        """原有的批量导入方法（重命名）"""
        # 这里就是原来 batch_ultra_fast_import 方法的代码
        # 只是改个名字避免冲突
        total_success = 0
        total_errors = 0
        processed_files = 0

    def clear_all_storage(self):
        """清除所有存储信息和缓存，恢复到初始状态"""
        try:
            print("[DEBUG] 开始清除所有存储信息...")
            
            # 1. 清除全局统一配置
            if hasattr(self, 'global_unified_config'):
                self.global_unified_config = None
                print("[DEBUG] 已清除全局统一配置")
            
            # 2. 清除字段映射缓存
            if hasattr(self, 'field_mapping_cache'):
                self.field_mapping_cache = {}
                print("[DEBUG] 已清除字段映射缓存")
            
            # 3. 清除文件历史记录
            if hasattr(self, 'previous_filename'):
                self.previous_filename = None
                print("[DEBUG] 已清除文件历史记录")
            
            # 4. 强制垃圾回收
            import gc
            gc.collect()
            print("[DEBUG] 已执行垃圾回收")
            
            # 5. 重新初始化关键组件
            self.load_users()  # 重新加载用户列表
            
            print("[DEBUG] 所有存储信息清除完成")
            
        except Exception as e:
            print(f"[DEBUG] 清除存储信息时出错: {str(e)}")

    def execute_unattended_batch_import_with_cleanup(self, filenames, use_existing_config, save_as_default, unattended_mode=True):
        """执行无人值守批量导入，每次导入后清理状态"""
        try:
            # 获取或创建统一配置
            if use_existing_config and hasattr(self, 'global_unified_config') and self.global_unified_config:
                unified_config = self.global_unified_config
                print("[DEBUG] 使用已保存的统一配置")
            else:
                # 使用第一个文件创建配置
                first_dialog = UltraFastImportDialog(filenames[0], self)
                if first_dialog.exec() != QDialog.Accepted:
                    return
                
                unified_config = first_dialog.get_import_config()
                print("[DEBUG] 使用第一个文件的配置作为统一配置")
            
            # 保存为默认配置（如果需要）
            if save_as_default:
                self.global_unified_config = unified_config
                print("[DEBUG] 已保存为默认统一配置")
            
            # 执行批量导入
            total_success = 0
            total_errors = 0
            processed_files = 0
            
            # 创建进度对话框
            progress_dialog = QProgressDialog("正在无人值守批量导入...", "取消", 0, len(filenames), self)
            progress_dialog.setWindowTitle("无人值守批量导入进度")
            progress_dialog.setWindowModality(Qt.WindowModal)
            progress_dialog.show()
            
            for i, filename in enumerate(filenames):
                if progress_dialog.wasCanceled():
                    break
                
                try:
                    # 修复：直接使用统一配置，不创建对话框
                    if i == 0:  # 第一个文件已经配置过了
                        import_config = unified_config
                    else:
                        # 对于后续文件，直接应用统一配置
                        import_config = unified_config.copy()
                        import_config['filename'] = filename  # 更新文件名
                    
                    # 直接执行导入，不显示对话框
                    success_count, error_count = self.execute_ultra_fast_import(import_config)
                    
                    total_success += success_count
                    total_errors += error_count
                    processed_files += 1
                    print(f"[DEBUG] 无人值守处理文件 {os.path.basename(filename)}: 成功{success_count}条, 失败{error_count}条")
                    
                    # 关键修改：每次导入后清除所有存储信息
                    self.clear_all_storage()
                    print(f"[DEBUG] 文件 {os.path.basename(filename)} 导入完成，已清除存储状态")
                    
                except Exception as e:
                    print(f"[DEBUG] 无人值守处理文件 {filename} 失败: {str(e)}")
                    total_errors += 1
                    # 即使失败也要清理状态
                    self.clear_all_storage()
                
                # 更新进度
                progress_dialog.setValue(i + 1)
                QApplication.processEvents()
            
            progress_dialog.close()
            
            # 显示结果
            result_msg = f"🤖 无人值守批量导入完成！\n\n" \
                        f"总共处理: {len(filenames)} 个文件\n" \
                        f"成功导入: {processed_files} 个文件\n" \
                        f"成功记录: {total_success} 条\n" \
                        f"失败/跳过: {total_errors} 条"
            
            if processed_files > 1:
                result_msg += f"\n\n✅ 已应用统一配置到所有文件"
            
            notify(self, result_msg, 'info')
            self.load_users()
            
        except Exception as e:
            notify(self, f'无人值守批量导入失败: {str(e)}', 'error')

    def batch_ultra_fast_import_with_cleanup(self, filenames):
        """交互式批量导入，每次导入后清理状态"""
        total_success = 0
        total_errors = 0
        processed_files = 0
        
        # 创建进度对话框
        progress_dialog = QProgressDialog("正在批量导入文件...", "取消", 0, len(filenames), self)
        progress_dialog.setWindowTitle("批量导入进度")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.show()
        
        for i, filename in enumerate(filenames):
            if progress_dialog.wasCanceled():
                break
                
            try:
                # 显示超快速导入对话框
                dialog = UltraFastImportDialog(filename, self)
                if dialog.exec() == QDialog.Accepted:
                    import_config = dialog.get_import_config()
                    
                    # 执行超快速导入
                    success_count, error_count = self.execute_ultra_fast_import(import_config)
                    
                    total_success += success_count
                    total_errors += error_count
                    processed_files += 1
                    
                    # 关键修改：每次导入后清除所有存储信息
                    self.clear_all_storage()
                    print(f"[DEBUG] 文件 {os.path.basename(filename)} 导入完成，已清除存储状态")
                    
            except Exception as e:
                print(f"[DEBUG] 导入文件 {filename} 失败: {str(e)}")
                total_errors += 1
                # 即使失败也要清理状态
                self.clear_all_storage()
            
            # 更新进度
            progress_dialog.setValue(i + 1)
            QApplication.processEvents()
        
        progress_dialog.close()
        
        # 显示批量导入结果
        result_msg = f"⚡📥批量导入完成！\n\n总共处理 {len(filenames)} 个文件\n成功导入 {processed_files} 个文件\n成功记录: {total_success} 条\n失败记录: {total_errors} 条"
        
        notify(self, result_msg, 'info')
        self.load_users()


class ImportUsersDialog(QDialog):
    def __init__(self, users_data, parent=None):
        super().__init__(parent)
        self.users_data = users_data
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle('选择要导入的用户')
        self.resize(800, 600)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 说明标签
        info_label = QLabel('请选择要导入的用户（已存在的用户将自动跳过）')
        layout.addWidget(info_label)
        
        # 用户表格
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.setHorizontalHeaderLabels(['导入', '用户名', '数据库文件'])
        self.table.setRowCount(len(self.users_data))
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        
        # 填充表格数据
        for row, user in enumerate(self.users_data):
            # 导入复选框
            checkbox = QCheckBox()
            checkbox.setChecked(True)
            checkbox_widget = QWidget()
            checkbox_layout = QHBoxLayout(checkbox_widget)
            checkbox_layout.addWidget(checkbox)
            checkbox_layout.setAlignment(Qt.AlignCenter)
            checkbox_layout.setContentsMargins(0, 0, 0, 0)
            self.table.setCellWidget(row, 0, checkbox_widget)
            
            # 用户名
            username_item = QTableWidgetItem(user.get('username', ''))
            username_item.setFlags(username_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 1, username_item)
            
            # 数据库文件
            db_file_item = QTableWidgetItem(user.get('db_file', f'user_{user.get("username", "")}.db'))
            db_file_item.setFlags(db_file_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, 2, db_file_item)
        
        layout.addWidget(self.table)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        select_all_btn = QPushButton('全选')
        select_all_btn.clicked.connect(lambda: self.set_all_checkboxes(True))
        button_layout.addWidget(select_all_btn)
        
        unselect_all_btn = QPushButton('全不选')
        unselect_all_btn.clicked.connect(lambda: self.set_all_checkboxes(False))
        button_layout.addWidget(unselect_all_btn)
        
        button_layout.addStretch()
        
        ok_btn = QPushButton('导入')
        ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(ok_btn)
        
        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(cancel_btn)
        
        layout.addLayout(button_layout)
    
    def set_all_checkboxes(self, checked):
        """设置所有复选框的状态"""
        for row in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(row, 0).findChild(QCheckBox)
            if checkbox:
                checkbox.setChecked(checked)
    
    def get_selected_users(self):
        """获取选中的用户数据"""
        selected_users = []
        for row in range(self.table.rowCount()):
            checkbox = self.table.cellWidget(row, 0).findChild(QCheckBox)
            if checkbox and checkbox.isChecked():
                user_data = {
                    'username': self.table.item(row, 1).text(),
                    'db_file': self.table.item(row, 2).text()
                }
                selected_users.append(user_data)
        return selected_users


# ===================== 条形码编码格式 / Code128 子集（★ v6.9）=====================
# 用户要求：「新建用户」与以后「修改列配置」时，都能逐列指定条形码的编码格式，
# 以及 Code128 用哪一个子集（A / B / C）。
#   * 格式：自动（13 位数字走 EAN13，其余走 Code128）/ Code128 / EAN13 / EAN8 / UPC-A / Code39
#   * 子集：仅 Code128 有效 —— A=大写+数字+控制符；B=大小写+数字+符号；C=纯数字
#     选「自动」＝交给 python-barcode 自动切换字符集（条码最短，推荐）。
#   ★ v6.17（用户要求）：「符合的都改成用户要求的，不符合的才改成自动」——
#     C 不再要求偶数位：纯数字一律用 C（偶数位全程 C；奇数位按 Code128 规范让
#     最后一位转一次 B，否则那一位会被丢掉）；只有含非数字字符才回退「自动」。
BARCODE_FORMAT_OPTIONS = [
    ('自动（13位数字用EAN13，其余Code128）', 'auto'),
    ('Code128', 'code128'),
    ('EAN13', 'ean13'),
    ('EAN8', 'ean8'),
    ('UPC-A', 'upca'),
    ('Code39', 'code39'),
]
BARCODE_SUBSET_OPTIONS = [
    ('自动', 'auto'),
    ('A', 'A'),
    ('B', 'B'),
    ('C', 'C'),
]
# 只有这些格式才用得上 Code128 子集（其余格式把子集下拉置灰）
BARCODE_SUBSET_FORMATS = ('auto', 'code128')
# ===================== 数据列表「显示列」设置（★ v6.10）=====================
# 用户要求：可以只显示部分列（不想看那么多列时），但添加/保存的数据都是完整的。
# 所以「隐藏列」只影响【显示】，数据照常录入 / 保存 / 搜索 / 导出。
#   * 存本用户库 config 表（key/value）——跟着用户库走，换用户互不影响；
#   * 键名必须【不能以 col_ 开头】（get_columns_config 用 LIKE "col_%" 扫列配置，
#     写成 col_xxx 会被误认成一列的中文名）；
#   * 存被【隐藏】的列名列表（而不是“显示”的），这样新增的列默认就是显示的。
HIDDEN_COLUMNS_CONFIG_KEY = 'hidden_columns'
# ★ v6.13：数据列表在数据列后面还会画「××条形码 / ××二维码」图列（图片列），
#   用户要求这些也要能在「显示列设置」里单独勾选。图列的键＝<列名>#barcode /
#   <列名>#qrcode，和数据列名一起存在同一个 hidden_columns 列表里（这些键不会和
#   真实列名撞车，也不参与任何数据读写，只用于“画不画这张图”）。
DISPLAY_BARCODE_KEY_SUFFIX = '#barcode'
DISPLAY_QRCODE_KEY_SUFFIX = '#qrcode'
# ===================== 详细信息「显示项」设置（★ v6.22）=====================
# 用户要求：「设置 增加 详细信息，哪些可以暂时不显示，就好像之前哪些列不显示一样的逻辑，
#   因为有时候可能只需要显示一些信息，包括可能只需要显示二维码或者一维码 或者 哪些信息，
#   可能不需要全部都显示。」
# 与「显示列设置」（hidden_columns）同一套逻辑：**存被隐藏的键**（空＝全部显示），
# 存在本用户库 config 表的 HIDDEN_DETAIL_CONFIG_KEY 里，跟着用户库走。
#   键前缀：field:<列名>（基本信息里的字段）/ barcode:<列名>（该列的条码块）/
#          qrcode:<列名>（该列的二维码块）
#   ★ 同样**不能**以 col_ 开头（get_columns_config 用 LIKE "col_%" 扫列配置）。
#   ★ 只影响「🔎详细信息」页画不画，数据/列表/打印/复制/导出都照旧完整。
HIDDEN_DETAIL_CONFIG_KEY = 'hidden_detail_items'
DETAIL_FIELD_PREFIX = 'field:'
DETAIL_BARCODE_PREFIX = 'barcode:'
DETAIL_QRCODE_PREFIX = 'qrcode:'
# 详情页三组（顺序＝详情页里的先后顺序）
DETAIL_GROUPS = (
    ('field', '📋 基本信息（字段）'),
    ('barcode', '▮ 条形码信息'),
    ('qrcode', '▦ 二维码信息'),
)


def display_image_entries(columns_config):
    """把列配置里带「条形码/二维码」的列展开成图列条目（★ v6.13，模块级单一来源）

    数据列表在数据列后面还会画 `<中文名>条形码` / `<中文名>二维码` 两张图列；这里把它们
    也当成可选条目（键＝`列名#barcode` / `列名#qrcode`），供「显示列设置」与数据列表共用。
    条目里带上来源列的出码格式/子集，画图时直接用。
    """
    out = []
    for col in (columns_config or []):
        if not col or not col.get('name'):
            continue
        name = col['name']
        label = col.get('label', name)
        if col.get('is_barcode', False):
            out.append({'name': name + DISPLAY_BARCODE_KEY_SUFFIX,
                        'label': f'{label}条形码', 'kind': 'barcode',
                        'source': name, 'source_label': label,
                        'barcode_format': col.get('barcode_format', 'auto'),
                        'barcode_subset': col.get('barcode_subset', 'auto')})
        if col.get('is_qrcode', False):
            out.append({'name': name + DISPLAY_QRCODE_KEY_SUFFIX,
                        'label': f'{label}二维码', 'kind': 'qrcode',
                        'source': name, 'source_label': label})
    return out


def display_entries_with_images(columns_config):
    """数据列 + 它的条形码/二维码图列（图列紧跟在来源列后面）（★ v6.13）"""
    entries = []
    for col in (columns_config or []):
        if not col or not col.get('name'):
            continue
        entries.append(dict(col))
        entries.extend(display_image_entries([col]))
    return entries


def display_entry_label(columns_config, key):
    """显示列设置里的键 → 中文名（★ v6.13，状态栏/提示用）"""
    for entry in display_entries_with_images(columns_config):
        if entry.get('name') == key:
            return entry.get('label', key)
    return key


def detail_item_entries(columns_config):
    """「🔎详细信息」页里可选显示的项目清单（★ v6.22，模块级单一来源）

    顺序＝详情页里的先后顺序，分三组（`DETAIL_GROUPS`）：
      * `field:<列名>`   —— 「基本信息」里的一个字段（＝一列一行）
      * `barcode:<列名>` —— 「条形码信息」里该列那一块（值 + 条码图 + 编码说明）
      * `qrcode:<列名>`  —— 「二维码信息」里该列那一块（值 + 二维码图）
    条目字段：key（存库用）/ group / label（给用户看的中文名）/ name（列名）。
    """
    out = []
    cols = [c for c in (columns_config or []) if c and c.get('name')]
    for col in cols:
        out.append({'key': DETAIL_FIELD_PREFIX + col['name'], 'group': 'field',
                    'name': col['name'], 'label': col.get('label', col['name'])})
    for col in cols:
        if col.get('is_barcode', False):
            out.append({'key': DETAIL_BARCODE_PREFIX + col['name'], 'group': 'barcode',
                        'name': col['name'],
                        'label': f"{col.get('label', col['name'])} 的条形码"})
    for col in cols:
        if col.get('is_qrcode', False):
            out.append({'key': DETAIL_QRCODE_PREFIX + col['name'], 'group': 'qrcode',
                        'name': col['name'],
                        'label': f"{col.get('label', col['name'])} 的二维码"})
    return out


def detail_entry_label(columns_config, key):
    """详细信息显示设置里的键 → 中文名（★ v6.22，状态栏/提示用）"""
    for entry in detail_item_entries(columns_config):
        if entry.get('key') == key:
            return entry.get('label', key)
    return key


def detail_group_label(group_key):
    """分组键 → 中文分组名（★ v6.22）"""
    for key, label in DETAIL_GROUPS:
        if key == group_key:
            return label
    return group_key


def sort_key_for_text(text):
    """表头排序用的键（★ v6.14）：数字按数值比，其余按**拼音**比（中文也順序），空值由调用方挑出去

    为什么要这样：
      * 按文本比会把 10 排在 2 前面（用户报的就是这类“排得不对”）；
      * 中文按 Unicode 编码点排是乱的（橙/苹/阿/香），按**拼音**排才是 A→Z 的感觉
        （阿 a / 橙 cheng / 苹 ping / 香 xiang）—— 本程序搜索/拼音列本来就用 pypinyin；
      * 元组里同位置的类型必须一致，否则 Python 3 会抛 TypeError。
    """
    s = str(text).strip()
    try:
        return (0, float(s.replace(',', '')), '')
    except (TypeError, ValueError):
        pass
    try:
        py = ''.join(lazy_pinyin(s)).casefold()
    except Exception:
        py = s.casefold()
    return (1, 0.0, py)

# 兼容手填/旧配置里的写法
_BARCODE_FORMAT_ALIASES = {
    'auto': 'auto', '': 'auto', '自动': 'auto', '默认': 'auto',
    'code128': 'code128', 'code 128': 'code128', '128': 'code128',
    'ean13': 'ean13', 'ean-13': 'ean13', 'ean13条码': 'ean13',
    'ean8': 'ean8', 'ean-8': 'ean8',
    'upca': 'upca', 'upc-a': 'upca', 'upc': 'upca',
    'code39': 'code39', 'code 39': 'code39',
}


def normalize_barcode_format(value):
    """把配置里的格式值规整成标准键（认不出来的按「自动」处理）"""
    return _BARCODE_FORMAT_ALIASES.get(str(value or '').strip().lower(), 'auto')


def normalize_barcode_subset(value):
    """把配置里的子集值规整成 auto/A/B/C（认不出来的按「自动」处理）"""
    text = str(value or '').strip().upper()
    return text if text in ('A', 'B', 'C') else 'auto'


def barcode_format_label(value):
    """格式键 → 中文名（提示/调试用）"""
    key = normalize_barcode_format(value)
    for label, val in BARCODE_FORMAT_OPTIONS:
        if val == key:
            return label
    return key


_LOCKED_CODE128_CLASS = None


def is_ascii_digits(text):
    """是不是「纯数字」（★ v6.17：只认 ASCII 的 0-9）

    **不能用 str.isdigit()**：全角「１２３４」「²²」「٣٤」它也返回 True，但这些字符
    Code128 根本编不出来（check_code 会抛 IllegalCharacterError → 条码空白）；
    只有 ASCII 的 0-9 才是 Code128-C 真正支持的“数字”。
    """
    return bool(text) and all('0' <= ch <= '9' for ch in text)


# ===================== 不符合 Code128 子集的值的标记色（★ v6.18）=====================
# 用户要求：「不符合 Code128 C 的，可以用颜色标记出来，因为背景色已经是固定的，改背景色
#   会设计太多东西，所以应该是字体颜色不一样，而且要跟背景色有反差，否则连字都看不清。」
#   → 只改【字体颜色】，不碰背景色（背景是 5 种马卡龙交替行色）；颜色必须跟这 5 种
#     行底色都有足够反差：#8B0000（深红）对它们的最低对比度 6.41:1（WCAG AA 正文要求
#     4.5:1），而普通文字色 #333333 在这些底色上是 8.09~10.57。
#   改颜色前先跑 `_scaffold\probe_contrast_colors.py` 重新核一遍对比度
#   （回归测试里也有断言盯着这条，改马卡龙色系或改标记色都会被拦下）。
BARCODE_MISMATCH_COLOR = '#8B0000'
# ★ v6.20：条码图本身是**白底**（行底色被图片完全盖住，用户根本看不见），所以“不符合
#   子集”的条码图直接换成**浅红底**最显眼 —— 黑条在 #FFCDD2 上对比度≈14:1（Scanner
#   友好），且跟 5 种马卡龙行底色都不同，一眼就能挑出来。
BARCODE_MISMATCH_BG_COLOR = '#FFCDD2'

# ===================== 条码出图分辨率（★ v6.21 修的扫描不出来）=====================
# 用户报：「Code128 C 还是不行，我有个程序需要扫 code128 C 才能识别…可以肯定是现在这个
#   程序里面生成的 C 子集还是有问题」。用 `_scaffold\probe_barcode_size.py` 量出来真因是
#   **出图被压得太小，不是编码错**：
#     值 6050532261（10 位）走 C 子集 → 原生 272x280px，每根黑条 2px；
#     旧代码在 `generate_barcode()` 里统一缩到**高 100px** ⇒ 97x100px、每根黑条只剩
#     **1px**、还多出 37 个**灰度过渡像素**、黑条从 25 根掉到 21 根（插值把 4 根条糊没了）。
#     1px 宽的条 + 灰边 ⇒ 任何扫描器都读不出来；而 B 子集同值出图 402px 宽（条更稀），
#     勉强能读 ⇒ 看起来就像「C 子集坏了」。
#   修法：**不再缩放位图**，改成让 python-barcode 直接以更高 dpi 出图（模块宽度=mm*dpi/25.4，
#     条宽永远是整数像素、无灰度）：dpi 300→条 2px，600→4px，900→7px（见探针输出）。
#   表格里要紧凑显示的地方自己缩缩略图（`barcode_preview_pixmap()`，行高仍是 100px）；
#   详情页 / 导出图片 / 打印 / HTML 报告用 `BARCODE_RENDER_SCALE` 倍分辨率（每模块 ≥4px，
#   屏幕上 1mm 多一个模块，扫描枪直接对着扫就能读）。
BARCODE_PREVIEW_HEIGHT = 100   # 表格里条码缩略图的高度（与旧版一致，行高不变）
BARCODE_RENDER_SCALE = 2       # 详情/导出/打印用的放大倍数（dpi=300*2=600）


def barcode_background_for(value, barcode_format='auto', subset='auto'):
    """这张条码图该用什么底色（★ v6.20）：装不进所选 Code128 子集 → 浅红，否则 None（白底）

    只影响**屏幕上画的图**；导出/打印仍用白底（扫描最安全，也不会把颜色印到标签上）。
    """
    if code128_subset_mismatch(value, barcode_format, subset):
        return BARCODE_MISMATCH_BG_COLOR
    return None


def code128_subset_mismatch(text, barcode_format='auto', subset='auto'):
    """值装不进该列选的 Code128 子集 → 返回一句原因；装得进 / 不涉及子集 → None（★ v6.18）

    只有「格式是 Code128 系（auto / code128）+ 子集锁定为 A/B/C」才谈得上“不符合”：
      * 子集选「自动」时 python-barcode 会自己切字符集，永远装得下，不用标记；
      * EAN13 / EAN8 / UPC-A / Code39 不涉及 Code128 子集，也不管；
      * 空值不算（没数据不是错）。
    """
    try:
        if text is None or str(text).strip() == '':
            return None
        if normalize_barcode_format(barcode_format) not in BARCODE_SUBSET_FORMATS:
            return None
        sub = normalize_barcode_subset(subset)
        if sub == 'auto':
            return None
        value = str(text).strip()
        if sub == 'C':
            if is_ascii_digits(value):
                return None
            bad = ''.join(sorted({ch for ch in value if not ('0' <= ch <= '9')}))
            return (f'Code128-C 只支持纯数字，这个值里还有「{bad[:12]}」这类非数字字符，'
                    '出码时会自动回退为「自动」子集（条码本身仍能扫）')
        from barcode.charsets import code128 as _c128
        table = _c128.A if sub == 'A' else _c128.B
        bad = ''.join(sorted({ch for ch in value if ch not in table}))
        if not bad:
            return None
        return (f'Code128-{sub} 装不下「{bad[:12]}」，'
                '出码时会自动回退为「自动」子集（条码本身仍能扫）')
    except Exception as e:      # 配置畸形也绝不能把界面搞崩
        print(f"[DEBUG] 判断 Code128 子集是否符合时出错: {e}")
        return None


def mark_barcode_mismatch(item, col_config, value):
    """把不符合该列 Code128 子集的值标成醒目的字体颜色（+ tooltip 说明原因）（★ v6.18）

    模块级函数（不挂 MainWindow）—— 表格渲染路径与测试里的替身对象都能直接用。
    返回 True 表示标了（方便测试断言）。
    """
    cfg = col_config or {}
    reason = code128_subset_mismatch(value, cfg.get('barcode_format', 'auto'),
                                    cfg.get('barcode_subset', 'auto'))
    if not reason:
        return False
    item.setForeground(QColor(BARCODE_MISMATCH_COLOR))
    item.setToolTip(f'⚠️ {reason}')
    return True


def _locked_code128_class():
    """惰性创建「锁定 Code128 子集」的子类（不在模块顶部导入 barcode，避免拖慢启动）"""
    global _LOCKED_CODE128_CLASS
    if _LOCKED_CODE128_CLASS is None:
        from barcode import Code128
        from barcode.writer import ImageWriter

        class LockedCode128(Code128):
            """固定使用某一个子集的 Code128（★ v6.9；v6.17 修两个坑）

            python-barcode 默认会为了让条码最短而在 A/B/C 之间来回切换；
            这里禁止切换，只用用户指定的子集（条码可能变长，但严格符合所选子集）。

            顺手修掉上游的两个问题：
              1. `_try_to_optimize()` 假设「起始符后面的第一个码一定是切换码」，会把
                 [105, 99, ...] 合并成 [105, ...] —— 可 99 在 C 里是**数据对「99」**，
                 于是「99」开头的数字（例如 9912345678）会被**静默吃掉两位**。
                 锁定子集时我们从不切换，不需要这种合并，直接不合并最安全。
              2. `_build()` 会改写 self._charset / self._buffer（C 里奇数位数字会把末位
                 转成 B），同一个对象再出一次码就变了。每次出码前复位成锁定值。
            """

            def __init__(self, code, charset='B', writer=None):
                super().__init__(code, writer=writer or ImageWriter())
                self._locked_charset = charset
                self._charset = charset

            def _maybe_switch_charset(self, pos):
                return []

            def _try_to_optimize(self, encoded):
                return encoded

            def _build(self):
                self._charset = self._locked_charset
                self._buffer = ''
                return super()._build()

        _LOCKED_CODE128_CLASS = LockedCode128
    return _LOCKED_CODE128_CLASS


def make_code128_object(data_str, subset='auto'):
    """创建 Code128 条码对象（★ v6.17：**装得进就按用户选的子集出码**，装不进才回退自动）

      * C：纯数字就用 C —— 偶数位全程 C；奇数位按 Code128 规范让最后一位转一次 B
        （Code128-C 每符号压两位数字，落单的那位只能这样编，不会丢数字）。
        只有含非数字字符才回退「自动」；首尾空格不参与判断。
      * A / B：数据里每个字符都在该子集的字符表里就用它；有装不进的字符才回退。
      * auto：交给 python-barcode 自动切换字符集（纯数字的结果与 C 相同，但走我们
        自己的锁定实现 —— 上游默认实现会把首对「99」误当切换码吃掉两位数字）。
    """
    from barcode import Code128
    from barcode.writer import ImageWriter

    text = str(data_str)
    sub = normalize_barcode_subset(subset)
    if sub == 'C':
        digits = text.strip()
        if is_ascii_digits(digits):
            return _locked_code128_class()(digits, 'C')
        print(f"[DEBUG] Code128-C 只能装纯数字，{digits!r} 含非数字字符，已回退为自动子集")
    elif sub in ('A', 'B'):
        from barcode.charsets import code128 as _c128
        table = _c128.A if sub == 'A' else _c128.B
        if text and all(ch in table for ch in text):
            return _locked_code128_class()(text, sub)
        print(f"[DEBUG] Code128-{sub} 不支持 {text!r} 里的字符，已回退为自动子集")
    elif is_ascii_digits(text):
        return _locked_code128_class()(text, 'C')
    return Code128(text, writer=ImageWriter())


def build_barcode_object(data_str, barcode_format='auto', subset='auto'):
    """按「编码格式 + Code128 子集」创建条码对象（★ v6.9）

    返回 (barcode 对象, 实际使用的格式, 备注或 None)。
    数据不满足所选格式时**不报错**，而是回退到 Code128 并给出备注 ——
    表格里永远能看到条码，不会因为一列设错格式就出现大片空白。
    """
    from barcode import EAN13, EAN8, UPCA, Code39
    from barcode.writer import ImageWriter

    text = str(data_str)
    fmt = normalize_barcode_format(barcode_format)
    note = None

    if fmt == 'auto':
        fmt = 'ean13' if (text.isdigit() and len(text) == 13) else 'code128'

    if fmt in ('ean13', 'ean8', 'upca'):
        need = {'ean13': 12, 'ean8': 7, 'upca': 11}[fmt]
        digits = ''.join(ch for ch in text if ch.isdigit())[:need]
        if len(digits) == need:
            cls = {'ean13': EAN13, 'ean8': EAN8, 'upca': UPCA}[fmt]
            return cls(digits, writer=ImageWriter()), fmt, note
        note = (f"{barcode_format_label(fmt)} 需要 {need} 位数字"
                "（校验位自动计算），已回退为 Code128")
        fmt = 'code128'
    elif fmt == 'code39':
        try:
            return Code39(text, writer=ImageWriter()), 'code39', note
        except Exception as e:
            print(f"[DEBUG] Code39 编码失败: {str(e)}")
            note = 'Code39 不支持该内容，已回退为 Code128'
            fmt = 'code128'

    return make_code128_object(text, subset), 'code128', note


# ===================== 「这张条码实际是用什么出的」说明（★ v6.19）=====================
# 用户曾问：「一维码 为什么没有实现 code128 C 子集方式？」—— 光看条码图根本看不出来，
#   所以把「实际用了哪个子集」直接显示出来：表格/搜索结果的**条码图上悬停看 tooltip**，
#   详细信息页在条码图下面写一行「编码：…」。这样值符不符合、有没有回退、起始符是
#   103/104/105 一目了然，不用再猜（C 编 10 位数字＝起始105＋5对＋校验，条码明显更窄）。
def barcode_display_text(data):
    """出码前把数据规整成可编码文本（★ v6.19 抽出来，供「出码」与「说明」共用）

    含非 ASCII（中文等）时转成「拼音首字母_6位哈希」——沿用最早的老逻辑，
    现在只此一处实现，免得说明与实际出码两张嘴。
    """
    text = str(data)
    if any(ord(ch) > 127 for ch in text):
        try:
            from pypinyin import lazy_pinyin
            import hashlib
            initials = ''.join([x[0] for x in lazy_pinyin(text) if x])
            digest = hashlib.md5(text.encode('utf-8')).hexdigest()[:6]
            text = f"{initials}_{digest}".upper()[:80]
        except Exception as e:
            print(f"[DEBUG] 非 ASCII 值转拼音失败，按原样出码: {e}")
    return text


def _code128_switch_names(codes):
    """列出 Code128 码元里真正“切字符集”的那些（★ v6.19）

    **不能只看 99/100/101**：码元含义随当前字符集变 —— 在 C 里 100=TO_B、101=TO_A，
    而 **99 是数据对「99」**（不是切换码）；在 B 里 99=TO_C、100 是数据字符 'ô'。
    返回切换到的子集名字列表，例如 ['B'] 表示中途切到过 B。
    """
    charset = {103: 'A', 104: 'B', 105: 'C'}.get(codes[0]) if codes else None
    out = []
    for code in codes[1:]:
        if charset == 'C':
            if code == 100:
                charset, target = 'B', 'B'
            elif code == 101:
                charset, target = 'A', 'A'
            else:
                continue
        elif code == 99:
            charset, target = 'C', 'C'
        elif charset == 'A' and code == 100:
            charset, target = 'B', 'B'
        elif charset == 'B' and code == 101:
            charset, target = 'A', 'A'
        else:
            continue
        out.append(target)
    return out


def describe_barcode_encoding(data, barcode_format='auto', subset='auto'):
    """一行中文说明「这张条码实际是用什么出的」（★ v6.19，实际构建对象后看起始符）

    例：`Code128-C 全程 C 子集（起始符 105）`
        `Code128-C，中间切了一次 B（起始符 105）`——奇数位数字，Code128 规范要求
        `Code128 自动切换（起始符 104）：所选 C 子集装不下，已回退`
        `EAN13`
    """
    if data is None or str(data).strip() == '':
        return '（空值，不出码）'
    text = barcode_display_text(data)
    try:
        obj, used, note = build_barcode_object(text, barcode_format, subset)
        if used != 'code128':
            desc = barcode_format_label(used)
        else:
            codes = list(obj.encoded)
            start = codes[0]
            name = {103: 'A', 104: 'B', 105: 'C'}.get(start, '?')
            switches = _code128_switch_names(codes)
            asked = normalize_barcode_subset(subset)
            if asked == 'auto':
                desc = f'Code128 自动切换字符集（起始符 {start}＝{name}）'
            elif code128_subset_mismatch(text, barcode_format, subset):
                desc = (f'Code128 自动切换（起始符 {start}＝{name}）：'
                        f'所选 {asked} 子集装不下，已回退')
            elif switches:
                desc = (f'Code128-{asked}，中间切了一次 {"、".join(switches)}'
                        f'（起始符 {start}）')
            else:
                desc = f'Code128-{asked} 全程 {asked} 子集（起始符 {start}）'
        if note:
            desc += f'；{note}'
        return desc
    except Exception as e:
        return f'（无法出码：{type(e).__name__}: {e}）'


class BarcodeZoomLabel(QLabel):
    """表格里的条码**缩略图**控件（★ v6.21）—— 双击就把这张条码放大出来

    为什么要它：表格里的缩略图只有 100px 高、条太细，扫描器/别家程序读不出来；
    `generate_barcode()` 现在会按 2 倍分辨率重新出一张 **大图**（每模块 ≈1.2mm），
    双击这张缩略图就能看到大图、直接对着屏幕扫，或者「另存为 PNG」交给别家程序读。

    事件必须 `accept()`：否则双击会穿透给表格的 `cellDoubleClicked`（那会跳去详情页）。
    """
    doubleClicked = Signal()

    def mouseDoubleClickEvent(self, event):
        self.doubleClicked.emit()
        event.accept()


class BarcodeZoomDialog(QDialog):
    """条码放大窗口（★ v6.21）：大图 + 编码说明 + 另存为 PNG

    `image` 必须是**已经按高分辨率出好的** QImage（如 `scale=BARCODE_RENDER_SCALE`），
    这里只负责显示与保存，不再做任何缩放（缩放会把条糊掉 —— v6.21 修的就是这个）。
    """
    MAX_SHOW_WIDTH = 900     # 屏幕上一行放不下时只缩显示，保存时仍存原图

    def __init__(self, parent, image, value='', encoding='', background=None):
        super().__init__(parent)
        self._image = image
        self._value = str(value)
        self._encoding = encoding or ''
        self.setWindowTitle(f'条码放大 - {self._value}' if self._value else '条码放大')
        self.setMinimumWidth(420)
        layout = QVBoxLayout(self)

        head = QLabel(f'<b>{self._value}</b>' if self._value else '')
        head.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(head)

        img_label = QLabel()
        img_label.setAlignment(Qt.AlignCenter)
        pixmap = QPixmap.fromImage(image)
        if pixmap.width() > self.MAX_SHOW_WIDTH:
            pixmap = pixmap.scaledToWidth(self.MAX_SHOW_WIDTH, Qt.SmoothTransformation)
        img_label.setPixmap(pixmap)
        img_label.setStyleSheet(f"background-color: {background or 'white'};")
        img_label.setToolTip('这张是放大后的条码，可以直接对着屏幕扫')
        img_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(img_label)

        if self._encoding:
            desc = QLabel(f'编码：{self._encoding}')
            desc.setStyleSheet('color: #666; font-size: 11px;')
            desc.setWordWrap(True)
            layout.addWidget(desc)

        size_label = QLabel(f'图片尺寸：{image.width()}×{image.height()} 像素'
                            f'　（每根条 {max(1, round(image.width() / 90))} 像素左右）')
        size_label.setStyleSheet('color: #888; font-size: 11px;')
        layout.addWidget(size_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        save_btn = QPushButton('💾 另存为 PNG')
        save_btn.clicked.connect(self.save_png)
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(save_btn)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

        self.resize(min(960, image.width() + 60), min(760, image.height() + 190))

    def save_png(self):
        """另存为 PNG（存的是原始大图，不经任何缩放）"""
        default = f'{self._value}_barcode.png' if self._value else 'barcode.png'
        filename, _ = QFileDialog.getSaveFileName(self, '保存条码图片', default,
                                                  'PNG图片 (*.png)')
        if not filename:
            return
        try:
            if self._image.save(filename):
                notify(self, f'条码已保存到：\n{filename}', 'success')
            else:
                notify(self, '保存失败：无法写入该文件', 'error')
        except Exception as e:
            notify(self, f'保存失败：{str(e)}', 'error')


class ColumnConfigDialog(QDialog):
    def __init__(self, parent=None):
        # ★ v6.5：支持传入父窗口（登录后从主窗口打开时居中显示/模态跟随）
        super().__init__(parent)
        self.setWindowTitle('列配置')
        self.resize(1080, 520)  # ★ v6.9：多了「条码格式 / Code128 子集」两列
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 说明标签
        info_label = QLabel('配置数据表的列结构。至少需要一列，可以设置列名、显示名称、数据类型和特殊属性。\n'
                            '勾选「条形码」的列还可以选择编码格式（自动/Code128/EAN13/EAN8/UPC-A/Code39）'
                            '与 Code128 子集（自动/A/B/C）。')
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # 表格用于配置列
        self.table = QTableWidget()
        self.table.setColumnCount(9)
        self.table.setHorizontalHeaderLabels(['列名', '显示名称', '数据类型', '必填', '唯一', '条形码', '二维码',
                                              '条码格式', 'Code128子集'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        layout.addWidget(self.table)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.add_btn = QPushButton('添加列 (+)')
        self.add_btn.clicked.connect(self.add_column)
        button_layout.addWidget(self.add_btn)
        
        self.remove_btn = QPushButton('删除列 (-)')
        self.remove_btn.clicked.connect(self.remove_column)
        button_layout.addWidget(self.remove_btn)
        
        button_layout.addStretch()
        
        self.ok_btn = QPushButton('确定')
        self.ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.ok_btn)
        
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)
        
        # 初始添加一行
        self.add_column()
    
    # ---------- 条形码格式 / Code128 子集（★ v6.9）----------
    def _make_format_combo(self):
        combo = QComboBox()
        for label, value in BARCODE_FORMAT_OPTIONS:
            combo.addItem(label, value)
        combo.setToolTip('条形码编码格式：\n'
                         '· 自动：13 位数字用 EAN13，其余用 Code128（推荐）\n'
                         '· EAN13/EAN8/UPC-A：只支持数字，位数不足会自动回退 Code128\n'
                         '· Code39：只支持大写字母/数字/-. $/+%，小写会自动转大写\n'
                         '· 任何格式数据不满足时都会回退 Code128，不会出现空白条码')
        return combo

    def _make_subset_combo(self):
        combo = QComboBox()
        for label, value in BARCODE_SUBSET_OPTIONS:
            combo.addItem(label, value)
        combo.setToolTip('仅 Code128 有效：A=大写+数字+控制符，B=大小写+数字+符号，'
                         'C=纯数字（任意位数；奇数位时最后一位按规范转 B）\n'
                         '能装进所选子集就按你的设置出码，装不进才回退「自动」')
        return combo

    def _apply_barcode_column_widths(self):
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(2, 120)   # 加宽以适应中文
        for i in range(3, 7):
            self.table.setColumnWidth(i, 60)
        self.table.setColumnWidth(7, 200)   # ★ v6.9 条码格式
        self.table.setColumnWidth(8, 110)   # ★ v6.9 Code128 子集

    def _row_of_widget(self, widget):
        """现查某个单元格控件所在行（删行后行号会变，所以不能缓存行号）"""
        for r in range(self.table.rowCount()):
            for c in range(self.table.columnCount()):
                if self.table.cellWidget(r, c) is widget:
                    return r
        return -1

    def _sync_subset_enabled(self, format_combo):
        """只有 Code128 系格式才用得上子集，其它格式把子集下拉置灰"""
        row = self._row_of_widget(format_combo)
        if row < 0:
            return
        subset_combo = self.table.cellWidget(row, 8)
        if subset_combo is None:
            return
        subset_combo.setEnabled(
            normalize_barcode_format(format_combo.currentData()) in BARCODE_SUBSET_FORMATS)

    def add_column(self):
        row = self.table.rowCount()
        self.table.insertRow(row)

        # ★ v6.7：默认列名/中文名避开已存在的名字，避免重名列（重名会让建表/迁移失败）
        existing_names = set()
        existing_labels = set()
        for r in range(self.table.rowCount()):
            wname = self.table.cellWidget(r, 0)
            wlabel = self.table.cellWidget(r, 1)
            if wname is not None and wname.text().strip():
                existing_names.add(wname.text().strip())
            if wlabel is not None and wlabel.text().strip():
                existing_labels.add(wlabel.text().strip())
        n = row + 1
        while f'column_{n}' in existing_names:
            n += 1
        m = n
        while f'列 {m}' in existing_labels:
            m += 1

        # 列名
        col_name = QLineEdit(f'column_{n}')
        self.table.setCellWidget(row, 0, col_name)
        
        # 显示名称
        col_label = QLineEdit(f'列 {m}')
        self.table.setCellWidget(row, 1, col_label)
        
        # 数据类型 - 增加EAN13选项
        data_type = QComboBox()
        data_type.addItems(['文本(TEXT)', '整数(INTEGER)', '小数(REAL)', '二进制(BLOB)', 'EAN13条码'])
        self.table.setCellWidget(row, 2, data_type)
        
        # 必填
        required_check = QCheckBox()
        self.table.setCellWidget(row, 3, required_check)
        
        # 唯一
        unique_check = QCheckBox()
        self.table.setCellWidget(row, 4, unique_check)
        
        # 条形码
        barcode_check = QCheckBox()
        self.table.setCellWidget(row, 5, barcode_check)
        
        # 二维码
        qrcode_check = QCheckBox()
        self.table.setCellWidget(row, 6, qrcode_check)
        
        # ★ v6.9：条形码编码格式 / Code128 子集
        format_combo = self._make_format_combo()
        self.table.setCellWidget(row, 7, format_combo)
        subset_combo = self._make_subset_combo()
        self.table.setCellWidget(row, 8, subset_combo)
        self._sync_subset_enabled(format_combo)
        format_combo.currentIndexChanged.connect(
            lambda _i, c=format_combo: self._sync_subset_enabled(c))
        
        # 设置列宽
        self._apply_barcode_column_widths()
    
    def remove_column(self):
        current_row = self.table.currentRow()
        if current_row < 0:
            notify(self, '请先选中要删除的那一行', 'info')
            return
        # ★ v6.7：不允许删到一列不剩（否则确定后会得到空配置，白忙一场）
        if self.table.rowCount() <= 1:
            notify(self, '至少要保留一列', 'warn')
            return
        self.table.removeRow(current_row)
    
    def get_columns_config(self):
        columns_config = []
        for row in range(self.table.rowCount()):
            col_name = self.table.cellWidget(row, 0).text().strip()
            if not col_name:
                continue
            
            # 处理数据类型选项
            type_text = self.table.cellWidget(row, 2).currentText()
            if type_text == 'EAN13条码':
                col_type = 'EAN13'  # 特殊类型，不是标准SQLite类型
            elif '(' in type_text and ')' in type_text:
                col_type = type_text.split('(')[1].split(')')[0]
            else:
                col_type = 'TEXT'  # 默认值
            
            config = {
                'name': col_name,
                'label': self.table.cellWidget(row, 1).text().strip(),
                'type': col_type,
                'is_required': self.table.cellWidget(row, 3).isChecked(),
                'is_unique': self.table.cellWidget(row, 4).isChecked(),
                'is_barcode': self.table.cellWidget(row, 5).isChecked(),
                'is_qrcode': self.table.cellWidget(row, 6).isChecked(),
                # ★ v6.9：条形码编码格式 / Code128 子集
                'barcode_format': normalize_barcode_format(
                    self._combo_value(row, 7)),
                'barcode_subset': normalize_barcode_subset(
                    self._combo_value(row, 8)),
            }
            columns_config.append(config)
        
        return columns_config

    def _combo_value(self, row, col, default='auto'):
        """取单元格里下拉框的当前值（控件缺失时给默认值）"""
        combo = self.table.cellWidget(row, col)
        if combo is None:
            return default
        value = combo.currentData()
        return default if value is None else value

    def load_existing_config(self, columns_config):
        """加载现有的列配置到对话框中"""
        # 清除当前所有行
        while self.table.rowCount() > 0:
            self.table.removeRow(0)

        # ★ v6.7：拿不到现有配置时也保留一行默认列。
        #   否则对话框是空白的，用户随手填两列就会把整张表的列配置换掉
        #   （实测就因此把“定位器ID”这一列换成了 column_1/column_2， 70 条数据被清空）。
        if not columns_config:
            self.add_column()
            return
        
        # 添加配置行
        for col_config in columns_config:
            row = self.table.rowCount()
            self.table.insertRow(row)
            
            # 列名
            col_name = QLineEdit(col_config.get('name', f'column_{row+1}'))
            self.table.setCellWidget(row, 0, col_name)
            
            # 显示名称
            col_label = QLineEdit(col_config.get('label', f'列 {row+1}'))
            self.table.setCellWidget(row, 1, col_label)
            
            # 数据类型
            data_type = QComboBox()
            data_type.addItems(['文本(TEXT)', '整数(INTEGER)', '小数(REAL)', '二进制(BLOB)', 'EAN13条码'])
            
            # 设置当前类型
            current_type = col_config.get('type', 'TEXT')
            if current_type == 'EAN13':
                data_type.setCurrentText('EAN13条码')
            elif current_type in ['INTEGER', 'REAL', 'BLOB']:
                data_type.setCurrentText(f'{current_type}({current_type})')
            else:
                data_type.setCurrentText('文本(TEXT)')
            
            self.table.setCellWidget(row, 2, data_type)
            
            # 必填
            required_check = QCheckBox()
            required_check.setChecked(col_config.get('is_required', False))
            self.table.setCellWidget(row, 3, required_check)
            
            # 唯一
            unique_check = QCheckBox()
            unique_check.setChecked(col_config.get('is_unique', False))
            self.table.setCellWidget(row, 4, unique_check)
            
            # 条形码
            barcode_check = QCheckBox()
            barcode_check.setChecked(col_config.get('is_barcode', False))
            self.table.setCellWidget(row, 5, barcode_check)
            
            # 二维码
            qrcode_check = QCheckBox()
            qrcode_check.setChecked(col_config.get('is_qrcode', False))
            self.table.setCellWidget(row, 6, qrcode_check)

            # ★ v6.9：条形码编码格式 / Code128 子集（旧配置没这两项 → 自动）
            format_combo = self._make_format_combo()
            fmt_idx = format_combo.findData(
                normalize_barcode_format(col_config.get('barcode_format', 'auto')))
            if fmt_idx >= 0:
                format_combo.setCurrentIndex(fmt_idx)
            self.table.setCellWidget(row, 7, format_combo)

            subset_combo = self._make_subset_combo()
            sub_idx = subset_combo.findData(
                normalize_barcode_subset(col_config.get('barcode_subset', 'auto')))
            if sub_idx >= 0:
                subset_combo.setCurrentIndex(sub_idx)
            self.table.setCellWidget(row, 8, subset_combo)

            self._sync_subset_enabled(format_combo)
            format_combo.currentIndexChanged.connect(
                lambda _i, c=format_combo: self._sync_subset_enabled(c))
        
        # 设置列宽
        self._apply_barcode_column_widths()


class DetailVisibilityDialog(QDialog):
    """「详细信息显示设置」对话框（★ v6.22）

    用户需求：详情页有时候只需要看一部分 —— 例如只想看二维码、或只想看一维码、
    或只想看某几个字段，不想把一长串字段全铺出来。逻辑与「显示列设置」一致：

      * 取消勾选 = 该项**不在「🔎详细信息」页里显示**（字段 / 该列的条码块 / 该列的二维码块）；
      * **数据一点不动**：数据列表、录入编辑、搜索、统计、打印、复制、导出都还是完整的；
      * 三组（基本信息字段 / 条形码 / 二维码）各自成组，组上面那个父节点勾一下＝整组开关；
      * 存在本用户库 config 表的 hidden_detail_items 键里，下次打开还是这套；换用户各管各的。
    """

    def __init__(self, entries, hidden_keys=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('详细信息显示设置')
        self.resize(540, 640)
        self.entries = [e for e in (entries or []) if e.get('key')]
        self._hidden = set(hidden_keys or [])

        layout = QVBoxLayout()
        self.setLayout(layout)

        info = QLabel(
            '勾选要在「🔎详细信息」页里显示的项目：\n'
            '基本信息里的每个字段、每一列的条形码、每一列的二维码，都可以单独取消勾选。\n'
            '取消勾选只是不在详情页里显示 —— 数据完全不受影响：\n'
            '数据列表、录入编辑、搜索、统计、打印、复制、导出都还是完整的。\n'
            '组名（基本信息 / 条形码 / 二维码）上点一下＝整组一起开关。')
        info.setWordWrap(True)
        layout.addWidget(info)

        # 常用场景的快捷按钮（用户：「可能只需要显示二维码或者一维码」）
        quick1 = QHBoxLayout()
        all_btn = QPushButton('✅全部显示')
        all_btn.clicked.connect(lambda: self.set_all(True))
        quick1.addWidget(all_btn)
        none_btn = QPushButton('🚫全部隐藏')
        none_btn.clicked.connect(lambda: self.set_all(False))
        quick1.addWidget(none_btn)
        invert_btn = QPushButton('🔁反选')
        invert_btn.clicked.connect(self.invert_selection)
        quick1.addWidget(invert_btn)
        quick1.addStretch()
        layout.addLayout(quick1)

        quick2 = QHBoxLayout()
        only_basic_btn = QPushButton('📋只看基本信息')
        only_basic_btn.clicked.connect(lambda: self.only_group('field'))
        quick2.addWidget(only_basic_btn)
        only_barcode_btn = QPushButton('▮只看一维码')
        only_barcode_btn.clicked.connect(lambda: self.only_group('barcode'))
        quick2.addWidget(only_barcode_btn)
        only_qrcode_btn = QPushButton('▦只看二维码')
        only_qrcode_btn.clicked.connect(lambda: self.only_group('qrcode'))
        quick2.addWidget(only_qrcode_btn)
        quick2.addStretch()
        layout.addLayout(quick2)

        # 清单（树：顶层＝分组，子项＝具体项目，子项可勾选；父项自动三态）
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setToolTip('勾选要显示的项目；分组名上点一下＝整组开关')
        for group_key, group_label in DETAIL_GROUPS:
            group_entries = [e for e in self.entries if e.get('group') == group_key]
            if not group_entries:
                continue
            top = QTreeWidgetItem([f'{group_label}（{len(group_entries)} 项）'])
            top.setFlags(top.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            top.setCheckState(0, Qt.Checked)
            top.setData(0, Qt.UserRole, None)   # 分组本身不是可保存的键
            for entry in group_entries:
                child = QTreeWidgetItem([entry.get('label', entry['key'])])
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)
                child.setCheckState(0, Qt.Unchecked if entry['key'] in self._hidden
                                    else Qt.Checked)
                child.setData(0, Qt.UserRole, entry['key'])
                child.setToolTip(0, entry['key'])
                top.addChild(child)
            self.tree.addTopLevelItem(top)
        self.tree.expandAll()
        self.tree.itemChanged.connect(self._update_summary)
        layout.addWidget(self.tree)

        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)

        button_row = QHBoxLayout()
        button_row.addStretch()
        ok_btn = QPushButton('确定')
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        button_row.addWidget(ok_btn)
        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

        self._update_summary()

    # ---------- 勾选状态读写 ----------

    def _leaf_items(self):
        """所有可勾选的子项（分组节点不在内）"""
        items = []
        for i in range(self.tree.topLevelItemCount()):
            top = self.tree.topLevelItem(i)
            for j in range(top.childCount()):
                items.append(top.child(j))
        return items

    def checked_keys(self):
        """当前勾选（要显示）的项目键"""
        return [it.data(0, Qt.UserRole) for it in self._leaf_items()
                if it.checkState(0) == Qt.Checked and it.data(0, Qt.UserRole)]

    def get_hidden_items(self):
        """要隐藏（未勾选）的项目键列表"""
        checked = set(self.checked_keys())
        return [e['key'] for e in self.entries if e['key'] not in checked]

    def set_all(self, checked):
        self._set_checked(None, checked)

    def _set_checked(self, keys, checked):
        """把指定键的子项设为 (不)勾选；keys=None 表示全部子项"""
        keys = None if keys is None else set(keys)
        state = Qt.Checked if checked else Qt.Unchecked
        self.tree.blockSignals(True)
        try:
            for item in self._leaf_items():
                key = item.data(0, Qt.UserRole)
                if keys is None or key in keys:
                    item.setCheckState(0, state)
        finally:
            self.tree.blockSignals(False)
        self._update_summary()

    def invert_selection(self):
        self.tree.blockSignals(True)
        try:
            for item in self._leaf_items():
                item.setCheckState(0, Qt.Unchecked if item.checkState(0) == Qt.Checked
                                   else Qt.Checked)
        finally:
            self.tree.blockSignals(False)
        self._update_summary()

    def only_group(self, group_key):
        """只显示某一组（📋只看基本信息 / ▮只看一维码 / ▦只看二维码）"""
        keys = {e['key'] for e in self.entries if e.get('group') == group_key}
        if not keys:
            notify(self, f'当前没有可显示的「{detail_group_label(group_key)}」项目'
                         f'（列配置里没有勾这一类）', 'warn')
            return
        self.tree.blockSignals(True)
        try:
            for item in self._leaf_items():
                item.setCheckState(0, Qt.Checked if item.data(0, Qt.UserRole) in keys
                                   else Qt.Unchecked)
        finally:
            self.tree.blockSignals(False)
        self._update_summary()

    # ---------- 统计 / 确定 ----------

    def _update_summary(self, *_args):
        shown = len(self.checked_keys())
        total = len(self.entries)
        # 统计行里用短名（分组标题带「（字段）」这类后缀，放统计里太啰嗦）
        short = {'field': '基本信息', 'barcode': '条形码', 'qrcode': '二维码'}
        parts = []
        for group_key in ('field', 'barcode', 'qrcode'):
            n = len([e for e in self.entries if e.get('group') == group_key])
            if n:
                parts.append(f'{short[group_key]} {n}')
        extra = '（' + ' · '.join(parts) + '）' if parts else ''
        if total and shown == 0:
            self.summary_label.setText(
                f'共 {total} 项{extra} ｜ ⚠️ 一项都没勾：至少勾一项才能确定')
        elif shown == total:
            self.summary_label.setText(f'共 {total} 项{extra} ｜ 全部显示')
        else:
            self.summary_label.setText(
                f'共 {total} 项{extra} ｜ 显示 {shown} 项，隐藏 {total - shown} 项')

    def _on_accept(self):
        """一项都不勾＝详情页空着，不让确定（与「显示列设置」同样的兜底）"""
        if not self.checked_keys():
            notify(self, '至少要勾选一项显示（否则详细页会整页空着）', 'warn')
            return
        self.accept()


class ColumnVisibilityDialog(QDialog):
    """「显示列设置」对话框（★ v6.10）

    用户需求：有时候不想在列表里显示全部列，只显示一部分就够 —— 但**添加进去的
    数据必须是完整的**。所以这里只挑「数据列表里显示哪些列」：

      * 取消勾选 = 该列不在数据列表里显示（连带它的条形码/二维码图也不显示）；
      * **数据本身完全不受影响**：录入表单、详情、打印、复制、导出、搜索
        仍然是全部列，什么都不丢；
      * 存在本用户库 config 表里，下次打开还是这套显示列；换用户各管各的。
    """

    def __init__(self, columns_config, hidden_columns=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('显示列设置')
        self.resize(460, 560)

        self.columns_config = [c for c in (columns_config or []) if c.get('name')]
        # ★ v6.13：条形码/二维码图列也列进来（紧跟在它的来源列后面）。数据列表里画的就
        #   是这些条目，所以这里用同一套展开逻辑（模块级 display_entries_with_images）。
        self.entries = display_entries_with_images(self.columns_config)
        self._hidden = set(hidden_columns or [])

        layout = QVBoxLayout()
        self.setLayout(layout)

        info_label = QLabel(
            '勾选要显示在「📊数据列表」里的列。\n'
            '条形码 / 二维码 图列也在这里（要是有的话），可以单独勾选。\n'
            '没勾的列只是不显示，数据照常录入、保存、搜索、导出；\n'
            '详情/打印/复制/导出仍然是完整数据（不会丢字段）。\n'
            '设置保存在当前用户的库里，下次打开还是这套显示列。')
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # 快捷按钮行
        quick_row = QHBoxLayout()
        all_btn = QPushButton('✅全部显示')
        all_btn.clicked.connect(lambda: self.set_all(True))
        quick_row.addWidget(all_btn)

        none_btn = QPushButton('🚫全部隐藏')
        none_btn.clicked.connect(lambda: self.set_all(False))
        quick_row.addWidget(none_btn)

        invert_btn = QPushButton('🔁反选')
        invert_btn.clicked.connect(self.invert_selection)
        quick_row.addWidget(invert_btn)
        quick_row.addStretch()
        layout.addLayout(quick_row)

        # 列清单（勾选 = 显示）
        self.list_widget = QListWidget()
        for col in self.entries:
            item = QListWidgetItem(self._item_text(col))
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(Qt.Unchecked if col['name'] in self._hidden else Qt.Checked)
            item.setData(Qt.UserRole, col['name'])
            self.list_widget.addItem(item)
        self.list_widget.itemChanged.connect(self._update_summary)
        layout.addWidget(self.list_widget)

        # 统计 + 按钮
        self.summary_label = QLabel()
        layout.addWidget(self.summary_label)

        button_row = QHBoxLayout()
        button_row.addStretch()

        ok_btn = QPushButton('确定')
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self._on_accept)
        button_row.addWidget(ok_btn)

        cancel_btn = QPushButton('取消')
        cancel_btn.clicked.connect(self.reject)
        button_row.addWidget(cancel_btn)
        layout.addLayout(button_row)

        self._update_summary()

    @staticmethod
    def _item_text(col):
        """一行的显示文案：中文名（列名 / 图列）+ 属性标记（★ v6.13 支持图列）"""
        kind = col.get('kind')
        if kind == 'barcode':
            src = col.get('source_label') or col.get('source')
            return f"{col.get('label', col['name'])}    (条形码图 · 来自「{src}」)"
        if kind == 'qrcode':
            src = col.get('source_label') or col.get('source')
            return f"{col.get('label', col['name'])}    (二维码图 · 来自「{src}」)"
        text = f"{col.get('label', col['name'])}    ({col['name']})"
        tags = []
        if col.get('is_barcode', False):
            tags.append('条形码')
        if col.get('is_qrcode', False):
            tags.append('二维码')
        if col.get('is_required', False):
            tags.append('必填')
        if col.get('is_unique', False):
            tags.append('唯一')
        if tags:
            text += '  【' + '·'.join(tags) + '】'
        return text

    def checked_names(self):
        """当前勾选（要显示）的列名列表"""
        names = []
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            if item.checkState() == Qt.Checked:
                names.append(item.data(Qt.UserRole))
        return names

    def get_hidden_columns(self):
        """要隐藏（未勾选）的键列表（★ v6.13：含条形码/二维码图列键 #barcode/#qrcode）"""
        checked = set(self.checked_names())
        return [e['name'] for e in self.entries if e['name'] not in checked]

    def set_all(self, checked):
        state = Qt.Checked if checked else Qt.Unchecked
        self.list_widget.blockSignals(True)
        for row in range(self.list_widget.count()):
            self.list_widget.item(row).setCheckState(state)
        self.list_widget.blockSignals(False)
        self._update_summary()

    def invert_selection(self):
        self.list_widget.blockSignals(True)
        for row in range(self.list_widget.count()):
            item = self.list_widget.item(row)
            item.setCheckState(Qt.Unchecked if item.checkState() == Qt.Checked else Qt.Checked)
        self.list_widget.blockSignals(False)
        self._update_summary()

    def _update_summary(self, *_args):
        shown = len(self.checked_names())
        total = self.list_widget.count()
        img_total = len([e for e in self.entries if e.get('kind')])
        extra = f'（{total - img_total} 列 + {img_total} 张图）' if img_total else ''
        if shown == 0:
            self.summary_label.setText(f'共 {total} 项{extra} ｜ ⚠️ 一项都没勾：至少保留一项才能确定')
        elif shown == total:
            self.summary_label.setText(f'共 {total} 项{extra} ｜ 全部显示')
        else:
            self.summary_label.setText(
                f'共 {total} 项{extra} ｜ 显示 {shown} 项，隐藏 {total - shown} 项')

    def _on_accept(self):
        """确定前兜底：一列都不勾等于列表空着，不让确定（★ 避免“表格全空”的懵状态）

        ★ v6.13：要求至少勾选一列**数据列** —— 只勾条形码/二维码图列也不行：
        数据列全隐藏时 `_visible_columns()` 会兜底显示全部，用户会以为设置没生效。
        """
        checked = set(self.checked_names())
        if not [e for e in self.entries if e['name'] in checked and not e.get('kind')]:
            notify(self, '至少要勾选一列数据列显示（否则数据列表会整张空着）', 'warn')
            return
        self.accept()


class MainWindow(QMainWindow):
    def __init__(self, username, db_file, settings, user_manager, parent_window=None):
        super().__init__()
        self.username = username
        self.db_file = db_file
        self.user_manager = user_manager
        self.parent_window = parent_window
        # ===== 全局设置（实时自动保存 + 持久化）=====
        try:
            _gs = load_global_settings()
            self.global_unified_config = _gs.get('global_unified_config', None)
            # ★ v6.11：主窗口也用同一套屏幕校验（换分辨率/拔显示器后旧坐标可能已在屏幕外）
            # ★ v6.12：摆放必须用 place_window()（resize + move）—— setGeometry 是客户区
            #   坐标，与设置里存的边框坐标差一个标题栏高度，每启动一次往上顶 31px
            _safe_geo = safe_window_geometry(_gs.get('main_geometry'), (800, 800))
            if _safe_geo:
                place_window(self, _safe_geo)
            self._start_maximized = bool(_gs.get('main_maximized', False))
        except Exception:
            self.global_unified_config = None
            self._start_maximized = False
        self.current_rowid = None
        self.is_data_loaded = False  # 新增：标记数据是否已加载
        self._closing = False  # ★ v6.11：窗口是否正在/已经关闭（关闭后不再起任何线程）
        self._switching_user = False  # ★ v6.15：是不是「切换用户登录」（决定关闭时回不回登录窗口）
        self._scroll_to_last_after_load = False  # ★ v6.6：刚添加完数据，加载完成后自动翻到最新行
        self._pending_scroll_rowid = None  # ★ v6.6：刚插入记录的 rowid（用于精确回位）
        self._detail_row = None  # ★ v6.22：详情页当前显示的是第几条（改显示设置后重画用）
        self.current_page = 0
        self.page_size = 100  # 每页显示100条记录
        self.total_pages = 0

        # ===== 多线程支持 =====
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(4)  # 最多4个并发线程
        self.active_workers = []  # 跟踪活跃的工作线程
        self._retired_workers = []  # ★ 待回收线程：run() 返回后仍需保活一拍，详见 _cleanup_worker()
        self._pool_workers = []  # ★ 线程池（QRunnable）任务保活，详见 _start_pool_worker()
        self.progress_dialog = None  # 进度对话框引用
        # ===== 多线程支持结束 =====
    
        # 确保chardet可用
        try:
            import chardet
            self.chardet = chardet
        except ImportError:
            print("[DEBUG] chardet不可用，将使用基础编码检测")
            self.chardet = None
        
        settings_config = []
        try:
            settings_config = json.loads(settings) if settings else []
        except Exception:
            settings_config = []
        if not isinstance(settings_config, list):
            settings_config = []

        self.user_db = UserDatabase(db_file)

        # ★ v6.7：列配置以【数据库真实表结构】为准。
        #   以前直接信 users.settings，一旦两者不一致（恢复过旧库备份、配置被别的
        #   路径改过、旧版本遗留等），就会出现「表格错位/满是 None」并且添加数据报
        #   “table data has no column named xxx”的没法录入状态（实测踩过）。
        db_config = self.user_db.get_columns_config() or []
        self.columns_config = db_config or settings_config
        if db_config:
            self._heal_settings_columns_config(settings_config, db_config)

        # ★ v6.10：「数据列表」显示哪些列（隐藏列只是不显示，数据照常录入/保存/导出）。
        #   存在本用户库 config 表的 hidden_columns 键里，跟着用户库走；
        #   顺手丢掉已经不存在的列名（列被删/改名后残留的），必要时回写库。
        self.hidden_columns = set()
        try:
            self.hidden_columns = set(self.user_db.get_hidden_columns())
            self._sanitize_hidden_columns()
        except Exception as e:
            print(f"[DEBUG] 读取显示列设置失败: {str(e)}")
            self.hidden_columns = set()

        # ★ v6.22：「🔎详细信息」页显示哪些项目（字段 / 条码块 / 二维码块）—— 与显示列同一套逻辑，
        #   存在本用户库 config 表的 hidden_detail_items 键里（存被隐藏的键，空＝全部显示）。
        self.hidden_detail_items = set()
        try:
            self.hidden_detail_items = set(self.user_db.get_hidden_detail_items())
            self._sanitize_hidden_detail_items()
        except Exception as e:
            print(f"[DEBUG] 读取详细信息显示设置失败: {str(e)}")
            self.hidden_detail_items = set()

        self.init_ui()
        
        # ★ v6.12：窗口显示出来后按真实边框再核一次位置（够不着就摆回屏幕中间）
        QTimer.singleShot(0, self._ensure_window_reachable)

        # 延迟加载数据，让界面先显示出来，避免启动卡顿
        QTimer.singleShot(200, self.delayed_load_data)

        icon_path = resource_path('icon.ico')
        if os.path.exists('icon.ico'):
            self.setWindowIcon(QIcon('icon.ico'))

        # 初始化时自动创建备份（使用线程池异步执行）
        self._start_pool_worker(self._background_backup)
        
        # 设置定时备份
        self.backup_timer = QTimer(self)
        self.backup_timer.timeout.connect(self.auto_backup_async)
        self.backup_timer.start(2 * 60 * 60 * 1000)  # 2小时备份一次

        # 设置定期维护
        self.maintenance_timer = QTimer(self)
        self.maintenance_timer.timeout.connect(self.periodic_maintenance)
        self.maintenance_timer.start(24 * 60 * 60 * 1000)  # 24小时执行一次

    def _heal_settings_columns_config(self, settings_config, db_config):
        """把 users.settings 里的列配置修正成与真实表结构一致（★ v6.7）

        只在两边列名不一致时才写库，不每次启动都写。
        """
        try:
            old_names = [c.get('name') for c in (settings_config or [])
                         if isinstance(c, dict)]
            new_names = [c.get('name') for c in (db_config or [])]
            if old_names == new_names:
                return
            print(f"[DEBUG] 列配置与表结构不一致，已按表结构修正: {old_names} -> {new_names}")
            self.user_manager.update_user_settings(
                self.username, json.dumps(db_config, ensure_ascii=False))
        except Exception as e:
            print(f"[DEBUG] 修正用户列配置失败: {str(e)}")

    def _sync_columns_config_with_db(self):
        """重新按真实表结构校正内存里的列配置（★ v6.7）

        返回 True 表示做了修正。用在“不能出现配置与表结构不一致”的关键入口：
        添加数据前、打开列配置对话框前、迁移完成后。
        """
        try:
            db_config = self.user_db.get_columns_config() or []
        except Exception as e:
            print(f"[DEBUG] 读取列配置失败: {str(e)}")
            return False
        if not db_config:
            return False
        if [c.get('name') for c in (self.columns_config or [])] == \
                [c.get('name') for c in db_config]:
            return False
        self.columns_config = db_config
        self._heal_settings_columns_config([], db_config)
        return True

    def _sanitize_hidden_columns(self, persist=False):
        """丢掉已经不存在的隐藏列名（★ v6.10）

        列被删除/改名后，config 里可能留着对不上的旧名字；不清掉的话，用户以后
        把新列取成同样的名字会“莫名其妙一加就是隐藏的”。变化时（或 persist=True）
        立即写回库。
        """
        try:
            names = {c.get('name') for c in (self.columns_config or []) if c.get('name')}
            # ★ v6.13：条形码/二维码图列的键也要留住（它们不在数据列名里）
            names |= {e['name'] for e in self._display_image_columns()}
            hidden = set(getattr(self, 'hidden_columns', None) or set())
            keep = {n for n in hidden if n in names}
            changed = (keep != hidden)
            self.hidden_columns = keep
            if changed or persist:
                self.user_db.set_hidden_columns(sorted(keep))
            return changed
        except Exception as e:
            print(f"[DEBUG] 校正显示列设置失败: {str(e)}")
            return False

    def _sanitize_hidden_detail_items(self, persist=False):
        """丢掉已经不存在的「详细信息显示项」键（★ v6.22）

        与 `_sanitize_hidden_columns` 同一套思路：列被删除/改名后，config 里可能留着
        对不上的旧键（field:旧列名 …）。不清掉的话，用户以后把新列取成同样的名字，
        会“莫名其妙一打开详情就看不见它”。变化时（或 persist=True）立即写回库。
        """
        try:
            valid = {e['key'] for e in detail_item_entries(self.columns_config or [])}
            hidden = set(getattr(self, 'hidden_detail_items', None) or set())
            keep = {k for k in hidden if k in valid}
            changed = (keep != hidden)
            self.hidden_detail_items = keep
            if changed or persist:
                self.user_db.set_hidden_detail_items(sorted(keep))
            return changed
        except Exception as e:
            print(f"[DEBUG] 校正详细信息显示设置失败: {str(e)}")
            return False

    def _detail_hidden_keys(self):
        """当前被隐藏的「详细信息」项目键（★ v6.22）"""
        return set(getattr(self, 'hidden_detail_items', None) or set())

    def _detail_item_visible(self, key):
        """「详细信息」里这一项要不要画（★ v6.22）"""
        return key not in self._detail_hidden_keys()

    def _all_detail_entries(self, columns_config=None):
        """「详细信息」可选项目清单（★ v6.22）"""
        cols = columns_config if columns_config is not None else (self.columns_config or [])
        return detail_item_entries(cols)

    def configure_detail_visibility(self):
        """「🔎详细信息显示设置」入口（★ v6.22）

        挑选详细信息页里显示哪些项目（字段 / 每个条码块 / 每个二维码块）→ 立即保存到
        本用户库；如果详情页正开着，马上重画一次让效果看得见。只改显示，不动数据。
        """
        try:
            self._sync_columns_config_with_db()
        except Exception as e:
            print(f"[DEBUG] 打开详细信息显示设置前同步列配置失败: {str(e)}")

        entries = self._all_detail_entries()
        if not entries:
            notify(self, '当前没有可显示的详情项目，请先到「工具→🧩修改列配置」配置列', 'warn')
            return

        self._sanitize_hidden_detail_items()
        dialog = DetailVisibilityDialog(entries, sorted(self.hidden_detail_items), self)
        if dialog.exec() != QDialog.Accepted:
            return

        hidden = dialog.get_hidden_items()
        self.hidden_detail_items = set(hidden)
        saved = self.user_db.set_hidden_detail_items(sorted(hidden))

        # 详情页正开着 → 立刻按新设置重画（没开着就不跳过去，避免打断当前操作）
        #   ★ 真实库探针（2026-09-28）发现：表格刚重画过时 currentRow() 可能是 -1，
        #     直接拿它重画会把详情页清空 ⇒ 退回「上一次显示的那一条」`_detail_row`，
        #     连它也没有就只提示、不动页面。
        try:
            if self.tab_widget.currentWidget() is self.detail_widget:
                row = self.data_table.currentRow()
                if row is None or row < 0:
                    row = getattr(self, '_detail_row', None)
                if row is not None and 0 <= row < self.data_table.rowCount():
                    self.show_item_detail(row=row)
                else:
                    notify(self, '设置已保存（详情页当前没有可显示的那一条，'
                                 '切到数据列表点一条就能看到新的显示效果）', 'info')
        except Exception as e:
            print(f"[DEBUG] 按新设置重画详情失败: {str(e)}")

        msg = (f'共 {len(entries)} 项，显示 {len(entries) - len(hidden)} 项、'
               f'隐藏 {len(hidden)} 项')
        if not saved:
            notify(self, f'✅ 详细信息显示项已更新（本次生效）：{msg}；'
                         f'但设置保存失败，下次启动会恢复', 'warn')
            return
        self.status_bar.showMessage(f'✅ 详细信息显示项已更新：{msg}', 8000)
        notify(self, f'✅ 详细信息显示项已更新并保存：{msg}\n'
                     f'（只影响「🔎详细信息」页显示，数据列表/录入/搜索/打印/导出都不变）',
               'success')

    def _visible_columns(self, columns_config=None):
        """「数据列表」要显示的列（★ v6.10）

        隐藏列只影响显示；为避免出现“整张表空着”的懵状态，一旦所有列都被隐藏
        （例如列配置大改后 hidden 名字全对上了别的列），就退化为显示全部。
        """
        cols = list(columns_config if columns_config is not None else (self.columns_config or []))
        hidden = set(getattr(self, 'hidden_columns', None) or set())
        if not hidden:
            return cols
        visible = [c for c in cols if c.get('name') not in hidden]
        return visible or cols

    def _display_image_columns(self, columns_config=None):
        """数据列表里的「条形码 / 二维码」图列清单（★ v6.13）

        数据列表在数据列后面还会画 `<列名>条形码` / `<列名>二维码` 两张图列，以前
        它们不在「显示列设置」清单里（只能跟着来源列一起显示/隐藏）。现在把它们也
        当成可选条目，键名＝`<列名>#barcode` / `<列名>#qrcode`（详见模块级
        `display_image_entries()`）。
        """
        cols = columns_config if columns_config is not None else (self.columns_config or [])
        return display_image_entries(cols)

    def _all_display_entries(self, columns_config=None):
        """「显示列设置」里的全部条目＝数据列 + 它的条形码/二维码图列（★ v6.13）"""
        cols = columns_config if columns_config is not None else (self.columns_config or [])
        return display_entries_with_images(cols)

    def _visible_image_columns(self, kind, columns_config=None):
        """该画到数据列表里的「条形码/二维码」图列（★ v6.13：键没被隐藏才画）

        kind = 'barcode' 或 'qrcode'。**这里按【全部列】算，不跟着“显示列”走** ——
        否则“隐藏某列”会连它的图列一起没了，用户就没法单独控制图列了。
        """
        hidden = set(getattr(self, 'hidden_columns', None) or set())
        return [e for e in self._display_image_columns(columns_config)
                if e.get('kind') == kind and e['name'] not in hidden]

    def _visible_display_entries(self, columns_config=None):
        """当前显示的全部条目，**与数据列表的表头顺序对齐**（★ v6.13）

        表头顺序＝数据列 → 条形码图列 → 二维码图列（与 `_update_table_with_data` 一致），
        所以这里的顺序不能变，否则“表头右键→隐藏这一列”会点错对象。
        """
        return (self._visible_columns(columns_config)
                + self._visible_image_columns('barcode', columns_config)
                + self._visible_image_columns('qrcode', columns_config))

    def _display_entry_label(self, key):
        """显示列设置里的键 → 中文名（★ v6.13，状态栏/提示用）"""
        return display_entry_label(self.columns_config or [], key)

    def configure_column_visibility(self):
        """「👁️显示列设置」入口（★ v6.10）

        挑选数据列表里显示哪些列 → 立即保存到本用户库并刷新（表格 + 统计）。
        只改显示，不动数据、不动表结构。
        """
        try:
            self._sync_columns_config_with_db()
        except Exception as e:
            print(f"[DEBUG] 打开显示列设置前同步列配置失败: {str(e)}")

        cols = [dict(c) for c in (self.columns_config or [])]
        if not cols:
            notify(self, '当前没有可显示的列，请先到「工具→🧩修改列配置」配置列', 'warn')
            return

        self._sanitize_hidden_columns()
        # ★ v6.13：对话框内部会把条形码/二维码图列也展开成条目（display_entries_with_images），
        #   所以这里传【数据列】即可。
        dialog = ColumnVisibilityDialog(cols, sorted(self.hidden_columns), self)
        if dialog.exec() != QDialog.Accepted:
            return

        hidden = dialog.get_hidden_columns()
        self.hidden_columns = set(hidden)
        saved = self.user_db.set_hidden_columns(sorted(hidden))

        entries = self._all_display_entries(cols)
        img_total = len([e for e in entries if e.get('kind')])
        shown = len([e for e in entries if e['name'] not in self.hidden_columns])
        # 表头/统计都要重建（走一次正常刷新，和自动跳行等逻辑保持一致）
        self.current_rowid = None
        try:
            self.sort_orders = {}
        except Exception:
            pass
        self.load_data()

        msg = (f'共 {len(entries)} 项（{len(cols)} 列 + {img_total} 张图），'
               f'显示 {shown} 项、隐藏 {len(hidden)} 项')
        if not saved:
            notify(self, f'✅ 显示列已更新（本次生效）：{msg}；但设置保存失败，下次启动会恢复', 'warn')
            return
        self.status_bar.showMessage(f'✅ 显示列已更新：{msg}', 8000)
        notify(self, f'✅ 显示列已更新并保存：{msg}\n（隐藏只影响数据列表显示，数据照常录入/保存/搜索/导出）',
               'success')

    def show_header_context_menu(self, position):
        """表头右键菜单（★ v6.10）：显示列设置 / 全部显示 / 隐藏当前列"""
        menu = QMenu(self)

        visibility_action = QAction('👁️ 显示列设置…', self)
        visibility_action.triggered.connect(self.configure_column_visibility)
        menu.addAction(visibility_action)

        show_all_action = QAction('✅ 全部显示', self)
        show_all_action.triggered.connect(self.show_all_columns)
        menu.addAction(show_all_action)

        # 右键点在哪一列上，就提供“隐藏这一列”
        # ★ v6.13：条目与表头一一对应（数据列 + 条形码/二维码图列），图列也能单独隐藏
        entries = self._visible_display_entries()
        col = self.data_table.horizontalHeader().logicalIndexAt(position)
        if 0 <= col < len(entries) and len(entries) > 1:
            entry = entries[col]
            name = entry.get('name')
            label = entry.get('label', name)
            hide_action = QAction(f'🚫 隐藏「{label}」', self)
            hide_action.triggered.connect(lambda _checked=False, n=name: self.hide_column(n))
            menu.addAction(hide_action)

        menu.exec(self.data_table.horizontalHeader().mapToGlobal(position))

    def _apply_hidden_columns(self, hidden, action_text):
        """保存隐藏列设置并刷新表格/统计（★ v6.10 内部公用）"""
        self.hidden_columns = set(hidden or set())
        saved = self.user_db.set_hidden_columns(sorted(self.hidden_columns))
        self.current_rowid = None
        try:
            self.sort_orders = {}
        except Exception:
            pass
        self.load_data()

        total = len(self._all_display_entries())
        shown = len(self._visible_display_entries())
        msg = f'{action_text}：共 {total} 项，显示 {shown} 项'
        self.status_bar.showMessage(f'✅ {msg}', 8000)
        if saved:
            notify(self, f'✅ {msg}', 'success')
        else:
            notify(self, f'✅ {msg}；但设置保存失败，下次启动会恢复', 'warn')

    def show_all_columns(self):
        """全部列都显示（清空隐藏设置）（★ v6.10）"""
        self._apply_hidden_columns(set(), '已恢复为显示全部列')

    def hide_column(self, name):
        """隐藏单列 / 单个图列（表头右键「隐藏这一列」用）（★ v6.10；★ v6.13 支持图列）"""
        if not name:
            return
        # ★ v6.13：图列可以随便藏；但**数据列至少要留一列**（全藏了数据列表会兜底
        #   显示全部，用户会以为没生效）
        entries = self._visible_display_entries()
        remaining_data = [e for e in entries
                          if not e.get('kind') and e.get('name') != name]
        if not remaining_data:
            notify(self, '至少要保留一列数据列显示', 'warn')
            return
        hidden = set(self.hidden_columns or set())
        hidden.add(name)
        self._apply_hidden_columns(hidden, f'已隐藏「{self._display_entry_label(name)}」')

    def _background_backup(self):
        """线程池里执行的自动备份

        ★ v6.3：使用工作线程专属的独立连接。原来直接拿 self.user_db（与主线程
        共用的池化连接），一旦主线程先执行了 close()/退出，备份就会报
        “Cannot operate on a closed database”，且存在并发用同一 Cursor 的风险。
        """
        db = None
        try:
            db = self.user_db.clone_for_thread()
            db.backup_database(backup_type="auto")
        except Exception as e:
            print(f"[DEBUG] 自动备份失败: {str(e)}")
        finally:
            try:
                if db is not None and getattr(db, 'private_connection', False):
                    db.close()
            except Exception:
                pass

    def auto_backup_async(self):
        """异步自动备份"""
        self._start_pool_worker(self._background_backup)

    def _start_pool_worker(self, fn):
        """把一次性任务交给线程池，并保留 Python 引用直到它结束

        ★ v6.4：QThreadPool.start() 后若 Python 侧不保留引用，runnable 及其
        WorkerSignals 可能被提前回收，任务内部再 emit 就会报
        RuntimeError: Signal source has been deleted。
        """
        worker = BaseWorker(fn)
        if not hasattr(self, '_pool_workers'):
            self._pool_workers = []
        self._pool_workers.append(worker)

        def _release(*_a):
            try:
                self._pool_workers.remove(worker)
            except ValueError:
                pass

        try:
            worker.signals.finished.connect(_release)
            worker.signals.error.connect(_release)
        except Exception:
            pass
        self.thread_pool.start(worker)
        return worker

    def _create_progress_dialog(self, title, max_value=0):
        """创建或重用进度对话框"""
        if self.progress_dialog is not None:
            try:
                self.progress_dialog.close()
            except:
                pass
        self.progress_dialog = QProgressDialog(title, "取消", 0, max_value, self)
        self.progress_dialog.setWindowTitle("请稍候...")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setMinimumDuration(300)  # 300ms后显示
        self.progress_dialog.setAutoClose(True)
        return self.progress_dialog

    def _cleanup_worker(self, worker):
        """清理完成的工作线程

        ★ v6.3 关键修复（“添加资料、点确定后程序自动退出”的根因）：
          ThreadedWorker.finished 是自定义信号，它在 run() 内部就 emit 了，
          此时 QThread 线程往往还没有真正结束。如果这里直接把 Python 引用丢掉，
          QThread 的 C++ 对象会被析构，而线程仍在运行 —— Qt 会立刻调用
          qFatal("QThread: Destroyed while thread is still running") 让整个进程
          无任何提示退出（退出码 0xC0000409）；有时则表现为下一次构造 QThread
          时访问违例（0xC0000005）。
          所以必须：① 仍在运行的先 cancel + wait；② 引用先挪进 _retired_workers
          保活，延迟一拍再释放，确保析构一定发生在 run() 返回之后。
        """
        if worker is None:
            return
        if worker in self.active_workers:
            try:
                self.active_workers.remove(worker)
            except ValueError:
                pass
        try:
            if worker.isRunning():
                worker.cancel()
                worker.wait(3000)
        except Exception:
            pass
        # 保活一拍：run() 返回与线程彻底退出之间仍有极短窗口
        if worker not in self._retired_workers:
            self._retired_workers.append(worker)
        QTimer.singleShot(1000, self._purge_retired_workers)

    def _retire_worker(self, worker):
        """被新任务取代的线程：取消并保活到它自己结束，绝不立即析构

        ★ 替换 self.xxx_worker 引用前必须先调用本方法，否则那个还在运行的
          QThread 会因为失去最后一个引用而被析构 -> Qt qFatal -> 进程消失。
        """
        if worker is None:
            return
        try:
            worker.cancel()
        except Exception:
            pass
        if worker not in self._retired_workers:
            self._retired_workers.append(worker)
        QTimer.singleShot(1000, self._purge_retired_workers)

    def _cleanup_finished_workers(self):
        """清理所有已经结束的工作线程（仍在运行的原样保留）"""
        for worker in list(self.active_workers):
            try:
                if worker is None or worker.isFinished():
                    self._cleanup_worker(worker)
            except Exception:
                continue

    def _purge_retired_workers(self):
        """释放已经彻底结束的线程对象；仍在运行的继续保活"""
        keep = []
        for worker in list(self._retired_workers):
            try:
                if worker is not None and worker.isRunning():
                    keep.append(worker)
            except Exception:
                continue
        self._retired_workers = keep

    @staticmethod
    def _clear_layout(layout):
        """安全清空布局里的所有子项

        ★ v6.3 关键修复（“添加资料、点确定后程序自动退出”的主因）：
          正确做法是 takeAt() 把布局项摘下来，再 deleteLater() 交给 Qt 销毁。
          原来用的 widget().setParent(None) 只是把控件变成顶级窗口，布局项
          仍然指向同一个控件；随后 Python 侧释放控件，布局里就留下悬空指针，
          下一次布局/重绘就会踩坏堆内存 —— 表现为之后随便一个操作（例如下一次
          添加资料时新建 LoadDataWorker 线程）进程无任何提示地直接退出。
        """
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()
            sub_layout = item.layout()
            if sub_layout is not None:
                MainWindow._clear_layout(sub_layout)
                sub_layout.deleteLater()
            del item

    def closeEvent(self, event):
        """关闭窗口时取消所有活跃线程"""
        # ★ v6.11：先置位表示“正在关闭” —— 关闭后绝不允许再新建线程。
        #   原因：构造窗口时会挂一个 200ms 后的 delayed_load_data 定时器，如果窗口在
        #   200ms 内就被关掉（或关掉后事件循环再转一圈），这个定时器仍会启动
        #   LoadDataWorker（而刚才关窗口时已经把数据库连接关了，它还会报
        #   Cannot operate on a closed database）；这个 QThread 在解释器退出时被析构
        #   → Qt qFatal("QThread: Destroyed while thread is still running") →
        #   进程无任何提示地退出（实测：重复跑测试脚本偶发 0xC0000409）。
        self._closing = True
        for timer_name in ('backup_timer', 'maintenance_timer'):
            try:
                timer = getattr(self, timer_name, None)
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        # 保存窗口几何/最大化（全局设置持久化）
        try:
            data = load_global_settings()
            # ★ v6.12：最大化时存 normalGeometry()（否则存的是 -8/-31 残影坐标，下次启动
            #   标题栏就在屏幕外了），并且过一遍 safe_window_geometry() 保证一定看得见
            geo = window_geometry_for_save(self, (800, 800))
            if geo:
                data['main_geometry'] = geo
            data['main_maximized'] = self.isMaximized()
            if self.global_unified_config is not None:
                data['global_unified_config'] = self.global_unified_config
            save_global_settings(data)
        except Exception:
            pass
        for worker in list(self.active_workers):
            try:
                worker.cancel()
            except:
                pass
        # ★ v6.3：退出前必须等所有 QThread 真正结束，否则解释器/GC 析构它们时
        #   会触发 Qt 的 qFatal（表现为关闭瞬间进程崩溃或无提示退出）
        for worker in list(self.active_workers) + list(self._retired_workers):
            try:
                if worker is not None and worker.isRunning():
                    worker.wait(3000)
            except Exception:
                pass
        self.thread_pool.clear()
        self.thread_pool.waitForDone(3000)
        # 退出前进行一次备份
        try:
            self.user_db.backup_database(backup_type="auto")
        except:
            pass
        self.user_db.close()
        # ★ v6.15：关闭就是关闭。
        #   以前这里无条件把登录窗口又显示出来，结果“点关闭”只是回到登录窗口、程序
        #   不退出（用户点名要求改掉：「点程序关闭就关闭，不用返回到登录窗口」）。
        #   现在只有走【切换用户登录】才会回到登录窗口，其余一律真退出。
        if getattr(self, '_switching_user', False) and self.parent_window:
            try:
                self.parent_window.show()
                self.parent_window.raise_()
                self.parent_window.activateWindow()
                print("[DEBUG] 已切回登录窗口（程序不退出）")
            except Exception as e:
                print(f"[DEBUG] 显示登录窗口失败: {str(e)}")
        else:
            print("[DEBUG] 关闭主窗口 → 退出程序")
            try:
                app = QApplication.instance()
                if app is not None:
                    app.quit()
            except Exception as e:
                print(f"[DEBUG] 退出程序失败: {str(e)}")
        event.accept()

    def periodic_maintenance(self):
        """定期维护"""
        try:
            # 当数据库超过一定大小时自动归档
            db_size = self.user_db.get_database_size() / (1024 * 1024)  # MB
            if db_size > 100:  # 超过100MB时自动归档
                self.user_db.cursor.execute("VACUUM")
                self.user_db.conn.commit()
        except:
            pass

    def load_data_paginated(self, page=0, page_size=100):
        """分页加载数据"""
        try:
            self.current_page = page
            self.page_size = page_size
            
            
            # 获取总记录数
            total_count = self.user_db.get_data_count()
            self.total_pages = (total_count + self.page_size - 1) // self.page_size
            
            # 计算偏移量
            offset = page * self.page_size
            
            # 分页查询数据
            self.cursor.execute(f'SELECT rowid, * FROM data LIMIT ? OFFSET ?', (self.page_size, offset))
            data = self.cursor.fetchall()
            
            # 更新表格显示
            self.update_table_with_data(data)
            
            # 更新分页信息
            self.update_pagination_info()
            
        except Exception as e:
            print(f"[DEBUG] 分页加载数据失败: {str(e)}")
            notify(self, f'加载数据失败: {str(e)}', 'error')

    def update_pagination_info(self):
        """更新分页信息显示"""
        page_info = f"第 {self.current_page + 1} 页 / 共 {self.total_pages} 页"
        self.status_bar.showMessage(f'用户: {self.username} | {page_info}')

    
    def create_pagination_controls(self):
        """创建分页控件"""
        pagination_layout = QHBoxLayout()
        
        first_btn = QPushButton('首页')
        prev_btn = QPushButton('上一页')
        next_btn = QPushButton('下一页')
        last_btn = QPushButton('末页')
        
        first_btn.clicked.connect(lambda: self.load_data_paginated(0))
        prev_btn.clicked.connect(lambda: self.load_data_paginated(max(0, self.current_page - 1)))
        next_btn.clicked.connect(lambda: self.load_data_paginated(min(self.total_pages - 1, self.current_page + 1)))
        last_btn.clicked.connect(lambda: self.load_data_paginated(self.total_pages - 1))
        
        pagination_layout.addWidget(first_btn)
        pagination_layout.addWidget(prev_btn)
        pagination_layout.addWidget(next_btn)
        pagination_layout.addWidget(last_btn)
        pagination_layout.addStretch()
        
        # 添加到主布局的合适位置
        self.centralWidget().layout().insertLayout(1, pagination_layout)

    def _ensure_window_reachable(self):
        """窗口显示后按真实边框核一次位置（★ v6.12）

        正常路径不会触发（启动时已经用 safe_window_geometry() + place_window() 摆好了）；
        这是最后一道保险：万一多屏/DPI 变化让窗口管理器把窗口摆到够不着的地方，
        就把它拉回屏幕中间。**窗口已经关了就别动**（`_closing`）。
        """
        if getattr(self, '_closing', False):
            return
        try:
            ensure_window_on_screen(self)
        except Exception as e:
            print(f"[DEBUG] 窗口位置核对失败: {str(e)}")

    def reset_window_position(self):
        """🪟 把窗口摆回屏幕中间（★ v6.12，工具菜单入口）

        给「窗口跑到屏幕外、鼠标抓不到标题栏」留的手动后路：菜单栏在客户区顶部，
        只要客户区还看得见就点得到（快捷键 Ctrl+Alt+C 也可以）。
        """
        try:
            if self.isMaximized() or self.isFullScreen():
                self.showNormal()
        except Exception:
            pass
        if center_window_on_screen(self):
            notify(self, '窗口已摆回屏幕中间', 'success')
        else:
            notify(self, '没能拿到屏幕信息，无法重置窗口位置', 'warn')

    def delayed_load_data(self):
        """延迟加载数据，先让界面显示出来"""
        # ★ v6.11：窗口已经关了（200ms 内被关掉）就别再起加载线程
        if getattr(self, '_closing', False):
            print("[DEBUG] 窗口已关闭，跳过延迟加载数据")
            return
        try:
            # 显示加载提示
            self.status_bar.showMessage("正在加载数据，请稍候...")
            QApplication.processEvents()  # 处理界面更新
            
            # 实际加载数据
            self.load_data()
            self.is_data_loaded = True
            self.status_bar.showMessage("数据加载完成")
            
        except Exception as e:
            self.status_bar.showMessage(f"数据加载失败: {str(e)}")
            print(f"[DEBUG] 延迟加载数据失败: {str(e)}")

    def init_ui(self):
        # self.setWindowTitle(f'数据管理系统 - {self.username}')
        self.setWindowTitle(f"{ProjectInfo.NAME} {ProjectInfo.VERSION} (Build: {ProjectInfo.BUILD_DATE}) - {self.username}")
        # self.setWindowTitle(f"{ProjectInfo.NAME} v{ProjectInfo.VERSION} | {self.username} | Build: {ProjectInfo.BUILD_DATE}")

        self.resize(800, 800)
        
        # 创建菜单栏
        self.create_menu_bar()
        
        # 创建工具栏
        self.create_tool_bar()
        
        # 主窗口中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 搜索区域
        search_group = QGroupBox('🔍搜索')
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('💬输入关键词搜索所有列...')
        self.search_input.returnPressed.connect(self.search_data)
        search_layout.addWidget(self.search_input)
        
        self.search_btn = QPushButton('🔍搜索所有列')
        self.search_btn.clicked.connect(self.search_data)
        search_layout.addWidget(self.search_btn)
        
        self.clear_search_btn = QPushButton('💥清除')
        self.clear_search_btn.clicked.connect(self.clear_search)
        search_layout.addWidget(self.clear_search_btn)
        
        search_group.setLayout(search_layout)
        main_layout.addWidget(search_group)
        
        # 数据显示区域
        self.tab_widget = QTabWidget()
        
        # 数据表格
        self.data_table = QTableWidget()
        self.data_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.data_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.data_table.doubleClicked.connect(self.show_item_detail)
        self.data_table.itemSelectionChanged.connect(self.table_selection_changed)

        # 添加右键菜单支持
        self.data_table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.data_table.customContextMenuRequested.connect(self.show_context_menu)

        self.tab_widget.addTab(self.data_table, '📊数据列表')
        
        # 详情视图
        self.detail_widget = QWidget()
        detail_outer = QVBoxLayout()
        self.detail_widget.setLayout(detail_outer)
        # ★ v6.16：详情页顶部常驻「⬆上一条 / ⬇下一条」导航（不随详情内容被清空）
        detail_outer.addLayout(self._build_detail_nav())
        self.detail_layout = QVBoxLayout()   # 详情内容（每次重建）
        detail_outer.addLayout(self.detail_layout, 1)
        self.tab_widget.addTab(self.detail_widget, '📝详细信息')
        
        # 统计视图
        self.stats_widget = QWidget()
        self.stats_layout = QVBoxLayout()
        self.stats_widget.setLayout(self.stats_layout)
        self.tab_widget.addTab(self.stats_widget, '📈数据统计')
        
        main_layout.addWidget(self.tab_widget)
        
        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.update_status_bar()
        
        # 不在此处同步加载数据，改为通过 QTimer 延迟异步加载，避免启动卡顿
        # self.load_data()  # 已移至 delayed_load_data 中延迟执行
        # 先显示空表格，给用户即时反馈
        self.data_table.setRowCount(0)
        self.status_bar.showMessage("正在启动，即将加载数据...")

        # 添加表头排序功能
        self.data_table.horizontalHeader().sectionClicked.connect(self.sort_table_by_column)

        # ★ v6.10：表头右键 → 显示列设置（“想只看哪几列”最顺手的位置）
        self.data_table.horizontalHeader().setContextMenuPolicy(Qt.CustomContextMenu)
        self.data_table.horizontalHeader().customContextMenuRequested.connect(
            self.show_header_context_menu)
        
        # 初始化排序状态（★ v6.14：三态 —— 升序 / 降序 / 默认（不排序））
        #   * 只记“现在排的是哪一列 + 什么方向”，按【数据列名】记（不是列序号）——
        #     隐藏列/增减列之后也不会排错列；
        #   * 排序在【数据层】做（渲染前对整份数据排），所以刷新/搜索/翻页后顺序一致，
        #     而且 rowid 与行内容不会错位；
        #   * `sort_orders` 是 v6.14 之前的旧结构，保留为空字典只为兼容别处的复位代码。
        self.sort_column_name = None   # 当前排序的数据列名（None＝默认排序）
        self.sort_order = None         # 'asc' / 'desc' / None
        self._render_source_data = []  # 最近一次拿到的原始数据（排序只在这上面做，不重新查库）
        self._render_source_keyword = None  # 最近一次是不是搜索结果（要把高亮一起重画）
        self.sort_orders = {}
        # 关掉 Qt 内置排序：否则点表头时它自己也会排一次，和我们的三态排序打架
        self.data_table.setSortingEnabled(False)

        # 添加快捷键支持
        self.setup_shortcuts()

    def setup_shortcuts(self):
        """设置应用程序快捷键"""
        # N 键 - 添加数据
        self.add_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        self.add_shortcut.activated.connect(self.add_data)
        
        # 其他常用快捷键（可选添加）
        self.search_shortcut = QShortcut(QKeySequence("Ctrl+F"), self)
        self.search_shortcut.activated.connect(self.search_data)
        
        self.refresh_shortcut = QShortcut(QKeySequence("F5"), self)
        self.refresh_shortcut.activated.connect(self.load_data)
        
        self.delete_shortcut = QShortcut(QKeySequence("Delete"), self)
        self.delete_shortcut.activated.connect(self.delete_data)

        self.edit_shortcut = QShortcut(QKeySequence("Ctrl+E"), self)
        self.edit_shortcut.activated.connect(self.edit_selected_data)

        # ★ v6.16：详情页翻上/下一条（Alt+↑ / Alt+↓，只在详情页生效）
        self.detail_prev_shortcut = QShortcut(QKeySequence("Alt+Up"), self)
        self.detail_prev_shortcut.activated.connect(
            lambda: self.detail_goto_offset(-1, need_detail_tab=True))
        self.detail_next_shortcut = QShortcut(QKeySequence("Alt+Down"), self)
        self.detail_next_shortcut.activated.connect(
            lambda: self.detail_goto_offset(1, need_detail_tab=True))


    def sort_table_by_column(self, column_index):
        """表头点击 → **升序 → 降序 → 默认（不排序）** 三态循环（★ v6.14）

        用户要求：「点表头排序，除了正反排序，还要有默认排序，就是不排序」。
        要点：
          * 状态按【数据列名】记（不按列序号）—— 隐藏列/增减列也不会排错列；
          * 排序在**数据层**做（渲染前把整份数据排一遍），所以刷新、搜索之后
            顺序都一致，而且 **rowid 与行内容不会错位**（老实现是原地改单元格文本，
            第一列的 rowid 不跟着走 —— 排完序再编辑/删除会动错行）；
          * 点第三下回到「默认排序」＝数据库里的原始顺序（按 rowid 升序）。
        """
        try:
            entries = self._visible_display_entries()
        except Exception:
            entries = []
        if not (0 <= column_index < len(entries)):
            return
        entry = entries[column_index]
        if entry.get('kind'):
            # 条形码/二维码图列没有可比的文本，不参与排序
            self.status_bar.showMessage('条形码/二维码 图列不参与排序（请点数据列的表头）', 6000)
            return

        name = entry.get('name')
        if name == self.sort_column_name:
            # 同一列循环：升序 → 降序 → 默认（不排序）
            self.sort_order = {'asc': 'desc', 'desc': None}.get(self.sort_order, 'asc')
            if self.sort_order is None:
                self.sort_column_name = None
        else:
            # 换一列：从升序开始
            self.sort_column_name = name
            self.sort_order = 'asc'

        self._render_from_source()
        self._show_sort_status(name)

    def _show_sort_status(self, name):
        """状态栏说明当前是哪种排序（★ v6.14，点了要能看到效果）"""
        label = self._display_entry_label(name) if name else ''
        if self.sort_order == 'asc':
            msg = f'🔼 按「{label}」升序（再点一次＝降序，第三次＝默认不排序）'
        elif self.sort_order == 'desc':
            msg = f'🔽 按「{label}」降序（再点一次＝默认不排序）'
        else:
            msg = '↩️ 已恢复默认排序（不排序，按录入顺序）'
        try:
            self.status_bar.showMessage(msg, 8000)
        except Exception:
            pass

    def _render_from_source(self, columns_config=None):
        """按当前排序状态，把「最近一次拿到的数据」重画一遍（★ v6.14）

        排序只在这份内存数据上做（不重新查库、不闪屏）；搜索/刷新之后也走这里，
        所以「搜索后点表头」和「排序后按 F5」都不会把排序状态弄丢。
        """
        cols = columns_config if columns_config is not None else self.columns_config
        src = list(getattr(self, '_render_source_data', None) or [])
        keyword = getattr(self, '_render_source_keyword', None)
        data = self._sorted_rows(src, cols)
        if keyword:
            self.display_search_results(data, keyword)
        else:
            self._update_table_with_data(data, cols)
        self._refresh_sort_indicator()

    def _sorted_rows(self, data, columns_config=None):
        """按当前排序状态排序（★ v6.14）：没排序就原样返回；空值永远排最后

        * 比的是**单元格里看到的文本**（与 `_value_by_name()` 一致，NULL 当空串）；
        * 数字按数值比（1 / 2 / 10，而不是 1 / 10 / 2）；
        * 排序列被删/改名了 → 排序状态自动失效，回到默认顺序。
        """
        rows = list(data or [])
        if not rows or not self.sort_order or not self.sort_column_name:
            return rows
        cols = list(columns_config if columns_config is not None else (self.columns_config or []))
        names = [c.get('name') for c in cols]
        if self.sort_column_name not in names:
            self.sort_column_name = None
            self.sort_order = None
            return rows

        col_index = self._table_column_index(cols)
        filled, empty = [], []
        for row in rows:
            try:
                text = self._value_by_name(col_index, row[1:], self.sort_column_name)
            except Exception:
                text = ''
            if text is None or str(text).strip() == '':
                empty.append(row)
            else:
                filled.append((sort_key_for_text(text), row))
        filled.sort(key=lambda pair: pair[0], reverse=(self.sort_order == 'desc'))
        return [row for _key, row in filled] + empty

    def _refresh_sort_indicator(self):
        """按当前状态画表头箭头（★ v6.14：默认排序时不显示箭头）

        注意：重建表头（`setHorizontalHeaderLabels`）会把箭头洗掉，所以每次渲染完
        都要调用一次（`_render_from_source()` 末尾会调）。
        """
        try:
            header = self.data_table.horizontalHeader()
            model = header.model()
            entries = self._visible_display_entries()
            names = [e.get('name') for e in entries]
            active = None
            if self.sort_order and self.sort_column_name in names:
                active = names.index(self.sort_column_name)
            for col in range(header.count()):
                text = str(model.headerData(col, Qt.Horizontal) or '')
                for mark in (' ↑', ' ↓'):
                    if text.endswith(mark):
                        text = text[:-len(mark)]
                if active is not None and col == active:
                    text += ' ↑' if self.sort_order == 'asc' else ' ↓'
                model.setHeaderData(col, Qt.Horizontal, text)
        except Exception as e:
            print(f"[DEBUG] 刷新排序箭头失败: {str(e)}")

    def perform_sorting(self, column_index, order):
        """【已废弃（★ v6.14），不再被调用 —— 别再启用】

        为什么废弃：它只把单元格**文本**重新写一遍，不搬 `Qt.UserRole` 里存的 rowid，
        所以排完序后第一列的 rowid 与行内容错位（再编辑/删除会动错行）；也没有「默认排序」
        第三态。新实现在 `sort_table_by_column()` / `_render_from_source()` / `_sorted_rows()`。
        """
        try:
            # 获取当前显示的所有数据（包括搜索后的数据）
            row_count = self.data_table.rowCount()
            if row_count == 0:
                return
            
            # 收集所有行的数据和rowid
            rows_data = []
            for row in range(row_count):
                row_data = []
                rowid = None
                
                # 获取该行的所有单元格数据
                for col in range(self.data_table.columnCount()):
                    item = self.data_table.item(row, col)
                    if item:
                        if col == 0:  # 第一列保存rowid
                            rowid = item.data(Qt.UserRole)
                        row_data.append(item.text())
                    else:
                        # 处理有widget的单元格（条形码/二维码列）
                        widget = self.data_table.cellWidget(row, col)
                        if widget and isinstance(widget, QLabel):
                            # 对于图像列，我们可以跳过排序或者使用特定值
                            row_data.append("")  # 图像列不参与排序
                        else:
                            row_data.append("")
                
                if rowid is not None:
                    rows_data.append((rowid, row_data))
            
            # 根据指定列进行排序
            def sort_key(item):
                text = item[1][column_index] if column_index < len(item[1]) else ""
                
                # 尝试转换为数字进行排序
                try:
                    if text.replace('.', '').replace('-', '').isdigit():
                        return float(text) if '.' in text else int(text)
                except:
                    pass
                
                # 返回文本（支持中文排序）
                return text
            
            # 执行排序
            reverse = (order == 'desc')
            rows_data.sort(key=sort_key, reverse=reverse)
            
            # 更新表格显示
            self.data_table.setSortingEnabled(False)  # 暂时禁用内置排序
            
            for row, (rowid, row_data) in enumerate(rows_data):
                for col, text in enumerate(row_data):
                    if col < self.data_table.columnCount():
                        item = self.data_table.item(row, col)
                        if item:
                            item.setText(text)
            
            # 更新表头显示排序指示器
            self.update_header_sort_indicator(column_index, order)
            
            self.data_table.setSortingEnabled(True)
            
        except Exception as e:
            print(f"排序失败: {str(e)}")

    def update_header_sort_indicator(self, sorted_column, order):
        """【已废弃（★ v6.14）】旧的表头箭头 —— 现在用 `_refresh_sort_indicator()`（不会再洗掉箭头）"""
        header = self.data_table.horizontalHeader()
        
        # 清除所有列的排序指示器
        for col in range(header.count()):
            header_item = header.model().headerData(col, Qt.Horizontal)
            if header_item:
                # 移除之前的排序指示器
                current_text = str(header_item)
                if current_text.endswith(' ↑') or current_text.endswith(' ↓'):
                    current_text = current_text[:-2]
                header.model().setHeaderData(col, Qt.Horizontal, current_text)
        
        # 为当前排序列添加指示器
        if order is not None:
            current_header = header.model().headerData(sorted_column, Qt.Horizontal)
            indicator = ' ↑' if order == 'asc' else ' ↓'
            new_header = str(current_header) + indicator
            header.model().setHeaderData(sorted_column, Qt.Horizontal, new_header)

    
    def create_menu_bar(self):
        menubar = self.menuBar()
        
        # 文件菜单
        file_menu = menubar.addMenu('📄文件')
    
        import_menu = file_menu.addMenu('📥导入')  
    
        # 更新导入菜单项
        enhanced_batch_import_action = QAction('🤖 智能批量导入', self)
        enhanced_batch_import_action.triggered.connect(self.batch_ultra_fast_import_enhanced)
        import_menu.addAction(enhanced_batch_import_action)
        
    
        fast_import_action = QAction('⚡📥超快速导入', self)
        fast_import_action.triggered.connect(self.ultra_fast_import_data)
        import_menu.addAction(fast_import_action)

        data_management_action = QAction('🗃️ 数据管理', self)
        data_management_action.triggered.connect(self.show_data_management_dialog)
        file_menu.addAction(data_management_action)

        # 添加备份菜单项
        backup_menu = file_menu.addMenu('📦备份')
        manual_backup_action = QAction('📦手动备份', self)
        manual_backup_action.triggered.connect(self.manual_backup)
        backup_menu.addAction(manual_backup_action)
        
        restore_action = QAction('📦恢复备份', self)
        restore_action.triggered.connect(self.show_restore_dialog)
        backup_menu.addAction(restore_action)
        
        export_action = QAction('📤导出数据', self)
        export_action.triggered.connect(self.export_data)
        file_menu.addAction(export_action)



        refresh_action = QAction('🔄刷新数据', self)
        refresh_action.triggered.connect(self.load_data)
        file_menu.addAction(refresh_action)
        
        # ★ v6.15：换人登录用「切换用户登录」；「退出程序」是真的退出（不再回登录窗口）
        switch_user_action = QAction('👤切换用户登录', self)
        switch_user_action.setStatusTip('回到登录窗口换其它用户登录（程序不退出）')
        switch_user_action.triggered.connect(self.switch_user)
        file_menu.addAction(switch_user_action)

        exit_action = QAction('❌退出程序', self)
        exit_action.setStatusTip('保存并关闭程序（就是退出，不再回登录窗口）')
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 工具菜单
        tools_menu = menubar.addMenu('🛠️工具')
        
        settings_action = QAction('🛠️用户设置', self)
        settings_action.triggered.connect(self.show_user_settings)
        tools_menu.addAction(settings_action)
        
        # ★ v6.5：登录后也能修改列配置（数据保留，改前自动备份）
        modify_columns_action = QAction('🧩修改列配置', self)
        modify_columns_action.triggered.connect(self.modify_columns_config)
        tools_menu.addAction(modify_columns_action)

        # ★ v6.10：数据列表只显示选中的列（只影响显示，数据照常录入/保存/导出）
        visibility_action = QAction('👁️显示列设置', self)
        visibility_action.triggered.connect(self.configure_column_visibility)
        tools_menu.addAction(visibility_action)

        # ★ v6.22：详细信息页只显示选中的项目（字段 / 每列的条码块 / 二维码块）
        detail_visibility_action = QAction('🔎详细信息显示设置', self)
        detail_visibility_action.setStatusTip('选择「详细信息」页里显示哪些项目和哪些条码/二维码')
        detail_visibility_action.triggered.connect(self.configure_detail_visibility)
        tools_menu.addAction(detail_visibility_action)

        # ★ v6.12：窗口跑到屏幕外时的救回入口（菜单栏在客户区顶部，客户区还看得见就点得到）
        tools_menu.addSeparator()
        reset_window_action = QAction('🪟窗口位置重置（居中显示）', self)
        reset_window_action.setShortcut('Ctrl+Alt+C')
        reset_window_action.setStatusTip('窗口跑到屏幕外、抓不住标题栏时，用这个把它摆回屏幕中间')
        reset_window_action.triggered.connect(self.reset_window_position)
        tools_menu.addAction(reset_window_action)
        
        # 帮助菜单
        help_menu = menubar.addMenu('📖帮助')
        
        help_action = QAction('📖使用说明', self)
        help_action.triggered.connect(self.show_help)
        help_menu.addAction(help_action)
        
        about_action = QAction('📖关于', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)



    def create_tool_bar(self):
        toolbar = QToolBar("🛠️主工具栏")
        self.addToolBar(toolbar)
        
        # 添加工具按钮
        add_action = QAction('📝添加数据', self)
        add_action.triggered.connect(self.add_data)
        toolbar.addAction(add_action)
        
        edit_action = QAction('✏️编辑数据', self)
        edit_action.triggered.connect(self.edit_selected_data)
        toolbar.addAction(edit_action)
        
        delete_action = QAction('💥删除数据', self)
        delete_action.triggered.connect(self.delete_data)
        toolbar.addAction(delete_action)
        
        toolbar.addSeparator()


        # 添加超快速导入按钮
        ultra_fast_import_action = QAction('⚡📥超快速导入', self)
        ultra_fast_import_action.triggered.connect(self.ultra_fast_import_data)
        toolbar.addAction(ultra_fast_import_action)
        
        search_action = QAction('🔍搜索', self)
        search_action.triggered.connect(self.search_data)
        toolbar.addAction(search_action)
        
        refresh_action = QAction('🔁刷新', self)
        refresh_action.triggered.connect(self.load_data)
        toolbar.addAction(refresh_action)
        
        toolbar.addSeparator()
        
        export_action = QAction('📤导出', self)
        export_action.triggered.connect(self.export_data)
        toolbar.addAction(export_action)
    
    def load_data(self):
        """使用多线程异步加载数据"""
        # ★ v6.11：已关闭的窗口不再起线程（退出时析构运行中的 QThread 会让进程无提示退出）
        if getattr(self, '_closing', False):
            print("[DEBUG] 窗口正在关闭，忽略本次数据加载")
            return
        try:
            self.status_bar.showMessage("正在加载数据，请稍候...")
            
            # 创建并启动数据加载线程
            # ★ v6.3：上一个加载线程可能还在跑，先取消+保活，避免它被析构
            self._retire_worker(getattr(self, 'load_worker', None))
            self.load_worker = LoadDataWorker(self.user_db.clone_for_thread(), self.columns_config)
            self.load_worker.finished.connect(self._on_data_loaded)
            self.load_worker.error.connect(self._on_load_error)
            self.load_worker.status.connect(lambda msg: self.status_bar.showMessage(msg))
            self.active_workers.append(self.load_worker)
            self.load_worker.start()
            
        except Exception as e:
            print(f"[DEBUG] 加载数据失败: {str(e)}")
            notify(self, f'加载数据失败: {str(e)}', 'error')

    def _on_data_loaded(self, result):
        """数据加载完成的回调（主线程）"""
        # ★ v6.3：用真正的发送者（旧线程可能已被新任务取代），
        #   拿 self.load_worker 会清错对象 -> 正在跑的线程失去引用 -> 进程崩溃
        sender = self.sender()
        current = getattr(self, 'load_worker', None)
        if sender is not None and sender is not current:
            # 旧任务的迟到结果，直接丢弃
            self._cleanup_worker(sender)
            return
        self._cleanup_worker(sender if sender is not None else current)
        self._cleanup_finished_workers()
        try:
            data = result['data']
            columns_config = result['columns_config']
            # ★ v6.14：记下这批数据（排序只在这份内存数据上做，刷新后排序状态不丢；
            #   排序期间不会重新查库，所以不会闪屏）
            self._render_source_data = data
            self._render_source_keyword = None
            self._render_from_source(columns_config)
            self.update_stats()
            self.update_status_bar()
            self.is_data_loaded = True
            self.status_bar.showMessage(f"数据加载完成，共 {len(data)} 条记录")
            # ★ v6.6：刚添加过数据时自动翻到最新行（放最后，避免被其它刷新盖掉）
            self._scroll_to_newest_row()
        except Exception as e:
            print(f"[DEBUG] 更新表格失败: {str(e)}")
            self.status_bar.showMessage("数据加载完成但表格更新失败")
            notify(self, f'数据显示失败: {str(e)}', 'error')

    def _scroll_to_newest_row(self):
        """★ v6.6：添加数据后自动翻到最新行（刚添加的那条）并选中它

        只在本窗口刚执行过「添加数据」时生效（`_scroll_to_last_after_load`），
        刷新/搜索/导入等其它刷新表格的场景不受影响。
        优先按刚插入的 rowid 定位（即使启用了表头排序也不会找错），
        找不到则退化为最后一行（数据按 rowid 升序加载，最新记录本就在末行）。
        """
        if not getattr(self, '_scroll_to_last_after_load', False):
            return
        self._scroll_to_last_after_load = False
        target_rowid = getattr(self, '_pending_scroll_rowid', None)
        self._pending_scroll_rowid = None
        try:
            rows = self.data_table.rowCount()
            if rows <= 0:
                return
            row = rows - 1
            if target_rowid is not None:
                for r in range(rows):
                    item = self.data_table.item(r, 0)
                    if item is not None and item.data(Qt.UserRole) == target_rowid:
                        row = r
                        break
            item = self.data_table.item(row, 0)
            if item is not None:
                self.data_table.scrollToItem(item, QAbstractItemView.PositionAtBottom)
            else:
                self.data_table.scrollToBottom()
            # 顺手选中新行，方便立即查看/编辑
            self.data_table.selectRow(row)
            self.data_table.setCurrentCell(row, 0)
        except Exception as e:
            print(f"[DEBUG] 滚动到最新行失败: {str(e)}")

    def _on_load_error(self, error_msg):
        """数据加载错误的回调（主线程）"""
        sender = self.sender()
        current = getattr(self, 'load_worker', None)
        if sender is not None and sender is not current:
            self._cleanup_worker(sender)
            return
        self._cleanup_worker(sender if sender is not None else current)
        self._cleanup_finished_workers()
        self.status_bar.showMessage(f"数据加载失败: {error_msg}")
        notify(self, f'加载数据失败: {error_msg}', 'error')

    def _table_column_index(self, columns_config=None):
        """{列名: 在 `SELECT rowid, * FROM data` 结果里的下标}（★ v6.7）

        表格填充统一走“列名 → 下标”，而不是按位置对号，避免配置与表结构/顺序
        不一致时串列。
        """
        try:
            db_columns = self.user_db.get_table_columns()
        except Exception:
            db_columns = []
        if not db_columns:
            db_columns = [c.get('name') for c in (columns_config or self.columns_config or [])]
        return {name: i for i, name in enumerate(db_columns) if name}

    @staticmethod
    def _value_by_name(col_index, row_values, col_name):
        """按列名从一行数据里取值；NULL/缺列统一给空字符串（★ v6.7）"""
        pos = (col_index or {}).get(col_name)
        if pos is None or pos >= len(row_values):
            return ''
        value = row_values[pos]
        return '' if value is None else str(value)

    def _update_table_with_data(self, data, columns_config=None):
        """在主线程中更新表格显示（提取公共逻辑）

        ★ v6.10：表头与单元格只渲染 `_visible_columns()` —— 用户挑的“显示列”；
        隐藏的列数据仍然照常加载/保存，只是不画到表格里（也不会画它的条码图）。
        """
        if columns_config is None:
            columns_config = self.columns_config
        # 「全部列」用于按列名取值（下标表），「显示列」用于画表格，两者分开
        all_columns = list(columns_config or [])
        # 兼容不完整的调用方（测试里的替身对象没有 _visible_columns，就当作全部显示）
        _visible_of = getattr(self, '_visible_columns', None)
        columns_config = _visible_of(all_columns) if callable(_visible_of) else all_columns

        # 设置表头
        headers = [col['label'] for col in columns_config]

        # ★ v6.13：条形码/二维码图列也受「显示列设置」控制，且按【全部列】算
        #   （不跟着“显示列”走）—— 否则隐藏某个数据列会连带把它的图列一起弄没，
        #   用户就没法单独控制图列了。
        _img_of = getattr(self, '_visible_image_columns', None)
        if callable(_img_of):
            barcode_cols = _img_of('barcode', all_columns)
            qrcode_cols = _img_of('qrcode', all_columns)
        else:
            # 测试替身（没有新方法）：退回旧行为（按显示列过滤），但表头/取值仍走同一套
            # 图列条目结构（label 已经带「条形码」后缀、source 指向来源列）
            barcode_cols = display_image_entries(
                [c for c in columns_config if c.get('is_barcode', False)])
            qrcode_cols = display_image_entries(
                [c for c in columns_config if c.get('is_qrcode', False)
                 and not c.get('is_barcode', False)])
        
        for col in barcode_cols:
            headers.append(col.get('label') or f"{col.get('name')}条形码")
        
        for col in qrcode_cols:
            headers.append(col.get('label') or f"{col.get('name')}二维码")
        
        self.data_table.setColumnCount(len(headers))
        self.data_table.setHorizontalHeaderLabels(headers)
        
        # 加载数据
        self.data_table.setRowCount(len(data))
        # ★ v6.13：必须先清掉上一次留下的 cellWidget —— setRowCount/setColumnCount 都不会
        #   动 cellWidget，而“显示列”一变，图表头的位置就会变（上一个条码图会正好盖住
        #   新的数据单元格，用户看到的是旧图）。clearContents() 会连 item 和 widget 一起清。
        self.data_table.clearContents()

        # ★ v6.7：按【列名】取值，而不是按位置。
        #   配置与表结构万一不一致（或顺序不同），也不会串列、也不会把 NULL 显示成
        #   “None”文本（旧版把 None 直接 str() 出来，用户以为数据丢了）。
        # ★ v6.10：下标表按【全部列】建，显示列只是它的子集，照样能按列名取到值。
        col_index = self._table_column_index(all_columns)

        def _value_of(col_name, row_values):
            return self._value_by_name(col_index, row_values, col_name)
        
        # 马卡龙色系交替行颜色
        colors = [
            MacaronColors.MINT_GREEN,    # 薄荷绿
            MacaronColors.SKY_BLUE,      # 天空蓝
            MacaronColors.LEMON_YELLOW,  # 柠檬黄
            MacaronColors.LAVENDER,      # 薰衣草紫
            MacaronColors.PEACH_ORANGE   # 蜜桃橙
        ]
        
        for row_idx, row_data in enumerate(data):
            # rowid是第一个元素
            rowid = row_data[0]
            row_values = row_data[1:]
            
            # 设置交替行背景色
            color = colors[row_idx % len(colors)]
            for col_idx in range(len(headers)):
                if col_idx < len(columns_config):
                    raw = _value_of(columns_config[col_idx]['name'], row_values)
                else:
                    raw = ''          # 条形码/二维码占位列：内容由下面的 cellWidget 负责
                item = QTableWidgetItem(raw)
                item.setData(Qt.UserRole, rowid)
                item.setBackground(QColor(color))
                item.setForeground(QColor('#333333'))
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                # ★ v6.18：不符合该列 Code128 子集的值 → 字体换成反差明显的深红 + tooltip
                if col_idx < len(columns_config):
                    mark_barcode_mismatch(item, columns_config[col_idx], raw)
                self.data_table.setItem(row_idx, col_idx, item)
            
            # 添加条形码
            col_offset = len(columns_config)
            for col in barcode_cols:
                # ★ v6.13：图列条目用 source 指向来源列（数据列自己没 source）
                barcode_value = _value_of(col.get('source') or col['name'], row_values)
                _bg = barcode_background_for(barcode_value, col.get('barcode_format', 'auto'),
                                            col.get('barcode_subset', 'auto'))
                barcode_img = (self.generate_barcode(barcode_value,
                                                     col.get('barcode_format', 'auto'),
                                                     col.get('barcode_subset', 'auto'),
                                                     background=_bg)
                               if barcode_value else None)
                if barcode_img:
                    # ★ v6.21：缩略图（行高不变）+ 双击放大（放大后可直接扫）
                    label = BarcodeZoomLabel()
                    label.setPixmap(self.barcode_preview_pixmap(barcode_img))
                    label.setAlignment(Qt.AlignCenter)
                    # ★ v6.20：不符合子集的条码图用浅红底（图片自身也已经是浅红底）
                    label.setStyleSheet(f"background-color: {_bg or color};")
                    # ★ v6.19：悬停就能看到这张条码实际用的哪个子集（起始符/是否回退）
                    label.setToolTip(describe_barcode_encoding(
                        barcode_value, col.get('barcode_format', 'auto'),
                        col.get('barcode_subset', 'auto'))
                        + '\n（表格里是缩略图 —— 双击这张条码可放大，放大后就能直接扫）')
                    label.doubleClicked.connect(partial(
                        self.show_barcode_zoom, barcode_value,
                        col.get('barcode_format', 'auto'),
                        col.get('barcode_subset', 'auto'), _bg))
                    self.data_table.setCellWidget(row_idx, col_offset, label)
                col_offset += 1
            
            # 添加二维码
            for col in qrcode_cols:
                qrcode_value = _value_of(col.get('source') or col['name'], row_values)
                qrcode_img = self.generate_qrcode(qrcode_value) if qrcode_value else None
                if qrcode_img:
                    label = QLabel()
                    label.setPixmap(QPixmap.fromImage(qrcode_img))
                    label.setAlignment(Qt.AlignCenter)
                    label.setStyleSheet(f"background-color: {color};")
                    self.data_table.setCellWidget(row_idx, col_offset, label)
                col_offset += 1
        
        # 调整表格样式
        self.data_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #E0E0E0;
                gridline-color: #E0E0E0;
                font-size: 12px;
            }
            QHeaderView::section {
                background-color: #F5F5F5;
                padding: 5px;
                border: 1px solid #E0E0E0;
                font-weight: bold;
            }
        """)
        
        self.data_table.resizeColumnsToContents()
        self.data_table.resizeRowsToContents()

    
    def update_stats(self):
        # 清除之前的统计内容（★ v6.3：改用安全的布局清空，原 setParent(None) 会踩坏堆内存）
        self._clear_layout(self.stats_layout)
        
        # 获取数据总数
        total_count = self.user_db.get_data_count()
        
        # 创建统计信息
        stats_group = QGroupBox('数据统计')
        stats_form = QFormLayout()
        
        stats_form.addRow(QLabel('总记录数:'), QLabel(str(total_count)))
        
        # 添加列统计（★ v6.10：只统计“数据列表里显示的那些列”，与看到的一致）
        for col in self._visible_columns():
            if col['type'] in ['INTEGER', 'REAL']:
                # 计算数值列的总和、平均值等
                self.user_db.cursor.execute(f'SELECT SUM({col["name"]}), AVG({col["name"]}), MIN({col["name"]}), MAX({col["name"]}) FROM data')
                result = self.user_db.cursor.fetchone()
                
                if result and result[0] is not None:
                    stats_form.addRow(QLabel(f'{col["label"]} 统计:'))
                    stats_form.addRow(QLabel('总和:'), QLabel(f'{result[0]:.2f}'))
                    stats_form.addRow(QLabel('平均值:'), QLabel(f'{result[1]:.2f}'))
                    stats_form.addRow(QLabel('最小值:'), QLabel(f'{result[2]:.2f}'))
                    stats_form.addRow(QLabel('最大值:'), QLabel(f'{result[3]:.2f}'))
        
        stats_group.setLayout(stats_form)
        self.stats_layout.addWidget(stats_group)
        
        # 添加图表区域 (可以扩展)
        chart_label = QLabel('图表区域 (可扩展)')
        chart_label.setAlignment(Qt.AlignCenter)
        self.stats_layout.addWidget(chart_label)
        
        self.stats_layout.addStretch()
    
    def update_status_bar(self):
        count = self.user_db.get_data_count()
        self.status_bar.showMessage(f'用户: {self.username} | 总记录数: {count}')

    def search_data(self):
        """搜索数据 - 现在调用新的全列搜索方法"""
        self.search_all_columns()

    def search_data_old(self):
        keyword = self.search_input.text().strip()
        if not keyword:
            self.load_data()
            return
        
        try:
            # 使用增强搜索方法
            data = self.user_db.search_data_enhanced(keyword)
            self.data_table.setRowCount(len(data))
            
            # 马卡龙色系交替行颜色
            colors = [
                MacaronColors.MINT_GREEN,    # 薄荷绿
                MacaronColors.SKY_BLUE,      # 天空蓝
                MacaronColors.LEMON_YELLOW,  # 柠檬黄
                MacaronColors.LAVENDER,      # 薰衣草紫
                MacaronColors.PEACH_ORANGE   # 蜜桃橙
            ]
            
            # ★ v6.7：搜索结果的填充也按列名取值（否则 NULL 会显示成 “None”）
            col_index = self._table_column_index()
            for row_idx, row_data in enumerate(data):
                # rowid是第一个元素
                rowid = row_data[0]
                row_values = row_data[1:]
                
                # 设置交替行背景色
                color = colors[row_idx % len(colors)]
                for col_idx, col in enumerate(self.columns_config):
                    item = QTableWidgetItem(
                        self._value_by_name(col_index, row_values, col['name']))
                    item.setData(Qt.UserRole, rowid)  # 保存rowid
                    
                    # 设置背景色和文本颜色
                    item.setBackground(QColor(color))
                    item.setForeground(QColor('#333333'))  # 深色文字提高可读性
                    
                    # ★ v6.18：不符合该列 Code128 子集的值 → 字体换成反差明显的深红
                    mark_barcode_mismatch(item, col, item.text())
                    
                    # 设置对齐方式
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                    
                    self.data_table.setItem(row_idx, col_idx, item)
                
                # 添加条形码
                col_offset = len(self.columns_config)
                barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
                for col in barcode_cols:
                    barcode_value = self._value_by_name(col_index, row_values, col['name'])
                    _bg = barcode_background_for(barcode_value, col.get('barcode_format', 'auto'),
                                                col.get('barcode_subset', 'auto'))
                    barcode_img = (self.generate_barcode(
                        barcode_value, col.get('barcode_format', 'auto'),
                        col.get('barcode_subset', 'auto'), background=_bg)
                        if barcode_value else None)
                    if barcode_img:
                        # ★ v6.21：缩略图（行高不变）+ 双击放大（放大后可直接扫）
                        label = BarcodeZoomLabel()
                        label.setPixmap(self.barcode_preview_pixmap(barcode_img))
                        label.setAlignment(Qt.AlignCenter)
                        label.setStyleSheet(f"background-color: {_bg or color};")
                        # ★ v6.19：悬停看实际用的哪个子集
                        label.setToolTip(describe_barcode_encoding(
                            barcode_value, col.get('barcode_format', 'auto'),
                            col.get('barcode_subset', 'auto'))
                            + '\n（表格里是缩略图 —— 双击这张条码可放大，放大后就能直接扫）')
                        label.doubleClicked.connect(partial(
                            self.show_barcode_zoom, barcode_value,
                            col.get('barcode_format', 'auto'),
                            col.get('barcode_subset', 'auto'), _bg))
                        self.data_table.setCellWidget(row_idx, col_offset, label)
                    col_offset += 1
                
                # 添加二维码
                qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
                for col in qrcode_cols:
                    qrcode_value = self._value_by_name(col_index, row_values, col['name'])
                    qrcode_img = self.generate_qrcode(qrcode_value) if qrcode_value else None
                    if qrcode_img:
                        label = QLabel()
                        label.setPixmap(QPixmap.fromImage(qrcode_img))
                        label.setAlignment(Qt.AlignCenter)
                        label.setStyleSheet(f"background-color: {color};")
                        self.data_table.setCellWidget(row_idx, col_offset, label)
                    col_offset += 1
            
            self.data_table.resizeColumnsToContents()
            self.data_table.resizeRowsToContents()
            
            # 显示搜索结果的详细信息
            from pypinyin import lazy_pinyin, Style
            pinyin_full = ''.join(lazy_pinyin(keyword))
            pinyin_initials = ''.join(lazy_pinyin(keyword, style=Style.FIRST_LETTER))
            
            search_info = f'找到 {len(data)} 条匹配记录'
            if pinyin_full and pinyin_full != keyword:
                search_info += f' (包含拼音: {pinyin_full})'
            if pinyin_initials and pinyin_initials != keyword:
                search_info += f' (包含首字母: {pinyin_initials})'
                
            self.status_bar.showMessage(search_info)
            
        except Exception as e:
            notify(self, f'搜索失败: {str(e)}', 'error')

    def search_all_columns(self):
        """搜索所有列的内容 - 增强版支持拼音（多线程）"""
        keyword = self.search_input.text().strip()
        if not keyword:
            self.load_data()
            return
        
        try:
            self.status_bar.showMessage(f"正在搜索: {keyword}...")
            
            # 使用多线程执行搜索
            # ★ v6.3：替换引用前先保活旧线程（同 load_data）
            self._retire_worker(getattr(self, 'search_worker', None))
            self.search_worker = SearchDataWorker(self.user_db.clone_for_thread(), keyword)
            self.search_worker.finished.connect(lambda data: self._on_search_finished(data, keyword))
            self.search_worker.error.connect(lambda e: notify(self, f'搜索失败: {e}', 'error'))
            self.search_worker.status.connect(lambda msg: self.status_bar.showMessage(msg))
            self.active_workers.append(self.search_worker)
            self.search_worker.start()
            
        except Exception as e:
            notify(self, f'搜索失败: {str(e)}', 'error')

    def _on_search_finished(self, data, keyword):
        """搜索完成回调（主线程）"""
        self._cleanup_worker(getattr(self, 'search_worker', None))
        self._cleanup_finished_workers()
        # ★ v6.14：搜索结果也当“当前数据源”——点表头排序时在（高亮过的）结果里排，
        #   而不是把搜索结果丢掉重新加载全部数据
        self._render_source_data = data
        self._render_source_keyword = keyword
        self._render_from_source()

    def display_search_results(self, data, keyword):
        """显示搜索结果

        ★ v6.10：与数据列表一样，只渲染「显示列」（隐藏列不画，数据也没丢，
        清除搜索/刷新后就回来）。
        """
        # ★ v6.10：搜索结果的表头/列数按当前显示列重建（切换显示列后立即搜索也不会错位）
        visible = self._visible_columns(self.columns_config)
        # ★ v6.13：条形码/二维码图列单独受显示列设置控制（且不跟着“显示列”走）
        barcode_cols = self._visible_image_columns('barcode', self.columns_config)
        qrcode_cols = self._visible_image_columns('qrcode', self.columns_config)
        headers = [col['label'] for col in visible]
        headers += [col.get('label') or f"{col.get('name')}条形码" for col in barcode_cols]
        headers += [col.get('label') or f"{col.get('name')}二维码" for col in qrcode_cols]
        self.data_table.setColumnCount(len(headers))
        self.data_table.setHorizontalHeaderLabels(headers)
        self.data_table.setRowCount(len(data))
        # ★ v6.13：同样要清掉上一次遗留的 cellWidget（与 _update_table_with_data 同理）
        self.data_table.clearContents()

        # ★ v6.7：按列名取值；★ v6.10：下标表按【全部列】建
        col_index = self._table_column_index(self.columns_config)
        
        # 马卡龙色系交替行颜色
        colors = [
            MacaronColors.MINT_GREEN,    # 薄荷绿
            MacaronColors.SKY_BLUE,      # 天空蓝
            MacaronColors.LEMON_YELLOW,  # 柠檬黄
            MacaronColors.LAVENDER,      # 薰衣草紫
            MacaronColors.PEACH_ORANGE   # 蜜桃橙
        ]
        
        for row_idx, row_data in enumerate(data):
            # rowid是第一个元素
            rowid = row_data[0]
            row_values = row_data[1:]
            
            # 设置交替行背景色
            color = colors[row_idx % len(colors)]
            
            # 高亮显示匹配的文本
            for col_idx, col in enumerate(visible):
                value = self._value_by_name(col_index, row_values, col['name'])
                display_text = value
                
                # 高亮匹配的关键词
                if value and keyword and keyword.lower() in value.lower():
                    display_text = self.highlight_keyword(value, keyword)
                
                item = QTableWidgetItem(display_text)
                item.setData(Qt.UserRole, rowid)
                
                # 设置背景色和文本颜色
                item.setBackground(QColor(color))
                item.setForeground(QColor('#333333'))
                
                # ★ v6.18：不符合该列 Code128 子集的值 → 字体换成反差明显的深红
                mark_barcode_mismatch(item, col, value)
                
                # 设置对齐方式
                item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                
                self.data_table.setItem(row_idx, col_idx, item)
            
            # 添加条形码和二维码（★ v6.13：与表头同一套“图列”开关）
            self.add_barcode_qrcode_to_table(row_idx, row_values, color, visible,
                                             barcode_cols, qrcode_cols)
        
        self.data_table.resizeColumnsToContents()
        self.data_table.resizeRowsToContents()
        
        # 更新状态栏
        self.status_bar.showMessage(f'找到 {len(data)} 条包含 "{keyword}" 的记录')

    def highlight_keyword(self, text, keyword):
        """在文本中高亮显示关键词"""
        # 这里可以返回带HTML格式的文本，但QTableWidgetItem不支持HTML
        # 所以返回原始文本，高亮效果可以在单元格渲染器中实现
        return text

    def add_barcode_qrcode_to_table(self, row_idx, row_values, color, columns=None,
                                    barcode_cols=None, qrcode_cols=None):
        """为表格行添加条形码和二维码

        ★ v6.10：只画“显示列”里的条码/二维码列，与表头一致；取值按【列名】取
        （原来是按位置，配置与表结构一旦不一致就会串列）。
        ★ v6.13：条形码/二维码图列可以单独控制（`_visible_image_columns()`）；
        图列条目用 `source` 指向来源列取值。
        """
        if columns is None:
            columns = self._visible_columns(self.columns_config)
        if barcode_cols is None:
            barcode_cols = self._visible_image_columns('barcode', self.columns_config)
        if qrcode_cols is None:
            qrcode_cols = self._visible_image_columns('qrcode', self.columns_config)
        col_index = self._table_column_index(self.columns_config)
        col_offset = len(columns)
        
        # 添加条形码
        for col in barcode_cols:
            barcode_value = self._value_by_name(col_index, row_values,
                                                col.get('source') or col['name'])
            _bg = barcode_background_for(barcode_value, col.get('barcode_format', 'auto'),
                                         col.get('barcode_subset', 'auto'))
            barcode_img = (self.generate_barcode(barcode_value,
                                                 col.get('barcode_format', 'auto'),
                                                 col.get('barcode_subset', 'auto'),
                                                 background=_bg)
                           if barcode_value else None)
            if barcode_img:
                # ★ v6.21：缩略图（行高不变）+ 双击放大（放大后可直接扫）
                label = BarcodeZoomLabel()
                label.setPixmap(self.barcode_preview_pixmap(barcode_img))
                label.setAlignment(Qt.AlignCenter)
                # ★ v6.20：不符合子集的条码图用浅红底
                label.setStyleSheet(f"background-color: {_bg or color};")
                # ★ v6.19：悬停看实际用的哪个子集
                label.setToolTip(describe_barcode_encoding(
                    barcode_value, col.get('barcode_format', 'auto'),
                    col.get('barcode_subset', 'auto'))
                    + '\n（表格里是缩略图 —— 双击这张条码可放大，放大后就能直接扫）')
                label.doubleClicked.connect(partial(
                    self.show_barcode_zoom, barcode_value,
                    col.get('barcode_format', 'auto'),
                    col.get('barcode_subset', 'auto'), _bg))
                self.data_table.setCellWidget(row_idx, col_offset, label)
            col_offset += 1
        
        # 添加二维码
        for col in qrcode_cols:
            qrcode_value = self._value_by_name(col_index, row_values,
                                               col.get('source') or col['name'])
            qrcode_img = self.generate_qrcode(qrcode_value) if qrcode_value else None
            if qrcode_img:
                label = QLabel()
                label.setPixmap(QPixmap.fromImage(qrcode_img))
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet(f"background-color: {color};")
                self.data_table.setCellWidget(row_idx, col_offset, label)
            col_offset += 1


    def clear_search(self):
        self.search_input.clear()
        self.load_data()
    
    def table_selection_changed(self):
        selected_rows = self.data_table.selectionModel().selectedRows()
        if selected_rows:
            row = selected_rows[0].row()
            rowid_item = self.data_table.item(row, 0)
            if rowid_item:
                self.current_rowid = rowid_item.data(Qt.UserRole)
                print(f"[DEBUG] Selected row ID: {self.current_rowid}")
            else:
                self.current_rowid = None
                print("[DEBUG] No row ID item found")
        else:
            self.current_rowid = None


    
    def edit_selected_data(self):
        """编辑选中的数据"""
        selected_rows = self.data_table.selectionModel().selectedRows()
        if not selected_rows:
            notify(self, '请选择要编辑的行', 'warn')
            return
        
        row = selected_rows[0].row()
        rowid_item = self.data_table.item(row, 0)
        
        if not rowid_item:
            notify(self, '无法获取行ID', 'warn')
            return
        
        self.current_rowid = rowid_item.data(Qt.UserRole)
        if not self.current_rowid:
            notify(self, '获取的行ID无效', 'warn')
            return
        
        data = self.user_db.get_data_by_id(self.current_rowid)
        if not data:
            notify(self, '无法获取数据', 'warn')
            return
        
        # 准备当前数据
        current_data = {}
        values = data[1:]  # 跳过rowid
        for col_idx, col in enumerate(self.columns_config):
            if col_idx < len(values):
                current_data[col['name']] = str(values[col_idx]) if values[col_idx] is not None else ""
        
        # 创建并显示编辑对话框
        dialog = EditDataDialog(self.columns_config, current_data, self)
        if dialog.exec() == QDialog.Accepted:
            new_data = dialog.get_data()
            
            # 检查唯一性约束
            for col in self.columns_config:
                if col.get('is_unique', False) and new_data[col['name']]:
                    if not self.user_db.check_unique(col['name'], new_data[col['name']], self.current_rowid):
                        notify(self, f"{col['label']} 的值必须唯一", 'warn')
                        return
            
            try:
                # 更新数据
                if self.user_db.update_data(self.current_rowid, new_data):
                    notify(self, '数据更新成功', 'info')
                    self.load_data()
                else:
                    notify(self, '更新数据失败', 'warn')
            except Exception as e:
                notify(self, f'更新数据失败: {str(e)}', 'warn')





    
    def add_data(self):
        """添加新数据"""
        # ★ v6.7：先确认列配置与真实表结构一致，否则会报 no column named xxx
        #   （列配置与表结构分裂时，录入框里的列名在表里根本不存在）
        if self._sync_columns_config_with_db():
            self.status_bar.showMessage('⚠️ 列配置与表结构不一致，已按表结构自动修正', 6000)
            self.load_data()
        dialog = EditDataDialog(self.columns_config, parent=self)
        if dialog.exec() == QDialog.Accepted:
            data = dialog.get_data()
            
            # 检查唯一性约束
            for col in self.columns_config:
                if col.get('is_unique', False) and data[col['name']]:
                    if not self.user_db.check_unique(col['name'], data[col['name']]):
                        notify(self, f"{col['label']} 的值必须唯一", 'warn')
                        return
            
            try:
                # 插入数据（insert_data 返回新记录的 rowid）
                new_rowid = self.user_db.insert_data(data)
                if new_rowid:
                    # ★ v6.4：按用户要求不再弹「数据添加成功」对话框，改在状态栏提示
                    # （顺带避免了嵌套事件循环里又去刷新表格）
                    self.status_bar.showMessage('✅ 数据添加成功', 5000)
                    # ★ v6.6：记下新记录 rowid，加载完成后自动翻到那一行
                    self._scroll_to_last_after_load = True
                    self._pending_scroll_rowid = new_rowid
                    self.load_data()
                else:
                    self.status_bar.showMessage('❌ 添加数据失败', 8000)
            except Exception as e:
                notify(self, f'添加数据失败: {str(e)}', 'warn')

    
    def update_data(self):
        """
        更新现有数据
        1. 检查是否选择了要更新的行
        2. 验证输入数据
        3. 检查唯一性约束
        4. 更新数据
        5. 刷新界面
        """
        # 检查是否选择了要更新的行
        if not hasattr(self, 'current_rowid') or not self.current_rowid:
            print("[DEBUG] current_rowid is None or empty")
            # 更详细的错误提示
            if not self.data_table.selectionModel().selectedRows():
                notify(self, '请先在表格中选择要更新的行', 'warn')
            else:
                print("[DEBUG] current_rowid is None or empty")
                notify(self, '无法获取选中行的ID，请尝试重新选择', 'warn')
            return
        
        print(f"[DEBUG] Updating data with rowid: {self.current_rowid}")

        # 验证输入数据
        data, errors = self.validate_input_data()
        if errors:
            notify(self, '\n'.join(errors), 'warn')
            return
        
        # 检查唯一性约束
        for col in self.columns_config:
            if col.get('is_unique', False) and data[col['name']]:
                if not self.user_db.check_unique(col['name'], data[col['name']], self.current_rowid):
                    notify(self, f"{col['label']} 的值必须唯一", 'warn')
                    self.input_widgets[col['name']].setStyleSheet("background-color: #FFCDD2;")
                    return
        
        try:
            # 更新数据
            if self.user_db.update_data(self.current_rowid, data):
                notify(self, '数据更新成功', 'info')
                # 重置状态并刷新数据
                self.current_rowid = None
                self.clear_inputs()
                self.load_data()
                self.update_btn.setEnabled(False)
            else:
                notify(self, '更新数据失败', 'warn')
        except Exception as e:
            notify(self, f'更新数据失败: {str(e)}', 'warn')

    
    def delete_data(self):
        selected_rows = self.data_table.selectionModel().selectedRows()
        if not selected_rows:
            notify(self, '请选择要删除的行', 'warn')
            return
        
        row = selected_rows[0].row()
        rowid = self.data_table.item(row, 0).data(Qt.UserRole)
        
        reply = QMessageBox.question(self, '确认', '确定要删除这条数据吗?', 
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                if self.user_db.delete_data(rowid):
                    notify(self, '数据删除成功', 'info')
                    self.load_data()
                else:
                    notify(self, '删除数据失败', 'warn')
            except Exception as e:
                notify(self, f'删除数据失败: {str(e)}', 'warn')
    
    def clear_inputs(self):
        for widget in self.input_widgets.values():
            widget.clear()
        self.current_rowid = None
        self.update_btn.setEnabled(False)

    
    def _build_detail_nav(self):
        """详情页导航条（★ v6.16）：⬆上一条 / ⬇下一条 + 位置提示 + 回到列表

        用户要求：「详细信息增加 向上和向下翻，就不用每次都回到列表点才能看详细，
        直接详细就可以直接下一个，或者上一个」。
        """
        nav = QHBoxLayout()

        self.detail_prev_btn = QPushButton('⬆ 上一条')
        self.detail_prev_btn.setToolTip('看上一条记录的详情（Alt+↑）')
        self.detail_prev_btn.clicked.connect(lambda: self.detail_goto_offset(-1))
        nav.addWidget(self.detail_prev_btn)

        self.detail_next_btn = QPushButton('⬇ 下一条')
        self.detail_next_btn.setToolTip('看下一条记录的详情（Alt+↓）')
        self.detail_next_btn.clicked.connect(lambda: self.detail_goto_offset(1))
        nav.addWidget(self.detail_next_btn)

        self.detail_pos_label = QLabel('')
        self.detail_pos_label.setStyleSheet('color: #666;')
        nav.addWidget(self.detail_pos_label)

        nav.addStretch()

        # ★ v6.22：详情页也能直接改“哪些项目不显示”（想只看二维码/只看一维码时最顺手）
        self.detail_visibility_btn = QPushButton('👁️ 显示设置')
        self.detail_visibility_btn.setToolTip(
            '选择这个详情页里显示哪些项目（字段 / 条形码 / 二维码）\n'
            '例如只看二维码、只看一维码；只影响详情页显示，数据不受影响')
        self.detail_visibility_btn.clicked.connect(self.configure_detail_visibility)
        nav.addWidget(self.detail_visibility_btn)

        self.detail_back_btn = QPushButton('📊 回到列表')
        self.detail_back_btn.setToolTip('切回「📊数据列表」（选中行已经跟着翻到这里了）')
        self.detail_back_btn.clicked.connect(self.detail_back_to_list)
        nav.addWidget(self.detail_back_btn)
        return nav

    def _update_detail_nav(self, row=None):
        """刷新详情页导航（★ v6.16）：第一条/最后一条时把对应按钮置灰 + 显示「第 N / M 条」"""
        try:
            rows = self.data_table.rowCount()
            if row is None:
                row = self.data_table.currentRow()
            if rows <= 0 or row is None or row < 0:
                self.detail_pos_label.setText('（没有数据）')
                self.detail_prev_btn.setEnabled(False)
                self.detail_next_btn.setEnabled(False)
                return
            self.detail_pos_label.setText(f'第 {row + 1} / {rows} 条')
            self.detail_prev_btn.setEnabled(row > 0)
            self.detail_next_btn.setEnabled(row < rows - 1)
        except Exception as e:
            print(f"[DEBUG] 刷新详情导航失败: {str(e)}")

    def detail_goto_offset(self, offset, need_detail_tab=False):
        """详情页翻上一条 / 下一条（★ v6.16）

        做法：把表格当前行上/下移一条（列表的选中也跟着走，回到列表时位置对得上），
        然后重画详情。到头了就状态栏提示，不会绕回另一头（避免“翻着翻着跑回去了”）。
        """
        if need_detail_tab and self.tab_widget.currentWidget() is not self.detail_widget:
            return  # 快捷键只在详情页生效
        rows = self.data_table.rowCount()
        if rows <= 0:
            notify(self, '当前没有数据', 'warn')
            return
        cur = self.data_table.currentRow()
        if cur is None or cur < 0:
            cur = 0
        target = cur + offset
        if target < 0:
            self.status_bar.showMessage('已经是第一条了', 4000)
            self._update_detail_nav(row=cur)
            return
        if target >= rows:
            self.status_bar.showMessage(f'已经是最后一条了（共 {rows} 条）', 4000)
            self._update_detail_nav(row=cur)
            return

        # 选中跟着一起走（blockSignals 避免重复触发选中变化处理）
        self.data_table.blockSignals(True)
        try:
            self.data_table.selectRow(target)
            self.data_table.setCurrentCell(target, 0)
        finally:
            self.data_table.blockSignals(False)
        item = self.data_table.item(target, 0)
        if item is not None:
            self.data_table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
        self.show_item_detail(row=target)

    def detail_back_to_list(self):
        """详情页 → 回到数据列表（★ v6.16；此时列表选中已经在同一条上）"""
        try:
            self.tab_widget.setCurrentWidget(self.data_table)
        except Exception as e:
            print(f"[DEBUG] 切回数据列表失败: {str(e)}")

    def show_item_detail(self, index=None, row=None):
        """显示选中项的详细信息，确保只显示配置中标记为条形码/二维码的列

        ★ v6.16：`index`（双击表格给的 QModelIndex）和 `row`（详情页翻上/下一条用）
        二选一；都不给就用当前选中行。
        """
        # 清除之前的详情内容（★ v6.3：改用安全的布局清空）
        self._clear_layout(self.detail_layout)
        
        # 获取选中数据
        if row is None:
            row = index.row() if index is not None else self.data_table.currentRow()
        if row is None or row < 0 or row >= self.data_table.rowCount():
            self._update_detail_nav(row=-1)
            return
        self._update_detail_nav(row=row)
        # ★ v6.22：记住“现在详情页显示的是第几条”
        #   —— 改显示设置后重画时用它兜底（表格刚重画过时 currentRow() 可能是 -1，
        #      直接用它重画会把详情页清空）
        self._detail_row = row

        item0 = self.data_table.item(row, 0)
        if item0 is None:
            print(f"[DEBUG] 第 {row} 行还没渲染出来，跳过详情")
            return
        rowid = item0.data(Qt.UserRole)
        data = self.user_db.get_data_by_id(rowid)
        print(f"[DEBUG] Selected row ID: {rowid}")

        if not data:
            print("[DEBUG] No data found for the selected row")
            notify(self, '无法获取数据详情', 'warn')
            return

        # 创建带滚动区域的容器
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        
        # 1. 基本信息区块
        #   ★ v6.22：只画「详细信息显示设置」里勾了的字段（全被隐藏就不画这一块）
        info_group = QGroupBox("基本信息")
        form = QFormLayout()
        
        # 修改点1：确保正确获取所有列值
        values = data[1:]  # 跳过rowid，保留所有列值
        
        # 修改点2：确保列配置和列值正确对应
        for col_idx, col in enumerate(self.columns_config):
            if col_idx < len(values):
                # ★ v6.22：这一列被设为“详情页不显示” → 跳过
                if not self._detail_item_visible(DETAIL_FIELD_PREFIX + str(col.get('name'))):
                    continue
                # 特殊字段标记
                label_text = col['label']
                if col.get('is_unique', False):
                    label_text += " 🔑"
                if col.get('is_required', False):
                    label_text += " *"
                
                label = QLabel(label_text)
                value = QLabel(str(values[col_idx]) if values[col_idx] else "(空)")
                value.setTextInteractionFlags(Qt.TextSelectableByMouse)
                
                # ★ v6.18：不符合该列 Code128 子集的值 → 跟数据列表一致用深红标出来
                #   （详情页底色是浅色窗底，深红对比度更高，一样看得清）
                _mismatch = code128_subset_mismatch(
                    values[col_idx], col.get('barcode_format', 'auto'),
                    col.get('barcode_subset', 'auto'))
                if _mismatch:
                    value.setStyleSheet(
                        f'color: {BARCODE_MISMATCH_COLOR}; font-weight: bold;')
                    value.setToolTip(f'⚠️ {_mismatch}')
                
                if col.get('is_unique', False):
                    label.setStyleSheet("color: #4CAF50; font-weight: bold;")
                if col.get('is_required', False):
                    label.setStyleSheet("color: #F44336; font-weight: bold;")
                
                form.addRow(label, value)
        
        # ★ v6.22：字段全被隐藏时这块整个不画（否则会出现一个空的「基本信息」框）
        if form.count() > 0:
            info_group.setLayout(form)
            layout.addWidget(info_group)

        # 2. 条形码区块 - 只处理配置中标记为is_barcode=True的列
        #   ★ v6.22：还要过「详细信息显示设置」（barcode:<列名> 没被隐藏才画）
        barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
        if barcode_cols:
            barcode_group = QGroupBox("条形码信息")
            barcode_layout = QVBoxLayout()

            for col in barcode_cols:
                col_idx = self.columns_config.index(col)
                if not self._detail_item_visible(DETAIL_BARCODE_PREFIX + str(col.get('name'))):
                    continue
                if col_idx < len(values) and values[col_idx]:
                    value = values[col_idx]
                    
                    # 生成条形码图像（★ v6.9：按该列配置的格式/子集；★ v6.20：不符合子集用浅红底）
                    _bg2 = barcode_background_for(value, col.get('barcode_format', 'auto'),
                                                  col.get('barcode_subset', 'auto'))
                    # ★ v6.21：详情页是「拿来扫」的地方 → 用 2 倍分辨率出图（每模块 ≥4px）
                    barcode_img = self.generate_barcode(value,
                                                        col.get('barcode_format', 'auto'),
                                                        col.get('barcode_subset', 'auto'),
                                                        background=_bg2,
                                                        scale=BARCODE_RENDER_SCALE)
                    if barcode_img:
                        # 原始值标签
                        value_label = QLabel(f"{col['label']}：{value}")
                        value_label.setStyleSheet("font-weight: bold;")
                        # ★ v6.18：这个值装不进所选 Code128 子集 → 与列表一致用深红标出来
                        _mismatch2 = code128_subset_mismatch(
                            value, col.get('barcode_format', 'auto'),
                            col.get('barcode_subset', 'auto'))
                        if _mismatch2:
                            value_label.setStyleSheet(
                                f'color: {BARCODE_MISMATCH_COLOR}; font-weight: bold;')
                            value_label.setToolTip(f'⚠️ {_mismatch2}')
                        barcode_layout.addWidget(value_label)
                        
                        # 条形码图像
                        img_label = QLabel()
                        pixmap = QPixmap.fromImage(barcode_img)
                        img_label.setPixmap(pixmap)
                        img_label.setAlignment(Qt.AlignCenter)
                        # ★ v6.20：不符合子集 → 浅红底
                        img_label.setStyleSheet(f"background-color: {_bg2 or 'white'};")
                        # ★ v6.19：条码图下面直接写出「实际用的哪种子集」
                        _enc_desc = describe_barcode_encoding(
                            value, col.get('barcode_format', 'auto'),
                            col.get('barcode_subset', 'auto'))
                        img_label.setToolTip(_enc_desc
                                             + '\n（这张是放大出图，可直接对着扫）')
                        barcode_layout.addWidget(img_label)
                        enc_label = QLabel(f'编码：{_enc_desc}　·　这张图已放大出图，可直接扫')
                        enc_label.setStyleSheet('color: #666; font-size: 11px;')
                        enc_label.setWordWrap(True)
                        barcode_layout.addWidget(enc_label)
                        
                        # 添加分隔线
                        barcode_layout.addWidget(QLabel("─"*50))

            if barcode_layout.count() > 0:
                barcode_group.setLayout(barcode_layout)
                layout.addWidget(barcode_group)

        # 3. 二维码区块 - 只处理配置中标记为is_qrcode=True的列
        #   ★ v6.22：还要过「详细信息显示设置」（qrcode:<列名> 没被隐藏才画）
        qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
        if qrcode_cols:
            qrcode_group = QGroupBox("二维码信息")
            qrcode_layout = QVBoxLayout()
            
            for col in qrcode_cols:
                col_idx = self.columns_config.index(col)
                if not self._detail_item_visible(DETAIL_QRCODE_PREFIX + str(col.get('name'))):
                    continue
                if col_idx < len(values) and values[col_idx]:
                    value = values[col_idx]
                    # 生成二维码图像
                    qrcode_img = self.generate_qrcode(value)
                    
                    if qrcode_img:
                        # 值标签
                        value_label = QLabel(f"{col['label']}：{value}")
                        value_label.setStyleSheet("font-weight: bold;")
                        qrcode_layout.addWidget(value_label)
                        
                        # 二维码图像
                        img_label = QLabel()
                        pixmap = QPixmap.fromImage(qrcode_img)
                        img_label.setPixmap(pixmap)
                        img_label.setAlignment(Qt.AlignCenter)
                        qrcode_layout.addWidget(img_label)
                        
                        # 添加分隔线
                        qrcode_layout.addWidget(QLabel("─"*50))
            
            if qrcode_layout.count() > 0:
                qrcode_group.setLayout(qrcode_layout)
                layout.addWidget(qrcode_group)

        # 4. 操作按钮区块
        btn_group = QWidget()
        btn_layout = QHBoxLayout()
        
        print_btn = QPushButton("🖨️ 打印详情")
        print_btn.setStyleSheet("padding: 8px;")
        print_btn.clicked.connect(lambda: self.print_item_detail(data))
        btn_layout.addWidget(print_btn)
        
        copy_btn = QPushButton("⎘ 复制文本")
        copy_btn.setStyleSheet("padding: 8px;")
        copy_btn.clicked.connect(lambda: self.copy_item_detail(data))
        btn_layout.addWidget(copy_btn)
        
        export_btn = QPushButton("💾 导出图片")
        export_btn.setStyleSheet("padding: 8px;")
        export_btn.clicked.connect(lambda: self.export_images(data))
        btn_layout.addWidget(export_btn)
        
        btn_group.setLayout(btn_layout)
        layout.addWidget(btn_group)

        # 设置滚动区域内容
        layout.addStretch()
        scroll.setWidget(content)
        self.detail_layout.addWidget(scroll)
        
        # 切换到详情标签页
        self.tab_widget.setCurrentIndex(1)



    def export_images(self, data):
        """导出条形码/二维码为图片"""
        path = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not path:
            return
        
        try:
            values = data[2:]
            
            # 导出条形码
            barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
            for col in barcode_cols:
                col_idx = self.columns_config.index(col)
                if col_idx < len(values) and values[col_idx]:
                    # ★ v6.21：导出的图片是给别家程序扫的 → 2 倍分辨率（原来只有 97px 宽）
                    img = self.generate_barcode(values[col_idx],
                                                col.get('barcode_format', 'auto'),
                                                col.get('barcode_subset', 'auto'),
                                                scale=BARCODE_RENDER_SCALE)
                    if img:
                        filename = f"{path}/{col['name']}_barcode.png"
                        img.save(filename)
            
            # 导出二维码
            qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
            for col in qrcode_cols:
                col_idx = self.columns_config.index(col)
                if col_idx < len(values) and values[col_idx]:
                    img = self.generate_qrcode(values[col_idx])
                    if img:
                        filename = f"{path}/{col['name']}_qrcode.png"
                        img.save(filename)
            
            notify(self, f"图片已保存到：\n{path}", 'info')
        except Exception as e:
            notify(self, f"导出失败：{str(e)}", 'error')

    
    def export_data(self):
        filename, _ = QFileDialog.getSaveFileName(self, '导出数据', '', '所有文件 (*.*);;CSV文件 (*.csv)')
        if not filename:
            return
        
        try:
            self.status_bar.showMessage("正在导出数据...")
            
            # 使用多线程导出
            self._retire_worker(getattr(self, 'export_worker', None))
            self.export_worker = ExportDataWorker(self.user_db.clone_for_thread(), filename)
            self.export_worker.finished.connect(lambda success: self._on_export_finished(success, filename))
            self.export_worker.error.connect(lambda e: notify(self, f'导出数据失败: {e}', 'error'))
            self.export_worker.status.connect(lambda msg: self.status_bar.showMessage(msg))
            self.active_workers.append(self.export_worker)
            self.export_worker.start()
            
        except Exception as e:
            notify(self, f'导出数据失败: {str(e)}', 'error')

    def _on_export_finished(self, success, filename):
        """导出完成回调"""
        self._cleanup_worker(getattr(self, 'export_worker', None))
        self._cleanup_finished_workers()
        if success:
            self.status_bar.showMessage(f"数据已导出到 {filename}")
            notify(self, f'数据已导出到 {filename}', 'info')
        else:
            self.status_bar.showMessage("导出数据失败")
            notify(self, '导出数据失败', 'warn')
    
    def show_user_settings(self):
        settings = self.user_manager.get_user_settings(self.username)
        try:
            columns_config = json.loads(settings)
        except:
            columns_config = []
        
        dialog = QDialog(self)
        dialog.setWindowTitle('用户设置')
        dialog.resize(600, 400)
        
        layout = QVBoxLayout()

        # ★ v6.10：让人一眼看到“现在到底显示了几列”
        try:
            total_cols = len(self.columns_config or [])
            visible_cols = len(self._visible_columns())
            hidden_cols = total_cols - visible_cols
            layout.addWidget(QLabel(
                f'列：共 {total_cols} 列，数据列表显示 {visible_cols} 列'
                + (f'（隐藏 {hidden_cols} 列）' if hidden_cols > 0 else '（全部显示）')))
        except Exception:
            pass
        
        layout.addWidget(QLabel('当前列配置（JSON）：'))

        # 显示当前列配置
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(json.dumps(columns_config, indent=2, ensure_ascii=False))
        layout.addWidget(text_edit)

        # ★ v6.5：登录后直接在这里改列配置
        btn_row = QHBoxLayout()
        modify_btn = QPushButton('🧩修改列配置')

        def _on_modify():
            clicked['modify'] = True
            dialog.accept()

        modify_btn.clicked.connect(_on_modify)
        btn_row.addWidget(modify_btn)

        # ★ v6.10：登录后直接在这里改“数据列表显示哪几列”
        visibility_btn = QPushButton('👁️显示列设置')

        def _on_visibility():
            clicked['visibility'] = True
            dialog.accept()

        visibility_btn.clicked.connect(_on_visibility)
        btn_row.addWidget(visibility_btn)

        # ★ v6.22：登录后直接在这里改“详细信息页显示哪些项目”
        detail_visibility_btn = QPushButton('🔎详细信息显示设置')

        def _on_detail_visibility():
            clicked['detail_visibility'] = True
            dialog.accept()

        detail_visibility_btn.clicked.connect(_on_detail_visibility)
        btn_row.addWidget(detail_visibility_btn)
        layout.addLayout(btn_row)
        
        # 关闭按钮
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.setLayout(layout)

        clicked = {'modify': False, 'visibility': False, 'detail_visibility': False}
        dialog.exec()

        if clicked['modify']:
            self.modify_columns_config()
        elif clicked['visibility']:
            self.configure_column_visibility()
        elif clicked['detail_visibility']:
            self.configure_detail_visibility()

    def modify_columns_config(self):
        """修改当前登录用户的列配置（★ v6.5）

        与登录窗口的那一项完全等价：先备份 → 迁移表结构（保留已有数据）→
        保存用户配置 → 刷新界面（表头/统计/详情）。
        """
        if not getattr(self, 'username', None) or not getattr(self, 'db_file', None):
            notify(self, '无法确定当前用户信息', 'warn')
            return

        # 当前配置：优先用数据库真实表结构（★ v6.7，避免拿到与表不符的旧配置）
        self._sync_columns_config_with_db()
        current_config = [dict(c) for c in (self.columns_config or [])]
        if not current_config:
            try:
                settings = self.user_manager.get_user_settings(self.username)
                current_config = json.loads(settings) if settings else []
            except Exception:
                current_config = []
        print(f"[DEBUG] 修改列配置：当前共 {len(current_config)} 列")

        col_dialog = ColumnConfigDialog(self)
        col_dialog.load_existing_config(current_config)
        if col_dialog.exec() != QDialog.Accepted:
            return

        new_columns_config = col_dialog.get_columns_config()
        if not new_columns_config:
            notify(self, '必须至少配置一列', 'warn')
            return

        dup = duplicate_column_names(new_columns_config)
        if dup:
            notify(self, f'列名重复：{"、".join(dup)}\n请先改成不同的列名', 'warn')
            return

        # ★ v6.7：把「哪些列保留数据、哪些列会丢」直接写在确认框里，
        #   免得用户“只想加一列”却把整表配置换掉
        preview = column_config_change_preview(self.db_file, new_columns_config)
        reply = QMessageBox.question(
            self, '修改列配置',
            '将修改当前用户的列配置：\n\n'
            '  • 新增列：已有记录该列为空\n'
            '  • 删除列：只丢该列的数据\n'
            '  • 列名改了但「中文名」没改：自动跟着改名，数据保留\n'
            + preview +
            '\n\n改前会自动备份数据库。确定要继续吗？',
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        # ★ 改配置会重建 data 表：先停掉正在跑的加载/搜索等线程，
        #   避免它们握着旧表结构（也避免写库时和迁移相撞）
        for worker in list(self.active_workers):
            try:
                worker.cancel()
            except Exception:
                pass
        for worker in list(self.active_workers) + list(self._retired_workers):
            try:
                if worker is not None and worker.isRunning():
                    worker.wait(2000)
            except Exception:
                pass

        try:
            ok, msg = apply_column_config_migration(self.db_file, self.username,
                                                    self.user_manager, new_columns_config)
        except Exception as e:
            notify(self, f'修改列配置失败: {str(e)}', 'error')
            return

        if not ok:
            notify(self, f'列配置修改失败: {msg}', 'warn')
            return

        # 刷新界面：列配置 / 表头 / 统计 / 详情
        self.columns_config = new_columns_config
        self.current_rowid = None
        # ★ v6.10：列改名/删列后，把对不上的“隐藏列”清掉，免得以后新列同名却天生隐藏
        try:
            self._sanitize_hidden_columns(persist=True)
        except Exception as e:
            print(f"[DEBUG] 校正显示列设置失败: {str(e)}")
        # ★ v6.22：详情页的“显示项”同理（field:/barcode:/qrcode: 旧键一起清）
        try:
            self._sanitize_hidden_detail_items(persist=True)
        except Exception as e:
            print(f"[DEBUG] 校正详细信息显示设置失败: {str(e)}")
        try:
            self.sort_orders = {}
            self._clear_layout(self.detail_layout)
        except Exception:
            pass

        self.load_data()
        self.status_bar.showMessage(f'✅ 列配置已更新（{msg}）', 8000)
        notify(self, f'列配置修改成功\n\n{msg}\n（已自动备份数据库，数据已保留）', 'info')
    
    def show_help(self):
        """显示使用说明（来自 ProjectInfo.HELP_TEXT，修复死代码）"""
        try:
            help_text = ProjectInfo.HELP_TEXT.format(cls=ProjectInfo)
        except Exception:
            help_text = ProjectInfo.HELP_TEXT
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle('使用说明')
        msg_box.setText(help_text)
        msg_box.exec()

    def show_about(self):
        """显示关于对话框，使用ProjectInfo中的元数据"""
        about_text = f"""
        {ProjectInfo.NAME} {ProjectInfo.VERSION} (Build: {ProjectInfo.BUILD_DATE})
        
        {ProjectInfo.DESCRIPTION}
        
        作者: {ProjectInfo.AUTHOR}
        许可证: {ProjectInfo.LICENSE}
        项目地址: {ProjectInfo.URL}
        
        {ProjectInfo.COPYRIGHT}
        """
        
        # 添加版本历史
        version_history = "\n版本历史:\n"
        for version, desc in sorted(ProjectInfo.VERSION_HISTORY.items(), reverse=True):
            version_history += f"  {version}: {desc}\n"
        
        about_text += version_history
        
        QMessageBox.about(self, '关于', about_text)

    def show_about_v2(self):
        """显示关于对话框，使用ProjectInfo中的元数据"""
        about_text = f"""
        <b>{ProjectInfo.NAME} {ProjectInfo.VERSION}</b> (Build: {ProjectInfo.BUILD_DATE})
        <p>{ProjectInfo.DESCRIPTION}</p>
        
        <table>
        <tr><td>作者:</td><td>{ProjectInfo.AUTHOR}</td></tr>
        <tr><td>许可证:</td><td>{ProjectInfo.LICENSE}</td></tr>
        <tr><td>项目地址:</td><td><a href="{ProjectInfo.URL}">{ProjectInfo.URL}</a></td></tr>
        </table>
        
        <p><i>{ProjectInfo.COPYRIGHT}</i></p>
        
        <h4>版本历史:</h4>
        <ul>
        """
        
        # 添加版本历史
        for version, desc in sorted(ProjectInfo.VERSION_HISTORY.items(), reverse=True):
            about_text += f"<li><b>{version}:</b> {desc}</li>"
        
        about_text += "</ul>"
        
        msg_box = QMessageBox(self)
        msg_box.setWindowTitle('关于')
        msg_box.setTextFormat(Qt.RichText)  # 启用富文本格式
        msg_box.setText(about_text)
        msg_box.exec()

    
    def switch_user(self):
        """👤切换用户登录（★ v6.15）：回到登录窗口换人登录，**程序不退出**

        用户要求：「点程序关闭就关闭，不用返回到登录窗口。程序增加切换用户登录就可以了。」
        所以「关闭 / ❌退出程序」现在是真退出，换人登录改由这里负责：
          * 先弹一次确认（避免误点）；
          * 置 `_switching_user=True` 再 `close()` —— `closeEvent` 里照旧保存设置、
            取消线程、自动备份、关库，**只有这种情况下**才把登录窗口显示回来。
        """
        reply = QMessageBox.question(
            self, '切换用户登录',
            '确定要切换到其它用户登录吗？\n\n当前数据已即时保存；'
            '切换后会回到登录窗口（程序不退出）。',
            QMessageBox.Yes | QMessageBox.No)
        if reply != QMessageBox.Yes:
            return
        self._switching_user = True
        self.close()

    def logout(self):
        """旧名字，保留兼容 —— 现在就是「👤切换用户登录」（★ v6.15）"""
        self.switch_user()
    
    def generate_barcode(self, data, barcode_format='auto', subset='auto', background=None,
                         scale=1):
        """
        生成条形码图像（返回**原始分辨率**的 QImage，不再缩小）

        ★ v6.9：支持按列配置指定编码格式与 Code128 子集
          barcode_format: auto / code128 / ean13 / ean8 / upca / code39
          subset:         Code128 子集 auto / A / B / C
        ★ v6.20：`background` 可指定条码底色（None＝白底）—— 表格里用浅红底标出
          “装不进所选子集”的条码图（`barcode_background_for()`），导出/打印仍用白底。
        ★ v6.21：`scale` = 出图分辨率倍数（dpi=300*scale）。**不再把图缩到高 100px** ——
          那会把 10 位数字的 C 条码压成 97x100px（黑条只剩 1px、还被插值糊出灰边），
          扫描器读不出来（用户报的「Code128 C 扫不出来」就是这个原因）。
          表格里要小图请用 `barcode_preview_pixmap()` 自己缩；详情/导出/打印传
          `BARCODE_RENDER_SCALE`（每模块 ≥4px）。
        对于非ASCII字符会自动转换为「拼音首字母+哈希」再编码（原有逻辑保留）。
        """
        # 延迟导入重型模块，减少启动时间
        from PIL import Image
        
        if not data:
            return None
        
        try:
            data_str = barcode_display_text(data)   # ★ v6.19：非 ASCII 转拼音+哈希统一在这里
            
            # ★ v6.9：按「列配置里选的格式 + Code128 子集」出码
            #   （数据不满足所选格式时自动回退 Code128，不会出现空白条码）
            barcode, used_format, note = build_barcode_object(
                data_str, barcode_format, subset)
            if note:
                print(f"[DEBUG] {note}（原始值={data_str}）")
            
            # ★ v6.21：靠提高 dpi 把图出大（条宽=整数像素、没有灰度过渡），
            #   绝不能先出小图再放大/缩小 —— 缩小会直接毁掉条码图案。
            opts = {'dpi': int(300 * max(1.0, float(scale or 1)))}
            if background:
                opts['background'] = background   # ★ v6.20
            buffer = io.BytesIO()
            barcode.write(buffer, options=opts)
            
            img = Image.open(buffer)
            img = img.convert("RGBA")
            raw = img.tobytes("raw", "RGBA")
            # .copy() 必须留：QImage 不复制 raw 缓冲区，raw 出作用域后就是野指针
            return QImage(raw, img.size[0], img.size[1],
                          QImage.Format_RGBA8888).copy()
        
        except Exception as e:
            print(f"生成条形码失败: {e}")
            return None
    
    def barcode_preview_pixmap(self, image):
        """把条码图缩成表格里用的**缩略图**（★ v6.21，高度 BARCODE_PREVIEW_HEIGHT）

        `generate_barcode()` 现在返回的是可扫描的高分辨率图（几百 px 高），
        直接塞进表格会把行高撑爆；表格单元格统一走这里缩略显示（行高与旧版一致）。
        缩略图只是给人看的示意图，**要扫描请双击看详细信息、或导出图片**。
        """
        if image is None:
            return None
        pixmap = QPixmap.fromImage(image)
        if pixmap.height() > BARCODE_PREVIEW_HEIGHT:
            pixmap = pixmap.scaledToHeight(BARCODE_PREVIEW_HEIGHT, Qt.SmoothTransformation)
        return pixmap
    def show_barcode_zoom(self, value, barcode_format='auto', subset='auto', background=None):
        """双击表格里的条码缩略图 → 弹出放大条码（★ v6.21，能直接被扫描器读）

        表格里的缩略图只有 100px 高、条太细（扫不出来），这里按 `BARCODE_RENDER_SCALE`
        **重新出一张高分辨率大图**（不缩放缩略图！缩放会把条糊掉），大图可以直接对着
        屏幕扫，也可以「另存为 PNG」交给别的程序读。
        """
        if not value:
            return
        try:
            image = self.generate_barcode(value, barcode_format, subset,
                                          background=background,
                                          scale=BARCODE_RENDER_SCALE)
            if image is None:
                notify(self, f'“{value}”生成不了条码图', 'warn')
                return
            encoding = describe_barcode_encoding(value, barcode_format, subset)
            dlg = BarcodeZoomDialog(self, image, value, encoding,
                                    background=BARCODE_MISMATCH_BG_COLOR if background else None)
            dlg.exec()
        except Exception as e:
            notify(self, f'放大条码失败：{str(e)}', 'error')



    
    def generate_qrcode(self, data):
        # 延迟导入重型模块，减少启动时间
        import qrcode
        from PIL import Image
        
        if not data:
            return None
        
        try:
            data_str = str(data)
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(data_str)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            qimage = QImage(data, img.size[0], img.size[1], QImage.Format_RGBA8888)
            
            return qimage.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception as e:
            print(f"生成二维码失败: {e}")
            return None

    def print_item_detail(self, data):
        """打印数据详情"""
        # 延迟导入打印模块，减少启动时间
        from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        
        try:
            # 创建打印机对象
            printer = QPrinter(QPrinter.HighResolution)
            printer.setPageSize(QPrinter.A4)
            printer.setPageOrientation(QPrinter.Portrait)
            
            # 显示打印对话框
            print_dialog = QPrintDialog(printer, self)
            if print_dialog.exec() != QDialog.Accepted:
                return
            
            # 创建打印文档
            document = QTextDocument()
            
            # 构建HTML格式的打印内容
            html = """
            <html>
            <head>
            <style>
                body { font-family: Arial; margin: 20px; }
                h1 { color: #333; text-align: center; }
                table { width: 100%; border-collapse: collapse; margin-top: 20px; }
                th { background-color: #f2f2f2; text-align: left; padding: 8px; }
                td { padding: 8px; border-bottom: 1px solid #ddd; }
                .barcode { text-align: center; margin: 20px 0; }
                .qrcode { text-align: center; margin: 20px 0; }
                .section { margin-bottom: 30px; }
            </style>
            </head>
            <body>
            <h1>数据详情</h1>
            """
            
            # 添加基本信息表格
            html += "<div class='section'><h2>基本信息</h2><table>"
            values = data[2:]  # 跳过rowid和第一个列
            
            for col_idx, col in enumerate(self.columns_config):
                if col_idx < len(values):
                    required = " (必填)" if col.get('is_required', False) else ""
                    unique = " (唯一)" if col.get('is_unique', False) else ""
                    html += f"""
                    <tr>
                        <th>{col['label']}{required}{unique}</th>
                        <td>{values[col_idx]}</td>
                    </tr>
                    """
            html += "</table></div>"
            
            # 添加条形码
            barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
            if barcode_cols:
                html += "<div class='section'><h2>条形码</h2>"
                for col in barcode_cols:
                    col_idx = self.columns_config.index(col)
                    if col_idx < len(values) and values[col_idx]:
                        barcode_value = values[col_idx]
                        # 保存条形码图片到临时文件
                        # ★ v6.21：打印出来的条码必须够清晰 → 2 倍分辨率
                        barcode_img = self.generate_barcode(
                            barcode_value, col.get('barcode_format', 'auto'),
                            col.get('barcode_subset', 'auto'),
                            scale=BARCODE_RENDER_SCALE)
                        if barcode_img:
                            temp_file = "temp_barcode.png"
                            barcode_img.save(temp_file)
                            html += f"""
                            <div class='barcode'>
                                <h3>{col['label']}</h3>
                                <p>{barcode_value}</p>
                                <img src='{temp_file}' width='300'/>
                            </div>
                            """
                html += "</div>"
            
            # 添加二维码
            qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
            if qrcode_cols:
                html += "<div class='section'><h2>二维码</h2>"
                for col in qrcode_cols:
                    col_idx = self.columns_config.index(col)
                    if col_idx < len(values) and values[col_idx]:
                        qrcode_value = values[col_idx]
                        # 保存二维码图片到临时文件
                        qrcode_img = self.generate_qrcode(qrcode_value)
                        if qrcode_img:
                            temp_file = "temp_qrcode.png"
                            qrcode_img.save(temp_file)
                            html += f"""
                            <div class='qrcode'>
                                <h3>{col['label']}</h3>
                                <p>{qrcode_value}</p>
                                <img src='{temp_file}' width='200'/>
                            </div>
                            """
                html += "</div>"
            
            html += "</body></html>"
            
            # 设置文档内容并打印
            document.setHtml(html)
            document.print_(printer)
            
            # 删除临时文件
            if barcode_cols and os.path.exists("temp_barcode.png"):
                os.remove("temp_barcode.png")
            if qrcode_cols and os.path.exists("temp_qrcode.png"):
                os.remove("temp_qrcode.png")
                
        except Exception as e:
            notify(self, f'打印过程中发生错误: {str(e)}', 'error')


    def copy_item_detail(self, data):
        """复制数据详情到剪贴板"""
        # 实现复制到剪贴板功能
        text = "数据详情:\n"
        values = data[2:]
        
        for col_idx, col in enumerate(self.columns_config):
            if col_idx < len(values):
                text += f"{col['label']}: {values[col_idx]}\n"
        
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        notify(self, '数据已复制到剪贴板', 'info')

    def validate_barcode(self, barcode_value):
        """
        验证条码格式是否正确
        :param barcode_value: 要验证的条码值
        :return: (bool, str) 第一个元素表示是否有效，第二个元素是错误信息
        """
        if not barcode_value:
            return False, "条码不能为空"
        
        # 检查是否为数字
        if not barcode_value.isdigit():
            return False, "EAN13条码必须全为数字"
        
        # 检查长度
        if len(barcode_value) != 13:
            return False, "EAN13条码必须为13位数字"
        
        # 计算校验位
        try:
            check_digit = int(barcode_value[-1])
            sum_ = 0
            for i, digit in enumerate(barcode_value[:-1]):
                digit = int(digit)
                if i % 2 == 0:  # 奇数位(从0开始)
                    sum_ += digit * 1
                else:  # 偶数位
                    sum_ += digit * 3
            calculated_check = (10 - (sum_ % 10)) % 10
            if calculated_check != check_digit:
                return False, "EAN13校验位不正确"
        except:
            return False, "条码格式无效"
        
        return True, ""

    def validate_ean13(self, ean13_value):
        """
        验证EAN13条码格式是否正确
        :param ean13_value: 要验证的EAN13值
        :return: (bool, str) 第一个元素表示是否有效，第二个元素是错误信息
        """
        if not ean13_value:
            return False, "EAN13条码不能为空"
        
        # 检查是否为数字
        if not ean13_value.isdigit():
            return False, "EAN13条码必须全为数字"
        
        # 检查长度
        if len(ean13_value) != 13:
            return False, "EAN13条码必须为13位数字"
        
        # 计算校验位
        try:
            check_digit = int(ean13_value[-1])
            sum_ = 0
            for i, digit in enumerate(ean13_value[:-1]):
                digit = int(digit)
                if i % 2 == 0:  # 奇数位(从0开始)
                    sum_ += digit * 1
                else:  # 偶数位
                    sum_ += digit * 3
            calculated_check = (10 - (sum_ % 10)) % 10
            if calculated_check != check_digit:
                return False, "EAN13校验位不正确"
        except:
            return False, "EAN13条码格式无效"
        
        return True, ""

    def validate_input_data(self):
        """验证输入数据是否符合要求"""
        data = {}
        errors = []
        
        for col in self.columns_config:
            widget = self.input_widgets[col['name']]
            value = widget.text().strip()
            
            # 检查必填项
            if col.get('is_required', False) and not value:
                errors.append(f"{col['label']} 是必填项")
                widget.setStyleSheet("background-color: #FFCDD2;")  # 红色背景提示错误
            else:
                widget.setStyleSheet("")  # 清除错误样式
            
            # 检查EAN13格式
            if col['type'] == 'EAN13' and value:
                is_valid, error_msg = self.validate_ean13(value)
                if not is_valid:
                    errors.append(f"{col['label']} {error_msg}")
                    widget.setStyleSheet("background-color: #FFCDD2;")
            
            data[col['name']] = value
        
        return data, errors

    def show_context_menu(self, position):
        """显示表格右键菜单 - 使用马卡龙色系风格"""
        selected_rows = self.data_table.selectionModel().selectedRows()
        if not selected_rows:
            return
        
        # 创建菜单并设置马卡龙色系样式
        menu = QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #F0E6DD;  /* 焦糖奶霜底色 */
                border: 1px solid #C7CEEA;  /* 薰衣草紫边框 */
                padding: 5px;
            }
            QMenu::item {
                padding: 5px 20px 5px 10px;
                margin: 2px;
                border-radius: 3px;
            }
            QMenu::item:selected {
                background-color: #C7CEEA;  /* 薰衣草紫选中背景 */
                color: #333333;
            }
            QMenu::item:disabled {
                color: #999999;
            }
            QMenu::separator {
                height: 1px;
                background: #E6E6FA;  /* 淡丁香分隔线 */
                margin: 5px 0;
            }
        """)
        
        # 基础操作
        edit_action = QAction('✏️ 编辑', self)
        edit_action.triggered.connect(self.edit_selected_data)
        menu.addAction(edit_action)
        
        delete_action = QAction('🗑️ 删除', self)
        delete_action.triggered.connect(self.delete_data)
        menu.addAction(delete_action)
        
        menu.addSeparator()
        
        # 复制相关操作
        copy_menu = menu.addMenu('⎘ 复制')
        
        copy_text_action = QAction('复制文本', self)
        copy_text_action.triggered.connect(self.copy_selected_text)
        copy_menu.addAction(copy_text_action)
        
        copy_row_action = QAction('复制整行', self)
        copy_row_action.triggered.connect(self.copy_selected_row)
        copy_menu.addAction(copy_row_action)
        
        copy_with_headers_action = QAction('复制带表头', self)
        copy_with_headers_action.triggered.connect(self.copy_selected_with_headers)
        copy_menu.addAction(copy_with_headers_action)
    
        # 复制为图片
        copy_as_image_action = QAction('复制为图片（包含条码）', self)
        copy_as_image_action.triggered.connect(self.copy_selected_as_image)
        copy_menu.addAction(copy_as_image_action)
    
        # 导出操作
        export_menu = menu.addMenu('💾 导出')
        
        export_csv_action = QAction('导出为CSV', self)
        export_csv_action.triggered.connect(self.export_selected_to_csv)
        export_menu.addAction(export_csv_action)
        
        export_json_action = QAction('导出为JSON', self)
        export_json_action.triggered.connect(self.export_selected_to_json)
        export_menu.addAction(export_json_action)
        
        export_images_action = QAction('导出图片', self)
        export_images_action.triggered.connect(self.export_selected_images)
        export_menu.addAction(export_images_action)
        
        menu.addSeparator()
        
        # 查看详情
        view_details_action = QAction('🔍 查看详情', self)
        view_details_action.triggered.connect(lambda: self.show_item_detail(self.data_table.currentIndex()))
        menu.addAction(view_details_action)
        
        # 打印
        print_action = QAction('🖨️ 打印', self)
        print_action.triggered.connect(lambda: self.print_item_detail(self.user_db.get_data_by_id(self.current_rowid)))
        menu.addAction(print_action)
        
        menu.addSeparator()

        # ★ v6.10：显示列设置（只影响显示，数据不动）
        visibility_action = QAction('👁️ 显示列设置', self)
        visibility_action.triggered.connect(self.configure_column_visibility)
        menu.addAction(visibility_action)
        
        # 刷新
        refresh_action = QAction('🔄 刷新', self)
        refresh_action.triggered.connect(self.load_data)
        menu.addAction(refresh_action)
        
        # 显示菜单
        menu.exec(self.data_table.viewport().mapToGlobal(position))



    def copy_selected_data(self):
        """复制选中数据到剪贴板"""
        selected_items = self.data_table.selectedItems()
        if not selected_items:
            return
        
        # 获取选中的行和列
        rows = set(item.row() for item in selected_items)
        cols = set(item.column() for item in selected_items)
        
        # 构建要复制的文本
        text = ""
        for row in sorted(rows):
            row_text = []
            for col in sorted(cols):
                item = self.data_table.item(row, col)
                if item:
                    row_text.append(item.text())
            text += "\t".join(row_text) + "\n"
        
        # 复制到剪贴板
        clipboard = QApplication.clipboard()
        clipboard.setText(text.strip())
        
        # 显示提示
        self.status_bar.showMessage(f"已复制 {len(rows)} 行数据", 3000)

    def export_selected_data(self):
        """导出选中行数据"""
        selected_rows = self.data_table.selectionModel().selectedRows()
        if not selected_rows:
            notify(self, '请先选择要导出的行', 'warn')
            return
        
        # 获取选中行的rowid
        rowids = []
        for row in selected_rows:
            rowid_item = self.data_table.item(row.row(), 0)
            if rowid_item:
                rowids.append(rowid_item.data(Qt.UserRole))
        
        if not rowids:
            notify(self, '无法获取选中行的ID', 'warn')
            return
        
        # 获取导出文件名
        filename, _ = QFileDialog.getSaveFileName(
            self, '导出选中数据', '', 
            'CSV文件 (*.csv);;文本文件 (*.txt);;所有文件 (*)'
        )
        
        if not filename:
            return
        
        try:
            # 获取选中行数据
            data = []
            for rowid in rowids:
                row_data = self.user_db.get_data_by_id(rowid)
                if row_data:
                    data.append(row_data[1:])  # 跳过rowid
            
            if not data:
                notify(self, '没有数据可导出', 'warn')
                return
            
            # 获取列名
            columns = [col['name'] for col in self.columns_config]
            
            # 写入文件
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(data)
            
            notify(self, f'已导出 {len(data)} 行数据到 {filename}', 'info')
        except Exception as e:
            notify(self, f'导出失败: {str(e)}', 'error')


    def copy_selected_text(self):
        """复制选中单元格文本"""
        selected_items = self.data_table.selectedItems()
        if not selected_items:
            return
        
        text = "\n".join(item.text() for item in selected_items)
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_bar.showMessage("已复制选中文本", 2000)

    def copy_selected_row(self):
        """复制整行数据"""
        selected_rows = {item.row() for item in self.data_table.selectedItems()}
        if not selected_rows:
            return
        
        text = ""
        for row in sorted(selected_rows):
            row_data = []
            for col in range(self.data_table.columnCount()):
                item = self.data_table.item(row, col)
                row_data.append(item.text() if item else "")
            text += "\t".join(row_data) + "\n"
        
        clipboard = QApplication.clipboard()
        clipboard.setText(text.strip())
        self.status_bar.showMessage(f"已复制 {len(selected_rows)} 行数据", 2000)

    def copy_selected_with_headers(self):
        """复制选中数据带表头"""
        selected_items = self.data_table.selectedItems()
        if not selected_items:
            return
        
        # 获取选中区域的行列范围
        rows = sorted({item.row() for item in selected_items})
        cols = sorted({item.column() for item in selected_items})
        
        # 获取表头
        headers = [self.data_table.horizontalHeaderItem(col).text() 
                for col in cols if self.data_table.horizontalHeaderItem(col)]
        
        # 获取数据
        data = []
        for row in rows:
            row_data = []
            for col in cols:
                item = self.data_table.item(row, col)
                row_data.append(item.text() if item else "")
            data.append(row_data)
        
        # 构建文本
        text = "\t".join(headers) + "\n"
        text += "\n".join("\t".join(row) for row in data)
        
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.status_bar.showMessage("已复制带表头数据", 2000)

    def export_selected_to_csv(self):
        """导出选中数据为CSV"""
        selected_rows = {item.row() for item in self.data_table.selectedItems()}
        if not selected_rows:
            notify(self, '请先选择要导出的数据', 'warn')
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, '导出为CSV', '', '所有文件 (*.*);;CSV文件 (*.csv)')
        if not filename:
            return
        
        try:
            # 获取表头
            headers = []
            for col in range(self.data_table.columnCount()):
                header = self.data_table.horizontalHeaderItem(col)
                if header:
                    headers.append(header.text())
            
            # 获取数据
            data = []
            for row in sorted(selected_rows):
                row_data = []
                for col in range(self.data_table.columnCount()):
                    item = self.data_table.item(row, col)
                    row_data.append(item.text() if item else "")
                data.append(row_data)
            
            # 写入文件
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(headers)
                writer.writerows(data)
            
            notify(self, f'已导出 {len(selected_rows)} 行数据到 {filename}', 'info')
        except Exception as e:
            notify(self, f'导出失败: {str(e)}', 'error')

    def export_selected_to_json(self):
        """导出选中数据为JSON"""
        selected_rows = {item.row() for item in self.data_table.selectedItems()}
        if not selected_rows:
            notify(self, '请先选择要导出的数据', 'warn')
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, '导出为JSON', '', '所有文件 (*.*);;JSON文件 (*.json)')
        if not filename:
            return
        
        try:
            # 获取表头
            headers = []
            for col in range(self.data_table.columnCount()):
                header = self.data_table.horizontalHeaderItem(col)
                if header:
                    headers.append(header.text())
            
            # 构建数据字典列表
            data = []
            for row in sorted(selected_rows):
                row_data = {}
                for col in range(self.data_table.columnCount()):
                    header = self.data_table.horizontalHeaderItem(col)
                    if header:
                        item = self.data_table.item(row, col)
                        row_data[header.text()] = item.text() if item else ""
                data.append(row_data)
            
            # 写入文件
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            
            notify(self, f'已导出 {len(selected_rows)} 行数据到 {filename}', 'info')
        except Exception as e:
            notify(self, f'导出失败: {str(e)}', 'error')

    def export_selected_images(self):
        """导出选中行的条形码/二维码图片"""
        selected_rows = {item.row() for item in self.data_table.selectedItems()}
        if not selected_rows:
            notify(self, '请先选择要导出的行', 'warn')
            return
        
        path = QFileDialog.getExistingDirectory(self, "选择保存目录")
        if not path:
            return
        
        try:
            exported_count = 0
            for row in sorted(selected_rows):
                rowid = self.data_table.item(row, 0).data(Qt.UserRole)
                if not rowid:
                    continue
                
                data = self.user_db.get_data_by_id(rowid)
                if not data:
                    continue
                
                # 导出条形码
                barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
                for col in barcode_cols:
                    col_idx = self.columns_config.index(col)
                    if col_idx < len(data)-1 and data[col_idx+1]:  # +1跳过rowid
                        # ★ v6.21：批量导出给别家程序扫 → 2 倍分辨率（原来只有 97px 宽）
                        img = self.generate_barcode(data[col_idx+1],
                                                    col.get('barcode_format', 'auto'),
                                                    col.get('barcode_subset', 'auto'),
                                                    scale=BARCODE_RENDER_SCALE)
                        if img:
                            filename = f"{path}/row{row}_{col['name']}_barcode.png"
                            img.save(filename)
                            exported_count += 1
                
                # 导出二维码
                qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
                for col in qrcode_cols:
                    col_idx = self.columns_config.index(col)
                    if col_idx < len(data)-1 and data[col_idx+1]:  # +1跳过rowid
                        img = self.generate_qrcode(data[col_idx+1])
                        if img:
                            filename = f"{path}/row{row}_{col['name']}_qrcode.png"
                            img.save(filename)
                            exported_count += 1
            
            notify(self, f"已导出 {exported_count} 张图片到：\n{path}", 'info')
        except Exception as e:
            notify(self, f"导出失败：{str(e)}", 'error')

    def get_cell_image(self, row, col):
        """获取单元格中的图像内容"""
        widget = self.data_table.cellWidget(row, col)
        if widget and isinstance(widget, QLabel):
            return widget.pixmap()
        return None



    def copy_selected_as_image(self):
        """将选中内容复制为图片到剪贴板（包含条形码和二维码）"""
        selected_items = self.data_table.selectedItems()
        if not selected_items:
            notify(self, '请先选择要复制的内容', 'warn')
            return
        
        try:
            # 获取选中的行和列范围
            rows = sorted({item.row() for item in selected_items})
            cols = sorted({item.column() for item in selected_items})
            
            # 计算要渲染的区域大小
            width = sum(self.data_table.columnWidth(col) for col in cols)
            height = self.data_table.horizontalHeader().height() + sum(self.data_table.rowHeight(row) for row in rows)
            
            # 创建QPixmap作为绘制表面
            pixmap = QPixmap(width, height)
            pixmap.fill(Qt.white)
            painter = QPainter(pixmap)
            
            # 设置抗锯齿和高质量渲染
            painter.setRenderHint(QPainter.Antialiasing)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            painter.setRenderHint(QPainter.TextAntialiasing)
            
            # 绘制表头
            x_offset = 0
            for col in cols:
                header = self.data_table.horizontalHeaderItem(col)
                col_width = self.data_table.columnWidth(col)
                
                if header:
                    header_rect = QRect(x_offset, 0, col_width, 
                                    self.data_table.horizontalHeader().height())
                    painter.fillRect(header_rect, QColor('#F5F5F5'))
                    painter.drawRect(header_rect)
                    painter.drawText(header_rect, Qt.AlignCenter, header.text())
                
                x_offset += col_width
            
            # 绘制单元格内容
            y_offset = self.data_table.horizontalHeader().height()
            for row in rows:
                x_offset = 0
                row_height = self.data_table.rowHeight(row)
                
                for col in cols:
                    col_width = self.data_table.columnWidth(col)
                    item = self.data_table.item(row, col)
                    
                    # 绘制单元格背景和边框
                    cell_rect = QRect(x_offset, y_offset, col_width, row_height)
                    bg_color = item.background() if item else QColor('#FFFFFF')
                    painter.fillRect(cell_rect, bg_color)
                    painter.drawRect(cell_rect)
                    
                    # 获取单元格图像或文本
                    cell_image = self.get_cell_image(row, col)
                    if cell_image:
                        # 绘制图像（居中显示）
                        img_rect = QRect(
                            x_offset + (col_width - cell_image.width()) // 2,
                            y_offset + (row_height - cell_image.height()) // 2,
                            cell_image.width(),
                            cell_image.height()
                        )
                        painter.drawPixmap(img_rect, cell_image)
                    elif item:
                        # 绘制文本
                        text_rect = cell_rect.adjusted(5, 0, -5, 0)
                        painter.drawText(text_rect, Qt.AlignLeft | Qt.AlignVCenter, item.text())
                    
                    x_offset += col_width
                
                y_offset += row_height
            
            painter.end()
            
            # 复制到剪贴板
            clipboard = QApplication.clipboard()
            clipboard.setPixmap(pixmap)
            
            self.status_bar.showMessage("已复制选中内容为图片", 2000)
        
        except Exception as e:
            notify(self, f"复制为图片失败: {str(e)}", 'error')






    def manual_backup(self):
        """手动备份数据库（多线程）"""
        self.status_bar.showMessage("正在备份数据库...")
        
        self._retire_worker(getattr(self, 'backup_worker', None))
        self.backup_worker = BackupWorker(self.user_db.clone_for_thread(), backup_type="manual")
        self.backup_worker.finished.connect(lambda path: self._on_backup_finished(path))
        self.backup_worker.error.connect(lambda e: notify(self, f'备份失败: {e}', 'warn'))
        self.backup_worker.status.connect(lambda msg: self.status_bar.showMessage(msg))
        self.active_workers.append(self.backup_worker)
        self.backup_worker.start()

    def _on_backup_finished(self, backup_path):
        """备份完成回调"""
        self._cleanup_worker(getattr(self, 'backup_worker', None))
        self._cleanup_finished_workers()
        if backup_path:
            self.status_bar.showMessage(f"数据库已备份到: {backup_path}")
            notify(self, f'数据库已备份到:\n{backup_path}', 'info')
        else:
            self.status_bar.showMessage("备份失败")
            notify(self, '备份失败', 'warn')
    
    def show_restore_dialog(self):
        """显示恢复备份对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle('恢复备份')
        dialog.resize(800, 600)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # 备份列表表格
        self.backup_table = QTableWidget()
        self.backup_table.setColumnCount(5)
        self.backup_table.setHorizontalHeaderLabels(['备份时间', '类型', '大小', '包含数据', '操作'])
        self.backup_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.backup_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 填充备份列表
        backups = self.user_db.get_backups_list()
        self.backup_table.setRowCount(len(backups))
        
        for row, backup in enumerate(backups):
            # 备份时间
            timestamp = datetime.fromtimestamp(backup["mtime"]).strftime("%Y-%m-%d %H:%M:%S")
            self.backup_table.setItem(row, 0, QTableWidgetItem(timestamp))
            
            # 备份类型
            type_item = QTableWidgetItem({"auto": "自动", "manual": "手动", "rollback": "回滚"}.get(backup["type"], "未知"))
            self.backup_table.setItem(row, 1, type_item)
            
            # 备份大小
            size_mb = backup["size"] / (1024 * 1024)
            self.backup_table.setItem(row, 2, QTableWidgetItem(f"{size_mb:.2f} MB"))
            
            # 包含数据范围 (需要分析备份文件)
            date_range = self.get_backup_date_range(backup["path"])
            self.backup_table.setItem(row, 3, QTableWidgetItem(date_range))
            
            # 操作按钮
            restore_btn = QPushButton('恢复')
            restore_btn.clicked.connect(lambda _, path=backup["path"]: self.restore_backup(path))
            self.backup_table.setCellWidget(row, 4, restore_btn)
        
        self.backup_table.resizeColumnsToContents()
        layout.addWidget(self.backup_table)
        
        # 筛选区域
        filter_group = QGroupBox('筛选备份')
        filter_layout = QHBoxLayout()
        
        type_filter = QComboBox()
        type_filter.addItems(['所有类型', '自动', '手动', '回滚'])
        type_filter.currentTextChanged.connect(self.filter_backups)
        filter_layout.addWidget(QLabel('类型:'))
        filter_layout.addWidget(type_filter)
        
        date_from = QDateEdit()
        date_from.setCalendarPopup(True)
        # date_from.setDate(QDate.currentDate().addMonths(-1))  # 默认显示最近一个月
        date_from.dateChanged.connect(self.filter_backups)
        filter_layout.addWidget(QLabel('从:'))
        filter_layout.addWidget(date_from)
        
        date_to = QDateEdit()
        date_to.setCalendarPopup(True)
        date_to.setDate(QDate.currentDate())
        date_to.dateChanged.connect(self.filter_backups)
        filter_layout.addWidget(QLabel('到:'))
        filter_layout.addWidget(date_to)
        
        filter_group.setLayout(filter_layout)
        layout.addWidget(filter_group)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.close)
        button_layout.addWidget(close_btn)
        layout.addLayout(button_layout)
        
        dialog.exec()
    
    def filter_backups(self):
        """根据筛选条件过滤备份列表"""
        type_filter = self.sender().parent().findChild(QComboBox).currentText()
        date_from = self.sender().parent().findChild(QDateEdit).date().toString("yyyy-MM-dd")
        date_to = self.sender().parent().findChild(QDateEdit, "date_to").date().toString("yyyy-MM-dd")
        
        backups = self.user_db.get_backups_list()
        
        for row in range(self.backup_table.rowCount()):
            should_show = True
            
            # 获取当前行的备份信息
            backup_time = self.backup_table.item(row, 0).text()
            backup_type = self.backup_table.item(row, 1).text()
            
            # 类型过滤
            if type_filter != "所有类型" and backup_type != type_filter:
                should_show = False
            
            # 日期过滤
            backup_date = datetime.strptime(backup_time, "%Y-%m-%d %H:%M:%S").strftime("%Y-%m-%d")
            if backup_date < date_from or backup_date > date_to:
                should_show = False
            
            # 显示/隐藏行
            self.backup_table.setRowHidden(row, not should_show)

    
    def get_backup_date_range(self, backup_path):
        """获取备份文件中包含的数据日期范围"""
        try:
            temp_conn = sqlite3.connect(backup_path)
            cursor = temp_conn.cursor()
            
            # 假设有一个包含日期的字段
            cursor.execute("SELECT MIN(date_field), MAX(date_field) FROM data")
            result = cursor.fetchone()
            temp_conn.close()
            
            if result and result[0] and result[1]:
                return f"{result[0]} 至 {result[1]}"
        except:
            pass
        return "未知"
    
    def restore_backup(self, backup_path):
        """从指定备份恢复数据库"""
        reply = QMessageBox.question(self, '确认', 
                                   '确定要从该备份恢复数据库吗?\n恢复前会创建回滚备份。',
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            if self.user_db.restore_from_backup(backup_path):
                notify(self, '数据库恢复成功!\n请重新启动应用使更改生效。', 'info')
                self.close()
            else:
                notify(self, '数据库恢复失败', 'warn')




    def validate_required_fields(self, data):
        """验证必填字段 - 修复版本"""
        try:
            required_fields = [col['name'] for col in self.columns_config if col.get('is_required', False)]
            
            for field in required_fields:
                # 检查字段是否存在且不为空
                if field not in data or data[field] is None or str(data[field]).strip() == '':
                    print(f"[DEBUG] 必填字段验证失败: {field} 为空或不存在")
                    return False
            
            return True
        except Exception as e:
            print(f"[DEBUG] 验证必填字段时出错: {str(e)}")
            return False

    def has_conflicts(self, data):
        """检查数据冲突 - 修复版本，使用安全的数据库方法"""
        try:
            unique_fields = [col['name'] for col in self.columns_config if col.get('is_unique', False)]
            
            for field in unique_fields:
                if field in data and data[field] and str(data[field]).strip():
                    # 使用UserDatabase的安全检查方法
                    if not self.user_db.check_unique(field, data[field]):
                        print(f"[DEBUG] 唯一性冲突: 字段 {field} 的值 {data[field]} 已存在")
                        return True
            
            return False
        except Exception as e:
            print(f"[DEBUG] 检查冲突时出错: {str(e)}")
            # 出错时默认返回无冲突，避免阻塞导入
            return False


    def update_existing_record(self, data):
        """更新现有记录 - 修复版本，使用安全的数据库方法"""
        try:
            unique_fields = [col['name'] for col in self.columns_config if col.get('is_unique', False)]
            
            for field in unique_fields:
                if field in data and data[field] and str(data[field]).strip():
                    # 使用安全的数据库查询方法查找记录
                    # 这里需要先找到要更新的记录ID
                    search_result = self.user_db.search_data_all_columns(str(data[field]))
                    if search_result:
                        # 找到第一条匹配的记录进行更新
                        rowid = search_result[0][0]  # 第一个元素的rowid
                        if self.user_db.update_data(rowid, data):
                            print(f"[DEBUG] 成功更新记录 rowid={rowid}")
                            return True
            
            print("[DEBUG] 未找到要更新的记录")
            return False
        except Exception as e:
            print(f"[DEBUG] 更新记录失败: {str(e)}")
            return False


    def read_import_file(self, filename, import_config):
        """读取导入文件，根据文件大小自动选择读取方法 - 移除行数限制"""
        file_type = self.detect_file_type(filename)
        
        if file_type in ['csv', 'ansi']:
            # 根据文件大小选择读取方式
            file_size = os.path.getsize(filename)
            
            # 阈值设置：50MB以上使用生成器方式，避免内存溢出
            if file_size > 50 * 1024 * 1024:  # 50MB
                print(f"[DEBUG] 检测到大文件 ({file_size/1024/1024:.2f}MB)，使用生成器方式读取")
                try:
                    # 使用生成器读取，完全移除行数限制
                    data = []
                    
                    # 直接收集生成器产生的所有记录，不限制行数
                    generator = self.read_large_csv_file_generator(filename, import_config)
                    for i, record in enumerate(generator):
                        data.append(record)
                        # 每处理10000行输出一次进度
                        if i > 0 and i % 10000 == 0:
                            print(f"[DEBUG] 已处理 {i} 行数据...")
                    
                    print(f"[DEBUG] 生成器方式成功读取 {len(data)} 行数据")
                    
                    if not data:
                        print("[DEBUG] 生成器返回空数据，尝试常规读取方法")
                        return self.read_csv_file(filename, import_config)
                    
                    return data
                        
                except Exception as e:
                    print(f"[DEBUG] 生成器读取失败: {str(e)}，尝试常规读取方法")
                    return self.read_csv_file(filename, import_config)
            else:
                print(f"[DEBUG] 检测到小文件 ({file_size/1024/1024:.2f}MB)，使用常规方式读取")
                return self.read_csv_file(filename, import_config)
        elif file_type == 'json':
            return self.read_json_file(filename)
        elif file_type in ['xlsx', 'xls']:
            return self.read_excel_file(filename, import_config)
        else:
            raise Exception(f"不支持的文件类型: {file_type}")




    def read_csv_file(self, filename, import_config):
        """读取CSV文件，增强编码检测和错误处理"""
        try:
            # 增强的编码检测列表
            encodings = [
                'utf-8', 'utf-8-sig',  # UTF-8 系列
                'gbk', 'gb2312', 'gb18030',  # 中文编码
                'big5', 'big5hkscs',  # 繁体中文
                'shift_jis', 'euc_jp', 'iso2022_jp',  # 日文编码
                'euc_kr', 'iso2022_kr',  # 韩文编码
                'windows-1250', 'windows-1251', 'windows-1252', 'windows-1253', 'windows-1254', 
                'windows-1255', 'windows-1256', 'windows-1257', 'windows-1258',  # Windows 代码页
                'iso-8859-1', 'iso-8859-2', 'iso-8859-3', 'iso-8859-4', 'iso-8859-5', 
                'iso-8859-6', 'iso-8859-7', 'iso-8859-8', 'iso-8859-9', 'iso-8859-10', 
                'iso-8859-11', 'iso-8859-13', 'iso-8859-14', 'iso-8859-15', 'iso-8859-16',  # ISO 拉丁系列
                'koi8-r', 'koi8-u',  # 俄语编码
                'mac_roman', 'mac_cyrillic', 'mac_greek', 'mac_turkish', 'mac_iceland',  # Mac 编码
                'cp437', 'cp850', 'cp852', 'cp855', 'cp857', 'cp860', 'cp861', 'cp862', 
                'cp863', 'cp864', 'cp865', 'cp866', 'cp869', 'cp874', 'cp932', 'cp936', 
                'cp949', 'cp950', 'cp1250', 'cp1251', 'cp1252', 'cp1253', 'cp1254', 
                'cp1255', 'cp1256', 'cp1257', 'cp1258',  # 代码页系列
                'ascii', 'latin-1'  # 基础编码
            ]

            # 首先尝试使用chardet自动检测编码
            try:
                with open(filename, 'rb') as f:
                    raw_data = f.read(10000)  # 读取前10KB用于检测
                    
                if hasattr(self, 'chardet') and self.chardet:
                    encoding_result = self.chardet.detect(raw_data)
                    detected_encoding = encoding_result.get('encoding')
                    confidence = encoding_result.get('confidence', 0)
                    
                    # 如果置信度较高，优先使用检测到的编码
                    if detected_encoding and confidence > 0.7:
                        print(f"[DEBUG] chardet检测到编码: {detected_encoding}, 置信度: {confidence}")
                        
                        # 标准化编码名称（处理别名）
                        encoding_aliases = {
                            'gb2312': 'gbk', 'cp936': 'gbk', 'windows-936': 'gbk',
                            'iso-8859-1': 'latin-1', 'iso8859-1': 'latin-1',
                            'cp1252': 'windows-1252', 'ms950': 'big5'
                        }
                        detected_encoding = encoding_aliases.get(detected_encoding.lower(), detected_encoding)
                        
                        # 如果检测到的编码不在列表中，添加到最前面
                        if detected_encoding.lower() not in [enc.lower() for enc in encodings]:
                            encodings.insert(0, detected_encoding)
                        else:
                            # 如果检测到的编码在列表中，移到最前面
                            for i, enc in enumerate(encodings):
                                if enc.lower() == detected_encoding.lower():
                                    encodings.pop(i)
                                    encodings.insert(0, detected_encoding)
                                    break
            except Exception as e:
                print(f"[DEBUG] chardet检测失败: {str(e)}")
            
            # 尝试各种编码
            for encoding in encodings:
                try:
                    print(f"[DEBUG] 尝试使用 {encoding} 编码读取CSV文件")
                    
                    with open(filename, 'r', encoding=encoding, errors='replace') as f:
                        # 跳过指定的行数
                        skip_rows = import_config.get('skip_rows', 0)
                        for _ in range(skip_rows):
                            line = f.readline()
                            if not line:
                                break
                        
                        # 读取第一行用于分隔符检测
                        first_line = f.readline().strip()
                        if not first_line:
                            continue
                        
                        # 回到文件开头（跳过已读的行）
                        f.seek(0)
                        for _ in range(skip_rows + 1):  # +1 因为已经读过第一行
                            f.readline()
                        
                        # 检测分隔符
                        delimiter = self.detect_csv_delimiter(first_line)
                        print(f"[DEBUG] 检测到分隔符: {repr(delimiter)}")
                        
                        # 验证分隔符是否有效
                        if not self.validate_delimiter(first_line, delimiter):
                            print(f"[DEBUG] 分隔符 {repr(delimiter)} 验证失败，尝试备选分隔符")
                            delimiter = self.fallback_delimiter_detection(first_line)
                            print(f"[DEBUG] 使用备选分隔符: {repr(delimiter)}")
                        
                        # 重新读取整个文件
                        f.seek(0)
                        for _ in range(skip_rows):
                            f.readline()
                        
                        # 修复：根据是否有表头创建不同的reader
                        first_row_as_header = import_config.get('first_row_as_header', True)
                        if first_row_as_header:
                            reader = csv.DictReader(f, delimiter=delimiter)
                            data = list(reader)
                        else:
                            # 没有表头时，使用列索引作为字段名
                            reader = csv.reader(f, delimiter=delimiter)
                            data = []
                            for row in reader:
                                if row:  # 跳过空行
                                    record = {f'Column_{i+1}': value for i, value in enumerate(row)}
                                    data.append(record)
                        
                        if data:
                            print(f"[DEBUG] 使用 {encoding} 编码成功读取 {len(data)} 行CSV数据")
                            # 添加编码统计信息
                            if len(data) > 0:
                                sample_record = data[0]
                                print(f"[DEBUG] 样本数据: {list(sample_record.keys())[:3]}...")  # 只显示前3个字段
                            return data
                        else:
                            print(f"[DEBUG] 使用 {encoding} 编码读取到空数据")
                            
                except UnicodeDecodeError as e:
                    print(f"[DEBUG] {encoding} 编码解码失败: {str(e)}")
                    continue
                except csv.Error as e:
                    print(f"[DEBUG] CSV解析错误 ({encoding}): {str(e)}")
                    continue
                except Exception as e:
                    print(f"[DEBUG] 使用 {encoding} 编码时发生其他错误: {str(e)}")
                    continue
            
            # 如果所有编码都失败，提供手动选择编码的选项
            encoding, ok = QInputDialog.getItem(
                self, '选择文件编码', 
                '自动检测编码失败，请手动选择文件编码:',
                encodings, 0, False
            )
            
            if ok and encoding:
                try:
                    with open(filename, 'r', encoding=encoding, errors='replace') as f:
                        skip_rows = import_config.get('skip_rows', 0)
                        for _ in range(skip_rows):
                            f.readline()
                        
                        # 检测分隔符
                        first_line = f.readline().strip()
                        delimiter = self.detect_csv_delimiter(first_line)
                        
                        # 验证分隔符
                        if not self.validate_delimiter(first_line, delimiter):
                            delimiter = self.fallback_delimiter_detection(first_line)
                        
                        f.seek(0)
                        for _ in range(skip_rows):
                            f.readline()
                        
                        # 修复：根据是否有表头创建不同的reader
                        first_row_as_header = import_config.get('first_row_as_header', True)
                        if first_row_as_header:
                            reader = csv.DictReader(f, delimiter=delimiter)
                            data = list(reader)
                        else:
                            # 没有表头时，使用列索引作为字段名
                            reader = csv.reader(f, delimiter=delimiter)
                            data = []
                            for row in reader:
                                if row:  # 跳过空行
                                    record = {f'Column_{i+1}': value for i, value in enumerate(row)}
                                    data.append(record)
                        
                        if data:
                            print(f"[DEBUG] 用户选择 {encoding} 编码，成功读取 {len(data)} 行数据")
                            return data
                except Exception as e:
                    raise Exception(f"使用 {encoding} 编码仍然失败: {str(e)}")
            
            raise Exception("无法读取CSV文件，请检查文件编码或格式")
            
        except Exception as e:
            raise Exception(f"读取CSV文件失败: {str(e)}")

    def validate_delimiter(self, line, delimiter):
        """验证分隔符是否有效"""
        if not line or not delimiter:
            return False
            
        if delimiter == '  ':  # 空格分隔符
            columns = [col for col in line.split(' ') if col.strip()]
        else:
            columns = line.split(delimiter)
        
        # 有效的分隔符应该产生至少2列，且不是所有列都为空
        valid = len(columns) >= 2 and any(col.strip() for col in columns)
        print(f"[DEBUG] 分隔符验证: {repr(delimiter)} -> {valid} (列数: {len(columns)})")
        return valid
    
    def fallback_delimiter_detection(self, line):
        """备选分隔符检测"""
        if not line:
            return ','
            
        common_delimiters = [',', '\t', ';', '|', ':', '~']
        
        for delim in common_delimiters:
            columns = line.split(delim)
            if len(columns) >= 2 and any(col.strip() for col in columns):
                print(f"[DEBUG] 使用备选分隔符: {repr(delim)} (产生 {len(columns)} 列)")
                return delim
        
        # 如果所有常见分隔符都失败，返回逗号
        print(f"[DEBUG] 所有备选分隔符都失败，使用默认逗号分隔符")
        return ','

    def normalize_encoding_name(self, encoding):
        """标准化编码名称"""
        encoding_map = {
            'gb2312': 'gbk',  # gb2312是gbk的子集
            'cp936': 'gbk',   # cp936就是gbk
            'windows-936': 'gbk',
            'ms936': 'gbk',
            'ansi_x3.4-1968': 'ascii',
            'us-ascii': 'ascii',
            'iso-8859-1': 'latin-1',
            'iso8859-1': 'latin-1',
            'cp1252': 'windows-1252',
            'ms950': 'big5',
            'csbig5': 'big5'
        }
        return encoding_map.get(encoding.lower(), encoding)

    def try_read_with_encoding(self, filename, import_config, encoding):
        """使用指定编码尝试读取文件"""
        with open(filename, 'r', encoding=encoding, errors='replace') as f:
            # 跳过指定的行数
            skip_rows = import_config.get('skip_rows', 0)
            for _ in range(skip_rows):
                line = f.readline()
                if not line:
                    break
            
            # 读取第一行用于分隔符检测
            first_line = f.readline().strip()
            if not first_line:
                return None
            
            # 回到文件开头（跳过已读的行）
            f.seek(0)
            for _ in range(skip_rows + 1):  # +1 因为已经读过第一行
                f.readline()
            
            # 检测分隔符
            delimiter = self.detect_csv_delimiter(first_line)
            print(f"[DEBUG] 检测到分隔符: {repr(delimiter)}")
            
            # 重新读取整个文件
            f.seek(0)
            for _ in range(skip_rows):
                f.readline()
            
            reader = csv.DictReader(f, delimiter=delimiter)
            data = list(reader)
            
            return data if data else None


    def show_import_errors(self, error_details):
        """显示导入错误详情 - 复用LoginWindow中的方法"""
        # 复用LoginWindow中的错误显示逻辑
        self.parent_window.show_import_errors(error_details)

    def detect_file_type(self, filename):
        """
        通过文件头和扩展名智能识别文件类型
        返回: 'json', 'csv', 'text', 'backup', 'sqlserver', 'unknown'
        """
        try:
            # 读取文件头进行识别
            with open(filename, 'rb') as f:
                header = f.read(1024)  # 读取前1024字节
            
            # 检测文件编码
            try:
                encoding_result = chardet.detect(header)
                file_encoding = encoding_result['encoding']
                if not file_encoding or encoding_result['confidence'] < 0.6:
                    file_encoding = 'utf-8'
            except:
                file_encoding = 'utf-8'
            
            # 尝试解码文件头内容
            try:
                header_text = header.decode(file_encoding, errors='ignore')
            except:
                header_text = header.decode('utf-8', errors='ignore')
            
            # 1. 检查JSON文件特征
            header_trimmed = header_text.strip()
            if (header_trimmed.startswith('{') and header_trimmed.endswith('}')) or \
            (header_trimmed.startswith('[') and header_trimmed.endswith(']')):
                return 'json'
            
            # 2. 检查CSV文件特征
            lines = header_text.split('\n')[:10]  # 检查前10行
            if len(lines) >= 2:
                # 检查是否有明显的分隔符（逗号、分号、制表符）
                first_line = lines[0].strip()
                second_line = lines[1].strip() if len(lines) > 1 else ""
                
                # 常见CSV分隔符
                for delimiter in [',', ';', '\t']:
                    if delimiter in first_line and len(first_line.split(delimiter)) > 1:
                        # 检查第二行是否有相同数量的列
                        if second_line and len(second_line.split(delimiter)) == len(first_line.split(delimiter)):
                            return 'csv'
            
            # 3. 检查SQLite备份文件特征
            if header.startswith(b'SQLite format 3') or filename.lower().endswith('.bak'):
                return 'backup'
            
            # 4. 检查SQL Server文件特征
            if self.is_sqlserver_file(header, filename):
                return 'sqlserver'
            
            # 5. 检查文本文件特征
            # 如果包含可读文本且不是二进制文件
            readable_chars = sum(1 for byte in header if 32 <= byte <= 126 or byte in [9, 10, 13])
            if readable_chars / len(header) > 0.7:  # 70%以上是可读字符
                # 尝试检测 ANSI 编码特征
                if self.is_ansi_file(header, filename):
                    return 'ansi'
                return 'text'

            # 7. 检查 ANSI 文件
            if self.is_ansi_file(header, filename):
                return 'ansi'

            
            # 6. 根据扩展名判断
            ext = os.path.splitext(filename)[1].lower()
            if ext == '.json':
                return 'json'
            elif ext == '.csv':
                return 'csv'
            elif ext == '.txt':
                return 'ansi'  # 文本文件通常使用 ANSI 编码
            elif ext == '.log':
                return 'ansi'  # 日志文件通常使用 ANSI 编码
            elif ext in ['.ini', '.inf', '.cfg']:
                return 'ansi'  # 配置文件通常使用 ANSI 编码
            elif ext == '.bak':
                return 'backup'
            elif ext in ['.mdf', '.ldf']:
                return 'sqlserver'

            return 'unknown'

            
        except Exception as e:
            print(f"[DEBUG] 文件类型检测失败: {str(e)}")
            # 回退到扩展名检测
            ext = os.path.splitext(filename)[1].lower()
            if ext == '.json':
                return 'json'
            elif ext == '.csv':
                return 'csv'
            elif ext == '.txt':
                return 'text'
            elif ext == '.bak':
                return 'backup'
            elif ext in ['.mdf', '.ldf']:
                return 'sqlserver'
            return 'unknown'
        
    def is_sqlserver_file(self, header, filename):
        """检查是否为SQL Server文件"""
        # 检查扩展名
        ext = os.path.splitext(filename)[1].lower()
        if ext in ['.mdf', '.ldf']:
            return True
        
        # 检查文件头特征
        # SQL Server文件通常有特定的文件头签名
        if len(header) >= 512:
            # 检查可能的SQL Server文件头特征
            # 这里可以根据实际文件特征进行调整
            try:
                # 尝试解析文件头中的可读信息
                header_text = header.decode('utf-8', errors='ignore')
                if 'Microsoft SQL Server' in header_text:
                    return True
            except:
                pass
        
        return False

    def detect_csv_delimiter(self, content):
        """增强的分隔符检测，处理各种CSV文件格式"""
        lines = content.split('\n')[:50]  # 增加检查行数到50行
        
        # 扩展分隔符列表，按优先级排序
        delimiters = [',', '\t', ';', '|', '  ', ':', '~', '#', '$']
        
        best_delimiter = ','
        max_consistency = 0
        max_columns = 0
        max_valid_lines = 0
        
        for delimiter in delimiters:
            column_counts = []
            valid_lines = 0
            
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                    
                # 跳过明显不是数据行的内容（如注释、空行等）
                if line.startswith(('#', '//', '--', '/*')) or len(line) < 2:
                    continue
                    
                if delimiter == '  ':  # 处理多个空格的情况
                    columns = [col.strip() for col in line.split(' ') if col.strip()]
                else:
                    columns = [col.strip() for col in line.split(delimiter)]
                
                # 检查是否是有效的分隔
                if (columns and len(columns) > 1 and 
                    len([col for col in columns if col]) > 1):  # 至少有两个非空列
                    column_counts.append(len(columns))
                    valid_lines += 1
            
            if column_counts and valid_lines >= 2:  # 降低有效行数要求
                from collections import Counter
                count_freq = Counter(column_counts)
                most_common_count, most_common_freq = count_freq.most_common(1)[0]
                consistency = most_common_freq / len(column_counts)
                
                # 更智能的评分算法
                score = consistency * 0.6 + (most_common_count / 10) * 0.2 + (valid_lines / len(lines)) * 0.2
                
                # 优先选择评分高、一致性高且有效行数多的分隔符
                if (score > max_consistency or 
                    (score == max_consistency and most_common_count > max_columns) or
                    (score == max_consistency and most_common_count == max_columns and valid_lines > max_valid_lines)):
                    best_delimiter = delimiter
                    max_columns = most_common_count
                    max_consistency = score
                    max_valid_lines = valid_lines
        
        print(f"[DEBUG] 检测到最佳分隔符: {repr(best_delimiter)}, "
            f"评分: {max_consistency:.2f}, 列数: {max_columns}, 有效行: {max_valid_lines}")
        
        # 如果检测结果不理想，提供手动选择选项
        if max_consistency < 0.3 or max_valid_lines < 2:
            return self.manual_select_delimiter(content)
        
        return best_delimiter

    def manual_select_delimiter(self, content):
        """当自动检测失败时，手动选择分隔符"""
        lines = content.split('\n')[:10]  # 显示前10行用于参考
        
        dialog = QDialog()
        dialog.setWindowTitle('选择分隔符')
        dialog.resize(600, 400)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # 显示文件预览
        preview_label = QLabel('文件预览（前10行）:')
        layout.addWidget(preview_label)
        
        preview_text = QTextEdit()
        preview_text.setPlainText('\n'.join(lines))
        preview_text.setReadOnly(True)
        preview_text.setStyleSheet("font-family: 'Courier New'; font-size: 10px;")
        layout.addWidget(preview_text)
        
        # 分隔符选择
        delimiter_group = QGroupBox('选择分隔符')
        delimiter_layout = QVBoxLayout()
        
        delimiter_combo = QComboBox()
        delimiters = [
            ('逗号 (,)', ','),
            ('制表符 (\\t)', '\t'),
            ('分号 (;)', ';'),
            ('竖线 (|)', '|'),
            ('冒号 (:)', ':'),
            ('波浪号 (~)', '~'),
            ('井号 (#)', '#'),
            ('美元符号 ($)', '$'),
            ('多个空格', '  ')
        ]
        
        for name, delim in delimiters:
            delimiter_combo.addItem(name, delim)
        
        delimiter_layout.addWidget(QLabel('请根据文件预览选择正确的分隔符:'))
        delimiter_layout.addWidget(delimiter_combo)
        delimiter_group.setLayout(delimiter_layout)
        layout.addWidget(delimiter_group)
        
        # 预览效果
        preview_result = QLabel('')
        preview_result.setWordWrap(True)
        preview_result.setStyleSheet("background-color: #f0f0f0; padding: 5px; border: 1px solid #ccc;")
        layout.addWidget(preview_result)
        
        def update_preview():
            selected_delim = delimiter_combo.currentData()
            if lines:
                first_line = lines[0]
                if selected_delim == '  ':
                    columns = [col for col in first_line.split(' ') if col.strip()]
                else:
                    columns = first_line.split(selected_delim)
                preview_result.setText(f"第一行将被解析为 {len(columns)} 列: {columns}")
        
        delimiter_combo.currentIndexChanged.connect(update_preview)
        update_preview()  # 初始更新
        
        # 按钮区域
        button_layout = QHBoxLayout()
        ok_btn = QPushButton('确定')
        cancel_btn = QPushButton('取消')
        
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        result = {'delimiter': ','}  # 默认值
        
        def on_accept():
            result['delimiter'] = delimiter_combo.currentData()
            dialog.accept()
        
        def on_reject():
            result['delimiter'] = ','  # 使用默认逗号
            dialog.reject()
        
        ok_btn.clicked.connect(on_accept)
        cancel_btn.clicked.connect(on_reject)
        
        if dialog.exec() == QDialog.Accepted:
            print(f"[DEBUG] 用户手动选择分隔符: {repr(result['delimiter'])}")
            return result['delimiter']
        else:
            print(f"[DEBUG] 用户取消手动选择，使用默认分隔符: ,")
            return ','  # 默认使用逗号




    def execute_ultra_fast_import(self, import_config):
        """执行超快速导入（多线程版本）"""
        filename = import_config['filename']
        
        # 创建进度对话框
        progress_dialog = self._create_progress_dialog("正在快速导入数据...")
        progress_dialog.canceled.connect(self._on_import_cancelled)

        # 创建并启动导入线程
        self._retire_worker(getattr(self, 'import_worker', None))
        self.import_worker = ImportDataWorker(import_config, self)
        self.import_worker.finished.connect(lambda result: self._on_import_finished(result, progress_dialog, filename))
        self.import_worker.error.connect(lambda e: self._on_import_error(e, progress_dialog))
        self.import_worker.progress.connect(lambda cur, tot: progress_dialog.setValue(cur) if progress_dialog else None)
        self.import_worker.status.connect(lambda msg: self.status_bar.showMessage(msg))
        
        # 连接取消信号
        if progress_dialog:
            progress_dialog.canceled.connect(self.import_worker.cancel)
        
        self.active_workers.append(self.import_worker)
        self.import_worker.start()

        # 返回成功/失败计数（初始为0，完成后由回调更新）
        return 0, 0

    def _on_import_cancelled(self):
        """导入取消"""
        if hasattr(self, 'import_worker') and self.import_worker:
            self.import_worker.cancel()
        self.status_bar.showMessage("导入已取消")

    def _on_import_finished(self, result, progress_dialog, filename):
        """导入完成回调"""
        self._cleanup_worker(getattr(self, 'import_worker', None))
        self._cleanup_finished_workers()
        if progress_dialog:
            progress_dialog.close()
        
        success_count = result.get('success', 0)
        error_count = result.get('errors', 0)
        error_details = result.get('error_details', [])
        
        if error_details:
            self.show_import_errors(error_details)
        
        result_msg = f"文件 {os.path.basename(filename)} 导入完成\n成功: {success_count} 条\n失败: {error_count} 条"
        self.status_bar.showMessage(result_msg)
        self.load_data()  # 刷新数据

    def _on_import_error(self, error_msg, progress_dialog):
        """导入错误回调"""
        self._cleanup_worker(getattr(self, 'import_worker', None))
        self._cleanup_finished_workers()
        if progress_dialog:
            progress_dialog.close()
        self.status_bar.showMessage(f"导入失败: {error_msg}")
        notify(self, f'导入失败: {error_msg}', 'error')

        
    def preprocess_import_data(self, data, mapping):
        """数据预处理，修复常见的数据问题"""
        processed_data = []
        skipped_records = 0
        fixed_records = 0
        
        print(f"[DEBUG] 开始数据预处理，原始数据量: {len(data)} 条")
        
        for i, record in enumerate(data):
            try:
                # 检查记录是否为空
                if not record or all(not str(v).strip() for v in record.values() if v is not None):
                    skipped_records += 1
                    if i < 10:  # 只记录前10个跳过的记录
                        print(f"[DEBUG] 跳过第{i+1}行: 空记录")
                    continue
                
                # 修复字段映射
                processed_record = {}
                mapping_found = False
                
                for source_field, target_field in mapping.items():
                    if source_field in record and target_field:
                        value = record[source_field]
                        # 处理空值
                        if value is None or str(value).strip() == '':
                            processed_record[target_field] = ""
                        else:
                            processed_record[target_field] = str(value).strip()
                        mapping_found = True
                
                # 如果没有找到映射，尝试直接映射
                if not mapping_found and record:
                    print(f"[DEBUG] 第{i+1}行未找到显式映射，尝试直接映射")
                    for key, value in record.items():
                        # 清理字段名
                        clean_key = key.strip().replace(' ', '_').lower()
                        if clean_key in [col['name'] for col in self.columns_config]:
                            processed_record[clean_key] = str(value).strip() if value is not None else ""
                            mapping_found = True
                
                # 检查是否有有效数据
                if processed_record and any(v for v in processed_record.values() if v):
                    processed_data.append(processed_record)
                    fixed_records += 1
                else:
                    skipped_records += 1
                    if i < 10:  # 只记录前10个跳过的记录
                        print(f"[DEBUG] 跳过第{i+1}行: 无有效数据")
                    
            except Exception as e:
                skipped_records += 1
                print(f"[DEBUG] 预处理第{i+1}行数据失败: {str(e)}")
                if i < 5:  # 只记录前5个错误
                    print(f"[DEBUG] 失败记录内容: {record}")
        
        # 输出预处理报告
        print(f"[DEBUG] 数据预处理完成: 原始{len(data)}条 -> 有效{len(processed_data)}条, "
            f"跳过{skipped_records}条, 修复{fixed_records}条")
        
        if skipped_records > 0:
            print(f"[DEBUG] 数据预处理: 跳过了 {skipped_records} 条无效记录")
        
        return processed_data
        
    def process_import_batch(self, batch, mapping, conflict_resolution, start_index):
        """处理单个数据批次 - 修复版本"""
        success_count = 0
        error_details = []
        
        try:
            # 准备批量数据
            batch_data = []
            for i, record in enumerate(batch):
                try:
                    mapped_data = {}
                    
                    # 调试：打印原始记录和映射关系
                    print(f"[DEBUG] 第{start_index + i}行原始记录: {record}")
                    print(f"[DEBUG] 字段映射关系: {mapping}")
                    
                    # 修复字段映射逻辑
                    mapping_found = False
                    for source_field, target_field in mapping.items():
                        if source_field in record and target_field:
                            value = record[source_field]
                            if value is None or str(value).strip() == '':
                                mapped_data[target_field] = ""
                            else:
                                mapped_data[target_field] = str(value)
                            mapping_found = True
                            print(f"[DEBUG] 映射字段: {source_field} -> {target_field} = {value}")
                    
                    # 如果没有找到任何映射，尝试使用列名直接映射
                    if not mapping_found and record:
                        print(f"[DEBUG] 未找到显式映射，尝试直接映射")
                        for key, value in record.items():
                            # 尝试将源字段名直接映射到目标字段
                            if key in [col['name'] for col in self.columns_config]:
                                mapped_data[key] = str(value) if value is not None else ""
                                mapping_found = True
                                print(f"[DEBUG] 直接映射: {key} = {value}")
                    
                    # 调试：打印映射后的数据
                    print(f"[DEBUG] 第{start_index + i}行映射数据: {mapped_data}")
                    
                    # 如果映射后数据为空，记录错误并跳过
                    if not mapped_data:
                        print(f"[DEBUG] 第{start_index + i}行映射后数据为空")
                        error_details.append(f"第{start_index + i}行: 字段映射失败，无有效数据")
                        print(f"[DEBUG] 第{start_index + i}条数据为空，跳过")
                        continue
                    
                    # 验证必填字段
                    if not self.validate_required_fields(mapped_data):
                        error_details.append(f"第{start_index + i}行: 必填字段缺失")
                        continue
                    
                    # 处理冲突
                    if conflict_resolution == 'skip' and self.has_conflicts(mapped_data):
                        error_details.append(f"第{start_index + i}行: 唯一性约束冲突，已跳过")
                        continue
                    elif conflict_resolution == 'update' and self.has_conflicts(mapped_data):
                        # 更新现有记录
                        if self.update_existing_record(mapped_data):
                            success_count += 1
                            continue
                        else:
                            error_details.append(f"第{start_index + i}行: 更新现有记录失败")
                            continue
                    
                    batch_data.append(mapped_data)
                    
                except Exception as e:
                    error_details.append(f"第{start_index + i}行: {str(e)}")
                    print(f"[DEBUG] 处理第{start_index + i}行时出错: {str(e)}")
            
            # 批量插入新数据
            if batch_data:
                print(f"[DEBUG] 准备批量插入 {len(batch_data)} 条数据")
                try:
                    inserted_count = self.user_db.bulk_insert_data(batch_data)
                    success_count += inserted_count
                    print(f"[DEBUG] 成功插入 {inserted_count} 条数据")
                except Exception as e:
                    error_details.append(f"批量插入失败: {str(e)}")
                    print(f"[DEBUG] 批量插入失败: {str(e)}")
        
        except Exception as e:
            error_details.append(f"批次处理失败: {str(e)}")
            print(f"[DEBUG] 批次处理失败: {str(e)}")
        
        return success_count, error_details



    def read_large_csv_file(self, filename, import_config):
        """优化的大文件CSV读取，支持流式处理和内存映射"""
        try:
            skip_rows = import_config.get('skip_rows', 0)
            max_rows = import_config.get('max_rows', 0)  # 0表示无限制
            
            # 检测文件编码
            with open(filename, 'rb') as f:
                raw_data = f.read(4096)  # 读取前4KB检测编码
                encoding_result = chardet.detect(raw_data)
                file_encoding = encoding_result.get('encoding', 'utf-8')
            
            data = []
            row_count = 0
            
            with open(filename, 'r', encoding=file_encoding, errors='replace') as f:
                # 跳过指定行数
                for _ in range(skip_rows):
                    f.readline()
                
                # 读取表头
                header_line = f.readline().strip()
                if not header_line:
                    return []
                    
                # 自动检测分隔符
                delimiter = self.detect_csv_delimiter(header_line)
                headers = [h.strip() for h in header_line.split(delimiter) if h.strip()]
                
                # 流式读取数据
                for line in f:
                    if max_rows > 0 and row_count >= max_rows:
                        break
                        
                    line = line.strip()
                    if not line:
                        continue
                    
                    values = line.split(delimiter)
                    # 确保列数一致
                    if len(values) > len(headers):
                        values = values[:len(headers)]
                    elif len(values) < len(headers):
                        values.extend([''] * (len(headers) - len(values)))
                    
                    record = dict(zip(headers, values))
                    data.append(record)
                    row_count += 1
                    
                    # 每1000行处理一次，避免内存占用过高
                    if len(data) >= 1000:
                        # 这里可以添加批量处理逻辑
                        pass
            
            return data
            
        except Exception as e:
            raise Exception(f"读取大文件失败: {str(e)}")

    def read_large_csv_file_generator(self, filename, import_config):
        """生成器版本，真正支持超大文件 - 增强数据质量检查"""
        try:
            skip_rows = import_config.get('skip_rows', 0)
            first_row_as_header = import_config.get('first_row_as_header', True)
            
            print(f"[DEBUG] 开始读取大文件: {filename}")
            print(f"[DEBUG] 跳过行数: {skip_rows}, 第一行作为表头: {first_row_as_header}")

            # 检测文件编码 - 使用更可靠的方法
            with open(filename, 'rb') as f:
                raw_data = f.read(8192)  # 读取更多字节提高检测准确性
                
            # 优先使用chardet检测编码
            file_encoding = 'utf-8'
            if hasattr(self, 'chardet') and self.chardet:
                encoding_result = self.chardet.detect(raw_data)
                detected_encoding = encoding_result.get('encoding')
                confidence = encoding_result.get('confidence', 0)
                
                if detected_encoding and confidence > 0.6:
                    file_encoding = detected_encoding
                    print(f"[DEBUG] chardet检测到编码: {file_encoding}, 置信度: {confidence}")
                else:
                    # 备选编码列表
                    encodings_to_try = ['utf-8', 'gbk', 'gb2312', 'latin-1', 'cp936', 'utf-8-sig']
                    for encoding in encodings_to_try:
                        try:
                            raw_data.decode(encoding)
                            file_encoding = encoding
                            print(f"[DEBUG] 备选编码成功: {file_encoding}")
                            break
                        except UnicodeDecodeError:
                            continue
            
            print(f"[DEBUG] 最终使用编码: {file_encoding}")

            with open(filename, 'r', encoding=file_encoding, errors='replace') as f:
                # 跳过指定行数
                for i in range(skip_rows):
                    skipped_line = f.readline()
                    print(f"[DEBUG] 跳过第{i+1}行: {skipped_line.strip()[:100]}...")
                
                # 读取表头或第一行数据
                if first_row_as_header:
                    header_line = f.readline().strip()
                    if not header_line:
                        print("[DEBUG] 表头行为空，无法继续读取")
                        return
                        
                    # 检测分隔符
                    delimiter = self.detect_csv_delimiter(header_line)
                    headers = [h.strip() for h in header_line.split(delimiter) if h.strip()]
                    print(f"[DEBUG] 检测到分隔符: {repr(delimiter)}")
                    print(f"[DEBUG] 表头: {headers}")
                    print(f"[DEBUG] 表头数量: {len(headers)}")
                else:
                    # 如果没有表头，读取第一行数据来推断列数
                    first_data_line = f.readline().strip()
                    if not first_data_line:
                        print("[DEBUG] 第一行数据为空，无法继续读取")
                        return
                        
                    delimiter = self.detect_csv_delimiter(first_data_line)
                    headers_count = len(first_data_line.split(delimiter))
                    headers = [f'Column_{i+1}' for i in range(headers_count)]
                    print(f"[DEBUG] 无表头模式，推断列数: {headers_count}")
                    # 重置文件指针到数据开始位置
                    f.seek(0)
                    for _ in range(skip_rows + 1):  # 重新跳过行数
                        f.readline()

                # 数据质量统计
                quality_stats = {
                    'total_rows': 0,
                    'empty_rows': 0,
                    'incomplete_rows': 0,
                    'valid_rows': 0,
                    'malformed_rows': 0
                }

                # 逐行生成数据
                line_count = 0
                empty_line_count = 0
                valid_record_count = 0
                
                for line in f:
                    line = line.strip()
                    if not line:
                        empty_line_count += 1
                        quality_stats['empty_rows'] += 1
                        continue
                    
                    values = line.split(delimiter)
                    quality_stats['total_rows'] += 1
                
                    # 检查数据完整性
                    if len(values) < len(headers):
                        quality_stats['incomplete_rows'] += 1
                        print(f"[DEBUG] 第{line_count+1}行列数不足 ({len(values)}/{len(headers)})，已填充空值")
                    
                    # 检查数据格式问题
                    if len(values) == 0 or (len(values) == 1 and not values[0].strip()):
                        quality_stats['malformed_rows'] += 1
                        print(f"[DEBUG] 第{line_count+1}行数据格式异常，跳过")
                        continue
                
                    # 确保列数一致
                    if len(values) > len(headers):
                        values = values[:len(headers)]
                        print(f"[DEBUG] 第{line_count+1}行列数过多，已截断")
                    elif len(values) < len(headers):
                        values.extend([''] * (len(headers) - len(values)))
                        print(f"[DEBUG] 第{line_count+1}行列数不足，已填充空值")
                    
                    record = dict(zip(headers, values))
                    line_count += 1
                    valid_record_count += 1
                    quality_stats['valid_rows'] += 1
                    
                    # 调试信息
                    if line_count <= 5:  # 只打印前5条记录的调试信息
                        print(f"[DEBUG] 第{line_count}行数据样本: {list(record.values())[:3]}...")
                    
                    if line_count % 10000 == 0:
                        print(f"[DEBUG] 已读取 {line_count} 行数据，有效记录: {valid_record_count}")
                    
                    yield record
            
                # 输出数据质量报告
                print(f"[DEBUG] 数据质量报告: 总计{quality_stats['total_rows']}行, "
                    f"有效{quality_stats['valid_rows']}行, "
                    f"空行{quality_stats['empty_rows']}行, "
                    f"不完整{quality_stats['incomplete_rows']}行, "
                    f"格式异常{quality_stats['malformed_rows']}行")
                
                print(f"[DEBUG] 大文件读取完成 - 总行数: {line_count}, 有效记录: {valid_record_count}, 空行: {empty_line_count}")
                
                if valid_record_count == 0:
                    print("[DEBUG] 警告: 没有读取到任何有效记录!")
                    # 打印文件前几行用于调试
                    with open(filename, 'r', encoding=file_encoding, errors='replace') as debug_f:
                        for i in range(10):
                            debug_line = debug_f.readline()
                            if not debug_line:
                                break
                            print(f"[DEBUG] 文件第{i+1}行: {debug_line.strip()}")
                        
        except Exception as e:
            print(f"[DEBUG] 读取大文件失败: {str(e)}")
            import traceback
            traceback.print_exc()
            raise Exception(f"读取大文件失败: {str(e)}")




    def ultra_fast_import_data(self):
        """超快速导入数据 - 支持多文件选择（多线程版本）"""
        filenames, _ = QFileDialog.getOpenFileNames(
            self, '选择数据文件', '', 
            '所有文件 (*.*);;CSV文件 (*.csv);;JSON文件 (*.json);;Excel文件 (*.xlsx *.xls)'
        )
        
        if not filenames:
            return
        
        for filename in filenames:
            try:
                # 显示超快速导入对话框（在UI线程）
                dialog = UltraFastImportDialog(filename, self)
                if dialog.exec() == QDialog.Accepted:
                    import_config = dialog.get_import_config()
                    # 执行异步导入（不等待结果）
                    self.execute_ultra_fast_import(import_config)
                    
            except Exception as e:
                print(f"[DEBUG] 导入文件 {filename} 失败: {str(e)}")
                notify(self, f'导入文件 {os.path.basename(filename)} 失败: {str(e)}', 'error')
        
        notify(self, f'已开始导入 {len(filenames)} 个文件，导入完成后将自动刷新数据', 'info')



    def is_ansi_file(self, header, filename):
        """检测是否为 ANSI 编码文件"""
        try:
            # ANSI 通常是系统默认编码，在中文Windows上是gbk
            # 检查文件扩展名
            ext = os.path.splitext(filename)[1].lower()
            common_ansi_extensions = ['.txt', '.csv', '.log', '.ini', '.inf']
            
            if ext in common_ansi_extensions:
                return True
            
            # 检查文件内容特征
            # ANSI 文件通常不包含 BOM 头
            if header.startswith(b'\xFF\xFE') or header.startswith(b'\xFE\xFF') or header.startswith(b'\xEF\xBB\xBF'):
                return False  # 有 BOM 头，不是纯 ANSI
            
            # 尝试用常见 ANSI 编码解码
            ansi_encodings = ['gbk', 'cp936', 'windows-1252', 'latin-1']
            for encoding in ansi_encodings:
                try:
                    header.decode(encoding)
                    return True
                except UnicodeDecodeError:
                    continue
            
            # 检查是否主要是 ASCII 字符（ANSI 兼容 ASCII）
            ascii_count = sum(1 for byte in header if byte <= 127)
            if ascii_count / len(header) > 0.9:  # 90% 以上是 ASCII
                return True
                
        except Exception:
            pass
        
        return False

    def batch_ultra_fast_import(self):
        """批量超快速导入 - 支持统一配置"""
        filenames, _ = QFileDialog.getOpenFileNames(
            self, '选择多个数据文件', '', 
            '所有文件 (*.*);;CSV文件 (*.csv);;JSON文件 (*.json);;Excel文件 (*.xlsx *.xls)'
        )
        
        if not filenames:
            return
        
        # 询问是否使用统一配置
        use_unified = False
        unified_config = {}
        
        if len(filenames) > 1:
            reply = QMessageBox.question(
                self, '统一配置', 
                f'检测到 {len(filenames)} 个文件，是否使用统一配置？\n\n'
                '是：第一个文件的配置将应用于所有文件\n'
                '否：每个文件单独配置',
                QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel,
                QMessageBox.Yes
            )
            
            if reply == QMessageBox.Cancel:
                return
            elif reply == QMessageBox.Yes:
                use_unified = True
        
        # 批量处理多个文件
        total_success = 0
        total_errors = 0
        processed_files = 0
        
        # 创建进度对话框
        progress_dialog = QProgressDialog("正在批量导入文件...", "取消", 0, len(filenames), self)
        progress_dialog.setWindowTitle("批量导入进度")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.show()
        
        first_file_config = None
        
        for i, filename in enumerate(filenames):
            if progress_dialog.wasCanceled():
                break
                
            try:
                # 显示超快速导入对话框
                dialog = UltraFastImportDialog(filename, self)
                
                # 如果是统一配置模式且不是第一个文件，应用第一个文件的配置
                if use_unified and i > 0 and first_file_config:
                    dialog.unified_config = first_file_config
                    dialog.unified_config_check.setChecked(True)
                    dialog.load_unified_config()
                    
                    # 直接使用第一个文件的配置，不显示对话框
                    import_config = dialog.get_import_config()
                else:
                    # 第一个文件或非统一配置模式，正常显示对话框
                    if dialog.exec() == QDialog.Accepted:
                        import_config = dialog.get_import_config()
                        
                        # 保存第一个文件的配置用于统一配置
                        if use_unified and i == 0:
                            first_file_config = import_config
                    else:
                        continue  # 用户取消了第一个文件的配置
                
                # 执行超快速导入
                success_count, error_count = self.execute_ultra_fast_import(import_config)
                
                total_success += success_count
                total_errors += error_count
                processed_files += 1
                
            except Exception as e:
                print(f"[DEBUG] 导入文件 {filename} 失败: {str(e)}")
                total_errors += 1
            
            # 更新进度
            progress_dialog.setValue(i + 1)
            QApplication.processEvents()
        
        progress_dialog.close()
        
        # 显示批量导入结果
        result_msg = f"⚡📥批量导入完成！\n\n总共处理 {len(filenames)} 个文件\n成功导入 {processed_files} 个文件\n成功记录: {total_success} 条\n失败记录: {total_errors} 条"
        
        if use_unified and processed_files > 1:
            result_msg += f"\n\n✅ 已应用统一配置到所有文件"
        
        notify(self, result_msg, 'info')
        self.load_data()

    def batch_ultra_fast_import_enhanced(self):
        """增强的批量超快速导入 - 支持真正的无人值守模式"""
        filenames, _ = QFileDialog.getOpenFileNames(
            self, '选择多个数据文件', '', 
            '所有文件 (*.*);;CSV文件 (*.csv);;JSON文件 (*.json);;Excel文件 (*.xlsx *.xls)'
        )
        
        if not filenames:
            return
        
        # 询问导入模式
        mode_dialog = QDialog(self)
        mode_dialog.setWindowTitle('选择导入模式')
        mode_dialog.resize(400, 250)
        
        layout = QVBoxLayout()
        mode_dialog.setLayout(layout)
        
        # 模式选择
        mode_group = QGroupBox('导入模式')
        mode_layout = QVBoxLayout()
        
        interactive_radio = QRadioButton('交互模式（推荐）')
        interactive_radio.setChecked(True)
        interactive_radio.setToolTip('每个文件单独配置，提供最大灵活性')
        
        unattended_radio = QRadioButton('无人值守模式')
        unattended_radio.setToolTip('使用统一配置自动处理所有文件，适合批量处理相似文件')
        
        mode_layout.addWidget(interactive_radio)
        mode_layout.addWidget(unattended_radio)
        mode_group.setLayout(mode_layout)
        layout.addWidget(mode_group)
        
        # 统一配置选项（仅无人值守模式可用）
        config_group = QGroupBox('统一配置选项')
        config_layout = QVBoxLayout()
        
        use_existing_config = QCheckBox('使用已保存的统一配置')
        use_existing_config.setEnabled(False)
        
        save_as_default = QCheckBox('将本次配置保存为默认配置')
        save_as_default.setEnabled(False)
        
        # 新增：无人值守模式选项
        unattended_mode = QCheckBox('启用无人值守模式')
        unattended_mode.setChecked(True)
        unattended_mode.setEnabled(False)

        config_layout.addWidget(use_existing_config)
        config_layout.addWidget(save_as_default)
        config_layout.addWidget(unattended_mode)
        config_group.setLayout(config_layout)
        layout.addWidget(config_group)
        
        # 连接信号
        def update_config_options():
            unattended = unattended_radio.isChecked()
            use_existing_config.setEnabled(unattended)
            save_as_default.setEnabled(unattended)
            unattended_mode.setEnabled(unattended)
        
        unattended_radio.toggled.connect(update_config_options)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        start_btn = QPushButton('开始导入')
        cancel_btn = QPushButton('取消')
        
        button_layout.addWidget(start_btn)
        button_layout.addWidget(cancel_btn)
        layout.addLayout(button_layout)
        
        # 连接按钮信号
        start_btn.clicked.connect(mode_dialog.accept)
        cancel_btn.clicked.connect(mode_dialog.reject)
        
        if mode_dialog.exec() != QDialog.Accepted:
            return
        
        # 根据选择的模式执行导入
        if unattended_radio.isChecked():
            # 新增：获取无人值守模式设置并传递
            unattended_mode_setting = unattended_mode.isChecked()
            self.execute_unattended_batch_import_with_cleanup(
                filenames, 
                use_existing_config.isChecked(),
                save_as_default.isChecked(),
                unattended_mode_setting  # 传递无人值守模式设置
            )
        else:
            self.batch_ultra_fast_import_with_cleanup(filenames)
    
    
    def execute_unattended_batch_import(self, filenames, use_existing_config, save_as_default, unattended_mode=True):
        """执行无人值守批量导入"""
        try:
            # 获取或创建统一配置
            if use_existing_config and hasattr(self, 'global_unified_config') and self.global_unified_config:
                unified_config = self.global_unified_config
                print("[DEBUG] 使用已保存的统一配置")
            else:
                # 使用第一个文件创建配置
                first_dialog = UltraFastImportDialog(filenames[0], self)
                if first_dialog.exec() != QDialog.Accepted:
                    return
                
                unified_config = first_dialog.get_import_config()
                print("[DEBUG] 使用第一个文件的配置作为统一配置")
            
            # 保存为默认配置（如果需要）
            if save_as_default:
                self.global_unified_config = unified_config
                print("[DEBUG] 已保存为默认统一配置")
            
            # 执行批量导入
            total_success = 0
            total_errors = 0
            processed_files = 0
            
            # 创建进度对话框
            progress_dialog = QProgressDialog("正在无人值守批量导入...", "取消", 0, len(filenames), self)
            progress_dialog.setWindowTitle("无人值守批量导入进度")
            progress_dialog.setWindowModality(Qt.WindowModal)
            progress_dialog.show()
            
            for i, filename in enumerate(filenames):
                if progress_dialog.wasCanceled():
                    break
                
                try:
                    # 修复：直接使用统一配置，不创建对话框
                    if i == 0:  # 第一个文件已经配置过了
                        import_config = unified_config
                    else:
                        # 对于后续文件，直接应用统一配置
                        import_config = unified_config.copy()
                        import_config['filename'] = filename  # 更新文件名
                    
                    # 直接执行导入，不显示对话框
                    success_count, error_count = self.execute_ultra_fast_import(import_config)
                    
                    total_success += success_count
                    total_errors += error_count
                    processed_files += 1
                    print(f"[DEBUG] 无人值守处理文件 {os.path.basename(filename)}: 成功{success_count}条, 失败{error_count}条")
                    
                except Exception as e:
                    print(f"[DEBUG] 无人值守处理文件 {filename} 失败: {str(e)}")
                    total_errors += 1
                
                # 更新进度
                progress_dialog.setValue(i + 1)
                QApplication.processEvents()
            
            progress_dialog.close()
            
            # 显示结果
            result_msg = f"🤖 无人值守批量导入完成！\n\n" \
                        f"总共处理: {len(filenames)} 个文件\n" \
                        f"成功导入: {processed_files} 个文件\n" \
                        f"成功记录: {total_success} 条\n" \
                        f"失败/跳过: {total_errors} 条"
            
            if processed_files > 1:
                result_msg += f"\n\n✅ 已应用统一配置到所有文件"
            
            notify(self, result_msg, 'info')
            self.load_data()
            
        except Exception as e:
            notify(self, f'无人值守批量导入失败: {str(e)}', 'error')



    def batch_ultra_fast_import_original(self, filenames):
        """原有的批量导入方法（重命名）"""
        # 这里就是原来 batch_ultra_fast_import 方法的代码
        # 只是改个名字避免冲突
        total_success = 0
        total_errors = 0
        processed_files = 0

    def clear_all_storage(self):
        """清除所有存储信息和缓存，恢复到初始状态"""
        try:
            print("[DEBUG] 开始清除所有存储信息...")
            
            # 1. 清除全局统一配置
            if hasattr(self, 'global_unified_config'):
                self.global_unified_config = None
                print("[DEBUG] 已清除全局统一配置")
            
            # 2. 清除字段映射缓存
            if hasattr(self, 'field_mapping_cache'):
                self.field_mapping_cache = {}
                print("[DEBUG] 已清除字段映射缓存")
            
            # 3. 清除文件历史记录
            if hasattr(self, 'previous_filename'):
                self.previous_filename = None
                print("[DEBUG] 已清除文件历史记录")
            
            # 4. 强制垃圾回收
            import gc
            gc.collect()
            print("[DEBUG] 已执行垃圾回收")
            
            # 5. 重新初始化关键组件
            self.load_users()  # 重新加载用户列表
            
            print("[DEBUG] 所有存储信息清除完成")
            
        except Exception as e:
            print(f"[DEBUG] 清除存储信息时出错: {str(e)}")

    def execute_unattended_batch_import_with_cleanup(self, filenames, use_existing_config, save_as_default, unattended_mode=True):
        """执行无人值守批量导入，每次导入后清理状态"""
        try:
            # 获取或创建统一配置
            if use_existing_config and hasattr(self, 'global_unified_config') and self.global_unified_config:
                unified_config = self.global_unified_config
                print("[DEBUG] 使用已保存的统一配置")
            else:
                # 使用第一个文件创建配置
                first_dialog = UltraFastImportDialog(filenames[0], self)
                if first_dialog.exec() != QDialog.Accepted:
                    return
                
                unified_config = first_dialog.get_import_config()
                print("[DEBUG] 使用第一个文件的配置作为统一配置")
            
            # 保存为默认配置（如果需要）
            if save_as_default:
                self.global_unified_config = unified_config
                print("[DEBUG] 已保存为默认统一配置")
            
            # 执行批量导入
            total_success = 0
            total_errors = 0
            processed_files = 0
            
            # 创建进度对话框
            progress_dialog = QProgressDialog("正在无人值守批量导入...", "取消", 0, len(filenames), self)
            progress_dialog.setWindowTitle("无人值守批量导入进度")
            progress_dialog.setWindowModality(Qt.WindowModal)
            progress_dialog.show()
            
            for i, filename in enumerate(filenames):
                if progress_dialog.wasCanceled():
                    break
                
                try:
                    # 修复：直接使用统一配置，不创建对话框
                    if i == 0:  # 第一个文件已经配置过了
                        import_config = unified_config
                    else:
                        # 对于后续文件，直接应用统一配置
                        import_config = unified_config.copy()
                        import_config['filename'] = filename  # 更新文件名
                    
                    # 直接执行导入，不显示对话框
                    success_count, error_count = self.execute_ultra_fast_import(import_config)
                    
                    total_success += success_count
                    total_errors += error_count
                    processed_files += 1
                    print(f"[DEBUG] 无人值守处理文件 {os.path.basename(filename)}: 成功{success_count}条, 失败{error_count}条")
                    
                    # 关键修改：每次导入后清除所有存储信息
                    self.clear_all_storage()
                    print(f"[DEBUG] 文件 {os.path.basename(filename)} 导入完成，已清除存储状态")
                    
                except Exception as e:
                    print(f"[DEBUG] 无人值守处理文件 {filename} 失败: {str(e)}")
                    total_errors += 1
                    # 即使失败也要清理状态
                    self.clear_all_storage()
                
                # 更新进度
                progress_dialog.setValue(i + 1)
                QApplication.processEvents()
            
            progress_dialog.close()
            
            # 显示结果
            result_msg = f"🤖 无人值守批量导入完成！\n\n" \
                        f"总共处理: {len(filenames)} 个文件\n" \
                        f"成功导入: {processed_files} 个文件\n" \
                        f"成功记录: {total_success} 条\n" \
                        f"失败/跳过: {total_errors} 条"
            
            if processed_files > 1:
                result_msg += f"\n\n✅ 已应用统一配置到所有文件"
            
            notify(self, result_msg, 'info')
            self.load_users()
            
        except Exception as e:
            notify(self, f'无人值守批量导入失败: {str(e)}', 'error')

    def batch_ultra_fast_import_with_cleanup(self, filenames):
        """交互式批量导入，每次导入后清理状态"""
        total_success = 0
        total_errors = 0
        processed_files = 0
        
        # 创建进度对话框
        progress_dialog = QProgressDialog("正在批量导入文件...", "取消", 0, len(filenames), self)
        progress_dialog.setWindowTitle("批量导入进度")
        progress_dialog.setWindowModality(Qt.WindowModal)
        progress_dialog.show()
        
        for i, filename in enumerate(filenames):
            if progress_dialog.wasCanceled():
                break
                
            try:
                # 显示超快速导入对话框
                dialog = UltraFastImportDialog(filename, self)
                if dialog.exec() == QDialog.Accepted:
                    import_config = dialog.get_import_config()
                    
                    # 执行超快速导入
                    success_count, error_count = self.execute_ultra_fast_import(import_config)
                    
                    total_success += success_count
                    total_errors += error_count
                    processed_files += 1
                    
                    # 关键修改：每次导入后清除所有存储信息
                    self.clear_all_storage()
                    print(f"[DEBUG] 文件 {os.path.basename(filename)} 导入完成，已清除存储状态")
                    
            except Exception as e:
                print(f"[DEBUG] 导入文件 {filename} 失败: {str(e)}")
                total_errors += 1
                # 即使失败也要清理状态
                self.clear_all_storage()
            
            # 更新进度
            progress_dialog.setValue(i + 1)
            QApplication.processEvents()
        
        progress_dialog.close()
        
        # 显示批量导入结果
        result_msg = f"⚡📥批量导入完成！\n\n总共处理 {len(filenames)} 个文件\n成功导入 {processed_files} 个文件\n成功记录: {total_success} 条\n失败记录: {total_errors} 条"
        
        notify(self, result_msg, 'info')
        self.load_users()

    def show_data_management_dialog(self):
        """显示数据管理对话框"""
        dialog = QDialog(self)
        dialog.setWindowTitle('数据管理')
        dialog.resize(500, 400)
        
        layout = QVBoxLayout()
        dialog.setLayout(layout)
        
        # 数据库信息
        info_group = QGroupBox('数据库信息')
        info_layout = QFormLayout()
        
        total_count = self.user_db.get_data_count()
        db_size = self.user_db.get_database_size() / (1024 * 1024)  # MB
        
        info_layout.addRow('总记录数:', QLabel(f"{total_count:,}"))
        info_layout.addRow('数据库大小:', QLabel(f"{db_size:.2f} MB"))
        
        info_group.setLayout(info_layout)
        layout.addWidget(info_group)
        
        # 数据归档选项
        archive_group = QGroupBox('数据归档')
        archive_layout = QVBoxLayout()
        
        # 归档条件选择
        condition_combo = QComboBox()
        condition_combo.addItems([
            '归档6个月前的数据',
            '归档1年前的数据',
            '归档特定条件的数据'
        ])
        archive_layout.addWidget(condition_combo)
        
        # 自定义条件输入
        custom_condition = QLineEdit()
        custom_condition.setPlaceholderText('例如: created_date < date("now", "-6 months")')
        archive_layout.addWidget(custom_condition)
        
        archive_btn = QPushButton('开始归档')
        archive_btn.clicked.connect(lambda: self.archive_data(condition_combo, custom_condition))
        archive_layout.addWidget(archive_btn)
        
        archive_group.setLayout(archive_layout)
        layout.addWidget(archive_group)
        
        # 数据清理选项
        cleanup_group = QGroupBox('数据清理')
        cleanup_layout = QVBoxLayout()
        
        cleanup_btn = QPushButton('清理重复数据')
        cleanup_btn.clicked.connect(self.cleanup_duplicate_data)
        cleanup_layout.addWidget(cleanup_btn)
        
        vacuum_btn = QPushButton('优化数据库 (VACUUM)')
        vacuum_btn.clicked.connect(self.vacuum_database)
        cleanup_layout.addWidget(vacuum_btn)
        
        cleanup_group.setLayout(cleanup_layout)
        layout.addWidget(cleanup_group)
        
        dialog.exec()

    def archive_data(self, condition_combo, custom_condition):
        """执行数据归档"""
        condition_text = condition_combo.currentText()
        
        if condition_text == '归档特定条件的数据':
            condition = custom_condition.text().strip()
            if not condition:
                notify(self, '请输入归档条件', 'warn')
                return
        else:
            # 根据选择的条件生成SQL条件
            if condition_text == '归档6个月前的数据':
                condition = "created_date < date('now', '-6 months')"
            else:  # 1年前的数据
                condition = "created_date < date('now', '-1 year')"
        
        reply = QMessageBox.question(
            self, '确认归档', 
            f'确定要归档符合条件的数据吗？\n条件: {condition}',
            QMessageBox.Yes | QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            archived_count = self.user_db.archive_old_data(condition)
            notify(self, f'已归档 {archived_count} 条记录', 'info')
            self.load_data()

    def cleanup_duplicate_data(self):
        """清理重复数据"""
        try:
            # 创建重复数据清理对话框
            dialog = QDialog(self)
            dialog.setWindowTitle('清理重复数据')
            dialog.resize(500, 400)
            
            layout = QVBoxLayout()
            dialog.setLayout(layout)
            
            # 说明信息
            info_label = QLabel('请选择用于识别重复数据的字段组合：')
            info_label.setWordWrap(True)
            layout.addWidget(info_label)
            
            # 字段选择区域
            field_group = QGroupBox('选择去重字段')
            field_layout = QVBoxLayout()
            
            # 获取所有字段
            fields = [col['name'] for col in self.columns_config]
            
            # 创建字段选择复选框
            self.duplicate_checkboxes = {}
            for field in fields:
                checkbox = QCheckBox(field)
                self.duplicate_checkboxes[field] = checkbox
                field_layout.addWidget(checkbox)
            
            field_group.setLayout(field_layout)
            layout.addWidget(field_group)
            
            # 清理选项
            options_group = QGroupBox('清理选项')
            options_layout = QVBoxLayout()
            
            # 保留策略
            retention_group = QGroupBox('保留策略')
            retention_layout = QVBoxLayout()
            
            self.retention_combo = QComboBox()
            self.retention_combo.addItems([
                '保留第一条记录',
                '保留最后一条记录',
                '保留最新记录（基于时间字段）',
                '保留最旧记录'
            ])
            retention_layout.addWidget(QLabel('重复数据保留策略:'))
            retention_layout.addWidget(self.retention_combo)
            
            # 时间字段选择（如果选择基于时间的策略）
            self.time_field_combo = QComboBox()
            self.time_field_combo.addItem('（自动检测）')
            self.time_field_combo.addItems([f for f in fields if any(keyword in f.lower() for keyword in ['date', 'time', 'create', 'update'])])
            retention_layout.addWidget(QLabel('时间字段:'))
            retention_layout.addWidget(self.time_field_combo)
            
            retention_group.setLayout(retention_layout)
            options_layout.addWidget(retention_group)
            
            # 预览选项
            preview_check = QCheckBox('先预览要删除的重复数据')
            preview_check.setChecked(True)
            options_layout.addWidget(preview_check)
            
            # 备份选项
            backup_check = QCheckBox('清理前自动备份数据库')
            backup_check.setChecked(True)
            options_layout.addWidget(backup_check)
            
            options_group.setLayout(options_layout)
            layout.addWidget(options_group)
            
            # 统计信息
            stats_label = QLabel('')
            stats_label.setStyleSheet("color: #666; font-size: 12px;")
            layout.addWidget(stats_label)
            
            selected_fields = []  # 提升到外层作用域，统计与按钮共用（修复作用域bug）
            def update_stats():
                nonlocal selected_fields
                selected_fields = [f for f, cb in self.duplicate_checkboxes.items() if cb.isChecked()]
                if selected_fields:
                    # 估算重复数据数量
                    duplicate_count = self.estimate_duplicate_count(selected_fields)
                    stats_label.setText(f"选中的字段: {', '.join(selected_fields)}\n预估重复记录数: {duplicate_count}")
                else:
                    stats_label.setText("请至少选择一个字段")
            
            # 连接复选框变化信号
            for checkbox in self.duplicate_checkboxes.values():
                checkbox.stateChanged.connect(update_stats)
            
            # 按钮区域
            button_layout = QHBoxLayout()
            
            preview_btn = QPushButton('🔍 预览重复数据')
            def on_preview():
                if not selected_fields:
                    notify(self, '请至少选择一个字段', 'warn')
                    return
                self.preview_duplicates(selected_fields)
            preview_btn.clicked.connect(on_preview)
            button_layout.addWidget(preview_btn)
            
            button_layout.addStretch()
            
            start_btn = QPushButton('🚀 开始清理')
            def on_cleanup():
                if not selected_fields:
                    notify(self, '请至少选择一个字段', 'warn')
                    return
                self.execute_duplicate_cleanup(
                    selected_fields, 
                    self.retention_combo.currentText(),
                    self.time_field_combo.currentText(),
                    preview_check.isChecked(),
                    backup_check.isChecked(),
                    dialog
                )
            start_btn.clicked.connect(on_cleanup)
            button_layout.addWidget(start_btn)
            
            cancel_btn = QPushButton('取消')
            cancel_btn.clicked.connect(dialog.reject)
            button_layout.addWidget(cancel_btn)
            
            layout.addLayout(button_layout)
            
            # 初始更新统计信息
            update_stats()
            
            if dialog.exec() == QDialog.Accepted:
                pass
                
        except Exception as e:
            notify(self, f'清理重复数据失败: {str(e)}', 'error')

    def estimate_duplicate_count(self, fields):
        """估算重复数据数量"""
        try:
            if not fields:
                return 0
                
            # 构建GROUP BY子句
            group_by_clause = ', '.join([f'"{field}"' for field in fields])
            
            # 查询重复数据
            sql = f'''
                SELECT COUNT(*) - COUNT(DISTINCT {group_by_clause}) as duplicate_count
                FROM data
                WHERE {group_by_clause} IS NOT NULL
            '''
            
            self.user_db.cursor.execute(sql)
            result = self.user_db.cursor.fetchone()
            return result[0] if result else 0
            
        except Exception as e:
            print(f"[DEBUG] 估算重复数据失败: {str(e)}")
            return 0

    def preview_duplicates(self, fields):
        """预览重复数据"""
        if not fields:
            notify(self, '请先选择去重字段', 'warn')
            return
            
        try:
            # 构建查询SQL
            group_by_clause = ', '.join([f'"{field}"' for field in fields])
            select_fields = ', '.join([f'"{field}"' for field in fields])
            
            sql = f'''
                SELECT {select_fields}, COUNT(*) as duplicate_count
                FROM data
                WHERE {group_by_clause} IS NOT NULL
                GROUP BY {group_by_clause}
                HAVING COUNT(*) > 1
                ORDER BY duplicate_count DESC
                LIMIT 100
            '''
            
            self.user_db.cursor.execute(sql)
            duplicates = self.user_db.cursor.fetchall()
            
            if not duplicates:
                notify(self, '未发现重复数据', 'info')
                return
            
            # 显示预览对话框
            dialog = QDialog(self)
            dialog.setWindowTitle('重复数据预览 (前100条)')
            dialog.resize(800, 600)
            
            layout = QVBoxLayout()
            dialog.setLayout(layout)
            
            # 信息标签
            info_label = QLabel(f'发现 {len(duplicates)} 组重复数据 (显示前100组)')
            info_label.setStyleSheet("color: #D32F2F; font-weight: bold; padding: 5px;")
            layout.addWidget(info_label)
            
            # 重复数据表格
            table = QTableWidget()
            table.setColumnCount(len(fields) + 1)
            table.setHorizontalHeaderLabels(fields + ['重复次数'])
            
            table.setRowCount(len(duplicates))
            for row, duplicate in enumerate(duplicates):
                for col, value in enumerate(duplicate):
                    item = QTableWidgetItem(str(value))
                    table.setItem(row, col, item)
            
            table.resizeColumnsToContents()
            layout.addWidget(table)
            
            # 关闭按钮
            close_btn = QPushButton('关闭')
            close_btn.clicked.connect(dialog.accept)
            layout.addWidget(close_btn)
            
            dialog.exec()
            
        except Exception as e:
            notify(self, f'预览重复数据失败: {str(e)}', 'error')

    def execute_duplicate_cleanup(self, fields, retention_strategy, time_field, preview_first, create_backup, parent_dialog):
        """执行重复数据清理"""
        if not fields:
            notify(self, '请至少选择一个字段', 'warn')
            return
        
        try:
            # 确认操作
            reply = QMessageBox.question(
                self, '确认清理',
                f'确定要清理基于字段 [{", ".join(fields)}] 的重复数据吗？\n'
                f'保留策略: {retention_strategy}\n\n'
                '此操作不可逆，建议先备份数据库！',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            
            if reply == QMessageBox.No:
                return
            
            # 创建备份
            if create_backup:
                backup_path = self.user_db.backup_database(backup_type="cleanup")
                if not backup_path:
                    notify(self, '备份创建失败，是否继续？', 'warn')
            
            # 执行清理
            deleted_count = self.perform_duplicate_cleanup(fields, retention_strategy, time_field)
            
            # 显示结果
            notify(self, f'重复数据清理完成！\n\n'
                f'删除的重复记录数: {deleted_count}\n'
                f'剩余唯一记录数: {self.user_db.get_data_count()}', 'info')
            
            # 关闭对话框并刷新数据
            parent_dialog.accept()
            self.load_data()
            
        except Exception as e:
            notify(self, f'清理重复数据失败: {str(e)}', 'error')

    def perform_duplicate_cleanup(self, fields, retention_strategy, time_field):
        """执行实际的重复数据清理"""
        try:
            # 构建字段列表
            field_list = ', '.join([f'"{field}"' for field in fields])
            
            # 根据保留策略构建SQL
            if retention_strategy == '保留第一条记录':
                sql = f'''
                    DELETE FROM data 
                    WHERE rowid NOT IN (
                        SELECT MIN(rowid) 
                        FROM data 
                        WHERE {field_list} IS NOT NULL
                        GROUP BY {field_list}
                    )
                    AND {field_list} IS NOT NULL
                '''
            elif retention_strategy == '保留最后一条记录':
                sql = f'''
                    DELETE FROM data 
                    WHERE rowid NOT IN (
                        SELECT MAX(rowid) 
                        FROM data 
                        WHERE {field_list} IS NOT NULL
                        GROUP BY {field_list}
                    )
                    AND {field_list} IS NOT NULL
                '''
            elif retention_strategy == '保留最新记录（基于时间字段）':
                if time_field == '（自动检测）':
                    # 尝试自动检测时间字段
                    time_fields = [f for f in self.columns_config if any(keyword in f['name'].lower() for keyword in ['date', 'time', 'create', 'update'])]
                    if time_fields:
                        time_field = time_fields[0]['name']
                    else:
                        notify(self, '未找到时间字段，使用默认策略', 'warn')
                        return self.perform_duplicate_cleanup(fields, '保留最后一条记录', '')
                
                sql = f'''
                    DELETE FROM data 
                    WHERE rowid NOT IN (
                        SELECT rowid FROM (
                            SELECT rowid, ROW_NUMBER() OVER (
                                PARTITION BY {field_list} 
                                ORDER BY "{time_field}" DESC
                            ) as rn 
                            FROM data 
                            WHERE {field_list} IS NOT NULL
                        ) WHERE rn = 1
                    )
                    AND {field_list} IS NOT NULL
                '''
            else:  # 保留最旧记录
                sql = f'''
                    DELETE FROM data 
                    WHERE rowid NOT IN (
                        SELECT MIN(rowid) 
                        FROM data 
                        WHERE {field_list} IS NOT NULL
                        GROUP BY {field_list}
                    )
                    AND {field_list} IS NOT NULL
                '''
            
            # 执行删除
            self.user_db.cursor.execute(sql)
            deleted_count = self.user_db.cursor.rowcount
            self.user_db.conn.commit()
            
            # 执行VACUUM以回收空间
            self.user_db.cursor.execute("VACUUM")
            self.user_db.conn.commit()
            
            return deleted_count
            
        except Exception as e:
            self.user_db.conn.rollback()
            raise e

    def find_duplicate_groups(self, fields, limit=10):
        """查找重复数据分组（用于调试）"""
        try:
            field_list = ', '.join([f'"{field}"' for field in fields])
            
            sql = f'''
                SELECT {field_list}, COUNT(*) as count 
                FROM data 
                WHERE {field_list} IS NOT NULL
                GROUP BY {field_list} 
                HAVING COUNT(*) > 1 
                ORDER BY count DESC 
                LIMIT {limit}
            '''
            
            self.user_db.cursor.execute(sql)
            return self.user_db.cursor.fetchall()
            
        except Exception as e:
            print(f"[DEBUG] 查找重复分组失败: {str(e)}")
            return []


    def vacuum_database(self):
        """执行数据库优化"""
        try:
            self.user_db.cursor.execute("VACUUM")
            self.user_db.conn.commit()
            notify(self, '数据库优化完成', 'info')
        except Exception as e:
            notify(self, f'数据库优化失败: {str(e)}', 'error')


class EditDataDialog(QDialog):
    def __init__(self, columns_config, current_data=None, parent=None):
        super().__init__(parent)
        self.columns_config = columns_config
        self.current_data = current_data or {}
        self.init_ui()
        
    def init_ui(self):
        self.setWindowTitle('编辑数据')
        self.resize(600, 400)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 创建表单布局
        form_layout = QFormLayout()
        self.input_widgets = {}
        
        for col in self.columns_config:
            label = col['label']
            if col.get('is_required', False):
                label += " *"
            
            widget = QLineEdit()
            if col['name'] in self.current_data:
                widget.setText(str(self.current_data[col['name']]))
            
            if col.get('is_required', False):
                widget.setStyleSheet("background-color: #FFF9C4;")
            
            self.input_widgets[col['name']] = widget
            form_layout.addRow(label, widget)
        
        layout.addLayout(form_layout)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.ok_btn = QPushButton('确定')
        self.ok_btn.clicked.connect(self.accept)
        button_layout.addWidget(self.ok_btn)
        
        self.cancel_btn = QPushButton('取消')
        self.cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(self.cancel_btn)
        
        layout.addLayout(button_layout)
    
    def get_data(self):
        data = {}
        for col_name, widget in self.input_widgets.items():
            data[col_name] = widget.text().strip()
        return data
    
    def validate_input(self):
        errors = []
        for col in self.columns_config:
            widget = self.input_widgets[col['name']]
            value = widget.text().strip()
            
            if col.get('is_required', False) and not value:
                errors.append(f"{col['label']} 是必填项")
                widget.setStyleSheet("background-color: #FFCDD2;")
            else:
                widget.setStyleSheet("")
                
            if col['type'] == 'EAN13' and value:
                is_valid, error_msg = self.parent().validate_ean13(value)
                if not is_valid:
                    errors.append(f"{col['label']} {error_msg}")
                    widget.setStyleSheet("background-color: #FFCDD2;")
        
        if errors:
            notify(self, '\n'.join(errors), 'warn')
            return False
        return True
    
    def accept(self):
        if self.validate_input():
            super().accept()



def resource_path(relative_path):
    """获取资源的绝对路径。用于PyInstaller打包后定位资源文件"""
    try:
        # PyInstaller创建的临时文件夹中的路径
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)




if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    # 设置应用程序样式
    app.setStyle('Fusion')
    
    # 初始化用户管理器
    user_manager = UserManager()
    
    # 显示登录窗口（按保存的最大化状态显示）
    login_window = LoginWindow(user_manager)
    if getattr(login_window, '_start_maximized', False):
        login_window.showMaximized()
    else:
        login_window.show()
    
    sys.exit(app.exec())