import os
import sys
import sqlite3
import csv
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                            QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                            QMessageBox, QComboBox, QTabWidget, QFormLayout, QGroupBox,
                            QInputDialog, QListWidget, QAbstractItemView, QHeaderView,
                            QDialog, QCheckBox, QFileDialog, QStatusBar, QAction, QMenu,
                            QToolBar, QSizePolicy, QSpacerItem, QSplitter, QTextEdit,
                            QScrollArea, QDateEdit)
from PyQt5.QtGui import QIcon, QPixmap, QImage, QFont, QTextDocument, QColor, QPainter
from PyQt5.QtCore import Qt, QSize, QTimer, QRect, QDate
from PyQt5.QtPrintSupport import QPrinter, QPrintDialog
from barcode import EAN13
import qrcode
from barcode import Code128
from barcode.writer import ImageWriter
import io
from PIL import Image
import json
from datetime import datetime
from pypinyin import lazy_pinyin, Style

import subprocess
import sys

def install_package(package):
    """自动安装缺失的包"""
    try:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        print(f"成功安装 {package}")
        return True
    except subprocess.CalledProcessError:
        print(f"安装 {package} 失败，请手动安装: pip install {package}")
        return False

# 检查并安装 pypinyin
try:
    from pypinyin import lazy_pinyin, Style
except ImportError:
    print("pypinyin 未安装，正在尝试自动安装...")
    if install_package("pypinyin"):
        from pypinyin import lazy_pinyin, Style
    else:
        print("pypinyin 安装失败，拼音搜索功能将不可用")


class ProjectInfo:
    """项目信息元数据（集中管理所有项目相关信息）"""
    VERSION = "4.21.0"
    BUILD_DATE = "2025-06-02"
    # BUILD_DATE = datetime.now().strftime("%Y-%m-%d")  # 修改为动态获取当前日期
    AUTHOR = "杜玛"
    LICENSE = "MIT"
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
        "4.21.0": "增加数据库备份恢复功能。"
    }
    HELP_TEXT = """
数据管理系统使用说明

1. 用户管理
   - 首次使用需要创建用户
   - 每个用户可以配置独立的数据库结构
   - 支持多用户同时使用

2. 数据操作
   - 添加数据: 点击工具栏"+"按钮或按Ctrl+N
   - 编辑数据: 双击表格行或右键选择"编辑"
   - 删除数据: 选中行后右键选择"删除"或按Delete键
   - 搜索数据: 在搜索框输入关键词后回车或点击搜索按钮

3. 条形码/二维码功能
   - 在列配置中勾选"条形码"或"二维码"选项
   - 支持EAN13标准条形码(13位数字)
   - 支持任意文本生成二维码

4. 数据导出
   - 支持导出为CSV文件
   - 支持打印数据详情
   - 支持导出条形码/二维码为图片

5. 快捷键
   - Ctrl+N: 添加新数据
   - Ctrl+F: 搜索
   - Ctrl+E: 编辑选中数据
   - Delete: 删除选中数据
   - F5: 刷新数据

6. 技术支持
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
    def __init__(self, db_file):
        self.db_file = db_file
        self.conn = sqlite3.connect(db_file)
        self.cursor = self.conn.cursor()
        self._create_tables()
        # 设置WAL模式
        self.cursor.execute("PRAGMA journal_mode=WAL")
    
    def _create_tables(self):
        pass
    
    def initialize_database(self, columns_config):
        self.cursor.execute('DROP TABLE IF EXISTS data')
        
        columns = []
        for col in columns_config:
            col_name = col['name']
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
        
        for col in columns_config:
            self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                              (f"col_{col['name']}_label", col['label']))
            if col.get('is_barcode', False):
                self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                                  (f"barcode_column_{col['name']}", "1"))
            if col.get('is_qrcode', False):
                self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                                  (f"qrcode_column_{col['name']}", "1"))
            if col.get('is_unique', False):
                self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                                  ('unique_column', col['name']))
            if col.get('is_required', False):
                self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                                  ('required_column', col['name']))
        
        self.conn.commit()
    
    def get_columns_config(self):
        self.cursor.execute('SELECT key, value FROM config WHERE key LIKE "col_%"')
        config = {}
        for key, value in self.cursor.fetchall():
            config[key] = value
        
        if not config:
            return None
        
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
            self.cursor.execute('SELECT value FROM config WHERE key=?', (f"barcode_column_{col}",))
            if self.cursor.fetchone():
                col_config['is_barcode'] = True
            
            # 检查是否是二维码列
            self.cursor.execute('SELECT value FROM config WHERE key=?', (f"qrcode_column_{col}",))
            if self.cursor.fetchone():
                col_config['is_qrcode'] = True
            
            # 检查是否是唯一列
            self.cursor.execute('SELECT value FROM config WHERE key="unique_column"')
            result = self.cursor.fetchone()
            if result and result[0] == col:
                col_config['is_unique'] = True
            
            # 检查是否是必填列
            self.cursor.execute('SELECT value FROM config WHERE key="required_column"')
            result = self.cursor.fetchone()
            if result and result[0] == col:
                col_config['is_required'] = True
            
            # 获取列类型
            self.cursor.execute('PRAGMA table_info(data)')
            for col_info in self.cursor.fetchall():
                if col_info[1] == col:
                    col_config['type'] = col_info[2].upper()
                    break
            
            columns_config.append(col_config)
        
        return columns_config

    
    def insert_data(self, data):
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        sql = f"INSERT INTO data ({columns}) VALUES ({placeholders})"
        self.cursor.execute(sql, tuple(data.values()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def update_data(self, rowid, data):
        set_clause = ', '.join([f"{key}=?" for key in data.keys()])
        sql = f"UPDATE data SET {set_clause} WHERE rowid=?"
        self.cursor.execute(sql, tuple(data.values()) + (rowid,))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def delete_data(self, rowid):
        sql = "DELETE FROM data WHERE rowid=?"
        self.cursor.execute(sql, (rowid,))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def check_unique(self, column, value, exclude_rowid=None):
        sql = f"SELECT COUNT(*) FROM data WHERE {column}=?"
        params = [value]
        
        if exclude_rowid:
            sql += " AND rowid!=?"
            params.append(exclude_rowid)
        
        self.cursor.execute(sql, tuple(params))
        return self.cursor.fetchone()[0] == 0
    
    def search_data(self, keyword):
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        conditions = []
        params = []
        for col in columns:
            conditions.append(f"{col} LIKE ?")
            params.append(f"%{keyword}%")
        
        sql = f"SELECT rowid, * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def search_data_enhanced(self, keyword):
        """增强搜索：支持中文、英文、拼音、拼音首字母模糊搜索"""
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
            # 原始关键词搜索
            conditions.append(f"{col} LIKE ?")
            params.append(f"%{keyword}%")
            
            # 拼音全拼搜索
            if pinyin_full and pinyin_full != keyword:
                conditions.append(f"{col} LIKE ?")
                params.append(f"%{pinyin_full}%")
            
            # 拼音首字母搜索
            if pinyin_initials and pinyin_initials != keyword:
                conditions.append(f"{col} LIKE ?")
                params.append(f"%{pinyin_initials}%")
        
        if not conditions:
            return []
        
        sql = f"SELECT DISTINCT rowid, * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()

    def search_data_all_columns(self, keyword):
        """搜索所有列，包含完整数据"""
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        if not columns:
            return []
        
        # 构建搜索条件
        conditions = []
        params = []
        for col in columns:
            conditions.append(f"{col} LIKE ?")
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
            shutil.copy2(backup_path, self.db_file)
            
            # 重新连接
            self.conn = sqlite3.connect(self.db_file)
            self.cursor = self.conn.cursor()
            
            return True
        except Exception as e:
            print(f"恢复失败: {str(e)}")
            return False

    def search_data_all_columns_enhanced(self, keyword):
        """增强的全列搜索：支持中文、英文、拼音、拼音首字母模糊搜索"""
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        if not columns:
            return []
        
        # 生成拼音和拼音首字母
        from pypinyin import lazy_pinyin, Style
        
        # 中文转拼音
        pinyin_full = ''.join(lazy_pinyin(keyword))
        # 中文转拼音首字母
        pinyin_initials = ''.join(lazy_pinyin(keyword, style=Style.FIRST_LETTER))
        
        conditions = []
        params = []
        
        for col in columns:
            # 原始关键词搜索
            conditions.append(f"{col} LIKE ?")
            params.append(f"%{keyword}%")
            
            # 拼音全拼搜索
            if pinyin_full and pinyin_full != keyword:
                conditions.append(f"{col} LIKE ?")
                params.append(f"%{pinyin_full}%")
            
            # 拼音首字母搜索
            if pinyin_initials and pinyin_initials != keyword:
                conditions.append(f"{col} LIKE ?")
                params.append(f"%{pinyin_initials}%")
        
        if not conditions:
            return []
        
        sql = f"SELECT DISTINCT rowid, * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()


class LoginWindow(QMainWindow):
    def __init__(self, user_manager):
        super().__init__()
        self.user_manager = user_manager
        self.init_ui()
        
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
        
        self.login_btn = QPushButton('登录')
        self.login_btn.clicked.connect(self.login)
        button_layout.addWidget(self.login_btn)
        
        self.add_user_btn = QPushButton('添加用户')
        self.add_user_btn.clicked.connect(self.add_user)
        button_layout.addWidget(self.add_user_btn)
        
        self.delete_user_btn = QPushButton('删除用户')
        self.delete_user_btn.clicked.connect(self.delete_user)
        button_layout.addWidget(self.delete_user_btn)

        self.edit_user_btn = QPushButton('修改用户名')
        self.edit_user_btn.clicked.connect(self.edit_username)
        button_layout.addWidget(self.edit_user_btn)
        
        self.import_data_btn = QPushButton('导入数据')
        self.import_data_btn.clicked.connect(self.import_data_file)
        button_layout.addWidget(self.import_data_btn)

        self.import_rules_btn = QPushButton('导入麻将规则')
        self.import_rules_btn.clicked.connect(self.import_rules_json)
        button_layout.addWidget(self.import_rules_btn)

        self.import_btn = QPushButton('导入用户')
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
            QMessageBox.warning(self, '警告', '请选择一个用户')
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
            QMessageBox.warning(self, '警告', '新用户名不能与原用户名相同')
            return
            
        # 调用UserManager更新用户名
        success, message = self.user_manager.update_username(old_username, new_username)
        if success:
            QMessageBox.information(self, '成功', message)
            self.load_users()  # 刷新用户列表
        else:
            QMessageBox.warning(self, '警告', message)

    def load_users(self):
        self.user_list.clear()
        users = self.user_manager.get_users()
        for user in users:
            self.user_list.addItem(user[0])
    
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
            QMessageBox.warning(self, '警告', '请选择一个用户')
            return
        
        username = selected_items[0].text()
        db_file = self.user_manager.get_user_db_file(username)
        settings = self.user_manager.get_user_settings(username)
        
        # 更新最后登录时间
        self.user_manager.update_last_login(username)
        
        self.main_window = MainWindow(username, db_file, settings, self.user_manager, self)
        self.main_window.show()
        self.hide()
    
    def add_user(self):
        username, ok = QInputDialog.getText(self, '添加用户', '输入用户名:')
        if not ok or not username:
            return
        
        if username in [self.user_list.item(i).text() for i in range(self.user_list.count())]:
            QMessageBox.warning(self, '警告', '用户名已存在')
            return
        
        col_dialog = ColumnConfigDialog()
        if col_dialog.exec_() == QDialog.Accepted:
            columns_config = col_dialog.get_columns_config()
            if not columns_config:
                QMessageBox.warning(self, '警告', '必须至少配置一列')
                return
            
            db_file = f'user_{username}.db'
            user_db = UserDatabase(db_file)
            user_db.initialize_database(columns_config)
            user_db.close()
            
            settings = json.dumps(columns_config, ensure_ascii=False)
            if self.user_manager.add_user(username, db_file, settings):
                self.load_users()
            else:
                QMessageBox.warning(self, '警告', '添加用户失败')
    
    def delete_user(self):
        selected_items = self.user_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, '警告', '请选择一个用户')
            return
        
        username = selected_items[0].text()
        reply = QMessageBox.question(self, '确认', f'确定要删除用户 {username} 吗?\n这将删除所有相关数据!', 
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            if self.user_manager.delete_user(username):
                db_file = f'user_{username}.db'
                if os.path.exists(db_file):
                    os.remove(db_file)
                self.load_users()
            else:
                QMessageBox.warning(self, '警告', '删除用户失败')

    def import_users(self):
        """导入用户数据"""
        filename, _ = QFileDialog.getOpenFileName(
            self, '选择用户数据文件', '', 
            'JSON文件 (*.json);;CSV文件 (*.csv);;所有文件 (*)'
        )
        
        if not filename:
            return
        
        try:
            if filename.endswith('.json'):
                with open(filename, 'r', encoding='utf-8') as f:
                    users_data = json.load(f)
            elif filename.endswith('.csv'):
                with open(filename, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    users_data = list(reader)
            else:
                QMessageBox.warning(self, '警告', '不支持的文件格式')
                return
            
            # 显示导入选项对话框
            dialog = ImportUsersDialog(users_data, self)
            if dialog.exec_() == QDialog.Accepted:
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
                
                QMessageBox.information(
                    self, '导入完成', 
                    f'成功导入 {imported_count} 个用户\n失败 {len(dialog.get_selected_users()) - imported_count} 个'
                )
                self.load_users()
                
        except Exception as e:
            QMessageBox.critical(self, '错误', f'导入用户失败: {str(e)}')

    def import_data_file(self):
        """导入数据文件并创建新用户"""
        filename, _ = QFileDialog.getOpenFileName(
            self, '选择数据文件', '', 
            'JSON文件 (*.json);;CSV文件 (*.csv);;所有文件 (*)'
        )
        
        if not filename:
            return
        
        try:
            # 读取文件内容
            if filename.endswith('.json'):
                with open(filename, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            elif filename.endswith('.csv'):
                with open(filename, 'r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    data = list(reader)
            else:
                QMessageBox.warning(self, '警告', '不支持的文件格式')
                return
            
            # 分析数据结构和内容
            if not data:
                QMessageBox.warning(self, '警告', '文件内容为空')
                return
            
            # 自动生成用户名
            base_name = os.path.splitext(os.path.basename(filename))[0]
            username = f"{base_name}_{datetime.now().strftime('%H%M%S')}"
            
            # 分析数据结构并生成列配置
            user_db = UserDatabase('')  # 临时实例用于调用方法
            columns_config = user_db.auto_detect_columns(data)
            
            if not columns_config:
                print("[DEBUG] 无法识别数据结构，使用默认配置")
                QMessageBox.warning(self, '警告', '无法识别数据结构')
                return
            
            # 创建新用户
            db_file = f'user_{username}.db'
            user_db = UserDatabase(db_file)
            user_db.initialize_database(columns_config)
            
            # 插入数据
            try:
                if isinstance(data, dict):
                    for key, items in data.items():
                        if isinstance(items, list):
                            for item in items:
                                if isinstance(item, dict):
                                    # 转换键名以匹配列名
                                    normalized_item = {}
                                    for k, v in item.items():
                                        col_name = k.replace(' ', '_').lower()
                                        normalized_item[col_name] = str(v) if v is not None else ""
                                    user_db.insert_data(normalized_item)
                elif isinstance(data, list):
                    for item in data:
                        normalized_item = {}
                        for k, v in item.items():
                            col_name = k.replace(' ', '_').lower()
                            normalized_item[col_name] = str(v) if v is not None else ""
                        user_db.insert_data(normalized_item)
            except Exception as e:
                user_db.close()
                os.remove(db_file)
                raise e
            
            user_db.close()
            
            # 添加用户到管理器
            settings = json.dumps(columns_config, ensure_ascii=False)
            if self.user_manager.add_user(username, db_file, settings):
                QMessageBox.information(
                    self, '成功', 
                    f'数据导入成功\n已创建用户: {username}\n导入记录数: {len(data) if isinstance(data, list) else "多组数据"}'
                )
                self.load_users()
            else:
                print(f"[DEBUG] 创建用户失败: {username}")
                QMessageBox.warning(self, '警告', '创建用户失败')
                
        except Exception as e:
            print(f"[DEBUG] 导入数据失败: {str(e)}")
            QMessageBox.critical(self, '错误', f'导入数据失败: {str(e)}')

    def import_rules_json(self):
        """专门导入麻将规则的JSON文件"""
        filename, _ = QFileDialog.getOpenFileName(
            self, '选择麻将规则JSON文件', '', 
            'JSON文件 (*.json);;所有文件 (*)'
        )
        
        if not filename:
            return
        
        try:
            with open(filename, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            if not isinstance(data, dict):
                QMessageBox.warning(self, '警告', '不是有效的规则JSON格式')
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
                            user_db.insert_data(record)
            
            user_db.close()
            
            # 添加用户到管理器
            settings = json.dumps(columns_config, ensure_ascii=False)
            if self.user_manager.add_user(username, db_file, settings):
                QMessageBox.information(
                    self, '成功', 
                    f'麻将规则导入成功\n已创建用户: {username}\n导入规则数: {sum(len(v) for v in data.values() if isinstance(v, list))}'
                )
                self.load_users()
            else:
                QMessageBox.warning(self, '警告', '创建用户失败')
                
        except Exception as e:
            QMessageBox.critical(self, '错误', f'导入规则失败: {str(e)}')


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


class ColumnConfigDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('列配置')
        self.resize(800, 500)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 说明标签
        info_label = QLabel('配置数据表的列结构。至少需要一列，可以设置列名、显示名称、数据类型和特殊属性。')
        info_label.setWordWrap(True)
        layout.addWidget(info_label)
        
        # 表格用于配置列
        self.table = QTableWidget()
        self.table.setColumnCount(7)
        self.table.setHorizontalHeaderLabels(['列名', '显示名称', '数据类型', '必填', '唯一', '条形码', '二维码'])
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
    
    def add_column(self):
        row = self.table.rowCount()
        self.table.insertRow(row)
        
        # 列名
        col_name = QLineEdit(f'column_{row+1}')
        self.table.setCellWidget(row, 0, col_name)
        
        # 显示名称
        col_label = QLineEdit(f'列 {row+1}')
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
        
        # 设置列宽
        self.table.setColumnWidth(0, 120)
        self.table.setColumnWidth(2, 120)  # 加宽以适应中文
        for i in range(3, 7):
            self.table.setColumnWidth(i, 60)
    
    def remove_column(self):
        current_row = self.table.currentRow()
        if current_row >= 0:
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
                'is_qrcode': self.table.cellWidget(row, 6).isChecked()
            }
            columns_config.append(config)
        
        return columns_config


class MainWindow(QMainWindow):
    def __init__(self, username, db_file, settings, user_manager, parent_window=None):
        super().__init__()
        self.username = username
        self.db_file = db_file
        self.user_manager = user_manager
        self.parent_window = parent_window
        self.current_rowid = None
        
        try:
            self.columns_config = json.loads(settings)
        except:
            self.columns_config = []
        
        self.user_db = UserDatabase(db_file)
        if not self.columns_config:
            self.columns_config = self.user_db.get_columns_config()
        
        self.init_ui()
        
        icon_path = resource_path('icon.ico')
        if os.path.exists('icon.ico'):
            self.setWindowIcon(QIcon('icon.ico'))

        # 初始化时自动创建备份
        self.user_db.backup_database(backup_type="auto")
        
        # 设置定时备份
        self.backup_timer = QTimer(self)
        self.backup_timer.timeout.connect(lambda: self.user_db.backup_database(backup_type="auto"))
        self.backup_timer.start(2 * 60 * 60 * 1000)  # 2小时备份一次
    
    def init_ui(self):
        # self.setWindowTitle(f'数据管理系统 - {self.username}')
        self.setWindowTitle(f"{ProjectInfo.NAME} {ProjectInfo.VERSION} (Build: {ProjectInfo.BUILD_DATE}) - {self.username}")
        # self.setWindowTitle(f"{ProjectInfo.NAME} v{ProjectInfo.VERSION} | {self.username} | Build: {ProjectInfo.BUILD_DATE}")

        self.resize(1200, 800)
        
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
        search_group = QGroupBox('搜索')
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('输入关键词搜索所有列...')
        self.search_input.returnPressed.connect(self.search_data)
        search_layout.addWidget(self.search_input)
        
        self.search_btn = QPushButton('搜索所有列')
        self.search_btn.clicked.connect(self.search_data)
        search_layout.addWidget(self.search_btn)
        
        self.clear_search_btn = QPushButton('清除')
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

        self.tab_widget.addTab(self.data_table, '数据列表')
        
        # 详情视图
        self.detail_widget = QWidget()
        self.detail_layout = QVBoxLayout()
        self.detail_widget.setLayout(self.detail_layout)
        self.tab_widget.addTab(self.detail_widget, '详细信息')
        
        # 统计视图
        self.stats_widget = QWidget()
        self.stats_layout = QVBoxLayout()
        self.stats_widget.setLayout(self.stats_layout)
        self.tab_widget.addTab(self.stats_widget, '数据统计')
        
        main_layout.addWidget(self.tab_widget)
        
        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.update_status_bar()
        
        # 加载数据
        self.load_data()

        # 添加表头排序功能
        self.data_table.horizontalHeader().sectionClicked.connect(self.sort_table_by_column)
        
        # 初始化排序状态
        self.sort_orders = {}  # 存储每列的排序状态：None(未排序), 'asc'(升序), 'desc'(降序)

    def sort_table_by_column(self, column_index):
        """根据点击的表头列进行排序"""
        # 获取当前列的排序状态
        current_order = self.sort_orders.get(column_index, None)
        
        # 切换排序状态：None -> asc -> desc -> None
        if current_order is None:
            new_order = 'asc'
        elif current_order == 'asc':
            new_order = 'desc'
        else:
            new_order = None
        
        # 更新排序状态
        self.sort_orders[column_index] = new_order
        
        if new_order is None:
            # 取消排序，恢复原始顺序
            self.load_data()
            return
        
        # 执行排序
        self.perform_sorting(column_index, new_order)

    def perform_sorting(self, column_index, order):
        """执行实际的表格排序"""
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
        """更新表头显示排序指示器"""
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
        file_menu = menubar.addMenu('文件')
        
        # 添加备份菜单项
        backup_menu = file_menu.addMenu('备份')
        manual_backup_action = QAction('手动备份', self)
        manual_backup_action.triggered.connect(self.manual_backup)
        backup_menu.addAction(manual_backup_action)
        
        restore_action = QAction('恢复备份', self)
        restore_action.triggered.connect(self.show_restore_dialog)
        backup_menu.addAction(restore_action)
        
        export_action = QAction('导出数据', self)
        export_action.triggered.connect(self.export_data)
        file_menu.addAction(export_action)
        
        refresh_action = QAction('刷新数据', self)
        refresh_action.triggered.connect(self.load_data)
        file_menu.addAction(refresh_action)
        
        logout_action = QAction('退出登录', self)
        logout_action.triggered.connect(self.logout)
        file_menu.addAction(logout_action)
        
        exit_action = QAction('退出', self)
        exit_action.triggered.connect(self.close)
        file_menu.addAction(exit_action)
        
        # 工具菜单
        tools_menu = menubar.addMenu('工具')
        
        settings_action = QAction('用户设置', self)
        settings_action.triggered.connect(self.show_user_settings)
        tools_menu.addAction(settings_action)
        
        # 帮助菜单
        help_menu = menubar.addMenu('帮助')
        
        about_action = QAction('关于', self)
        about_action.triggered.connect(self.show_about)
        help_menu.addAction(about_action)
    
    def create_tool_bar(self):
        toolbar = QToolBar("主工具栏")
        self.addToolBar(toolbar)
        
        # 添加工具按钮
        add_action = QAction(QIcon.fromTheme('list-add'), '添加数据', self)
        add_action.triggered.connect(self.add_data)
        toolbar.addAction(add_action)
        
        edit_action = QAction(QIcon.fromTheme('edit'), '编辑数据', self)
        edit_action.triggered.connect(self.edit_selected_data)
        toolbar.addAction(edit_action)
        
        delete_action = QAction(QIcon.fromTheme('edit-delete'), '删除数据', self)
        delete_action.triggered.connect(self.delete_data)
        toolbar.addAction(delete_action)
        
        toolbar.addSeparator()
        
        search_action = QAction(QIcon.fromTheme('system-search'), '搜索', self)
        search_action.triggered.connect(self.search_data)
        toolbar.addAction(search_action)
        
        refresh_action = QAction(QIcon.fromTheme('view-refresh'), '刷新', self)
        refresh_action.triggered.connect(self.load_data)
        toolbar.addAction(refresh_action)
        
        toolbar.addSeparator()
        
        export_action = QAction(QIcon.fromTheme('document-export'), '导出', self)
        export_action.triggered.connect(self.export_data)
        toolbar.addAction(export_action)
    
    def load_data(self):
        try:
            # 设置表头
            headers = [col['label'] for col in self.columns_config]
            
            # 添加条形码和二维码列
            barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
            qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
            
            for col in barcode_cols:
                headers.append(f"{col['label']}条形码")
            
            for col in qrcode_cols:
                headers.append(f"{col['label']}二维码")
            
            self.data_table.setColumnCount(len(headers))
            self.data_table.setHorizontalHeaderLabels(headers)
            
            # 加载数据
            data = self.user_db.get_all_data()
            self.data_table.setRowCount(len(data))
            
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
                    item = QTableWidgetItem(str(row_values[col_idx]) if col_idx < len(row_values) else QTableWidgetItem(""))
                    item.setData(Qt.UserRole, rowid)  # 保存rowid
                    
                    # 设置背景色和文本颜色
                    item.setBackground(QColor(color))
                    item.setForeground(QColor('#333333'))  # 深色文字提高可读性
                    
                    # 设置对齐方式
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                    
                    self.data_table.setItem(row_idx, col_idx, item)
                
                # 添加条形码
                col_offset = len(self.columns_config)
                for col in barcode_cols:
                    barcode_value = row_values[self.columns_config.index(col)]
                    barcode_img = self.generate_barcode(barcode_value)
                    if barcode_img:
                        label = QLabel()
                        label.setPixmap(QPixmap.fromImage(barcode_img))
                        label.setAlignment(Qt.AlignCenter)
                        label.setStyleSheet(f"background-color: {color};")
                        self.data_table.setCellWidget(row_idx, col_offset, label)
                    col_offset += 1
                
                # 添加二维码
                for col in qrcode_cols:
                    qrcode_value = row_values[self.columns_config.index(col)]
                    qrcode_img = self.generate_qrcode(qrcode_value)
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
            
            # 更新统计信息
            self.update_stats()
            self.update_status_bar()
            
        except Exception as e:
            print(f"[DEBUG] 加载数据失败: {str(e)}")
            QMessageBox.critical(self, '错误', f'加载数据失败: {str(e)}')

    
    def update_stats(self):
        # 清除之前的统计内容
        for i in reversed(range(self.stats_layout.count())): 
            item = self.stats_layout.itemAt(i)
            if item and item.widget():
                item.widget().setParent(None)
        
        # 获取数据总数
        total_count = self.user_db.get_data_count()
        
        # 创建统计信息
        stats_group = QGroupBox('数据统计')
        stats_form = QFormLayout()
        
        stats_form.addRow(QLabel('总记录数:'), QLabel(str(total_count)))
        
        # 添加列统计
        for col in self.columns_config:
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
            
            for row_idx, row_data in enumerate(data):
                # rowid是第一个元素
                rowid = row_data[0]
                row_values = row_data[1:]
                
                # 设置交替行背景色
                color = colors[row_idx % len(colors)]
                for col_idx, value in enumerate(row_values):
                    if col_idx < len(self.columns_config):  # 只处理数据列，不处理条码列
                        item = QTableWidgetItem(str(value))
                        item.setData(Qt.UserRole, rowid)  # 保存rowid
                        
                        # 设置背景色和文本颜色
                        item.setBackground(QColor(color))
                        item.setForeground(QColor('#333333'))  # 深色文字提高可读性
                        
                        # 设置对齐方式
                        item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                        
                        self.data_table.setItem(row_idx, col_idx, item)
                
                # 添加条形码
                col_offset = len(self.columns_config)
                barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
                for col in barcode_cols:
                    barcode_value = row_values[self.columns_config.index(col)]
                    barcode_img = self.generate_barcode(barcode_value)
                    if barcode_img:
                        label = QLabel()
                        label.setPixmap(QPixmap.fromImage(barcode_img))
                        label.setAlignment(Qt.AlignCenter)
                        label.setStyleSheet(f"background-color: {color};")
                        self.data_table.setCellWidget(row_idx, col_offset, label)
                    col_offset += 1
                
                # 添加二维码
                qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
                for col in qrcode_cols:
                    qrcode_value = row_values[self.columns_config.index(col)]
                    qrcode_img = self.generate_qrcode(qrcode_value)
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
            QMessageBox.critical(self, '错误', f'搜索失败: {str(e)}')

    def search_all_columns(self):
        """搜索所有列的内容 - 增强版支持拼音"""
        keyword = self.search_input.text().strip()
        if not keyword:
            self.load_data()
            return
        
        try:
            # 使用增强的全列搜索方法
            data = self.user_db.search_data_all_columns_enhanced(keyword)
            self.display_search_results(data, keyword)
            
        except Exception as e:
            QMessageBox.critical(self, '错误', f'搜索失败: {str(e)}')

    def display_search_results(self, data, keyword):
        """显示搜索结果"""
        self.data_table.setRowCount(len(data))
        
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
            for col_idx, value in enumerate(row_values):
                if col_idx < len(self.columns_config):  # 只处理数据列
                    display_text = str(value) if value else ""
                    
                    # 高亮匹配的关键词
                    if keyword.lower() in str(value).lower():
                        display_text = self.highlight_keyword(str(value), keyword)
                    
                    item = QTableWidgetItem(display_text)
                    item.setData(Qt.UserRole, rowid)
                    
                    # 设置背景色和文本颜色
                    item.setBackground(QColor(color))
                    item.setForeground(QColor('#333333'))
                    
                    # 设置对齐方式
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                    
                    self.data_table.setItem(row_idx, col_idx, item)
            
            # 添加条形码和二维码（与原来相同）
            self.add_barcode_qrcode_to_table(row_idx, row_values, color)
        
        self.data_table.resizeColumnsToContents()
        self.data_table.resizeRowsToContents()
        
        # 更新状态栏
        self.status_bar.showMessage(f'找到 {len(data)} 条包含 "{keyword}" 的记录')

    def highlight_keyword(self, text, keyword):
        """在文本中高亮显示关键词"""
        # 这里可以返回带HTML格式的文本，但QTableWidgetItem不支持HTML
        # 所以返回原始文本，高亮效果可以在单元格渲染器中实现
        return text

    def add_barcode_qrcode_to_table(self, row_idx, row_values, color):
        """为表格行添加条形码和二维码"""
        col_offset = len(self.columns_config)
        
        # 添加条形码
        barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
        for col in barcode_cols:
            barcode_value = row_values[self.columns_config.index(col)]
            barcode_img = self.generate_barcode(barcode_value)
            if barcode_img:
                label = QLabel()
                label.setPixmap(QPixmap.fromImage(barcode_img))
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet(f"background-color: {color};")
                self.data_table.setCellWidget(row_idx, col_offset, label)
            col_offset += 1
        
        # 添加二维码
        qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
        for col in qrcode_cols:
            qrcode_value = row_values[self.columns_config.index(col)]
            qrcode_img = self.generate_qrcode(qrcode_value)
            if qrcode_img:
                label = QLabel()
                label.setPixmap(QPixmap.fromImage(qrcode_img))
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet(f"background-color: {color};")
                self.data_table.setCellWidget(row_idx, col_offset, label)
            col_offset += 1



    def display_search_results(self, data, keyword):
        """显示搜索结果"""
        self.data_table.setRowCount(len(data))
        
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
            for col_idx, value in enumerate(row_values):
                if col_idx < len(self.columns_config):  # 只处理数据列
                    display_text = str(value) if value else ""
                    
                    # 高亮匹配的关键词
                    if keyword.lower() in str(value).lower():
                        display_text = self.highlight_keyword(str(value), keyword)
                    
                    item = QTableWidgetItem(display_text)
                    item.setData(Qt.UserRole, rowid)
                    
                    # 设置背景色和文本颜色
                    item.setBackground(QColor(color))
                    item.setForeground(QColor('#333333'))
                    
                    # 设置对齐方式
                    item.setTextAlignment(Qt.AlignVCenter | Qt.AlignLeft)
                    
                    self.data_table.setItem(row_idx, col_idx, item)
            
            # 添加条形码和二维码（与原来相同）
            self.add_barcode_qrcode_to_table(row_idx, row_values, color)
        
        self.data_table.resizeColumnsToContents()
        self.data_table.resizeRowsToContents()
        
        # 更新状态栏
        self.status_bar.showMessage(f'找到 {len(data)} 条包含 "{keyword}" 的记录')

    def highlight_keyword(self, text, keyword):
        """在文本中高亮显示关键词"""
        # 这里可以返回带HTML格式的文本，但QTableWidgetItem不支持HTML
        # 所以返回原始文本，高亮效果可以在单元格渲染器中实现
        return text

    def add_barcode_qrcode_to_table(self, row_idx, row_values, color):
        """为表格行添加条形码和二维码"""
        col_offset = len(self.columns_config)
        
        # 添加条形码
        barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
        for col in barcode_cols:
            barcode_value = row_values[self.columns_config.index(col)]
            barcode_img = self.generate_barcode(barcode_value)
            if barcode_img:
                label = QLabel()
                label.setPixmap(QPixmap.fromImage(barcode_img))
                label.setAlignment(Qt.AlignCenter)
                label.setStyleSheet(f"background-color: {color};")
                self.data_table.setCellWidget(row_idx, col_offset, label)
            col_offset += 1
        
        # 添加二维码
        qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
        for col in qrcode_cols:
            qrcode_value = row_values[self.columns_config.index(col)]
            qrcode_img = self.generate_qrcode(qrcode_value)
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
            QMessageBox.warning(self, '警告', '请选择要编辑的行')
            return
        
        row = selected_rows[0].row()
        rowid_item = self.data_table.item(row, 0)
        
        if not rowid_item:
            QMessageBox.warning(self, '警告', '无法获取行ID')
            return
        
        self.current_rowid = rowid_item.data(Qt.UserRole)
        if not self.current_rowid:
            QMessageBox.warning(self, '警告', '获取的行ID无效')
            return
        
        data = self.user_db.get_data_by_id(self.current_rowid)
        if not data:
            QMessageBox.warning(self, '警告', '无法获取数据')
            return
        
        # 准备当前数据
        current_data = {}
        values = data[1:]  # 跳过rowid
        for col_idx, col in enumerate(self.columns_config):
            if col_idx < len(values):
                current_data[col['name']] = str(values[col_idx]) if values[col_idx] is not None else ""
        
        # 创建并显示编辑对话框
        dialog = EditDataDialog(self.columns_config, current_data, self)
        if dialog.exec_() == QDialog.Accepted:
            new_data = dialog.get_data()
            
            # 检查唯一性约束
            for col in self.columns_config:
                if col.get('is_unique', False) and new_data[col['name']]:
                    if not self.user_db.check_unique(col['name'], new_data[col['name']], self.current_rowid):
                        QMessageBox.warning(self, '警告', f"{col['label']} 的值必须唯一")
                        return
            
            try:
                # 更新数据
                if self.user_db.update_data(self.current_rowid, new_data):
                    QMessageBox.information(self, '成功', '数据更新成功')
                    self.load_data()
                else:
                    QMessageBox.warning(self, '警告', '更新数据失败')
            except Exception as e:
                QMessageBox.warning(self, '错误', f'更新数据失败: {str(e)}')





    
    def add_data(self):
        """添加新数据"""
        dialog = EditDataDialog(self.columns_config, parent=self)
        if dialog.exec_() == QDialog.Accepted:
            data = dialog.get_data()
            
            # 检查唯一性约束
            for col in self.columns_config:
                if col.get('is_unique', False) and data[col['name']]:
                    if not self.user_db.check_unique(col['name'], data[col['name']]):
                        QMessageBox.warning(self, '警告', f"{col['label']} 的值必须唯一")
                        return
            
            try:
                # 插入数据
                if self.user_db.insert_data(data):
                    QMessageBox.information(self, '成功', '数据添加成功')
                    self.load_data()
                else:
                    QMessageBox.warning(self, '警告', '添加数据失败')
            except Exception as e:
                QMessageBox.warning(self, '错误', f'添加数据失败: {str(e)}')

    
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
                QMessageBox.warning(self, '警告', '请先在表格中选择要更新的行')
            else:
                print("[DEBUG] current_rowid is None or empty")
                QMessageBox.warning(self, '警告', '无法获取选中行的ID，请尝试重新选择')
            return
        
        print(f"[DEBUG] Updating data with rowid: {self.current_rowid}")

        # 验证输入数据
        data, errors = self.validate_input_data()
        if errors:
            QMessageBox.warning(self, '输入错误', '\n'.join(errors))
            return
        
        # 检查唯一性约束
        for col in self.columns_config:
            if col.get('is_unique', False) and data[col['name']]:
                if not self.user_db.check_unique(col['name'], data[col['name']], self.current_rowid):
                    QMessageBox.warning(self, '警告', f"{col['label']} 的值必须唯一")
                    self.input_widgets[col['name']].setStyleSheet("background-color: #FFCDD2;")
                    return
        
        try:
            # 更新数据
            if self.user_db.update_data(self.current_rowid, data):
                QMessageBox.information(self, '成功', '数据更新成功')
                # 重置状态并刷新数据
                self.current_rowid = None
                self.clear_inputs()
                self.load_data()
                self.update_btn.setEnabled(False)
            else:
                QMessageBox.warning(self, '警告', '更新数据失败')
        except Exception as e:
            QMessageBox.warning(self, '错误', f'更新数据失败: {str(e)}')

    
    def delete_data(self):
        selected_rows = self.data_table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, '警告', '请选择要删除的行')
            return
        
        row = selected_rows[0].row()
        rowid = self.data_table.item(row, 0).data(Qt.UserRole)
        
        reply = QMessageBox.question(self, '确认', '确定要删除这条数据吗?', 
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            try:
                if self.user_db.delete_data(rowid):
                    QMessageBox.information(self, '成功', '数据删除成功')
                    self.load_data()
                else:
                    QMessageBox.warning(self, '警告', '删除数据失败')
            except Exception as e:
                QMessageBox.warning(self, '错误', f'删除数据失败: {str(e)}')
    
    def clear_inputs(self):
        for widget in self.input_widgets.values():
            widget.clear()
        self.current_rowid = None
        self.update_btn.setEnabled(False)

    
    def show_item_detail(self, index):
        """显示选中项的详细信息，确保只显示配置中标记为条形码/二维码的列"""
        # 清除之前的详情内容
        for i in reversed(range(self.detail_layout.count())): 
            item = self.detail_layout.itemAt(i)
            if item.widget():
                item.widget().setParent(None)
        
        # 获取选中数据
        row = index.row()
        rowid = self.data_table.item(row, 0).data(Qt.UserRole)
        data = self.user_db.get_data_by_id(rowid)
        print(f"[DEBUG] Selected row ID: {rowid}")

        if not data:
            print("[DEBUG] No data found for the selected row")
            QMessageBox.warning(self, '警告', '无法获取数据详情')
            return

        # 创建带滚动区域的容器
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        layout = QVBoxLayout(content)
        
        # 1. 基本信息区块
        info_group = QGroupBox("基本信息")
        form = QFormLayout()
        
        # 修改点1：确保正确获取所有列值
        values = data[1:]  # 跳过rowid，保留所有列值
        
        # 修改点2：确保列配置和列值正确对应
        for col_idx, col in enumerate(self.columns_config):
            if col_idx < len(values):
                # 特殊字段标记
                label_text = col['label']
                if col.get('is_unique', False):
                    label_text += " 🔑"
                if col.get('is_required', False):
                    label_text += " *"
                
                label = QLabel(label_text)
                value = QLabel(str(values[col_idx]) if values[col_idx] else "(空)")
                value.setTextInteractionFlags(Qt.TextSelectableByMouse)
                
                if col.get('is_unique', False):
                    label.setStyleSheet("color: #4CAF50; font-weight: bold;")
                if col.get('is_required', False):
                    label.setStyleSheet("color: #F44336; font-weight: bold;")
                
                form.addRow(label, value)
        
        info_group.setLayout(form)
        layout.addWidget(info_group)

        # 2. 条形码区块 - 只处理配置中标记为is_barcode=True的列
        barcode_cols = [col for col in self.columns_config if col.get('is_barcode', False)]
        if barcode_cols:
            barcode_group = QGroupBox("条形码信息")
            barcode_layout = QVBoxLayout()

            for col in barcode_cols:
                col_idx = self.columns_config.index(col)
                if col_idx < len(values) and values[col_idx]:
                    value = values[col_idx]
                    
                    # 生成条形码图像
                    barcode_img = self.generate_barcode(value)
                    if barcode_img:
                        # 原始值标签
                        value_label = QLabel(f"{col['label']}：{value}")
                        value_label.setStyleSheet("font-weight: bold;")
                        barcode_layout.addWidget(value_label)
                        
                        # 条形码图像
                        img_label = QLabel()
                        pixmap = QPixmap.fromImage(barcode_img)
                        img_label.setPixmap(pixmap)
                        img_label.setAlignment(Qt.AlignCenter)
                        barcode_layout.addWidget(img_label)
                        
                        # 添加分隔线
                        barcode_layout.addWidget(QLabel("─"*50))

            if barcode_layout.count() > 0:
                barcode_group.setLayout(barcode_layout)
                layout.addWidget(barcode_group)

        # 3. 二维码区块 - 只处理配置中标记为is_qrcode=True的列
        qrcode_cols = [col for col in self.columns_config if col.get('is_qrcode', False)]
        if qrcode_cols:
            qrcode_group = QGroupBox("二维码信息")
            qrcode_layout = QVBoxLayout()
            
            for col in qrcode_cols:
                col_idx = self.columns_config.index(col)
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
                    img = self.generate_barcode(values[col_idx])
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
            
            QMessageBox.information(self, "成功", f"图片已保存到：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败：{str(e)}")

    
    def export_data(self):
        filename, _ = QFileDialog.getSaveFileName(self, '导出数据', '', 'CSV文件 (*.csv);;所有文件 (*)')
        if not filename:
            return
        
        try:
            if self.user_db.export_to_csv(filename):
                QMessageBox.information(self, '成功', f'数据已导出到 {filename}')
            else:
                QMessageBox.warning(self, '警告', '导出数据失败')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'导出数据失败: {str(e)}')
    
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
        
        # 显示当前列配置
        text_edit = QTextEdit()
        text_edit.setReadOnly(True)
        text_edit.setPlainText(json.dumps(columns_config, indent=2, ensure_ascii=False))
        layout.addWidget(text_edit)
        
        # 关闭按钮
        close_btn = QPushButton('关闭')
        close_btn.clicked.connect(dialog.accept)
        layout.addWidget(close_btn)
        
        dialog.setLayout(layout)
        dialog.exec_()
    
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
        msg_box.exec_()

    
    def logout(self):
        reply = QMessageBox.question(self, '确认', '确定要退出当前用户吗?', 
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.user_db.close()
            if self.parent_window:  # 检查父窗口是否存在
                self.parent_window.show()
            self.close()
    
    def generate_barcode(self, data):
        """
        生成条形码图像，支持Code128和EAN13标准
        对于非ASCII字符会自动转换为哈希值
        """
        if not data:
            return None
        
        try:
            data_str = str(data)
            
            # 检查是否包含非ASCII字符
            requires_encoding = False
            for char in data_str:
                if ord(char) > 127:  # 非ASCII字符
                    requires_encoding = True
                    break
            
            # 如果包含非条形码支持字符
            if requires_encoding:
                # 使用更友好的编码方式 - 首字母拼音+哈希
                from pypinyin import lazy_pinyin
                import hashlib
                
                # 获取拼音首字母
                pinyin_initials = ''.join([x[0] for x in lazy_pinyin(data_str) if x])
                
                # 生成短哈希
                hash_str = hashlib.md5(data_str.encode('utf-8')).hexdigest()[:6]
                
                # 组合成最终编码
                data_str = f"{pinyin_initials}_{hash_str}".upper()
                
                # 确保不超过长度限制
                data_str = data_str[:80]
            
            # 自动选择条码类型
            if data_str.isdigit() and len(data_str) == 13:  # EAN13标准
                barcode = EAN13(data_str, writer=ImageWriter())
            else:  # 默认使用Code128
                barcode = Code128(data_str, writer=ImageWriter())
            
            buffer = io.BytesIO()
            barcode.write(buffer)
            
            img = Image.open(buffer)
            img = img.convert("RGBA")
            
            # 调整大小
            width, height = img.size
            new_height = 100
            new_width = int(width * (new_height / height))
            
            data = img.tobytes("raw", "RGBA")
            qimage = QImage(data, img.size[0], img.size[1], QImage.Format_RGBA8888)
            
            return qimage.scaled(new_width, new_height, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        
        except Exception as e:
            print(f"生成条形码失败: {e}")
            return None



    
    def generate_qrcode(self, data):
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
        try:
            # 创建打印机对象
            printer = QPrinter(QPrinter.HighResolution)
            printer.setPageSize(QPrinter.A4)
            printer.setOrientation(QPrinter.Portrait)
            
            # 显示打印对话框
            print_dialog = QPrintDialog(printer, self)
            if print_dialog.exec_() != QDialog.Accepted:
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
                        barcode_img = self.generate_barcode(barcode_value)
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
            QMessageBox.critical(self, '打印错误', f'打印过程中发生错误: {str(e)}')


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
        QMessageBox.information(self, '提示', '数据已复制到剪贴板')

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
        
        # 刷新
        refresh_action = QAction('🔄 刷新', self)
        refresh_action.triggered.connect(self.load_data)
        menu.addAction(refresh_action)
        
        # 显示菜单
        menu.exec_(self.data_table.viewport().mapToGlobal(position))



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
            QMessageBox.warning(self, '警告', '请先选择要导出的行')
            return
        
        # 获取选中行的rowid
        rowids = []
        for row in selected_rows:
            rowid_item = self.data_table.item(row.row(), 0)
            if rowid_item:
                rowids.append(rowid_item.data(Qt.UserRole))
        
        if not rowids:
            QMessageBox.warning(self, '警告', '无法获取选中行的ID')
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
                QMessageBox.warning(self, '警告', '没有数据可导出')
                return
            
            # 获取列名
            columns = [col['name'] for col in self.columns_config]
            
            # 写入文件
            with open(filename, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow(columns)
                writer.writerows(data)
            
            QMessageBox.information(self, '成功', f'已导出 {len(data)} 行数据到 {filename}')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'导出失败: {str(e)}')


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
            QMessageBox.warning(self, '警告', '请先选择要导出的数据')
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, '导出为CSV', '', 'CSV文件 (*.csv);;所有文件 (*)')
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
            
            QMessageBox.information(self, '成功', f'已导出 {len(selected_rows)} 行数据到 {filename}')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'导出失败: {str(e)}')

    def export_selected_to_json(self):
        """导出选中数据为JSON"""
        selected_rows = {item.row() for item in self.data_table.selectedItems()}
        if not selected_rows:
            QMessageBox.warning(self, '警告', '请先选择要导出的数据')
            return
        
        filename, _ = QFileDialog.getSaveFileName(
            self, '导出为JSON', '', 'JSON文件 (*.json);;所有文件 (*)')
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
            
            QMessageBox.information(self, '成功', f'已导出 {len(selected_rows)} 行数据到 {filename}')
        except Exception as e:
            QMessageBox.critical(self, '错误', f'导出失败: {str(e)}')

    def export_selected_images(self):
        """导出选中行的条形码/二维码图片"""
        selected_rows = {item.row() for item in self.data_table.selectedItems()}
        if not selected_rows:
            QMessageBox.warning(self, '警告', '请先选择要导出的行')
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
                        img = self.generate_barcode(data[col_idx+1])
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
            
            QMessageBox.information(self, "成功", f"已导出 {exported_count} 张图片到：\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"导出失败：{str(e)}")

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
            QMessageBox.warning(self, '警告', '请先选择要复制的内容')
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
            QMessageBox.critical(self, "错误", f"复制为图片失败: {str(e)}")






    def closeEvent(self, event):
        # 退出前进行一次备份
        self.user_db.backup_database(backup_type="auto")
        self.user_db.close()
        if self.parent_window and not self.parent_window.isVisible():
            self.parent_window.show()
        event.accept()

    def manual_backup(self):
        """手动备份数据库"""
        backup_path = self.user_db.backup_database(backup_type="manual")
        if backup_path:
            QMessageBox.information(self, '成功', f'数据库已备份到:\n{backup_path}')
        else:
            QMessageBox.warning(self, '警告', '备份失败')
    
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
        
        dialog.exec_()
    
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
                QMessageBox.information(self, '成功', '数据库恢复成功!\n请重新启动应用使更改生效。')
                self.close()
            else:
                QMessageBox.warning(self, '警告', '数据库恢复失败')

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
            QMessageBox.warning(self, '输入错误', '\n'.join(errors))
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
    
    # 显示登录窗口
    login_window = LoginWindow(user_manager)
    login_window.show()
    
    sys.exit(app.exec_())