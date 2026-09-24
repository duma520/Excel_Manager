import os
# 检查并安装缺失的依赖
try:
    import qrcode
    import barcode
    from barcode.writer import ImageWriter
except ImportError:
    import subprocess
    import sys
    packages = ['qrcode[pil]', 'python-barcode', 'pillow']
    subprocess.check_call([sys.executable, "-m", "pip", "install", *packages])
    import qrcode
    import barcode
    from barcode.writer import ImageWriter

import sys
import sqlite3
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                             QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                             QComboBox, QMessageBox, QTabWidget, QDialog, QFormLayout,
                             QSpinBox, QCheckBox, QGroupBox, QSizePolicy, QScrollArea)
from PyQt5.QtCore import Qt, QSize
from PyQt5.QtGui import QIcon, QFont
import qrcode
import barcode
from barcode.writer import ImageWriter
from io import BytesIO
from PIL import Image

class UserSetupDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("创建新用户")
        self.setMinimumWidth(400)
        
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
        self.columns_spin.setMaximum(20)
        self.columns_spin.setValue(5)
        self.columns_layout.addWidget(self.columns_label)
        self.columns_layout.addWidget(self.columns_spin)
        
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
            self.columns_config_layout.removeWidget(widget['group'])
            widget['group'].deleteLater()
        self.column_widgets.clear()
        
        # 添加新的配置
        columns_count = self.columns_spin.value()
        for i in range(columns_count):
            group = QGroupBox(f"列 {i+1}")
            layout = QFormLayout()
            
            name_label = QLabel("列说明:")
            name_input = QLineEdit()
            layout.addRow(name_label, name_input)
            
            barcode_check = QCheckBox("一维码")
            qrcode_check = QCheckBox("二维码")
            unique_check = QCheckBox("唯一性")
            
            checks_layout = QHBoxLayout()
            checks_layout.addWidget(barcode_check)
            checks_layout.addWidget(qrcode_check)
            checks_layout.addWidget(unique_check)
            layout.addRow(QLabel("特殊属性:"), checks_layout)
            
            group.setLayout(layout)
            self.columns_config_layout.addWidget(group)
            
            self.column_widgets.append({
                'group': group,
                'name_input': name_input,
                'barcode_check': barcode_check,
                'qrcode_check': qrcode_check,
                'unique_check': unique_check
            })
    
    def get_user_config(self):
        config = {
            'username': self.username_input.text(),
            'columns': []
        }
        
        for widget in self.column_widgets:
            column_config = {
                'name': widget['name_input'].text(),
                'is_barcode': widget['barcode_check'].isChecked(),
                'is_qrcode': widget['qrcode_check'].isChecked(),
                'is_unique': widget['unique_check'].isChecked()
            }
            config['columns'].append(column_config)
        
        return config

class LoginWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("用户登录")
        self.resize(400, 300)
        
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
        title_label = QLabel("电子表格管理系统")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setFont(QFont("Arial", 16, QFont.Bold))
        layout.addWidget(title_label)
        
        # 用户选择
        self.user_combo = QComboBox()
        self.user_combo.setPlaceholderText("选择用户")
        layout.addWidget(self.user_combo)
        
        # 登录按钮
        login_btn = QPushButton("登录")
        login_btn.clicked.connect(self.login)
        layout.addWidget(login_btn)
        
        # 添加用户按钮
        add_user_btn = QPushButton("添加用户")
        add_user_btn.clicked.connect(self.show_add_user_dialog)
        layout.addWidget(add_user_btn)
        
        # 删除用户按钮
        del_user_btn = QPushButton("删除用户")
        del_user_btn.clicked.connect(self.delete_user)
        layout.addWidget(del_user_btn)
        
        # 添加间距
        layout.addStretch()
    
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
            config TEXT NOT NULL
        )
        """)
        self.main_db.commit()
        
        # 加载现有用户
        cursor.execute("SELECT username FROM users")
        users = cursor.fetchall()
        
        self.user_combo.clear()
        for user in users:
            self.user_combo.addItem(user[0])
    
    def show_add_user_dialog(self):
        dialog = UserSetupDialog(self)
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
                    columns.append(f"col{i+1} TEXT")
                
                create_table_sql = f"""
                CREATE TABLE data (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
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
                
                # 保存列配置
                for i, col in enumerate(user_config['columns']):
                    cursor_user.execute("""
                    INSERT INTO config (key, value) VALUES (?, ?)
                    """, (f"col{i+1}_name", col['name']))
                    
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
                
                user_db.commit()
                user_db.close()
                
                # 添加到主数据库
                import json
                cursor.execute("""
                INSERT INTO users (username, db_file, config) VALUES (?, ?, ?)
                """, (user_config['username'], db_file, json.dumps(user_config)))
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
    
    def login(self):
        username = self.user_combo.currentText()
        if not username:
            QMessageBox.warning(self, "错误", "请选择用户！")
            return
        
        # 获取用户数据库文件
        cursor = self.main_db.cursor()
        cursor.execute("SELECT db_file FROM users WHERE username=?", (username,))
        result = cursor.fetchone()
        
        if not result:
            QMessageBox.warning(self, "错误", "用户不存在！")
            return
        
        db_file = result[0]
        
        # 打开用户数据库
        try:
            user_db = sqlite3.connect(db_file)
            self.main_db.close()
            
            # 打开主窗口
            self.main_window = MainWindow(username, user_db)
            self.main_window.show()
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "错误", f"无法打开用户数据库: {str(e)}")

class MainWindow(QMainWindow):
    def __init__(self, username, db_connection):
        super().__init__()
        self.username = username
        self.db = db_connection
        self.current_data = []
        
        self.setWindowTitle(f"电子表格管理系统 - {username}")
        self.resize(1000, 700)
        
        if os.path.exists("icon.ico"):
            self.setWindowIcon(QIcon("icon.ico"))
        
        self.init_ui()
        self.load_config()
        self.load_data()
    
    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)
        
        # 搜索区域
        search_group = QGroupBox("搜索")
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("输入搜索内容...")
        self.search_input.returnPressed.connect(self.search_data)
        
        search_btn = QPushButton("搜索")
        search_btn.clicked.connect(self.search_data)
        
        clear_btn = QPushButton("清除")
        clear_btn.clicked.connect(self.clear_search)
        
        search_layout.addWidget(self.search_input)
        search_layout.addWidget(search_btn)
        search_layout.addWidget(clear_btn)
        search_group.setLayout(search_layout)
        
        main_layout.addWidget(search_group)
        
        # 表格区域
        self.table = QTableWidget()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        main_layout.addWidget(self.table)
        
        # 操作按钮区域
        button_layout = QHBoxLayout()
        
        add_btn = QPushButton("添加")
        add_btn.clicked.connect(self.show_add_dialog)
        
        edit_btn = QPushButton("编辑")
        edit_btn.clicked.connect(self.show_edit_dialog)
        
        delete_btn = QPushButton("删除")
        delete_btn.clicked.connect(self.delete_data)
        
        barcode_btn = QPushButton("生成一维码")
        barcode_btn.clicked.connect(self.generate_barcode)
        
        qrcode_btn = QPushButton("生成二维码")
        qrcode_btn.clicked.connect(self.generate_qrcode)
        
        button_layout.addWidget(add_btn)
        button_layout.addWidget(edit_btn)
        button_layout.addWidget(delete_btn)
        button_layout.addWidget(barcode_btn)
        button_layout.addWidget(qrcode_btn)
        
        main_layout.addLayout(button_layout)
    
    def load_config(self):
        self.column_names = []
        self.barcode_col = None
        self.qrcode_col = None
        self.unique_col = None
        
        cursor = self.db.cursor()
        
        # 获取列名
        cursor.execute("SELECT key, value FROM config WHERE key LIKE 'col%_name'")
        col_configs = cursor.fetchall()
        
        for config in col_configs:
            col_num = int(config[0].split('_')[0][3:])
            col_name = config[1]
            
            # 确保列名列表足够长
            while len(self.column_names) < col_num:
                self.column_names.append("")
            
            self.column_names[col_num-1] = col_name
        
        # 获取特殊列
        cursor.execute("SELECT key, value FROM config WHERE key IN ('barcode_col', 'qrcode_col', 'unique_col')")
        special_cols = cursor.fetchall()
        
        for col in special_cols:
            if col[0] == 'barcode_col':
                self.barcode_col = int(col[1])
            elif col[0] == 'qrcode_col':
                self.qrcode_col = int(col[1])
            elif col[0] == 'unique_col':
                self.unique_col = int(col[1])
    
    def load_data(self, search_term=None):
        cursor = self.db.cursor()
        
        # 构建查询
        if search_term:
            query = "SELECT * FROM data WHERE "
            conditions = []
            params = []
            
            # 为每一列添加搜索条件
            for i in range(1, len(self.column_names)+1):
                conditions.append(f"col{i} LIKE ?")
                params.append(f"%{search_term}%")
            
            query += " OR ".join(conditions)
            cursor.execute(query, params)
        else:
            cursor.execute("SELECT * FROM data")
        
        self.current_data = cursor.fetchall()
        
        # 更新表格
        self.table.setRowCount(len(self.current_data))
        self.table.setColumnCount(len(self.column_names))
        self.table.setHorizontalHeaderLabels(self.column_names)
        
        for row_idx, row in enumerate(self.current_data):
            for col_idx in range(len(self.column_names)):
                # 跳过ID列
                item = QTableWidgetItem(str(row[col_idx+1] if col_idx+1 < len(row) else ""))
                self.table.setItem(row_idx, col_idx, item)
        
        # 调整列宽
        self.table.resizeColumnsToContents()
    
    def search_data(self):
        search_term = self.search_input.text()
        self.load_data(search_term)
    
    def clear_search(self):
        self.search_input.clear()
        self.load_data()
    
    def show_add_dialog(self):
        dialog = DataEditDialog(self.column_names, self)
        if dialog.exec_() == QDialog.Accepted:
            new_data = dialog.get_data()
            
            # 检查唯一性
            if self.unique_col is not None:
                unique_value = new_data[self.unique_col-1]
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
                self.db.commit()
                self.load_data()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"添加数据失败: {str(e)}")
    
    def show_edit_dialog(self):
        selected_rows = self.table.selectionModel().selectedRows()
        if not selected_rows:
            QMessageBox.warning(self, "错误", "请选择要编辑的行！")
            return
        
        row_idx = selected_rows[0].row()
        if row_idx >= len(self.current_data):
            return
        
        # 获取当前数据 (跳过ID列)
        current_row = self.current_data[row_idx][1:]
        
        dialog = DataEditDialog(self.column_names, self, current_row)
        if dialog.exec_() == QDialog.Accepted:
            new_data = dialog.get_data()
            
            # 检查唯一性
            if self.unique_col is not None:
                unique_value = new_data[self.unique_col-1]
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
            UPDATE data SET {', '.join(updates)}
            WHERE id=?
            """
            
            try:
                cursor = self.db.cursor()
                cursor.execute(query, values)
                self.db.commit()
                self.load_data()
            except Exception as e:
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
                cursor.execute("DELETE FROM data WHERE id=?", (self.current_data[row_idx][0],))
                self.db.commit()
                self.load_data()
            except Exception as e:
                QMessageBox.critical(self, "错误", f"删除数据失败: {str(e)}")
    
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
        
        barcode_text = self.current_data[row_idx][self.barcode_col]
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
        
        qrcode_text = self.current_data[row_idx][self.qrcode_col]
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

class DataEditDialog(QDialog):
    def __init__(self, column_names, parent=None, current_data=None):
        super().__init__(parent)
        self.setWindowTitle("编辑数据" if current_data else "添加数据")
        self.column_names = column_names
        
        self.layout = QVBoxLayout()
        
        # 创建输入字段
        self.inputs = []
        for i, name in enumerate(column_names):
            row_layout = QHBoxLayout()
            
            label = QLabel(f"{name}:")
            input_field = QLineEdit()
            
            if current_data and i < len(current_data):
                input_field.setText(str(current_data[i]))
            
            row_layout.addWidget(label)
            row_layout.addWidget(input_field)
            
            self.layout.addLayout(row_layout)
            self.inputs.append(input_field)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        ok_btn = QPushButton("确定")
        ok_btn.clicked.connect(self.accept)
        
        cancel_btn = QPushButton("取消")
        cancel_btn.clicked.connect(self.reject)
        
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        
        self.layout.addLayout(button_layout)
        self.setLayout(self.layout)
    
    def get_data(self):
        return [input_field.text() for input_field in self.inputs]

if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # 设置中文显示
    font = QFont()
    font.setFamily("Microsoft YaHei")
    app.setFont(font)
    
    login_window = LoginWindow()
    login_window.show()
    
    sys.exit(app.exec_())