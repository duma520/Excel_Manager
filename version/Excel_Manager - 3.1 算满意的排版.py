import os
import sys
import sqlite3
from PyQt5.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, 
                            QLabel, QLineEdit, QPushButton, QTableWidget, QTableWidgetItem,
                            QMessageBox, QComboBox, QTabWidget, QFormLayout, QGroupBox,
                            QInputDialog, QListWidget, QAbstractItemView, QHeaderView,
                            QDialog, QCheckBox)
from PyQt5.QtGui import QIcon, QPixmap, QImage
from PyQt5.QtCore import Qt, QSize
import qrcode
from barcode import Code128
from barcode.writer import ImageWriter
import io
from PIL import Image

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
                settings TEXT NOT NULL
            )
        ''')
        self.conn.commit()
    
    def add_user(self, username, db_file, settings):
        try:
            self.cursor.execute('INSERT INTO users VALUES (?, ?, ?)', 
                              (username, db_file, settings))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
    
    def delete_user(self, username):
        self.cursor.execute('DELETE FROM users WHERE username=?', (username,))
        self.conn.commit()
        return self.cursor.rowcount > 0
    
    def get_users(self):
        self.cursor.execute('SELECT username FROM users')
        return [row[0] for row in self.cursor.fetchall()]
    
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
    
    def close(self):
        self.conn.close()

class UserDatabase:
    def __init__(self, db_file):
        self.conn = sqlite3.connect(db_file)
        self.cursor = self.conn.cursor()
        self._create_tables()
    
    def _create_tables(self):
        # 表格结构在用户创建时动态生成
        pass
    
    def initialize_database(self, columns_config):
        # 删除旧表（如果存在）
        self.cursor.execute('DROP TABLE IF EXISTS data')
        
        # 创建新表
        columns = []
        for col in columns_config:
            col_name = col['name']
            col_type = col.get('type', 'TEXT')
            columns.append(f"{col_name} {col_type}")
        
        create_table_sql = f"CREATE TABLE data ({', '.join(columns)})"
        self.cursor.execute(create_table_sql)
        
        # 保存列配置
        self.cursor.execute('DROP TABLE IF EXISTS config')
        self.cursor.execute('''
            CREATE TABLE config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        ''')
        
        # 保存列配置
        for col in columns_config:
            self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                              (f"col_{col['name']}_label", col['label']))
            if col.get('is_barcode', False):
                self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                                  ('barcode_column', col['name']))
            if col.get('is_qrcode', False):
                self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                                  ('qrcode_column', col['name']))
            if col.get('is_unique', False):
                self.cursor.execute('INSERT INTO config VALUES (?, ?)', 
                                  ('unique_column', col['name']))
        
        self.conn.commit()
    
    def get_columns_config(self):
        self.cursor.execute('SELECT key, value FROM config WHERE key LIKE "col_%"')
        config = {}
        for key, value in self.cursor.fetchall():
            config[key] = value
        
        if not config:
            return None
        
        # 获取列名
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        # 构建列配置
        columns_config = []
        for col in columns:
            col_config = {
                'name': col,
                'label': config.get(f'col_{col}_label', col)
            }
            columns_config.append(col_config)
        
        # 添加特殊列标记
        self.cursor.execute('SELECT value FROM config WHERE key="barcode_column"')
        result = self.cursor.fetchone()
        if result:
            barcode_col = result[0]
            for col in columns_config:
                if col['name'] == barcode_col:
                    col['is_barcode'] = True
        
        self.cursor.execute('SELECT value FROM config WHERE key="qrcode_column"')
        result = self.cursor.fetchone()
        if result:
            qrcode_col = result[0]
            for col in columns_config:
                if col['name'] == qrcode_col:
                    col['is_qrcode'] = True
        
        self.cursor.execute('SELECT value FROM config WHERE key="unique_column"')
        result = self.cursor.fetchone()
        if result:
            unique_col = result[0]
            for col in columns_config:
                if col['name'] == unique_col:
                    col['is_unique'] = True
        
        return columns_config
    
    def insert_data(self, data):
        columns = ', '.join(data.keys())
        placeholders = ', '.join(['?'] * len(data))
        sql = f"INSERT INTO data ({columns}) VALUES ({placeholders})"
        self.cursor.execute(sql, tuple(data.values()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def check_unique(self, column, value):
        sql = f"SELECT COUNT(*) FROM data WHERE {column}=?"
        self.cursor.execute(sql, (value,))
        return self.cursor.fetchone()[0] == 0
    
    def search_data(self, keyword):
        # 获取所有列名
        self.cursor.execute('PRAGMA table_info(data)')
        columns = [row[1] for row in self.cursor.fetchall()]
        
        # 构建搜索条件
        conditions = []
        params = []
        for col in columns:
            conditions.append(f"{col} LIKE ?")
            params.append(f"%{keyword}%")
        
        sql = f"SELECT * FROM data WHERE {' OR '.join(conditions)}"
        self.cursor.execute(sql, params)
        return self.cursor.fetchall()
    
    def get_all_data(self):
        self.cursor.execute('SELECT * FROM data')
        return self.cursor.fetchall()
    
    def close(self):
        self.conn.close()

class LoginWindow(QMainWindow):
    def __init__(self, user_manager):
        super().__init__()
        self.user_manager = user_manager
        self.init_ui()
        
        # 检查图标文件
        if os.path.exists('icon.ico'):
            self.setWindowIcon(QIcon('icon.ico'))
    
    def init_ui(self):
        self.setWindowTitle('用户登录')
        self.resize(400, 300)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # 用户列表
        self.user_list = QListWidget()
        self.user_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.user_list.itemDoubleClicked.connect(self.login)
        layout.addWidget(QLabel('选择用户:'))
        layout.addWidget(self.user_list)
        
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
        
        layout.addLayout(button_layout)
        
        # 加载用户列表
        self.load_users()
    
    def load_users(self):
        self.user_list.clear()
        users = self.user_manager.get_users()
        for user in users:
            self.user_list.addItem(user)
    
    def login(self):
        selected_items = self.user_list.selectedItems()
        if not selected_items:
            QMessageBox.warning(self, '警告', '请选择一个用户')
            return
        
        username = selected_items[0].text()
        db_file = self.user_manager.get_user_db_file(username)
        settings = self.user_manager.get_user_settings(username)
        
        self.main_window = MainWindow(username, db_file, settings, self.user_manager)
        self.main_window.show()
        self.hide()
    
    def add_user(self):
        username, ok = QInputDialog.getText(self, '添加用户', '输入用户名:')
        if not ok or not username:
            return
        
        if username in self.user_manager.get_users():
            QMessageBox.warning(self, '警告', '用户名已存在')
            return
        
        # 获取列配置
        col_dialog = ColumnConfigDialog()
        if col_dialog.exec_() == QDialog.Accepted:
            columns_config = col_dialog.get_columns_config()
            if not columns_config:
                QMessageBox.warning(self, '警告', '必须至少配置一列')
                return
            
            # 创建用户数据库
            db_file = f'user_{username}.db'
            user_db = UserDatabase(db_file)
            user_db.initialize_database(columns_config)
            user_db.close()
            
            # 保存用户
            settings = str(columns_config)  # 简单序列化，实际应用中应该使用JSON
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
        reply = QMessageBox.question(self, '确认', f'确定要删除用户 {username} 吗?', 
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            if self.user_manager.delete_user(username):
                # 删除数据库文件
                db_file = f'user_{username}.db'
                if os.path.exists(db_file):
                    os.remove(db_file)
                self.load_users()
            else:
                QMessageBox.warning(self, '警告', '删除用户失败')

class ColumnConfigDialog(QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('列配置')
        self.resize(600, 400)
        
        layout = QVBoxLayout()
        self.setLayout(layout)
        
        # 表格用于配置列
        self.table = QTableWidget()
        self.table.setColumnCount(6)
        self.table.setHorizontalHeaderLabels(['列名', '显示名称', '数据类型', '条形码列', '二维码列', '唯一列'])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        layout.addWidget(self.table)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        
        self.add_btn = QPushButton('添加列')
        self.add_btn.clicked.connect(self.add_column)
        button_layout.addWidget(self.add_btn)
        
        self.remove_btn = QPushButton('删除列')
        self.remove_btn.clicked.connect(self.remove_column)
        button_layout.addWidget(self.remove_btn)
        
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
        
        # 数据类型
        data_type = QComboBox()
        data_type.addItems(['TEXT', 'INTEGER', 'REAL', 'BLOB'])
        self.table.setCellWidget(row, 2, data_type)
        
        # 条形码列
        barcode_check = QCheckBox()
        self.table.setCellWidget(row, 3, barcode_check)
        
        # 二维码列
        qrcode_check = QCheckBox()
        self.table.setCellWidget(row, 4, qrcode_check)
        
        # 唯一列
        unique_check = QCheckBox()
        self.table.setCellWidget(row, 5, unique_check)
    
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
            
            config = {
                'name': col_name,
                'label': self.table.cellWidget(row, 1).text().strip(),
                'type': self.table.cellWidget(row, 2).currentText(),
                'is_barcode': self.table.cellWidget(row, 3).isChecked(),
                'is_qrcode': self.table.cellWidget(row, 4).isChecked(),
                'is_unique': self.table.cellWidget(row, 5).isChecked()
            }
            columns_config.append(config)
        
        # 检查条形码和二维码列配置
        barcode_cols = [col for col in columns_config if col.get('is_barcode', False)]
        qrcode_cols = [col for col in columns_config if col.get('is_qrcode', False)]
        
        if len(barcode_cols) > 1:
            QMessageBox.warning(self, '警告', '只能设置一列为条形码列')
            return None
        
        if len(qrcode_cols) > 1:
            QMessageBox.warning(self, '警告', '只能设置一列为二维码列')
            return None
        
        return columns_config

class MainWindow(QMainWindow):
    def __init__(self, username, db_file, settings, user_manager):
        super().__init__()
        self.username = username
        self.db_file = db_file
        self.user_manager = user_manager
        
        # 解析设置
        try:
            self.columns_config = eval(settings)  # 实际应用中应该使用JSON解析
        except:
            self.columns_config = []
        
        self.user_db = UserDatabase(db_file)
        if not self.columns_config:
            self.columns_config = self.user_db.get_columns_config()
        
        self.init_ui()
        
        # 检查图标文件
        if os.path.exists('icon.ico'):
            self.setWindowIcon(QIcon('icon.ico'))
    
    def init_ui(self):
        self.setWindowTitle(f'数据管理系统 - {self.username}')
        self.resize(1000, 600)
        
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        
        layout = QVBoxLayout()
        central_widget.setLayout(layout)
        
        # 搜索区域
        search_group = QGroupBox('搜索')
        search_layout = QHBoxLayout()
        
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText('输入关键词搜索...')
        self.search_input.returnPressed.connect(self.search_data)
        search_layout.addWidget(self.search_input)
        
        self.search_btn = QPushButton('搜索')
        self.search_btn.clicked.connect(self.search_data)
        search_layout.addWidget(self.search_btn)
        
        search_group.setLayout(search_layout)
        layout.addWidget(search_group)
        
        # 数据录入区域
        input_group = QGroupBox('数据录入')
        input_layout = QFormLayout()
        
        self.input_widgets = {}
        for col in self.columns_config:
            label = col['label']
            widget = QLineEdit()
            input_layout.addRow(label, widget)
            self.input_widgets[col['name']] = widget
        
        self.add_btn = QPushButton('添加数据')
        self.add_btn.clicked.connect(self.add_data)
        input_layout.addRow(self.add_btn)
        
        input_group.setLayout(input_layout)
        layout.addWidget(input_group)
        
        # 数据显示区域
        self.tab_widget = QTabWidget()
        layout.addWidget(self.tab_widget)
        
        # 数据表格
        self.data_table = QTableWidget()
        self.data_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.data_table.doubleClicked.connect(self.show_item_detail)
        self.tab_widget.addTab(self.data_table, '数据列表')
        
        # 详情视图
        self.detail_widget = QWidget()
        self.detail_layout = QVBoxLayout()
        self.detail_widget.setLayout(self.detail_layout)
        self.tab_widget.addTab(self.detail_widget, '详细信息')
        
        # 加载数据
        self.load_data()
    
    def load_data(self):
        # 设置表头
        headers = [col['label'] for col in self.columns_config]
        
        # 添加条形码和二维码列
        barcode_col = next((col for col in self.columns_config if col.get('is_barcode', False)), None)
        if barcode_col:
            headers.append('条形码')
        
        qrcode_col = next((col for col in self.columns_config if col.get('is_qrcode', False)), None)
        if qrcode_col:
            headers.append('二维码')
        
        self.data_table.setColumnCount(len(headers))
        self.data_table.setHorizontalHeaderLabels(headers)
        
        # 加载数据
        data = self.user_db.get_all_data()
        self.data_table.setRowCount(len(data))
        
        for row_idx, row_data in enumerate(data):
            for col_idx, value in enumerate(row_data):
                item = QTableWidgetItem(str(value))
                self.data_table.setItem(row_idx, col_idx, item)
            
            # 添加条形码和二维码
            col_offset = len(self.columns_config)
            if barcode_col:
                barcode_value = row_data[self.columns_config.index(barcode_col)]
                barcode_img = self.generate_barcode(barcode_value)
                if barcode_img:
                    label = QLabel()
                    label.setPixmap(QPixmap.fromImage(barcode_img))
                    label.setAlignment(Qt.AlignCenter)
                    self.data_table.setCellWidget(row_idx, col_offset, label)
                    col_offset += 1
            
            if qrcode_col:
                qrcode_value = row_data[self.columns_config.index(qrcode_col)]
                qrcode_img = self.generate_qrcode(qrcode_value)
                if qrcode_img:
                    label = QLabel()
                    label.setPixmap(QPixmap.fromImage(qrcode_img))
                    label.setAlignment(Qt.AlignCenter)
                    self.data_table.setCellWidget(row_idx, col_offset, label)
        
        self.data_table.resizeColumnsToContents()
        self.data_table.resizeRowsToContents()
    
    def search_data(self):
        keyword = self.search_input.text().strip()
        if not keyword:
            self.load_data()
            return
        
        data = self.user_db.search_data(keyword)
        self.data_table.setRowCount(len(data))
        
        barcode_col = next((col for col in self.columns_config if col.get('is_barcode', False)), None)
        qrcode_col = next((col for col in self.columns_config if col.get('is_qrcode', False)), None)
        
        for row_idx, row_data in enumerate(data):
            for col_idx, value in enumerate(row_data):
                item = QTableWidgetItem(str(value))
                self.data_table.setItem(row_idx, col_idx, item)
            
            # 添加条形码和二维码
            col_offset = len(self.columns_config)
            if barcode_col:
                barcode_value = row_data[self.columns_config.index(barcode_col)]
                barcode_img = self.generate_barcode(barcode_value)
                if barcode_img:
                    label = QLabel()
                    label.setPixmap(QPixmap.fromImage(barcode_img))
                    label.setAlignment(Qt.AlignCenter)
                    self.data_table.setCellWidget(row_idx, col_offset, label)
                    col_offset += 1
            
            if qrcode_col:
                qrcode_value = row_data[self.columns_config.index(qrcode_col)]
                qrcode_img = self.generate_qrcode(qrcode_value)
                if qrcode_img:
                    label = QLabel()
                    label.setPixmap(QPixmap.fromImage(qrcode_img))
                    label.setAlignment(Qt.AlignCenter)
                    self.data_table.setCellWidget(row_idx, col_offset, label)
        
        self.data_table.resizeColumnsToContents()
        self.data_table.resizeRowsToContents()
    
    def add_data(self):
        data = {}
        for col in self.columns_config:
            value = self.input_widgets[col['name']].text().strip()
            
            # 检查唯一性
            if col.get('is_unique', False) and value:
                if not self.user_db.check_unique(col['name'], value):
                    QMessageBox.warning(self, '警告', f"{col['label']} 的值必须唯一")
                    return
            
            data[col['name']] = value
        
        # 检查必填项
        for col in self.columns_config:
            if col.get('is_unique', False) and not data[col['name']]:
                QMessageBox.warning(self, '警告', f"{col['label']} 是必填项")
                return
        
        try:
            self.user_db.insert_data(data)
            QMessageBox.information(self, '成功', '数据添加成功')
            
            # 清空输入框
            for widget in self.input_widgets.values():
                widget.clear()
            
            # 刷新数据
            self.load_data()
        except Exception as e:
            QMessageBox.warning(self, '错误', f'添加数据失败: {str(e)}')
    
    def show_item_detail(self, index):
        # 清除之前的详情内容
        for i in reversed(range(self.detail_layout.count())): 
            item = self.detail_layout.itemAt(i)
            if item and item.widget():  # 双重检查
                item.widget().setParent(None)
        
        row = index.row()
        data = []
        for col in range(len(self.columns_config)):
            item = self.data_table.item(row, col)
            data.append(item.text() if item else "")
        
        # 显示详细信息
        form_layout = QFormLayout()
        
        for col_idx, col in enumerate(self.columns_config):
            label = QLabel(col['label'])
            value = QLabel(data[col_idx])
            form_layout.addRow(label, value)
        
        self.detail_layout.addLayout(form_layout)
        
        # 显示条形码和二维码
        barcode_col = next((col for col in self.columns_config if col.get('is_barcode', False)), None)
        if barcode_col:
            barcode_value = data[self.columns_config.index(barcode_col)]
            if barcode_value:  # 检查是否有值
                barcode_img = self.generate_barcode(barcode_value)
                if barcode_img:
                    label = QLabel('条形码:')
                    self.detail_layout.addWidget(label)
                    
                    barcode_label = QLabel()
                    barcode_label.setPixmap(QPixmap.fromImage(barcode_img))
                    barcode_label.setAlignment(Qt.AlignCenter)
                    self.detail_layout.addWidget(barcode_label)
        
        qrcode_col = next((col for col in self.columns_config if col.get('is_qrcode', False)), None)
        if qrcode_col:
            qrcode_value = data[self.columns_config.index(qrcode_col)]
            if qrcode_value:  # 检查是否有值
                qrcode_img = self.generate_qrcode(qrcode_value)
                if qrcode_img:
                    label = QLabel('二维码:')
                    self.detail_layout.addWidget(label)
                    
                    qrcode_label = QLabel()
                    qrcode_label.setPixmap(QPixmap.fromImage(qrcode_img))
                    qrcode_label.setAlignment(Qt.AlignCenter)
                    self.detail_layout.addWidget(qrcode_label)
        
        self.tab_widget.setCurrentIndex(1)

    
    def generate_barcode(self, data):
        if not data:
            return None
        
        try:
            # 确保数据是字符串
            data_str = str(data)
            barcode = Code128(data_str, writer=ImageWriter())
            
            # 保存到内存
            buffer = io.BytesIO()
            barcode.write(buffer)
            
            # 转换为QImage
            img = Image.open(buffer)
            img = img.convert("RGBA")
            
            data = img.tobytes("raw", "RGBA")
            qimage = QImage(data, img.size[0], img.size[1], QImage.Format_RGBA8888)
            
            return qimage.scaledToWidth(200, Qt.SmoothTransformation)
        except Exception as e:
            print(f"生成条形码失败: {e}")
            return None
    
    def generate_qrcode(self, data):
        if not data:
            return None
        
        try:
            # 确保数据是字符串
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
            
            # 转换为QImage
            img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            qimage = QImage(data, img.size[0], img.size[1], QImage.Format_RGBA8888)
            
            return qimage.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        except Exception as e:
            print(f"生成二维码失败: {e}")
            return None

    
    def closeEvent(self, event):
        self.user_db.close()
        event.accept()

if __name__ == '__main__':
    app = QApplication(sys.argv)
    
    # 初始化用户管理器
    user_manager = UserManager()
    
    # 显示登录窗口
    login_window = LoginWindow(user_manager)
    login_window.show()
    
    sys.exit(app.exec_())