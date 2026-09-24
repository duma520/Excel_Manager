import os
import sys
import sqlite3
import csv
import json
from datetime import datetime
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                             QComboBox, QMessageBox, QTabWidget, QDialog, QFormLayout,
                             QSpinBox, QCheckBox, QGroupBox, QSizePolicy, QScrollArea,
                             QFileDialog, QInputDialog, QProgressDialog, QAction, QMenu,
                             QToolBar, QStatusBar, QHeaderView, QGridLayout)
from PyQt5.QtCore import Qt, QSize, QTimer
from PyQt5.QtGui import QIcon, QFont, QColor, QBrush
import qrcode
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
from PIL import Image
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

class EnhancedUserSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("创建新用户")
        self.setMinimumWidth(500)
        
        self.layout = QVBoxLayout()
        
        # 用户名输入
        self.username_layout = QHBoxLayout()
        self.username_label = QLabel("用户名:")
        self.username_input = QLineEdit()
        self.username_layout.addWidget(self.username_label)
        self.username_layout.addWidget(self.username_input)
        
        # 列数设置
        self.columns_layout = QHBoxLayout()
        self.columns_label = QLabel("列数:")
        self.columns_spin = QSpinBox()
        self.columns_spin.setMinimum(1)
        self.columns_spin.setMaximum(50)
        self.columns_spin.setValue(5)
        self.columns_layout.addWidget(self.columns_label)
        self.columns_layout.addWidget(self.columns_spin)
        
        # 列类型选项
        self.column_types = {
            "文本": "TEXT",
            "整数": "INTEGER",
            "实数": "REAL",
            "日期": "TEXT",
            "时间": "TEXT",
            "日期时间": "TEXT",
            "布尔值": "INTEGER"
        }
        
        # 列配置区域
        self.columns_config_group = QGroupBox("列配置")
        self.columns_config_layout = QVBoxLayout()
        self.columns_config_group.setLayout(self.columns_config_layout)
        
        # 按钮区域
        self.button_layout = QHBoxLayout()
        self.ok_button = QPushButton("确定")
        self.cancel_button = QPushButton("取消")
        self.button_layout.addWidget(self.ok_button)
        self.button_layout.addWidget(self.cancel_button)
        
        # 添加到主布局
        self.layout.addLayout(self.username_layout)
        self.layout.addLayout(self.columns_layout)
        self.layout.addWidget(self.columns_config_group)
        self.layout.addLayout(self.button_layout)
        
        self.setLayout(self.layout)
        
        # 信号连接
        self.columns_spin.valueChanged.connect(self.update_columns_config)
        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        
        # 初始化列配置
        self.column_widgets = []
        self.update_columns_config()
    
    def update_columns_config(self):
        # 清除现有配置
        for widget in self.column_widgets:
            self.columns_config_layout.removeWidget(widget)
            widget.deleteLater()
        self.column_widgets.clear()
        
        # 添加新的配置
        columns_count = self.columns_spin.value()
        for i in range(columns_count):
            group = QGroupBox(f"列 {i+1}")
            layout = QFormLayout()
            
            # 列名
            name_label = QLabel("列说明:")
            name_input = QLineEdit()
            layout.addRow(name_label, name_input)
            
            # 列类型
            type_label = QLabel("数据类型:")
            type_combo = QComboBox()
            type_combo.addItems(self.column_types.keys())
            layout.addRow(type_label, type_combo)
            
            # 特殊属性
            barcode_check = QCheckBox("一维码")
            qrcode_check = QCheckBox("二维码")
            unique_check = QCheckBox("唯一性")
            required_check = QCheckBox("必填")
            searchable_check = QCheckBox("可搜索")
            searchable_check.setChecked(True)
            
            checks_layout = QHBoxLayout()
            checks_layout.addWidget(barcode_check)
            checks_layout.addWidget(qrcode_check)
            checks_layout.addWidget(unique_check)
            checks_layout.addWidget(required_check)
            checks_layout.addWidget(searchable_check)
            layout.addRow(QLabel("特殊属性:"), checks_layout)
            
            group.setLayout(layout)
            self.columns_config_layout.addWidget(group)
            
            self.column_widgets.append({
                'group': group,
                'name_input': name_input,
                'type_combo': type_combo,
                'barcode_check': barcode_check,
                'qrcode_check': qrcode_check,
                'unique_check': unique_check,
                'required_check': required_check,
                'searchable_check': searchable_check
            })
    
    def get_user_config(self):
        config = {
            'username': self.username_input.text(),
            'columns': []
        }
        
        for widget in self.column_widgets:
            column_config = {
                'name': widget['name_input'].text(),
                'type': self.column_types[widget['type_combo'].currentText()],
                'is_barcode': widget['barcode_check'].isChecked(),
                'is_qrcode': widget['qrcode_check'].isChecked(),
                'is_unique': widget['unique_check'].isChecked(),
                'is_required': widget['required_check'].isChecked(),
                'is_searchable': widget['searchable_check'].isChecked()
            }
            config['columns'].append(column_config)
        
        return config

class EnhancedLoginWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("用户登录")
        self.resize(500, 350)
        
        self.init_ui()
        self.load_users()
        
        # 检查并设置图标
        if os.path.exists("icon.ico"):
            self.setWindowIcon(QIcon("icon.ico"))
    
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # 标题
        title_label = QLabel("增强版电子表格管理系统")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("Arial", 18, QFont.Bold))
        layout.addWidget(title_label)
        
        # 用户选择
        self.user_combo = QComboBox()
        self.user_combo.setPlaceholderText("选择用户")
        layout.addWidget(self.user_combo)
        
        # 按钮区域
        button_layout = QGridLayout()
        
        login_btn = QPushButton("登录")
        login_btn.clicked.connect(self.login)
        button_layout.addWidget(login_btn, 0, 0)
        
        add_user_btn = QPushButton("添加用户")
        add_user_btn.clicked.connect(self.show_add_user_dialog)
        button_layout.addWidget(add_user_btn, 0, 1)
        
        del_user_btn = QPushButton("删除用户")
        del_user_btn.clicked.connect(self.delete_user)
        button_layout.addWidget(del_user_btn, 1, 0)
        
        backup_btn = QPushButton("备份用户")
        backup_btn.clicked.connect(self.backup_user)
        button_layout.addWidget(backup_btn, 1, 1)
        
        restore_btn = QPushButton("恢复用户")
        restore_btn.clicked.connect(self.restore_user)
        button_layout.addWidget(restore_btn, 2, 0)
        
        layout.addLayout(button_layout)
        
        # 添加间距
        layout.addStretch()
        
        # 状态栏
        self.statusBar().showMessage("就绪")
    
    def load_users(self):
        # 创建主数据库连接
        self.main_db = sqlite3.connect("users.db")
        cursor = self.main_db.cursor()
        
        # 创建用户表如果不存在
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            db_file TEXT NOT NULL,
            config TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            last_login TEXT
        )
        """)
        self.main_db.commit()
        
        # 加载现有用户
        cursor.execute("SELECT username FROM users ORDER BY last_login DESC, username")
        users = cursor.fetchall()
        
        self.user_combo.clear()
        for user in users:
            self.user_combo.addItem(user[0])
    
    def show_add_user_dialog(self):
        dialog = EnhancedUserSetupDialog(self)
        if dialog.exec_() == QDialog.Accepted:
            user_config = dialog.get_user_config()
            if not user_config['username']:
                QMessageBox.warning(self, "错误", "用户名不能为空！")
                return
            
            # 检查用户名是否已存在
            cursor = self.main_db.cursor()
            cursor.execute("SELECT username FROM users WHERE username=?", (user_config['username'],))
            if cursor.fetchone():
                QMessageBox.warning(self, "错误", "用户名已存在！")
                return
            
            # 创建用户数据库
            db_file = f"user_{user_config['username']}.db"
            try:
                user_db = sqlite3.connect(db_file)
                cursor_user = user_db.cursor()
                
                # 创建数据表
                columns = []
                for i, col in enumerate(user_config['columns']):
                    columns.append(f"col{i+1} {col['type']}")
                
                create_table_sql = f"""
                CREATE TABLE data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    {', '.join(columns)}
                )
                """
                cursor_user.execute(create_table_sql)
                
                # 创建配置表
                cursor_user.execute("""
                CREATE TABLE config (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    key TEXT NOT NULL,
                    value TEXT
                )
                """)
                
                # 创建索引表
                cursor_user.execute("""
                CREATE TABLE search_index (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    col_id INTEGER NOT NULL,
                    value TEXT NOT NULL,
                    data_id INTEGER NOT NULL,
                    FOREIGN KEY(data_id) REFERENCES data(id)
                )
                """)
                
                # 保存列配置
                for i, col in enumerate(user_config['columns']):
                    cursor_user.execute("""
                    INSERT INTO config (key, value) VALUES (?, ?)
                    """, (f"col{i+1}_name", col['name']))
                    
                    cursor_user.execute("""
                    INSERT INTO config (key, value) VALUES (?, ?)
                    """, (f"col{i+1}_type", col['type']))
                    
                    if col['is_barcode']:
                        cursor_user.execute("""
                        INSERT INTO config (key, value) VALUES (?, ?)
                        """, ('barcode_col', i+1))
                    
                    if col['is_qrcode']:
                        cursor_user.execute("""
                        INSERT INTO config (key, value) VALUES (?, ?)
                        """, ('qrcode_col', i+1))
                    
                    if col['is_unique']:
                        cursor_user.execute("""
                        INSERT INTO config (key, value) VALUES (?, ?)
                        """, ('unique_col', i+1))
                        # 创建唯一索引
                        cursor_user.execute(f"""
                        CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_col{i+1} 
                        ON data(col{i+1})
                        """)
                    
                    if col['is_required']:
                        cursor_user.execute("""
                        INSERT INTO config (key, value) VALUES (?, ?)
                        """, (f"required_col", i+1))
                    
                    if col['is_searchable']:
                        cursor_user.execute("""
                        INSERT INTO config (key, value) VALUES (?, ?)
                        """, (f"searchable_col", i+1))
                
                user_db.commit()
                user_db.close()
                
                # 添加到主数据库
                import json
                cursor.execute("""
                INSERT INTO users (username, db_file, config) 
                VALUES (?, ?, ?)
                """, (user_config['username'], db_file, json.dumps(user_config, ensure_ascii=False)))
                self.main_db.commit()
                
                # 更新用户列表
                self.load_users()
                self.user_combo.setCurrentText(user_config['username'])
                
                QMessageBox.information(self, "成功", "用户创建成功！")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"创建用户失败: {str(e)}")
                if 'user_db' in locals():
                    user_db.close()
                if os.path.exists(db_file):
                    os.remove(db_file)
    
    def delete_user(self):
        username = self.user_combo.currentText()
        if not username:
            QMessageBox.warning(self, "错误", "请选择要删除的用户！")
            return
        
        reply = QMessageBox.question(
            self, '确认删除',
            f"确定要删除用户 '{username}' 吗？所有数据将永久丢失！",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QMessageBox.Yes:
            cursor = self.main_db.cursor()
            
            # 获取数据库文件路径
            cursor.execute("SELECT db_file FROM users WHERE username=?", (username,))
            db_file = cursor.fetchone()[0]
            
            # 从主数据库删除用户
            cursor.execute("DELETE FROM users WHERE username=?", (username,))
            self.main_db.commit()
            
            # 删除用户数据库文件
            if os.path.exists(db_file):
                try:
                    os.remove(db_file)
                except Exception as e:
                    QMessageBox.warning(self, "警告", f"删除数据库文件失败: {str(e)}")
            
            # 更新用户列表
            self.load_users()
            QMessageBox.information(self, "成功", "用户已删除！")
    
    def backup_user(self):
        username = self.user_combo.currentText()
        if not username:
            QMessageBox.warning(self, "错误", "请选择要备份的用户！")
            return
        
        cursor = self.main_db.cursor()
        cursor.execute("SELECT db_file FROM users WHERE username=?", (username,))
        db_file = cursor.fetchone()[0]
        
        if not os.path.exists(db_file):
            QMessageBox.warning(self, "错误", "找不到用户数据库文件！")
            return
        
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(
            self, "备份用户数据", f"{username}_backup_{datetime.now().strftime('%Y%m%d')}.db", 
            "Database Files (*.db)", options=options)
        
        if file_name:
            try:
                import shutil
                shutil.copy2(db_file, file_name)
                QMessageBox.information(self, "成功", f"用户 '{username}' 备份成功！")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"备份失败: {str(e)}")
    
    def restore_user(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getOpenFileName(
            self, "恢复用户数据", "", 
            "Database Files (*.db)", options=options)
        
        if file_name:
            try:
                # 从备份文件中读取配置
                temp_db = sqlite3.connect(file_name)
                cursor = temp_db.cursor()
                
                cursor.execute("SELECT value FROM config WHERE key='username'")
                username = cursor.fetchone()[0]
                temp_db.close()
                
                if not username:
                    QMessageBox.warning(self, "错误", "无效的备份文件！")
                    return
                
                # 检查用户名是否已存在
                cursor = self.main_db.cursor()
                cursor.execute("SELECT username FROM users WHERE username=?", (username,))
                if cursor.fetchone():
                    reply = QMessageBox.question(
                        self, '确认覆盖',
                        f"用户 '{username}' 已存在，是否覆盖？",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.No
                    )
                    if reply != QMessageBox.Yes:
                        return
                
                # 恢复数据库
                db_file = f"user_{username}.db"
                import shutil
                shutil.copy2(file_name, db_file)
                
                # 更新主数据库
                cursor.execute("""
                INSERT OR REPLACE INTO users (username, db_file) 
                VALUES (?, ?)
                """, (username, db_file))
                self.main_db.commit()
                
                # 更新用户列表
                self.load_users()
                self.user_combo.setCurrentText(username)
                
                QMessageBox.information(self, "成功", f"用户 '{username}' 恢复成功！")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"恢复失败: {str(e)}")
    
    def login(self):
        username = self.user_combo.currentText()
        if not username:
            QMessageBox.warning(self, "错误", "请选择用户！")
            return
        
        # 更新最后登录时间
        cursor = self.main_db.cursor()
        cursor.execute("""
        UPDATE users SET last_login = datetime('now') 
        WHERE username = ?
        """, (username,))
        self.main_db.commit()
        
        # 获取用户数据库文件
        cursor.execute("SELECT db_file FROM users WHERE username=?", (username,))
        result = cursor.fetchone()
        
        if not result:
            QMessageBox.warning(self, "错误", "用户不存在！")
            return
        
        db_file = result[0]
        
        # 打开用户数据库
        try:
            user_db = sqlite3.connect(db_file)
            
            # 打开主窗口
            self.main_window = EnhancedMainWindow(username, user_db)
            self.main_window.show()
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开用户数据库: {str(e)}")

class EnhancedMainWindow(QMainWindow):
    def __init__(self, username, db_connection):
        super().__init__()
        self.username = username
        self.db = db_connection
        self.current_data = []
        self.filtered_data = []
        self.current_sort_column = None
        self.current_sort_order = Qt.AscendingOrder
        
        self.setWindowTitle(f"增强版电子表格管理系统 - {username}")
        self.resize(1200, 800)
        
        if os.path.exists("icon.ico"):
            self.setWindowIcon(QIcon("icon.ico"))
        
        self.init_ui()
        self.load_config()
        self.load_data()
        self.create_actions()
        self.create_menus()
        self.create_toolbar()
    
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 搜索区域
        search_group = QGroupBox("高级搜索")
        search_layout = QVBoxLayout()
        
        # 基本搜索
        basic_search_layout = QHBoxLayout()
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入搜索内容...")
        self.search_input.returnPressed.connect(self.search_data)
        
        search_btn = QPushButton("搜索")
        search_btn.clicked.connect(self.search_data)
        
        clear_btn = QPushButton("清除")
        clear_btn.clicked.connect(self.clear_search)
        
        basic_search_layout.addWidget(self.search_input)
        basic_search_layout.addWidget(search_btn)
        basic_search_layout.addWidget(clear_btn)
        
        # 高级搜索
        self.advanced_search_btn = QPushButton("高级搜索选项")
        self.advanced_search_btn.clicked.connect(self.show_advanced_search)
        self.advanced_search_btn.setCheckable(True)
        
        self.advanced_search_widget = QWidget()
        self.advanced_search_layout = QFormLayout()
        
        self.column_search_combo = QComboBox()
        self.column_search_operator = QComboBox()
        self.column_search_operator.addItems(["包含", "等于", "开头为", "结尾为", "大于", "小于"])
        self.column_search_value = QLineEdit()
        
        self.advanced_search_layout.addRow("列:", self.column_search_combo)
        self.advanced_search_layout.addRow("条件:", self.column_search_operator)
        self.advanced_search_layout.addRow("值:", self.column_search_value)
        
        self.advanced_search_widget.setLayout(self.advanced_search_layout)
        self.advanced_search_widget.setVisible(False)
        
        search_layout.addLayout(basic_search_layout)
        search_layout.addWidget(self.advanced_search_btn)
        search_layout.addWidget(self.advanced_search_widget)
        search_group.setLayout(search_layout)
        
        main_layout.addWidget(search_group)
        
        # 表格区域
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionsMovable(True)
        self.table.horizontalHeader().sectionClicked.connect(self.sort_table)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        
        main_layout.addWidget(self.table)
        
        # 状态栏
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        # 数据统计区域
        self.stats_label = QLabel()
        self.status_bar.addPermanentWidget(self.stats_label)
    
    def create_actions(self):
        # 文件操作
        self.export_csv_action = QAction("导出为CSV", self)
        self.export_csv_action.triggered.connect(self.export_to_csv)
        
        self.export_json_action = QAction("导出为JSON", self)
        self.export_json_action.triggered.connect(self.export_to_json)
        
        self.import_csv_action = QAction("从CSV导入", self)
        self.import_csv_action.triggered.connect(self.import_from_csv)
        
        self.import_json_action = QAction("从JSON导入", self)
        self.import_json_action.triggered.connect(self.import_from_json)
        
        self.backup_action = QAction("备份数据", self)
        self.backup_action.triggered.connect(self.backup_data)
        
        self.exit_action = QAction("退出", self)
        self.exit_action.triggered.connect(self.close)
        
        # 编辑操作
        self.add_action = QAction("添加", self)
        self.add_action.triggered.connect(self.show_add_dialog)
        
        self.edit_action = QAction("编辑", self)
        self.edit_action.triggered.connect(self.show_edit_dialog)
        
        self.delete_action = QAction("删除", self)
        self.delete_action.triggered.connect(self.delete_data)
        
        self.batch_delete_action = QAction("批量删除", self)
        self.batch_delete_action.triggered.connect(self.batch_delete_data)
        
        # 工具操作
        self.generate_barcode_action = QAction("生成一维码", self)
        self.generate_barcode_action.triggered.connect(self.generate_barcode)
        
        self.generate_qrcode_action = QAction("生成二维码", self)
        self.generate_qrcode_action.triggered.connect(self.generate_qrcode)
        
        self.batch_barcode_action = QAction("批量生成一维码", self)
        self.batch_barcode_action.triggered.connect(self.batch_generate_barcode)
        
        self.batch_qrcode_action = QAction("批量生成二维码", self)
        self.batch_qrcode_action.triggered.connect(self.batch_generate_qrcode)
        
        # 视图操作
        self.refresh_action = QAction("刷新", self)
        self.refresh_action.triggered.connect(self.load_data)
        
        self.toggle_search_action = QAction("显示/隐藏搜索", self)
        self.toggle_search_action.triggered.connect(self.toggle_search)
        
        # 帮助操作
        self.about_action = QAction("关于", self)
        self.about_action.triggered.connect(self.show_about)
    
    def create_menus(self):
        menubar = self.menuBar()
        
        # 文件菜单
        file_menu = menubar.addMenu("文件")
        file_menu.addAction(self.export_csv_action)
        file_menu.addAction(self.export_json_action)
        file_menu.addSeparator()
        file_menu.addAction(self.import_csv_action)
        file_menu.addAction(self.import_json_action)
        file_menu.addSeparator()
        file_menu.addAction(self.backup_action)
        file_menu.addSeparator()
        file_menu.addAction(self.exit_action)
        
        # 编辑菜单
        edit_menu = menubar.addMenu("编辑")
        edit_menu.addAction(self.add_action)
        edit_menu.addAction(self.edit_action)
        edit_menu.addAction(self.delete_action)
        edit_menu.addAction(self.batch_delete_action)
        
        # 工具菜单
        tools_menu = menubar.addMenu("工具")
        tools_menu.addAction(self.generate_barcode_action)
        tools_menu.addAction(self.generate_qrcode_action)
        tools_menu.addSeparator()
        tools_menu.addAction(self.batch_barcode_action)
        tools_menu.addAction(self.batch_qrcode_action)
        
        # 视图菜单
        view_menu = menubar.addMenu("视图")
        view_menu.addAction(self.refresh_action)
        view_menu.addAction(self.toggle_search_action)
        
        # 帮助菜单
        help_menu = menubar.addMenu("帮助")
        help_menu.addAction(self.about_action)
    
    def create_toolbar(self):
        toolbar = self.addToolBar("工具栏")
        toolbar.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        
        toolbar.addAction(self.add_action)
        toolbar.addAction(self.edit_action)
        toolbar.addAction(self.delete_action)
        toolbar.addSeparator()
        toolbar.addAction(self.generate_barcode_action)
        toolbar.addAction(self.generate_qrcode_action)
        toolbar.addSeparator()
        toolbar.addAction(self.refresh_action)
    
    def show_context_menu(self, position):
        menu = QMenu()
        
        selected_rows = self.table.selectionModel().selectedRows()
        if selected_rows:
            menu.addAction(self.edit_action)
            menu.addAction(self.delete_action)
            menu.addSeparator()
            menu.addAction(self.generate_barcode_action)
            menu.addAction(self.generate_qrcode_action)
        else:
            menu.addAction(self.add_action)
        
        menu.exec_(self.table.viewport().mapToGlobal(position))
    
    def load_config(self):
        self.column_names = []
        self.column_types = []
        self.barcode_col = None
        self.qrcode_col = None
        self.unique_col = None
        self.required_cols = []
        self.searchable_cols = []
        
        cursor = self.db.cursor()
        
        # 获取列名和类型
        cursor.execute("SELECT key, value FROM config WHERE key LIKE 'col%_name' OR key LIKE 'col%_type'")
        configs = cursor.fetchall()
        
        col_config = {}
        for key, value in configs:
            parts = key.split('_')
            col_num = int(parts[0][3:])
            config_type = parts[1]
            
            if col_num not in col_config:
                col_config[col_num] = {}
            
            col_config[col_num][config_type] = value
        
        # 按列号排序
        sorted_cols = sorted(col_config.items())
        
        for col_num, config in sorted_cols:
            self.column_names.append(config.get('name', f"列{col_num}"))
            self.column_types.append(config.get('type', 'TEXT'))
        
        # 获取特殊列
        cursor.execute("""
        SELECT key, value FROM config 
        WHERE key IN ('barcode_col', 'qrcode_col', 'unique_col', 'required_col', 'searchable_col')
        """)
        special_cols = cursor.fetchall()
        
        for key, value in special_cols:
            if key == 'barcode_col':
                self.barcode_col = int(value)
            elif key == 'qrcode_col':
                self.qrcode_col = int(value)
            elif key == 'unique_col':
                self.unique_col = int(value)
            elif key == 'required_col':
                self.required_cols.append(int(value))
            elif key == 'searchable_col':
                self.searchable_cols.append(int(value))
        
        # 更新高级搜索的列选择
        self.column_search_combo.clear()
        for i, name in enumerate(self.column_names):
            self.column_search_combo.addItem(name, i+1)
    
    def load_data(self, search_term=None, advanced_search=None):
        cursor = self.db.cursor()
        
        # 构建查询
        query = "SELECT * FROM data"
        params = []
        
        conditions = []
        if search_term:
            # 基本搜索 - 在所有可搜索列中查找
            search_conditions = []
            for col in self.searchable_cols:
                search_conditions.append(f"col{col} LIKE ?")
                params.append(f"%{search_term}%")
            
            if search_conditions:
                conditions.append(f"({' OR '.join(search_conditions)})")
        
        if advanced_search:
            col = advanced_search['column']
            operator = advanced_search['operator']
            value = advanced_search['value']
            
            if operator == "包含":
                conditions.append(f"col{col} LIKE ?")
                params.append(f"%{value}%")
            elif operator == "等于":
                conditions.append(f"col{col} = ?")
                params.append(value)
            elif operator == "开头为":
                conditions.append(f"col{col} LIKE ?")
                params.append(f"{value}%")
            elif operator == "结尾为":
                conditions.append(f"col{col} LIKE ?")
                params.append(f"%{value}")
            elif operator == "大于":
                conditions.append(f"col{col} > ?")
                params.append(value)
            elif operator == "小于":
                conditions.append(f"col{col} < ?")
                params.append(value)
        
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        
        # 添加排序
        if self.current_sort_column is not None:
            query += f" ORDER BY col{self.current_sort_column+1}"
            if self.current_sort_order == Qt.DescendingOrder:
                query += " DESC"
            else:
                query += " ASC"
        
        cursor.execute(query, params)
        self.current_data = cursor.fetchall()
        self.filtered_data = self.current_data.copy()
        
        # 更新表格
        self.table.setRowCount(len(self.current_data))
        self.table.setColumnCount(len(self.column_names))
        self.table.setHorizontalHeaderLabels(self.column_names)
        
        for row_idx, row in enumerate(self.current_data):
            for col_idx in range(len(self.column_names)):
                # 跳过ID、created_at、updated_at列
                item = QTableWidgetItem(str(row[col_idx+3] if col_idx+3 < len(row) else ""))
                
                # 根据数据类型设置对齐方式
                col_type = self.column_types[col_idx]
                if col_type in ('INTEGER', 'REAL'):
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                
                self.table.setItem(row_idx, col_idx, item)
        
        # 调整列宽
        self.table.resizeColumnsToContents()
        
        # 更新状态栏统计
        self.update_stats()
    
    def update_stats(self):
        total = len(self.current_data)
        filtered = len(self.filtered_data) if hasattr(self, 'filtered_data') else total
        self.stats_label.setText(f"总数: {total} | 显示: {filtered}")
    
    def search_data(self):
        search_term = self.search_input.text()
        
        if self.advanced_search_btn.isChecked() and self.column_search_combo.currentIndex() >= 0:
            advanced_search = {
                'column': self.column_search_combo.currentData(),
                'operator': self.column_search_operator.currentText(),
                'value': self.column_search_value.text()
            }
            self.load_data(search_term=None, advanced_search=advanced_search)
        else:
            self.load_data(search_term=search_term)
    
    def show_advanced_search(self):
        self.advanced_search_widget.setVisible(self.advanced_search_btn.isChecked())
    
    def clear_search(self):
        self.search_input.clear()
        self.column_search_value.clear()
        self.load_data()
    
    def sort_table(self, column):
        if self.current_sort_column == column:
            # 切换排序顺序
            self.current_sort_order = Qt.DescendingOrder if self.current_sort_order == Qt.AscendingOrder else Qt.AscendingOrder
        else:
            # 新列排序
            self.current_sort_column = column
            self.current_sort_order = Qt.AscendingOrder
        
        self.load_data()
        
        # 更新表头指示器
        self.table.horizontalHeader().setSortIndicator(self.current_sort_column, self.current_sort_order)
    
    def show_add_dialog(self):
        dialog = EnhancedDataEditDialog(self.column_names, self.column_types, self.required_cols, self)
        if dialog.exec_() == QDialog.Accepted:
            new_data = dialog.get_data()
            
            # 检查必填项
            for col in self.required_cols:
                if not new_data[col-1]:
                    QMessageBox.warning(self, "错误", f"{self.column_names[col-1]} 是必填项！")
                    return
            
            # 检查唯一性
            if self.unique_col is not None:
                unique_value = new_data[self.unique_col-1]
                if not unique_value:
                    QMessageBox.warning(self, "错误", f"{self.column_names[self.unique_col-1]} 不能为空！")
                    return
                
                cursor = self.db.cursor()
                cursor.execute(f"SELECT id FROM data WHERE col{self.unique_col}=?", (unique_value,))
                if cursor.fetchone():
                    QMessageBox.warning(self, "错误", f"{self.column_names[self.unique_col-1]} 必须唯一！")
                    return
            
            # 插入数据
            columns = []
            placeholders = []
            values = []
            
            for i in range(len(self.column_names)):
                columns.append(f"col{i+1}")
                placeholders.append("?")
                values.append(new_data[i] if i < len(new_data) else "")
            
            query = f"""
            INSERT INTO data ({', '.join(columns)}) 
            VALUES ({', '.join(placeholders)})
            """
            
            try:
                cursor = self.db.cursor()
                cursor.execute(query, values)
                
                # 更新搜索索引
                for i in range(len(self.column_names)):
                    if i+1 in self.searchable_cols:
                        cursor.execute("""
                        INSERT INTO search_index (col_id, value, data_id)
                        VALUES (?, ?, ?)
                        """, (i+1, new_data[i], cursor.lastrowid))
                
                self.db.commit()
                self.load_data()
            except Exception as e:
                self.db.rollback()
                QMessageBox.critical(self, "错误", f"添加数据失败: {str(e)}")
    
    def show_edit_dialog(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "错误", "请选择要编辑的行！")
            return
        
        row_idx = selected_rows[0].row()
        if row_idx >= len(self.current_data):
            return
        
        # 获取当前数据 (跳过ID、created_at、updated_at列)
        current_row = self.current_data[row_idx][3:]
        
        dialog = EnhancedDataEditDialog(self.column_names, self.column_types, self.required_cols, self, current_row)
        if dialog.exec_() == QDialog.Accepted:
            new_data = dialog.get_data()
            
            # 检查必填项
            for col in self.required_cols:
                if not new_data[col-1]:
                    QMessageBox.warning(self, "错误", f"{self.column_names[col-1]} 是必填项！")
                    return
            
            # 检查唯一性
            if self.unique_col is not None:
                unique_value = new_data[self.unique_col-1]
                if not unique_value:
                    QMessageBox.warning(self, "错误", f"{self.column_names[self.unique_col-1]} 不能为空！")
                    return
                
                current_id = self.current_data[row_idx][0]
                cursor = self.db.cursor()
                cursor.execute(
                    f"SELECT id FROM data WHERE col{self.unique_col}=? AND id!=?", 
                    (unique_value, current_id)
                )
                if cursor.fetchone():
                    QMessageBox.warning(self, "错误", f"{self.column_names[self.unique_col-1]} 必须唯一！")
                    return
            
            # 更新数据
            updates = []
            values = []
            
            for i in range(len(self.column_names)):
                updates.append(f"col{i+1}=?")
                values.append(new_data[i] if i < len(new_data) else "")
            
            values.append(self.current_data[row_idx][0])
            
            query = f"""
            UPDATE data SET {', '.join(updates)}, updated_at=datetime('now')
            WHERE id=?
            """
            
            try:
                cursor = self.db.cursor()
                cursor.execute(query, values)
                
                # 更新搜索索引
                for i in range(len(self.column_names)):
                    if i+1 in self.searchable_cols:
                        cursor.execute("""
                        UPDATE search_index SET value=?
                        WHERE col_id=? AND data_id=?
                        """, (new_data[i], i+1, self.current_data[row_idx][0]))
                
                self.db.commit()
                self.load_data()
            except Exception as e:
                self.db.rollback()
                QMessageBox.critical(self, "错误", f"更新数据失败: {str(e)}")
    
    def delete_data(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "错误", "请选择要删除的行！")
            return
        
        row_idx = selected_rows[0].row()
        if row_idx >= len(self.current_data):
            return
        
        reply = QMessageBox.question(
            self, '确认删除',
            "确定要删除选中的数据吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QDialog.Accepted:
            try:
                cursor = self.db.cursor()
                
                # 删除搜索索引
                cursor.execute("""
                DELETE FROM search_index 
                WHERE data_id=?
                """, (self.current_data[row_idx][0],))
                
                # 删除数据
                cursor.execute("""
                DELETE FROM data 
                WHERE id=?
                """, (self.current_data[row_idx][0],))
                
                self.db.commit()
                self.load_data()
            except Exception as e:
                self.db.rollback()
                QMessageBox.critical(self, "错误", f"删除数据失败: {str(e)}")
    
    def batch_delete_data(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "错误", "请选择要删除的行！")
            return
        
        reply = QMessageBox.question(
            self, '确认批量删除',
            f"确定要删除选中的 {len(selected_rows)} 行数据吗？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No
        )
        
        if reply == QDialog.Accepted:
            try:
                cursor = self.db.cursor()
                ids = [self.current_data[row.row()][0] for row in selected_rows]
                
                # 使用事务批量删除
                cursor.execute("BEGIN TRANSACTION")
                
                # 删除搜索索引
                cursor.executemany("""
                DELETE FROM search_index 
                WHERE data_id=?
                """, [(id,) for id in ids])
                
                # 删除数据
                cursor.executemany("""
                DELETE FROM data 
                WHERE id=?
                """, [(id,) for id in ids])
                
                cursor.execute("COMMIT")
                self.db.commit()
                self.load_data()
                
                QMessageBox.information(self, "成功", f"已删除 {len(selected_rows)} 行数据")
            except Exception as e:
                cursor.execute("ROLLBACK")
                QMessageBox.critical(self, "错误", f"批量删除失败: {str(e)}")
    
    def generate_barcode(self):
        if self.barcode_col is None:
            QMessageBox.warning(self, "错误", "没有配置一维码列！")
            return
        
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "错误", "请选择要生成一维码的行！")
            return
        
        row_idx = selected_rows[0].row()
        if row_idx >= len(self.current_data):
            return
        
        barcode_text = self.current_data[row_idx][self.barcode_col+3]  # 跳过ID、created_at、updated_at
        if not barcode_text:
            QMessageBox.warning(self, "错误", "选中的行没有一维码数据！")
            return
        
        try:
            # 生成一维码
            barcode_class = barcode.get_barcode_class('code128')
            barcode_img = barcode_class(barcode_text, writer=ImageWriter())
            
            # 保存到临时文件
            temp_file = "temp_barcode.png"
            barcode_img.save(temp_file)
            
            # 显示图像
            self.show_image(temp_file, f"一维码: {barcode_text}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成一维码失败: {str(e)}")
    
    def generate_qrcode(self):
        if self.qrcode_col is None:
            QMessageBox.warning(self, "错误", "没有配置二维码列！")
            return
        
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "错误", "请选择要生成二维码的行！")
            return
        
        row_idx = selected_rows[0].row()
        if row_idx >= len(self.current_data):
            return
        
        qrcode_text = self.current_data[row_idx][self.qrcode_col+3]  # 跳过ID、created_at、updated_at
        if not qrcode_text:
            QMessageBox.warning(self, "错误", "选中的行没有二维码数据！")
            return
        
        try:
            # 生成二维码
            qr = qrcode.QRCode(
                version=1,
                error_correction=qrcode.constants.ERROR_CORRECT_L,
                box_size=10,
                border=4,
            )
            qr.add_data(qrcode_text)
            qr.make(fit=True)
            
            img = qr.make_image(fill_color="black", back_color="white")
            
            # 保存到临时文件
            temp_file = "temp_qrcode.png"
            img.save(temp_file)
            
            # 显示图像
            self.show_image(temp_file, f"二维码: {qrcode_text}")
        except Exception as e:
            QMessageBox.critical(self, "错误", f"生成二维码失败: {str(e)}")
    
    def batch_generate_barcode(self):
        if self.barcode_col is None:
            QMessageBox.warning(self, "错误", "没有配置一维码列！")
            return
        
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "错误", "请选择要生成一维码的行！")
            return
        
        # 选择输出目录
        options = QFileDialog.Options()
        output_dir = QFileDialog.getExistingDirectory(
            self, "选择输出目录", "", options=options)
        
        if not output_dir:
            return
        
        # 创建进度对话框
        progress = QProgressDialog("正在生成一维码...", "取消", 0, len(selected_rows), self)
        progress.setWindowTitle("批量生成一维码")
        progress.setWindowModality(Qt.WindowModal)
        progress.show()
        
        success_count = 0
        for i, row in enumerate(selected_rows):
            row_idx = row.row()
            if row_idx >= len(self.current_data):
                continue
            
            barcode_text = self.current_data[row_idx][self.barcode_col+3]
            if not barcode_text:
                continue
            
            try:
                # 生成一维码
                barcode_class = barcode.get_barcode_class('code128')
                barcode_img = barcode_class(barcode_text, writer=ImageWriter())
                
                # 保存文件
                output_file = os.path.join(output_dir, f"barcode_{barcode_text}.png")
                barcode_img.save(output_file)
                success_count += 1
            except:
                pass
            
            progress.setValue(i+1)
            if progress.wasCanceled():
                break
        
        progress.close()
        QMessageBox.information(
            self, "完成", 
            f"成功生成 {success_count}/{len(selected_rows)} 个一维码文件到目录: {output_dir}")
    
    def batch_generate_qrcode(self):
        if self.qrcode_col is None:
            QMessageBox.warning(self, "错误", "没有配置二维码列！")
            return
        
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "错误", "请选择要生成二维码的行！")
            return
        
        # 选择输出目录
        options = QFileDialog.Options()
        output_dir = QFileDialog.getExistingDirectory(
            self, "选择输出目录", "", options=options)
        
        if not output_dir:
            return
        
        # 创建进度对话框
        progress = QProgressDialog("正在生成二维码...", "取消", 0, len(selected_rows), self)
        progress.setWindowTitle("批量生成二维码")
        progress.setWindowModality(Qt.WindowModal)
        progress.show()
        
        success_count = 0
        for i, row in enumerate(selected_rows):
            row_idx = row.row()
            if row_idx >= len(self.current_data):
                continue
            
            qrcode_text = self.current_data[row_idx][self.qrcode_col+3]
            if not qrcode_text:
                continue
            
            try:
                # 生成二维码
                qr = qrcode.QRCode(
                    version=1,
                    error_correction=qrcode.constants.ERROR_CORRECT_L,
                    box_size=10,
                    border=4,
                )
                qr.add_data(qrcode_text)
                qr.make(fit=True)
                
                img = qr.make_image(fill_color="black", back_color="white")
                
                # 保存文件
                output_file = os.path.join(output_dir, f"qrcode_{qrcode_text}.png")
                img.save(output_file)
                success_count += 1
            except:
                pass
            
            progress.setValue(i+1)
            if progress.wasCanceled():
                break
        
        progress.close()
        QMessageBox.information(
            self, "完成", 
            f"成功生成 {success_count}/{len(selected_rows)} 个二维码文件到目录: {output_dir}")
    
    def show_image(self, image_path, title):
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setMinimumSize(400, 400)
        
        layout = QVBoxLayout()
        
        # 显示图像
        image_label = QLabel()
        pixmap = QIcon(image_path).pixmap(QSize(400, 400))
        image_label.setPixmap(pixmap)
        image_label.setAlignment(Qt.AlignCenter)
        
        # 保存按钮
        save_btn = QPushButton("保存图像")
        save_btn.clicked.connect(lambda: self.save_image(image_path))
        
        layout.addWidget(image_label)
        layout.addWidget(save_btn)
        
        dialog.setLayout(layout)
        dialog.exec_()
        
        # 删除临时文件
        try:
            os.remove(image_path)
        except:
            pass
    
    def save_image(self, image_path):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(
            self, "保存图像", "", "PNG 图像 (*.png)", options=options)
        
        if file_name:
            try:
                import shutil
                shutil.copy2(image_path, file_name)
                QMessageBox.information(self, "成功", "图像保存成功！")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"保存图像失败: {str(e)}")
    
    def export_to_csv(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(
            self, "导出为CSV", "", "CSV 文件 (*.csv)", options=options)
        
        if file_name:
            try:
                with open(file_name, 'w', newline='', encoding='utf-8-sig') as csvfile:
                    writer = csv.writer(csvfile)
                    
                    # 写入标题行
                    writer.writerow(['ID', '创建时间', '更新时间'] + self.column_names)
                    
                    # 写入数据行
                    for row in self.current_data:
                        writer.writerow(row)
                
                QMessageBox.information(self, "成功", f"数据已导出到 {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败: {str(e)}")
    
    def export_to_json(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(
            self, "导出为JSON", "", "JSON 文件 (*.json)", options=options)
        
        if file_name:
            try:
                data = []
                for row in self.current_data:
                    item = {
                        'id': row[0],
                        'created_at': row[1],
                        'updated_at': row[2]
                    }
                    
                    for i, col_name in enumerate(self.column_names):
                        item[col_name] = row[i+3] if i+3 < len(row) else ""
                    
                    data.append(item)
                
                with open(file_name, 'w', encoding='utf-8') as jsonfile:
                    json.dump(data, jsonfile, ensure_ascii=False, indent=2)
                
                QMessageBox.information(self, "成功", f"数据已导出到 {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出失败: {str(e)}")
    
    def import_from_csv(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getOpenFileName(
            self, "从CSV导入", "", "CSV 文件 (*.csv)", options=options)
        
        if file_name:
            try:
                with open(file_name, 'r', newline='', encoding='utf-8-sig') as csvfile:
                    reader = csv.reader(csvfile)
                    
                    # 读取标题行
                    headers = next(reader)
                    if len(headers) < len(self.column_names) + 3:
                        QMessageBox.warning(
                            self, "错误", 
                            f"CSV文件列数不匹配！需要 {len(self.column_names)+3} 列，实际 {len(headers)} 列")
                        return
                    
                    # 创建进度对话框
                    row_count = sum(1 for _ in reader)  # 计算行数
                    csvfile.seek(0)  # 重置文件指针
                    next(reader)  # 跳过标题行
                    
                    progress = QProgressDialog("正在导入数据...", "取消", 0, row_count, self)
                    progress.setWindowTitle("从CSV导入")
                    progress.setWindowModality(Qt.WindowModal)
                    progress.show()
                    
                    cursor = self.db.cursor()
                    success_count = 0
                    
                    try:
                        cursor.execute("BEGIN TRANSACTION")
                        
                        for i, row in enumerate(reader):
                            # 检查唯一性
                            if self.unique_col is not None:
                                unique_value = row[self.unique_col+2]  # 跳过ID、created_at、updated_at
                                cursor.execute(f"SELECT id FROM data WHERE col{self.unique_col}=?", (unique_value,))
                                if cursor.fetchone():
                                    continue  # 跳过重复项
                            
                            # 构建插入语句
                            columns = []
                            values = []
                            
                            for j in range(len(self.column_names)):
                                columns.append(f"col{j+1}")
                                values.append(row[j+3] if j+3 < len(row) else "")
                            
                            query = f"""
                            INSERT INTO data ({', '.join(columns)}) 
                            VALUES ({', '.join(['?']*len(columns))})
                            """
                            
                            cursor.execute(query, values)
                            
                            # 更新搜索索引
                            data_id = cursor.lastrowid
                            for j in range(len(self.column_names)):
                                if j+1 in self.searchable_cols:
                                    cursor.execute("""
                                    INSERT INTO search_index (col_id, value, data_id)
                                    VALUES (?, ?, ?)
                                    """, (j+1, values[j], data_id))
                            
                            success_count += 1
                            progress.setValue(i+1)
                            
                            if progress.wasCanceled():
                                break
                        
                        cursor.execute("COMMIT")
                        self.db.commit()
                        
                        progress.close()
                        QMessageBox.information(
                            self, "成功", 
                            f"成功导入 {success_count}/{row_count} 行数据")
                        
                        self.load_data()
                    except Exception as e:
                        cursor.execute("ROLLBACK")
                        progress.close()
                        QMessageBox.critical(self, "错误", f"导入失败: {str(e)}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导入失败: {str(e)}")
    
    def import_from_json(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getOpenFileName(
            self, "从JSON导入", "", "JSON 文件 (*.json)", options=options)
        
        if file_name:
            try:
                with open(file_name, 'r', encoding='utf-8') as jsonfile:
                    data = json.load(jsonfile)
                
                if not isinstance(data, list):
                    QMessageBox.warning(self, "错误", "JSON文件格式不正确！")
                    return
                
                # 创建进度对话框
                progress = QProgressDialog("正在导入数据...", "取消", 0, len(data), self)
                progress.setWindowTitle("从JSON导入")
                progress.setWindowModality(Qt.WindowModal)
                progress.show()
                
                cursor = self.db.cursor()
                success_count = 0
                
                try:
                    cursor.execute("BEGIN TRANSACTION")
                    
                    for i, item in enumerate(data):
                        # 检查唯一性
                        if self.unique_col is not None:
                            col_name = self.column_names[self.unique_col-1]
                            if col_name in item:
                                unique_value = item[col_name]
                                cursor.execute(f"SELECT id FROM data WHERE col{self.unique_col}=?", (unique_value,))
                                if cursor.fetchone():
                                    continue  # 跳过重复项
                        
                        # 构建插入语句
                        columns = []
                        values = []
                        
                        for j, col_name in enumerate(self.column_names):
                            columns.append(f"col{j+1}")
                            values.append(item.get(col_name, ""))
                        
                        query = f"""
                        INSERT INTO data ({', '.join(columns)}) 
                        VALUES ({', '.join(['?']*len(columns))})
                        """
                        
                        cursor.execute(query, values)
                        
                        # 更新搜索索引
                        data_id = cursor.lastrowid
                        for j in range(len(self.column_names)):
                            if j+1 in self.searchable_cols:
                                cursor.execute("""
                                INSERT INTO search_index (col_id, value, data_id)
                                VALUES (?, ?, ?)
                                """, (j+1, values[j], data_id))
                        
                        success_count += 1
                        progress.setValue(i+1)
                        
                        if progress.wasCanceled():
                            break
                    
                    cursor.execute("COMMIT")
                    self.db.commit()
                    
                    progress.close()
                    QMessageBox.information(
                        self, "成功", 
                        f"成功导入 {success_count}/{len(data)} 行数据")
                    
                    self.load_data()
                except Exception as e:
                    cursor.execute("ROLLBACK")
                    progress.close()
                    QMessageBox.critical(self, "错误", f"导入失败: {str(e)}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"导入失败: {str(e)}")
    
    def backup_data(self):
        options = QFileDialog.Options()
        file_name, _ = QFileDialog.getSaveFileName(
            self, "备份数据", f"{self.username}_backup_{datetime.now().strftime('%Y%m%d')}.db", 
            "Database Files (*.db)", options=options)
        
        if file_name:
            try:
                # 关闭当前连接
                self.db.close()
                
                # 复制数据库文件
                import shutil
                shutil.copy2(f"user_{self.username}.db", file_name)
                
                # 重新打开连接
                self.db = sqlite3.connect(f"user_{self.username}.db")
                
                QMessageBox.information(self, "成功", f"数据备份到 {file_name}")
            except Exception as e:
                QMessageBox.critical(self, "错误", f"备份失败: {str(e)}")
                # 尝试重新连接
                try:
                    self.db = sqlite3.connect(f"user_{self.username}.db")
                except:
                    pass
    
    def toggle_search(self):
        search_group = self.findChild(QGroupBox, "搜索")
        if search_group:
            search_group.setVisible(not search_group.isVisible())
    
    def show_about(self):
        QMessageBox.about(self, "关于", 
            "增强版电子表格管理系统\n\n"
            "版本: 2.0\n"
            "功能:\n"
            "- 多用户管理\n"
            "- 自定义列配置\n"
            "- 数据导入导出\n"
            "- 一维码/二维码生成\n"
            "- 高级搜索功能\n"
            "- 数据统计分析")

class EnhancedDataEditDialog(QDialog):
    def __init__(self, column_names, column_types, required_cols, parent=None, current_data=None):
        super().__init__(parent)
        self.setWindowTitle("编辑数据" if current_data else "添加数据")
        self.column_names = column_names
        self.column_types = column_types
        self.required_cols = required_cols
        
        self.layout = QVBoxLayout()
        self.scroll_area = QScrollArea()
        self.scroll_content = QWidget()
        self.scroll_layout = QVBoxLayout()
        
        # 创建输入字段
        self.inputs = []
        for i, (name, col_type) in enumerate(zip(column_names, column_types)):
            row_layout = QHBoxLayout()
            
            label = QLabel(f"{name}:")
            if i+1 in required_cols:
                label.setText(f"{name}*:")
                label.setStyleSheet("color: red;")
            
            # 根据类型创建不同的输入控件
            if col_type == "INTEGER":
                input_field = QSpinBox()
                input_field.setRange(-2147483648, 2147483647)
                if current_data and i < len(current_data):
                    try:
                        input_field.setValue(int(current_data[i]))
                    except:
                        pass
            elif col_type == "REAL":
                input_field = QLineEdit()
                input_field.setValidator(QDoubleValidator())
                if current_data and i < len(current_data):
                    input_field.setText(str(current_data[i]))
            elif col_type == "TEXT":
                input_field = QLineEdit()
                if current_data and i < len(current_data):
                    input_field.setText(str(current_data[i]))
            elif col_type == "BOOLEAN":
                input_field = QCheckBox()
                if current_data and i < len(current_data):
                    input_field.setChecked(bool(current_data[i]))
            else:
                input_field = QLineEdit()
                if current_data and i < len(current_data):
                    input_field.setText(str(current_data[i]))
            
            row_layout.addWidget(label)
            row_layout.addWidget(input_field)
            
            self.scroll_layout.addLayout(row_layout)
            self.inputs.append(input_field)
        
        self.scroll_content.setLayout(self.scroll_layout)
        self.scroll_area.setWidget(self.scroll_content)
        self.scroll_area.setWidgetResizable(True)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        
        self.layout.addWidget(self.scroll_area)
        self.layout.addLayout(button_layout)
        self.setLayout(self.layout)
        
        # 调整对话框大小
        self.resize(500, 400)
    
    def get_data(self):
        data = []
        for i, (input_field, col_type) in enumerate(zip(self.inputs, self.column_types)):
            if col_type == "INTEGER":
                data.append(input_field.value())
            elif col_type == "REAL":
                try:
                    data.append(float(input_field.text()))
                except:
                    data.append(0.0)
            elif col_type == "TEXT":
                data.append(input_field.text())
            elif col_type == "BOOLEAN":
                data.append(1 if input_field.isChecked() else 0)
            else:
                data.append(input_field.text())
        return data

class StatsDialog(QDialog):
    def __init__(self, data, column_names, parent=None):
        super().__init__(parent)
        self.setWindowTitle("数据统计")
        self.resize(800, 600)
        
        self.layout = QVBoxLayout()
        
        # 创建图表
        self.figure = plt.figure()
        self.canvas = FigureCanvas(self.figure)
        
        self.layout.addWidget(self.canvas)
        self.setLayout(self.layout)
        
        # 绘制统计图表
        self.plot_stats(data, column_names)
    
    def plot_stats(self, data, column_names):
        self.figure.clear()
        
        # 只统计前5列（避免图表太多）
        cols_to_plot = min(5, len(column_names))
        
        for i in range(cols_to_plot):
            ax = self.figure.add_subplot(cols_to_plot, 1, i+1)
            
            # 收集列数据
            col_data = []
            for row in data:
                if i+3 < len(row):  # 跳过ID、created_at、updated_at
                    col_data.append(row[i+3])
            
            # 根据数据类型选择统计方式
            try:
                # 尝试转换为数字
                numeric_data = [float(x) for x in col_data if x]
                ax.hist(numeric_data, bins=10)
                ax.set_title(f"{column_names[i]} - 数值分布")
            except ValueError:
                # 文本数据 - 统计频率
                from collections import Counter
                counter = Counter(col_data)
                labels, values = zip(*counter.most_common(10))
                ax.bar(labels, values)
                ax.set_title(f"{column_names[i]} - 频率统计")
                plt.setp(ax.get_xticklabels(), rotation=45, ha='right')
        
        self.figure.tight_layout()
        self.canvas.draw()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 设置中文显示
    font = QFont()
    font.setFamily("Microsoft YaHei")
    app.setFont(font)
    
    login_window = EnhancedLoginWindow()
    login_window.show()
    
    sys.exit(app.exec_())