from __future__ import annotations

import json
import locale
import math
import os
import re
import shutil
import struct
import sys
import time
from pathlib import Path

from PySide6.QtCore import (
    QPoint,
    QPointF,
    QProcess,
    QPropertyAnimation,
    QTimer,
    Qt,
)
from PySide6.QtGui import (
    QColor,
    QImage,
    QImageReader,
    QGuiApplication,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QTextCursor,
)
from PySide6.QtGui import QRegion
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)


INVALID_NAME_CHARACTERS = re.compile(r'[<>:"/\\|?*]')


class StartupSplash(QWidget):
    CUBE_VERTICES = (
        (-1, -1, -1),
        (1, -1, -1),
        (1, 1, -1),
        (-1, 1, -1),
        (-1, -1, 1),
        (1, -1, 1),
        (1, 1, 1),
        (-1, 1, 1),
    )
    CUBE_EDGES = (
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    )

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.SplashScreen | Qt.WindowType.FramelessWindowHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(440, 290)
        self.started_at = time.monotonic()
        self.fade_animation: QPropertyAnimation | None = None
        self.animation_timer = QTimer(self)
        self.animation_timer.setInterval(16)
        self.animation_timer.timeout.connect(self.update)
        self.animation_timer.start()

        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.move(screen.availableGeometry().center() - self.rect().center())

    def paintEvent(self, event: object) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bounds = self.rect().adjusted(1, 1, -1, -1)
        surface = QPainterPath()
        surface.addRoundedRect(bounds, 16, 16)
        painter.fillPath(surface, QColor("#f7f9fc"))

        elapsed = time.monotonic() - self.started_at
        center_x = self.width() * 0.5
        self._draw_cube(painter, elapsed, center_x, 94.0)

        painter.setPen(QColor("#26384a"))
        title_font = painter.font()
        title_font.setFamily("Microsoft YaHei UI")
        title_font.setPointSize(14)
        title_font.setBold(True)
        painter.setFont(title_font)
        painter.drawText(
            0, 177, self.width(), 28, Qt.AlignmentFlag.AlignCenter, "Python 工具箱"
        )

        painter.setPen(QColor("#6b7c8f"))
        subtitle_font = painter.font()
        subtitle_font.setPointSize(9)
        subtitle_font.setBold(False)
        painter.setFont(subtitle_font)
        painter.drawText(
            0,
            207,
            self.width(),
            22,
            Qt.AlignmentFlag.AlignCenter,
            "正在启动",
        )

        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(3):
            phase = (elapsed * 4 - index * 0.8) % (2 * math.pi)
            alpha = 80 + int(120 * (0.5 + 0.5 * math.sin(phase)))
            painter.setBrush(QColor(23, 105, 170, alpha))
            painter.drawEllipse(QPointF(center_x + (index - 1) * 14, 244), 3, 3)

        painter.setPen(QPen(QColor("#dce4ee"), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(surface)
        painter.end()

    def closeEvent(self, event: object) -> None:
        self.animation_timer.stop()
        super().closeEvent(event)

    @classmethod
    def _draw_cube(
        cls, painter: QPainter, elapsed: float, center_x: float, center_y: float
    ) -> None:
        angle_y = elapsed * 0.8
        angle_x = elapsed * 0.5
        scale = 43.0
        projected: list[QPointF] = []
        depths: list[float] = []

        for x, y, z in cls.CUBE_VERTICES:
            x1 = x * math.cos(angle_y) + z * math.sin(angle_y)
            z1 = -x * math.sin(angle_y) + z * math.cos(angle_y)
            y2 = y * math.cos(angle_x) - z1 * math.sin(angle_x)
            z2 = y * math.sin(angle_x) + z1 * math.cos(angle_x)
            perspective = 3.6 / (3.6 + z2)
            projected.append(
                QPointF(
                    center_x + x1 * scale * perspective,
                    center_y + y2 * scale * perspective,
                )
            )
            depths.append(z2)

        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for start, end in cls.CUBE_EDGES:
            is_far_edge = (depths[start] + depths[end]) / 2 > 0
            color = QColor("#9eb9d2") if is_far_edge else QColor("#1769aa")
            width = 1.5 if is_far_edge else 2.1
            painter.setPen(
                QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
            )
            painter.drawLine(projected[start], projected[end])
        painter.restore()


def parse_interpreter_paths(output: str) -> list[str]:
    """Extract interpreter paths from `py list --format=exe` output."""
    paths: list[str] = []
    for line in output.splitlines():
        candidate = line.strip().strip('"')
        executable_name = Path(candidate).name.lower()
        is_python = re.fullmatch(
            r"(?:pythonw?|python\d+(?:\.\d+)*|pypy\d*)\.exe", executable_name
        )
        if is_python and candidate not in paths:
            paths.append(candidate)
    return paths


def parse_available_package_versions(output: str) -> list[str]:
    match = re.search(r"(?im)^Available versions:\s*(.+)$", output)
    if not match:
        return []
    return [version.strip() for version in match.group(1).split(",") if version.strip()]


class CustomTitleBar(QWidget):
    def __init__(self, window: QMainWindow) -> None:
        super().__init__(window)
        self.window = window
        self._drag_offset: QPoint | None = None
        self.setObjectName("titleBar")
        self.setFixedHeight(42)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 0, 8, 0)
        layout.setSpacing(8)
        title = QLabel("Python 工具箱")
        title.setObjectName("windowTitle")
        title.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(title)
        layout.addStretch()

        minimize_button = QPushButton("—")
        minimize_button.setObjectName("titleBarButton")
        minimize_button.setToolTip("最小化")
        minimize_button.clicked.connect(window.showMinimized)
        layout.addWidget(minimize_button)

        close_button = QPushButton("×")
        close_button.setObjectName("closeButton")
        close_button.setToolTip("关闭")
        close_button.clicked.connect(window.close)
        layout.addWidget(close_button)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        super().mouseReleaseEvent(event)


def build_arguments(
    source: Path,
    output: Path,
    name: str,
    one_file: bool,
    hide_console: bool,
    icon: Path | None,
) -> list[str]:
    """Create the PyInstaller arguments without invoking a shell."""
    arguments = [
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--name",
        name,
        "--distpath",
        str(output),
        "--workpath",
        str(output / ".build"),
        "--specpath",
        str(output),
        "--onefile" if one_file else "--onedir",
        "--windowed" if hide_console else "--console",
    ]
    if icon is not None:
        arguments.extend(["--icon", str(icon)])
    arguments.append(str(source))
    return arguments


def encode_monochrome(image: QImage, threshold: int) -> tuple[bytes, QImage]:
    return encode_image(image, "mono1", threshold)


def encode_image(
    image: QImage, pixel_format: str, threshold: int = 127
) -> tuple[bytes, QImage]:
    """Encode a frame as raw pixels, compositing transparency onto black."""
    width, height = image.width(), image.height()
    binary_data = bytearray()
    preview = QImage(width, height, QImage.Format.Format_RGB888)

    for y in range(height):
        row = bytearray()
        packed_byte = 0
        for x in range(width):
            color = image.pixelColor(x, y)
            alpha = color.alpha()
            red = color.red() * alpha // 255
            green = color.green() * alpha // 255
            blue = color.blue() * alpha // 255
            preview.setPixelColor(
                x,
                y,
                QColor(red, green, blue),
            )
            if pixel_format == "mono1":
                gray = (red * 299 + green * 587 + blue * 114) // 1000
                packed_byte = (packed_byte << 1) | int(gray >= threshold)
                if x % 8 == 7 or x == width - 1:
                    row.append(packed_byte << (7 - x % 8))
                    packed_byte = 0
            elif pixel_format == "gray8":
                row.append((red * 299 + green * 587 + blue * 114) // 1000)
            elif pixel_format == "rgb565le":
                value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
                row.extend(struct.pack("<H", value))
            elif pixel_format == "rgb888":
                row.extend((red, green, blue))
            else:
                raise ValueError(f"Unsupported pixel format: {pixel_format}")
        binary_data.extend(row)
    return bytes(binary_data), preview


class PyInstallerGui(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Python 打包工具")
        self.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFixedSize(900, 650)
        rounded_path = QPainterPath()
        rounded_path.addRoundedRect(self.rect(), 16, 16)
        self.setMask(QRegion(rounded_path.toFillPolygon().toPolygon()))
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.cancel_requested = False
        self.tool_process = QProcess(self)
        self.tool_process.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.tool_action = ""
        self.tool_output_buffer = ""
        self.startup_animation: QPropertyAnimation | None = None
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Microsoft YaHei UI";
                font-size: 10pt;
                color: #26384a;
            }
            QWidget#appSurface {
                background: #f7f9fc; border: 1px solid #c8d3e0;
                border-radius: 14px;
            }
            QWidget#titleBar { background: #f7f9fc; border-top-left-radius: 14px; border-top-right-radius: 14px; }
            QLabel#windowTitle { font-size: 11pt; font-weight: 700; color: #24364b; }
            QPushButton#titleBarButton, QPushButton#closeButton {
                min-width: 38px; min-height: 28px; max-height: 28px;
                padding: 0; border: none; border-radius: 7px;
                background: transparent; color: #33465b; font-size: 13pt;
            }
            QPushButton#titleBarButton:hover { background: #e5ebf2; }
            QPushButton#closeButton:hover { background: #d9363e; color: white; }
            QLineEdit, QComboBox, QTextEdit {
                color: #26384a; background: #ffffff;
                selection-color: #ffffff; selection-background-color: #1769aa;
                border: 1px solid #b8c4d1; border-radius: 6px;
            }
            QLineEdit { min-height: 28px; padding: 2px 6px; }
            QComboBox { min-height: 30px; padding: 2px 8px; }
            QComboBox QAbstractItemView {
                color: #26384a; background: #ffffff;
                selection-color: #ffffff; selection-background-color: #1769aa;
            }
            QPushButton {
                min-height: 30px; padding: 2px 12px;
                color: #26384a; background: #edf2f7;
                border: 1px solid #c5d0dc; border-radius: 6px;
            }
            QPushButton:hover { background: #dce8f4; }
            QPushButton:disabled { color: #7b8794; background: #edf0f3; }
            QTextEdit { padding: 5px; }
            QGroupBox {
                color: #26384a; border: 1px solid #c5d0dc;
                border-radius: 7px; margin-top: 10px; padding-top: 8px;
            }
            QGroupBox::title {
                subcontrol-origin: margin; left: 8px; padding: 0 4px;
                color: #1769aa; background: #f7f9fc; font-weight: 600;
            }
            QTextEdit { font-size: 9pt; }
            QTabWidget::pane {
                background: #f7f9fc; border: 1px solid #8a9bb0; top: -1px;
            }
            QTabBar::tab {
                min-width: 145px; min-height: 42px; padding: 6px 18px;
                margin-right: 4px; font-size: 11pt; font-weight: 700;
                color: #26384a; background: #e5ebf2;
                border: 1px solid #aebdcd; border-bottom: none;
                border-top-left-radius: 7px; border-top-right-radius: 7px;
            }
            QTabBar::tab:selected {
                color: white; background: #1769aa; border-color: #1769aa;
            }
            QTabBar::tab:hover:!selected { background: #d2deeb; }
            """
        )

        self.source_edit = QLineEdit()
        self.source_edit.setPlaceholderText("选择要打包的 .py 文件")
        self.source_button = QPushButton("选择文件")
        self.source_button.clicked.connect(self.choose_source)

        self.output_edit = QLineEdit()
        self.output_edit.setPlaceholderText("选择 EXE 的输出目录")
        self.output_button = QPushButton("选择目录")
        self.output_button.clicked.connect(self.choose_output)

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("留空时使用 Python 文件名")

        self.icon_edit = QLineEdit()
        self.icon_edit.setPlaceholderText("可选，使用 .ico 图标")
        self.icon_button = QPushButton("选择图标")
        self.icon_button.clicked.connect(self.choose_icon)

        self.one_file_check = QCheckBox("单文件 EXE")
        self.one_file_check.setChecked(True)
        self.hide_console_check = QCheckBox("隐藏控制台")
        self.hide_console_check.setChecked(True)
        self.one_file_check.setToolTip("勾选后生成一个 EXE，便于分发；启动速度可能稍慢。")
        self.hide_console_check.setToolTip("适合图形界面程序；命令行工具通常应取消勾选。")

        self.build_button = QPushButton("开始打包")
        self.build_button.setDefault(True)
        self.build_button.clicked.connect(self.start_build)
        self.cancel_button = QPushButton("取消打包")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_build)
        self.status_label = QLabel("请选择 Python 文件。")

        self.log_view = QTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.document().setMaximumBlockCount(10000)
        self.log_view.setPlaceholderText("构建输出...")

        form = QFormLayout()
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)
        form.addRow("Python 文件：", self._with_button(self.source_edit, self.source_button))
        form.addRow("输出目录：", self._with_button(self.output_edit, self.output_button))
        form.addRow("EXE 名称：", self.name_edit)
        form.addRow("应用图标：", self._with_button(self.icon_edit, self.icon_button))

        options_group = QGroupBox("打包选项")
        options_layout = QHBoxLayout(options_group)
        options_layout.addWidget(self.one_file_check)
        options_layout.addWidget(self.hide_console_check)
        options_layout.addStretch()

        actions = QHBoxLayout()
        actions.addWidget(self.build_button)
        actions.addWidget(self.cancel_button)
        actions.addSpacing(12)
        actions.addWidget(self.status_label, 1)

        packaging_page = QWidget()
        central_layout = QVBoxLayout(packaging_page)
        central_layout.setContentsMargins(20, 18, 20, 18)
        central_layout.setSpacing(12)
        heading = QLabel("Python 打包工具")
        heading.setStyleSheet("font-size: 16pt; font-weight: 600;")
        central_layout.addWidget(heading)
        central_layout.addLayout(form)
        central_layout.addWidget(options_group)
        central_layout.addLayout(actions)
        central_layout.addWidget(QLabel("构建日志："))
        central_layout.addWidget(self.log_view, 1)

        tabs = QTabWidget()
        tabs.addTab(packaging_page, "Python 打包")
        tabs.addTab(self._create_environment_tab(), "Python 环境")
        tabs.addTab(self._create_image_tab(), "图片转 BIN")
        surface = QWidget()
        surface.setObjectName("appSurface")
        surface_layout = QVBoxLayout(surface)
        surface_layout.setContentsMargins(1, 1, 1, 1)
        surface_layout.setSpacing(0)
        surface_layout.addWidget(CustomTitleBar(self))
        surface_layout.addWidget(tabs, 1)
        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(6, 6, 6, 6)
        outer_layout.addWidget(surface)
        self.setCentralWidget(outer)

        self.process.readyReadStandardOutput.connect(self.read_process_output)
        self.process.finished.connect(self.build_finished)
        self.process.errorOccurred.connect(self.process_error)
        self.tool_process.readyReadStandardOutput.connect(self.read_tool_output)
        self.tool_process.finished.connect(self.tool_finished)
        self.tool_process.errorOccurred.connect(self.tool_process_error)

    def _create_environment_tab(self) -> QWidget:
        page = QWidget()
        page.setObjectName("environmentPage")
        page.setStyleSheet("background-color: #f7f9fc; color: #26384a;")
        layout = QVBoxLayout(page)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        content = QWidget()
        content.setObjectName("environmentContent")
        content.setStyleSheet("background-color: #f7f9fc; color: #26384a;")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(12, 10, 12, 12)
        content_layout.setSpacing(10)

        info_group = QGroupBox("当前 Python 环境")
        info_layout = QVBoxLayout(info_group)
        self.environment_label = QLabel(
            f"解释器：{sys.executable}\n版本：{sys.version.split()[0]}"
        )
        self.environment_label.setWordWrap(True)
        info_layout.addWidget(self.environment_label)
        interpreter_row = QHBoxLayout()
        self.interpreter_combo = QComboBox()
        self.interpreter_combo.addItem(
            f"当前环境：{sys.executable}", sys.executable
        )
        self.refresh_interpreters_button = QPushButton("刷新解释器")
        self.refresh_interpreters_button.clicked.connect(self.refresh_interpreters)
        self.add_path_button = QPushButton("添加到 PATH")
        self.add_path_button.clicked.connect(self.add_interpreter_to_path)
        interpreter_row.addWidget(QLabel("包安装目标："))
        interpreter_row.addWidget(self.interpreter_combo, 1)
        interpreter_row.addWidget(self.refresh_interpreters_button)
        interpreter_row.addWidget(self.add_path_button)
        info_layout.addLayout(interpreter_row)
        self.refresh_packages_button = QPushButton("查看已安装的包")
        self.refresh_packages_button.clicked.connect(self.list_packages)
        info_layout.addWidget(self.refresh_packages_button)
        content_layout.addWidget(info_group)

        pip_group = QGroupBox("pip 包管理")
        pip_layout = QFormLayout(pip_group)
        self.package_edit = QLineEdit()
        self.package_edit.setPlaceholderText("搜索模块，例如 numpy")
        search_row = QHBoxLayout()
        search_row.addWidget(self.package_edit, 1)
        self.search_package_button = QPushButton("搜索版本")
        self.search_package_button.clicked.connect(self.search_package_versions)
        search_row.addWidget(self.search_package_button)
        pip_layout.addRow("搜索模块：", search_row)
        self.package_version_combo = QComboBox()
        self.package_version_combo.setPlaceholderText("先搜索可用版本")
        pip_layout.addRow("可用版本：", self.package_version_combo)
        package_actions = QHBoxLayout()
        self.install_selected_package_button = QPushButton("安装所选版本")
        self.install_selected_package_button.clicked.connect(
            self.install_selected_package
        )
        self.install_package_button = QPushButton("按版本约束安装")
        self.install_package_button.clicked.connect(self.install_package)
        self.uninstall_package_button = QPushButton("卸载包")
        self.uninstall_package_button.clicked.connect(self.uninstall_package)
        package_actions.addWidget(self.install_selected_package_button)
        package_actions.addWidget(self.install_package_button)
        package_actions.addWidget(self.uninstall_package_button)
        package_actions.addStretch()
        pip_layout.addRow("操作：", package_actions)
        content_layout.addWidget(pip_group)

        python_group = QGroupBox("安装 Python")
        python_layout = QGridLayout(python_group)
        python_layout.setContentsMargins(16, 22, 16, 14)
        python_layout.setHorizontalSpacing(12)
        python_layout.setVerticalSpacing(10)
        self.python_version_combo = QComboBox()
        for label, version in (
            ("Python 2.7.18（旧版）", "2.7"),
            ("Python 3.8", "3.8"),
            ("Python 3.9", "3.9"),
            ("Python 3.10", "3.10"),
            ("Python 3.11", "3.11"),
            ("Python 3.12", "3.12"),
            ("Python 3.13", "3.13"),
            ("Python 3.14", "3.14"),
            ("最新稳定版", "latest"),
        ):
            self.python_version_combo.addItem(label, version)
        self.python_version_combo.setMinimumWidth(280)
        python_layout.addWidget(QLabel("Python 版本："), 0, 0)
        python_layout.addWidget(self.python_version_combo, 0, 1)
        self.install_python_button = QPushButton("下载并安装")
        self.install_python_button.clicked.connect(self.install_python)
        self.install_python_button.setMinimumWidth(150)
        python_layout.addWidget(self.install_python_button, 1, 1, Qt.AlignmentFlag.AlignRight)
        python_layout.setColumnStretch(1, 1)
        content_layout.addWidget(python_group)
        self.environment_status_label = QLabel("就绪。")
        self.environment_status_label.setWordWrap(True)
        content_layout.addWidget(self.environment_status_label)
        environment_help = QLabel(
            "包管理操作只影响当前选择的解释器。安装 Python 需要 Windows "
            "Python Install Manager（py）和网络连接；Python 2.7 使用 python.org "
            "提供的旧版 MSI 安装程序。"
        )
        environment_help.setWordWrap(True)
        content_layout.addWidget(environment_help)

        self.environment_log = QTextEdit()
        self.environment_log.setReadOnly(True)
        self.environment_log.document().setMaximumBlockCount(5000)
        self.environment_log.setPlaceholderText("环境与 pip 操作输出...")
        content_layout.addWidget(self.environment_log, 1)
        scroll_area = QScrollArea()
        scroll_area.setObjectName("environmentScrollArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet(
            "QScrollArea { background-color: #f7f9fc; border: none; }"
        )
        scroll_area.viewport().setStyleSheet("background-color: #f7f9fc;")
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_area.setWidget(content)
        layout.addWidget(scroll_area)
        return page

    def _create_image_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        form = QFormLayout()
        self.image_source_edit = QLineEdit()
        self.image_source_edit.setPlaceholderText("选择要转换的图片")
        source_button = QPushButton("选择图片")
        source_button.clicked.connect(self.choose_image)
        form.addRow("输入图片：", self._with_button(self.image_source_edit, source_button))

        self.image_output_edit = QLineEdit()
        self.image_output_edit.setPlaceholderText("输出 .bin 文件路径")
        output_button = QPushButton("选择输出")
        output_button.clicked.connect(self.choose_image_output)
        form.addRow("输出 BIN：", self._with_button(self.image_output_edit, output_button))

        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 255)
        self.threshold_spin.setValue(127)
        self.threshold_spin.setToolTip("低于阈值的像素转为黑色，其余转为白色。")
        form.addRow("黑白阈值：", self.threshold_spin)
        self.pixel_format_combo = QComboBox()
        self.pixel_format_combo.addItem("1 位黑白（按行字节对齐）", "mono1")
        self.pixel_format_combo.addItem("8 位灰度", "gray8")
        self.pixel_format_combo.addItem("16 位 RGB565（小端，Pico/TFT）", "rgb565le")
        self.pixel_format_combo.addItem("24 位 RGB888（RGB 顺序）", "rgb888")
        self.pixel_format_combo.setCurrentIndex(2)
        self.pixel_format_combo.currentIndexChanged.connect(
            self.update_pixel_format_controls
        )
        form.addRow("像素格式：", self.pixel_format_combo)
        layout.addLayout(form)

        self.image_format_help = QLabel()
        self.image_format_help.setWordWrap(True)
        layout.addWidget(self.image_format_help)
        self.update_pixel_format_controls()
        self.convert_image_button = QPushButton("转换图片")
        self.convert_image_button.clicked.connect(self.convert_image)
        layout.addWidget(self.convert_image_button)
        layout.addStretch()
        return page

    @staticmethod
    def _with_button(edit: QLineEdit, button: QPushButton) -> QWidget:
        container = QWidget()
        row = QHBoxLayout(container)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(edit, 1)
        row.addWidget(button)
        return container

    def choose_source(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self, "选择 Python 文件", "", "Python 文件 (*.py)"
        )
        if not file_name:
            return
        source = Path(file_name)
        self.source_edit.setText(str(source))
        if not self.name_edit.text().strip():
            self.name_edit.setText(source.stem)
        if not self.output_edit.text().strip():
            self.output_edit.setText(str(source.parent / "dist"))

    def choose_output(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "选择输出目录")
        if directory:
            self.output_edit.setText(directory)

    def choose_icon(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self, "选择图标文件", "", "图标文件 (*.ico)"
        )
        if file_name:
            self.icon_edit.setText(file_name)

    def list_packages(self) -> None:
        interpreter = self.selected_interpreter()
        if interpreter is None:
            return
        self.start_tool_process(
            interpreter,
            ["-m", "pip", "list", "--format=columns"],
            "正在读取已安装的包...",
        )

    def refresh_interpreters(self) -> None:
        launcher = shutil.which("py")
        if launcher is None:
            self.environment_log.append(
                "找不到 Python Install Manager 命令 py，无法发现其他解释器。"
            )
            self.environment_status_label.setText("未找到 Python Install Manager。")
            return
        self.start_tool_process(
            launcher,
            ["list", "--format=exe"],
            "正在查找本机 Python 解释器...",
            action="list_interpreters",
        )

    def selected_interpreter(self) -> str | None:
        interpreter = self.interpreter_combo.currentData()
        if not interpreter:
            self.show_validation_error("请选择要操作的 Python 解释器。")
            return None
        return str(interpreter)

    def search_package_versions(self) -> None:
        package_name = self.package_edit.text().strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package_name):
            self.show_validation_error("请输入有效的模块名称，例如 numpy。")
            return
        interpreter = self.selected_interpreter()
        if interpreter is None:
            return
        self.package_version_combo.clear()
        self.start_tool_process(
            interpreter,
            ["-m", "pip", "index", "versions", package_name],
            f"正在搜索 {package_name} 的可用版本...",
            action="search_package_versions",
        )

    def install_selected_package(self) -> None:
        package_name = self.package_edit.text().strip()
        version = self.package_version_combo.currentText().strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", package_name):
            self.show_validation_error("请输入有效的模块名称，例如 numpy。")
            return
        if not version:
            self.show_validation_error("请先搜索并选择一个模块版本。")
            return
        interpreter = self.selected_interpreter()
        if interpreter is None:
            return
        self.start_tool_process(
            interpreter,
            ["-m", "pip", "install", f"{package_name}=={version}"],
            f"正在为所选 Python 环境安装 {package_name} {version}...",
        )

    def add_interpreter_to_path(self) -> None:
        if sys.platform != "win32":
            self.environment_status_label.setText("添加用户 PATH 仅支持 Windows。")
            return
        interpreter = self.selected_interpreter()
        if interpreter is None:
            return
        executable = Path(interpreter).expanduser()
        if not executable.is_file():
            self.show_validation_error(f"解释器文件不存在：{executable}")
            return
        candidates = [executable.parent, executable.parent / "Scripts"]
        additions = [str(path) for path in candidates if path.is_dir()]
        if not additions:
            self.show_validation_error("找不到解释器目录，未修改 PATH。")
            return
        answer = self.show_message_box(
            QMessageBox.Icon.Question,
            "添加到用户 PATH",
            "将以下目录添加到当前 Windows 用户的 PATH 环境变量吗？\n\n"
            + "\n".join(additions),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                "Environment",
                0,
                winreg.KEY_QUERY_VALUE | winreg.KEY_SET_VALUE,
            ) as key:
                try:
                    current_path, value_type = winreg.QueryValueEx(key, "Path")
                except FileNotFoundError:
                    current_path, value_type = "", winreg.REG_EXPAND_SZ
                current_path = str(current_path or "")
                existing = {
                    os.path.normcase(item.strip().strip('"').rstrip("\\"))
                    for item in current_path.split(os.pathsep)
                    if item.strip()
                }
                new_entries = [
                    item
                    for item in additions
                    if os.path.normcase(item.rstrip("\\")) not in existing
                ]
                if not new_entries:
                    self.environment_status_label.setText(
                        "解释器目录已经存在于用户 PATH 中。"
                    )
                    return
                updated_path = os.pathsep.join(
                    [current_path.rstrip(os.pathsep), *new_entries]
                    if current_path
                    else new_entries
                )
                winreg.SetValueEx(
                    key,
                    "Path",
                    0,
                    value_type
                    if value_type in (winreg.REG_SZ, winreg.REG_EXPAND_SZ)
                    else winreg.REG_EXPAND_SZ,
                    updated_path,
                )
            import ctypes

            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
            )
        except OSError as error:
            self.show_message_box(
                QMessageBox.Icon.Warning,
                "添加失败",
                f"无法更新用户 PATH：{error}",
            )
            self.environment_status_label.setText("添加 PATH 失败。")
            return
        self.environment_status_label.setText(
            "已添加到用户 PATH；已打开的终端需要重新打开后生效。"
        )

    def install_package(self) -> None:
        packages = self._package_arguments()
        if packages is None:
            return
        interpreter = self.selected_interpreter()
        if interpreter is None:
            return
        self.start_tool_process(
            interpreter,
            ["-m", "pip", "install", *packages],
            "正在安装包...",
        )

    def uninstall_package(self) -> None:
        packages = self._package_arguments(uninstall=True)
        if packages is None:
            return
        answer = self.show_message_box(
            QMessageBox.Icon.Question,
            "确认卸载",
            f"确定从当前 Python 环境卸载以下包吗？\n{', '.join(packages)}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        interpreter = self.selected_interpreter()
        if interpreter is None:
            return
        self.start_tool_process(
            interpreter,
            ["-m", "pip", "uninstall", "--yes", *packages],
            "正在卸载包...",
        )

    def _package_arguments(self, uninstall: bool = False) -> list[str] | None:
        raw_value = self.package_edit.text().strip()
        if not raw_value:
            self.show_validation_error("请先输入包名称。")
            return None
        packages = [item for item in re.split(r"[,;\s]+", raw_value) if item]
        if uninstall:
            valid = all(
                re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", item)
                for item in packages
            )
        else:
            package_spec = (
                r"[A-Za-z0-9][A-Za-z0-9._-]*"
                r"(?:\[[A-Za-z0-9._,-]+\])?"
                r"(?:\s*(?:===|==|~=|!=|<=|>=|<|>)\s*[A-Za-z0-9.*+!_-]+)?"
            )
            valid = all(re.fullmatch(package_spec, item) for item in packages)
        if not valid:
            self.show_validation_error(
                "包名称格式不正确。多个包请用逗号分隔；安装时可使用如 requests>=2.30 的版本约束。"
            )
            return None
        return packages

    def install_python(self) -> None:
        version = self.python_version_combo.currentData()
        if version == "2.7":
            self.install_python_27()
            return
        launcher = shutil.which("py")
        if launcher is None:
            self.environment_log.append(
                "找不到 Windows Python Install Manager 命令 py。"
                "请先安装官方 Python Install Manager，再重试。"
            )
            self.environment_status_label.setText("未找到 Python Install Manager。")
            return
        requested_version = "3" if version == "latest" else version
        display_version = "最新稳定版" if version == "latest" else version
        self.start_tool_process(
            launcher,
            ["install", requested_version],
            f"正在下载并安装 Python {display_version}...",
        )

    def install_python_27(self) -> None:
        curl = shutil.which("curl.exe") or shutil.which("curl")
        if curl is None:
            self.environment_log.append("找不到 curl，无法下载 Python 2.7.18 安装程序。")
            self.environment_status_label.setText("缺少 curl，无法安装 Python 2.7。")
            return
        installer_name = (
            "python-2.7.18.amd64.msi"
            if sys.maxsize > 2**32
            else "python-2.7.18.msi"
        )
        installer_url = (
            f"https://www.python.org/ftp/python/2.7.18/{installer_name}"
        )
        installer_dir = Path.home() / "Downloads" / "PythonToC"
        installer_path = installer_dir / installer_name
        try:
            installer_dir.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.environment_log.append(f"无法创建安装文件目录：{error}")
            self.environment_status_label.setText("无法准备 Python 2.7 安装文件。")
            return
        self.start_tool_process(
            curl,
            ["--location", "--fail", "--output", str(installer_path), installer_url],
            "正在从 python.org 下载 Python 2.7.18...",
            action="python27_download",
        )

    def start_tool_process(
        self,
        program: str,
        arguments: list[str],
        status: str,
        action: str = "",
    ) -> None:
        if self.tool_process.state() != QProcess.ProcessState.NotRunning:
            self.environment_log.append("另一个环境操作仍在运行，请稍候。")
            return
        self.tool_action = action
        self.tool_output_buffer = ""
        self.environment_log.clear()
        self.environment_log.append(f"命令：{program} {' '.join(arguments)}\n")
        self.environment_status_label.setText(status)
        self._set_tool_running(True)
        self.tool_process.start(program, arguments)

    def read_tool_output(self) -> None:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        output = bytes(self.tool_process.readAllStandardOutput()).decode(
            encoding, errors="replace"
        )
        if output:
            self.tool_output_buffer += output
            self.environment_log.moveCursor(QTextCursor.MoveOperation.End)
            self.environment_log.insertPlainText(output)
            self.environment_log.ensureCursorVisible()

    def tool_finished(
        self, exit_code: int, exit_status: QProcess.ExitStatus
    ) -> None:
        self.read_tool_output()
        succeeded = (
            exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit
        )
        if self.tool_action == "list_interpreters":
            self.tool_action = ""
            if succeeded:
                current = self.interpreter_combo.currentData()
                current_path = str(Path(sys.executable))
                discovered = parse_interpreter_paths(self.tool_output_buffer)
                self.interpreter_combo.clear()
                self.interpreter_combo.addItem(
                    f"当前环境：{sys.executable}", sys.executable
                )
                for path in discovered:
                    if os.path.normcase(path) == os.path.normcase(current_path):
                        continue
                    self.interpreter_combo.addItem(f"Python：{path}", path)
                selected = self.interpreter_combo.findData(current)
                if selected >= 0:
                    self.interpreter_combo.setCurrentIndex(selected)
                self.environment_status_label.setText(
                    f"已找到 {self.interpreter_combo.count()} 个 Python 解释器。"
                )
            else:
                self.environment_status_label.setText(
                    f"查找解释器失败（退出代码：{exit_code}），请查看输出。"
                )
            self._set_tool_running(False)
            return
        if self.tool_action == "search_package_versions":
            self.tool_action = ""
            versions = (
                parse_available_package_versions(self.tool_output_buffer)
                if succeeded
                else []
            )
            self.package_version_combo.clear()
            self.package_version_combo.addItems(versions)
            if versions:
                self.environment_status_label.setText(
                    f"找到 {len(versions)} 个可用版本，请选择后安装。"
                )
            else:
                self.environment_status_label.setText(
                    "没有找到可用版本或搜索失败，请检查模块名、网络和 pip 输出。"
                )
            self._set_tool_running(False)
            return
        if self.tool_action == "python27_download" and succeeded:
            installer_name = (
                "python-2.7.18.amd64.msi"
                if sys.maxsize > 2**32
                else "python-2.7.18.msi"
            )
            installer_path = Path.home() / "Downloads" / "PythonToC" / installer_name
            self.start_tool_process(
                "msiexec.exe",
                ["/i", str(installer_path), "/passive", "/norestart"],
                "正在安装 Python 2.7.18...",
                action="python27_install",
            )
            return
        self.tool_action = ""
        if succeeded:
            self.environment_status_label.setText("操作完成。")
        else:
            self.environment_status_label.setText(
                f"操作失败（退出代码：{exit_code}），请查看输出。"
            )
        self._set_tool_running(False)

    def tool_process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.environment_log.append(
                f"\n无法启动进程：{self.tool_process.errorString()}"
            )
            self.tool_action = ""
            self.environment_status_label.setText("无法启动 Python 环境操作。")
            self._set_tool_running(False)

    def _set_tool_running(self, running: bool) -> None:
        for button in (
            self.refresh_interpreters_button,
            self.refresh_packages_button,
            self.search_package_button,
            self.install_selected_package_button,
            self.install_package_button,
            self.uninstall_package_button,
            self.install_python_button,
            self.add_path_button,
        ):
            button.setEnabled(not running)
        self.interpreter_combo.setEnabled(not running)
        self.package_edit.setEnabled(not running)
        self.package_version_combo.setEnabled(not running)

    def choose_image(self) -> None:
        file_name, _ = QFileDialog.getOpenFileName(
            self,
            "选择图片",
            "",
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff)",
        )
        if not file_name:
            return
        source = Path(file_name)
        self.image_source_edit.setText(str(source))
        if not self.image_output_edit.text().strip():
            self.image_output_edit.setText(str(source.with_suffix(".bin")))

    def choose_image_output(self) -> None:
        initial_path = self.image_output_edit.text().strip()
        if not initial_path and self.image_source_edit.text().strip():
            initial_path = str(Path(self.image_source_edit.text()).with_suffix(".bin"))
        file_name, _ = QFileDialog.getSaveFileName(
            self, "保存 BIN 文件", initial_path, "BIN 文件 (*.bin)"
        )
        if file_name:
            self.image_output_edit.setText(file_name)

    def update_pixel_format_controls(self) -> None:
        is_monochrome = self.pixel_format_combo.currentData() == "mono1"
        self.threshold_spin.setEnabled(is_monochrome)
        descriptions = {
            "mono1": (
                "1 bit/像素；每行按字节对齐，最高位先写，1 为白、0 为黑。"
                "透明区域按黑色处理。"
            ),
            "gray8": "8 bit/像素灰度，无行填充；透明区域按黑色合成。",
            "rgb565le": (
                "16 bit/像素 RGB565，小端字节序（低字节先写），无行填充；"
                "透明区域按黑色合成。"
            ),
            "rgb888": (
                "24 bit/像素 RGB888，字节顺序为 R、G、B，无行填充；"
                "透明区域按黑色合成。"
            ),
        }
        self.image_format_help.setText(
            descriptions[self.pixel_format_combo.currentData()]
            + " GIF 动画会逐帧生成 BIN 和 PNG 预览，JSON 记录每帧时长。"
        )

    def convert_image(self) -> None:
        source_text = self.image_source_edit.text().strip()
        output_text = self.image_output_edit.text().strip()
        if not source_text:
            self.show_validation_error("请先选择要转换的图片。")
            return
        source = Path(source_text).expanduser()
        if not source.is_file():
            self.show_validation_error("所选图片不存在。")
            return
        if not output_text:
            self.show_validation_error("请先指定 BIN 输出路径。")
            return
        output = Path(output_text).expanduser()
        if output.suffix.lower() != ".bin":
            self.show_validation_error("BIN 输出文件扩展名必须为 .bin。")
            return

        reader = QImageReader(str(source))
        reader.setAutoTransform(True)
        animated = reader.supportsAnimation()
        frames: list[tuple[QImage, int]] = []
        while reader.canRead():
            image = reader.read()
            if image.isNull():
                break
            frames.append((image, max(0, reader.nextImageDelay())))
            if not animated:
                break
        if not frames:
            self.show_validation_error("无法读取图片，请检查文件格式或文件是否损坏。")
            return
        if reader.error() != QImageReader.ImageReaderError.UnknownError:
            self.show_validation_error(
                f"读取图片帧时发生错误：{reader.errorString()}"
            )
            return

        pixel_format = self.pixel_format_combo.currentData()
        threshold = self.threshold_spin.value()
        bytes_per_pixel = {
            "mono1": None,
            "gray8": 1,
            "rgb565le": 2,
            "rgb888": 3,
        }[pixel_format]
        format_names = {
            "mono1": "1-bit monochrome, row byte-aligned, MSB first",
            "gray8": "8-bit grayscale",
            "rgb565le": "16-bit RGB565, little-endian",
            "rgb888": "24-bit RGB888, R-G-B byte order",
        }
        frame_records: list[dict[str, object]] = []
        metadata_path = output.with_suffix(".json")
        try:
            output.parent.mkdir(parents=True, exist_ok=True)
            for index, (image, delay_ms) in enumerate(frames):
                if animated:
                    frame_output = output.with_name(
                        f"{output.stem}_frame_{index:04d}{output.suffix}"
                    )
                    preview_path = output.with_name(
                        f"{output.stem}_frame_{index:04d}_preview.png"
                    )
                else:
                    frame_output = output
                    preview_path = output.with_name(f"{output.stem}_preview.png")
                binary_data, preview = encode_image(image, pixel_format, threshold)
                frame_output.write_bytes(binary_data)
                if not preview.save(str(preview_path), "PNG"):
                    raise OSError(f"无法写入预览图片：{preview_path}")
                frame_records.append(
                    {
                        "index": index,
                        "width": image.width(),
                        "height": image.height(),
                        "duration_ms": delay_ms,
                        "bin": frame_output.name,
                        "preview": preview_path.name,
                        "bytes": len(binary_data),
                    }
                )

            metadata: dict[str, object] = {
                "format": format_names[pixel_format],
                "frame_count": len(frame_records),
                "animated": animated,
                "playback": "play frames in order using duration_ms; repeat as desired",
                "transparency": "composited onto black",
                "frames": frame_records,
            }
            if pixel_format == "mono1":
                metadata.update(
                    {
                        "row_alignment": "byte-aligned",
                        "bit_order": "most-significant-bit first",
                        "white_pixel": 1,
                        "black_pixel": 0,
                        "threshold": threshold,
                    }
                )
            elif bytes_per_pixel is not None:
                metadata["bytes_per_pixel"] = bytes_per_pixel
            if pixel_format == "rgb565le":
                metadata["byte_order"] = "little-endian"
            metadata_path.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError as error:
            self.show_message_box(
                QMessageBox.Icon.Warning,
                "转换失败",
                f"无法保存转换结果：{error}",
            )
            return

        self.show_message_box(
            QMessageBox.Icon.Information,
            "转换完成",
            f"已转换 {len(frame_records)} 帧。\nBIN 文件：{output.parent}\n"
            f"尺寸说明：{metadata_path}",
        )

    def start_build(self) -> None:
        source_text = self.source_edit.text().strip()
        output_text = self.output_edit.text().strip()
        name = self.name_edit.text().strip()
        icon_text = self.icon_edit.text().strip()

        if not source_text:
            self.show_validation_error("请先选择要打包的 Python 文件。")
            return
        source = Path(source_text).expanduser()
        if not source.is_file() or source.suffix.lower() != ".py":
            self.show_validation_error("所选文件不存在或不是 .py 文件。")
            return
        if not output_text:
            self.show_validation_error("请先选择 EXE 的输出目录。")
            return
        if not name:
            name = source.stem
            self.name_edit.setText(name)
        if INVALID_NAME_CHARACTERS.search(name) or name in {".", ".."}:
            self.show_validation_error("EXE 名称不能包含 Windows 文件名非法字符。")
            return

        output = Path(output_text).expanduser()
        icon: Path | None = None
        if icon_text:
            icon = Path(icon_text).expanduser()
            if not icon.is_file() or icon.suffix.lower() != ".ico":
                self.show_validation_error("图标文件不存在或不是 .ico 文件。")
                return

        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            self.show_validation_error(f"无法创建输出目录：{error}")
            return

        arguments = build_arguments(
            source,
            output,
            name,
            self.one_file_check.isChecked(),
            self.hide_console_check.isChecked(),
            icon,
        )
        self.log_view.clear()
        self.log_view.append(f"运行命令：{sys.executable} {' '.join(arguments)}")
        self.cancel_requested = False
        self.set_building(True)
        self.status_label.setText("正在打包，请稍候...")
        self.process.setWorkingDirectory(str(source.parent))
        self.process.start(sys.executable, arguments)

    def cancel_build(self) -> None:
        if self.process.state() == QProcess.ProcessState.NotRunning:
            return
        self.cancel_requested = True
        self.status_label.setText("正在取消...")
        self.process.terminate()
        QTimer.singleShot(3000, self.kill_if_still_running)

    def kill_if_still_running(self) -> None:
        if self.cancel_requested and self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.kill()

    def read_process_output(self) -> None:
        encoding = locale.getpreferredencoding(False) or "utf-8"
        output = bytes(self.process.readAllStandardOutput()).decode(
            encoding, errors="replace"
        )
        if output:
            self.log_view.moveCursor(QTextCursor.MoveOperation.End)
            self.log_view.insertPlainText(output)
            self.log_view.ensureCursorVisible()

    def build_finished(self, exit_code: int, exit_status: QProcess.ExitStatus) -> None:
        self.read_process_output()
        if self.cancel_requested:
            self.status_label.setText("打包已取消。")
        elif exit_code == 0 and exit_status == QProcess.ExitStatus.NormalExit:
            self.status_label.setText("打包成功。")
            self.show_message_box(
                QMessageBox.Icon.Information,
                "打包完成",
                f"EXE 已生成到：\n{self.output_edit.text().strip()}",
            )
        else:
            self.status_label.setText(f"打包失败（退出代码：{exit_code}），请查看日志。")
        self.set_building(False)

    def process_error(self, error: QProcess.ProcessError) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.status_label.setText("无法启动打包进程。")
            self.log_view.append(
                f"\n启动失败：{self.process.errorString()}\n"
                "请确认当前 Python 环境已安装 PyInstaller。"
            )
            self.set_building(False)

    def set_building(self, building: bool) -> None:
        for widget in (
            self.source_edit,
            self.source_button,
            self.output_edit,
            self.output_button,
            self.name_edit,
            self.icon_edit,
            self.icon_button,
            self.one_file_check,
            self.hide_console_check,
        ):
            widget.setEnabled(not building)
        self.build_button.setEnabled(not building)
        self.cancel_button.setEnabled(building)

    def show_validation_error(self, message: str) -> None:
        self.show_message_box(QMessageBox.Icon.Warning, "输入有误", message)

    def show_message_box(
        self,
        icon: QMessageBox.Icon,
        title: str,
        message: str,
        buttons: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
        default_button: QMessageBox.StandardButton = QMessageBox.StandardButton.Ok,
    ) -> QMessageBox.StandardButton:
        dialog = QMessageBox(self)
        dialog.setIcon(icon)
        dialog.setWindowTitle(title)
        dialog.setText(message)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setStandardButtons(buttons)
        dialog.setDefaultButton(default_button)
        dialog.setMinimumWidth(420)
        dialog.setStyleSheet(
            """
            QMessageBox {
                background-color: #ffffff;
                color: #172b3f;
            }
            QMessageBox QLabel {
                color: #172b3f;
                background-color: transparent;
                font-size: 10pt;
                min-width: 300px;
            }
            QMessageBox QPushButton {
                color: #ffffff;
                background-color: #1769aa;
                border: 1px solid #12578f;
                border-radius: 6px;
                min-width: 88px;
                min-height: 34px;
                padding: 4px 14px;
            }
            QMessageBox QPushButton:hover {
                background-color: #12578f;
            }
            QMessageBox QPushButton:pressed {
                background-color: #0d426e;
            }
            """
        )
        return QMessageBox.StandardButton(dialog.exec())


def main() -> int:
    app = QApplication(sys.argv)
    window = PyInstallerGui()
    splash = StartupSplash()
    window.setWindowOpacity(0.0)
    splash.show()

    def reveal_main_window() -> None:
        window.show()
        window.startup_animation = QPropertyAnimation(window, b"windowOpacity", window)
        window.startup_animation.setDuration(500)
        window.startup_animation.setStartValue(0.0)
        window.startup_animation.setEndValue(1.0)
        window.startup_animation.start()

        splash.fade_animation = QPropertyAnimation(
            splash, b"windowOpacity", splash
        )
        splash.fade_animation.setDuration(350)
        splash.fade_animation.setStartValue(1.0)
        splash.fade_animation.setEndValue(0.0)
        splash.fade_animation.finished.connect(splash.close)
        splash.fade_animation.start()

    QTimer.singleShot(1700, reveal_main_window)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
