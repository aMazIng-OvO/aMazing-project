# Python 打包为 EXE

一个基于 PySide6 的 Windows 图形界面工具，通过 PyInstaller 将 Python 脚本打包成 EXE。

## 安装和运行

建议使用 Python 3.10 或更高版本，并在项目目录中运行：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

## 使用

### Python 打包

1. 选择要打包的 `.py` 文件和 EXE 输出目录。
2. 可选设置 EXE 名称和 `.ico` 图标。
3. 选择单文件或目录模式，以及是否隐藏控制台窗口。
4. 点击“开始打包”，构建日志会显示在窗口下方。

生成的程序位于指定的输出目录。被打包的程序如果依赖额外数据文件或动态导入模块，可能还需要在 PyInstaller 命令中添加相应配置。

### Python 环境

- 查看当前运行本工具的 Python 解释器、版本和已安装包。
- 点击“刷新解释器”发现本机已安装的 Python，然后在“包安装目标”中选择环境。
- 输入模块名（例如 `numpy`）并搜索可用版本，选择版本后安装到当前选定的解释器；也可以使用版本约束安装，或卸载包。
- “添加到 PATH”会将所选解释器目录及其 `Scripts` 目录写入当前 Windows 用户的 PATH。应用前会要求确认，已打开的终端需重开后生效。
- 可选安装 Python 2.7.18、3.8–3.14 或最新稳定版。Python 2.7.18 从 python.org 下载与本程序位数匹配的 MSI 并启动安装；其他版本通过 Windows Python Install Manager（命令 `py`）安装。以上操作会修改本机环境，需联网；pip 操作只影响当前选择的解释器。
- 环境页支持滚动查看完整控件；提示、确认等弹窗使用高对比文字和按钮样式。

窗口使用固定尺寸、圆角无边框标题栏；可拖动标题栏移动，点击“×”关闭或点击“—”最小化。

### 图片转 BIN

支持 PNG、JPEG、BMP、TIFF 和 GIF。可选无文件头的原始像素格式：

- **1 位黑白**：每行独立按字节对齐，最高位先写，`1` 为白、`0` 为黑；不足一字节的行尾补 0，可设置黑白阈值。
- **8 位灰度**：每像素一个灰度字节，不额外填充。
- **16 位 RGB565**：红 5 位、绿 6 位、蓝 5 位，小端序（低字节先写），适用于使用相同字节序的 Pico/TFT 驱动。
- **24 位 RGB888**：每像素按 R、G、B 三字节排列。

透明像素会合成到黑色背景。静态图片生成指定的 `.bin` 和 `_preview.png`；动画 GIF 逐帧生成 `*_frame_0000.bin`、对应 PNG 预览和 JSON 帧时长/尺寸说明。Pico 端按帧顺序读取并使用 `duration_ms` 播放，循环策略由驱动程序控制。TFT 驱动对 RGB565 的字节序要求可能不同，请按驱动 API 确认小端字节序是否适用。

## 将本工具自身打包

安装依赖后，可以在 PowerShell 中运行：

```powershell
python -m PyInstaller --noconfirm --clean --onefile --windowed main.py
```

生成的 `main.exe` 位于 `dist` 目录。
