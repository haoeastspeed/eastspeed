# -*- coding: utf-8 -*-
"""IDM 风格浅色主题：简洁、扁平、蓝色强调。"""

PRIMARY = "#2D7FF9"
SUCCESS = "#22A55A"
DANGER = "#E5484D"
BG = "#F3F5F8"
BORDER = "#E4E9F0"

APP_QSS = """
* { font-family: "Microsoft YaHei UI", "Segoe UI", "PingFang SC", sans-serif; }
QWidget { font-size: 9pt; color: #1F2937; }
QMainWindow, QDialog { background: %BG%; }

/* 工具栏 */
QToolBar { background: #FFFFFF; border: none; border-bottom: 1px solid %BORDER%;
           spacing: 4px; padding: 5px 10px; }
QToolBar QToolButton { color: #3A4456; padding: 3px 12px; border-radius: 6px;
                       font-size: 9pt; }
QToolBar QToolButton:hover { background: #EEF3FA; }
QToolBar QToolButton:pressed, QToolBar QToolButton:checked { background: #DCEBFF; }
QToolBar QToolButton:disabled { color: #B6BFCC; }
QToolBar::separator { width: 1px; background: %BORDER%; margin: 4px 8px; }

/* 任务表 */
QTableView { background: #FFFFFF; border: 1px solid %BORDER%; border-radius: 6px;
             gridline-color: #EEF1F5; selection-background-color: #DCEBFF;
             selection-color: #1F2937; alternate-background-color: #FAFBFD; }
QTableView::item { padding: 2px 4px; }
QTableView::item:selected { background: #DCEBFF; }
QHeaderView::section { background: #F7F9FC; color: #5B6B7F; border: none;
                       border-right: 1px solid #EEF1F5; border-bottom: 1px solid %BORDER%;
                       padding: 7px 8px; font-weight: 600; }
QTableCornerButton::section { background: #F7F9FC; border: none;
                              border-bottom: 1px solid %BORDER%; }

/* 左侧分类 */
QListWidget#CategoryList { background: #FFFFFF; border: 1px solid %BORDER%;
                           border-radius: 6px; outline: none; font-size: 9.5pt; }
QListWidget#CategoryList::item { height: 34px; padding-left: 12px; border: none; }
QListWidget#CategoryList::item:hover { background: #F2F6FC; }
QListWidget#CategoryList::item:selected { background: #E3EFFE; color: #1456C0;
                                          border-left: 3px solid %PRIMARY%;
                                          padding-left: 9px; font-weight: 600; }

/* 进度条 */
QProgressBar { background: #E8ECF1; border: none; border-radius: 5px;
               text-align: center; color: #374151; }
QProgressBar::chunk { background: %SUCCESS%; border-radius: 5px; }

/* 按钮 */
QPushButton { background: #FFFFFF; border: 1px solid #D0D7E2; border-radius: 5px;
              padding: 6px 16px; color: #1F2937; }
QPushButton:hover { border-color: #9DB8E0; background: #F5F9FF; }
QPushButton:pressed { background: #E7F0FE; }
QPushButton:disabled { color: #B6BFCC; border-color: #E4E9F0; background: #F7F9FC; }
QPushButton[primary="true"] { background: %PRIMARY%; border: 1px solid %PRIMARY%;
                              color: white; font-weight: 600; }
QPushButton[primary="true"]:hover { background: #4A90FF; border-color: #4A90FF; }
QPushButton[primary="true"]:pressed { background: #1E6FE0; }

/* 输入控件 */
QLineEdit, QSpinBox, QComboBox { background: #FFFFFF; border: 1px solid #D0D7E2;
                                 border-radius: 5px; padding: 4px 8px;
                                 selection-background-color: %PRIMARY%; }
QLineEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid %PRIMARY%; }
QSpinBox::up-button, QSpinBox::down-button { width: 16px; border: none;
                                             background: transparent; }

/* 分组框 */
QGroupBox { background: #FFFFFF; border: 1px solid %BORDER%; border-radius: 6px;
            margin-top: 12px; padding: 12px 10px 10px 10px; font-weight: 600; }
QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 4px;
                   color: #3A4456; }
QCheckBox { spacing: 8px; background: transparent; }
QLabel { background: transparent; }

/* 菜单 */
QMenuBar { background: #FFFFFF; border-bottom: 1px solid %BORDER%; }
QMenuBar::item { padding: 5px 12px; background: transparent; }
QMenuBar::item:selected { background: #EAF2FE; border-radius: 4px; }
QMenu { background: #FFFFFF; border: 1px solid #D9E0EA; border-radius: 6px;
        padding: 4px; }
QMenu::item { padding: 6px 24px 6px 16px; border-radius: 4px; }
QMenu::item:selected { background: #E3EFFE; }
QMenu::separator { height: 1px; background: %BORDER%; margin: 4px 6px; }

/* 状态栏 */
QStatusBar { background: #FFFFFF; border-top: 1px solid %BORDER%; color: #5B6B7F; }
QStatusBar QLabel { padding: 0 10px; }

QToolTip { background: #1F2937; color: white; border: none; padding: 4px 8px;
           border-radius: 4px; }

/* 滚动条 */
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: #CBD3DE; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: #9AA7B8; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: #CBD3DE; border-radius: 4px; min-width: 30px; }
QScrollBar::handle:horizontal:hover { background: #9AA7B8; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

QDialogButtonBox QPushButton { min-width: 72px; }
""".replace("%BG%", BG).replace("%BORDER%", BORDER).replace("%PRIMARY%", PRIMARY)


def apply_theme(app) -> None:
    app.setStyleSheet(APP_QSS)
