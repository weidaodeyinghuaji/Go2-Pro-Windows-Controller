# Python 小白系统学习指南（结合本项目）

这份指南不要求你先把 Python 全学完。我们直接使用这个真实项目，把语法、程序结构、界面、网络和测试逐层拆开学习。

## 1. 这个项目是不是 Python 写的

是。核心程序全部是 Python：

```text
main.py                         最短的脚本入口
go2_safe_control/app.py         Tkinter 图形界面
go2_safe_control/safety.py      速度和安全规则
go2_safe_control/protocol.py    Go2 API 编号与消息格式
go2_safe_control/session.py     WebRTC 后台通信
go2_safe_control/workflow.py    自动流程
go2_safe_control/windows_input.py  Windows 输入法控制
tests/test_*.py                 离线测试
```

项目里的其他文件有不同用途：

- `.py`：Python 源代码。
- `.bat`、`.cmd`：Windows 启动脚本，负责进入正确目录并选择正确的 Python。
- `.md`：说明文档，不会控制机器人。
- `.xml`：PyCharm 项目配置。
- `.json`：保存自动流程的数据格式。

## 2. 为什么能“一键启动”

双击启动不是把 Python 变成了 EXE，而是 Windows 脚本替你执行了命令：

```text
双击 02_启动安全控制器_双击.bat
    ↓
scripts/start.cmd
    ↓
.venv/Scripts/python.exe main.py
    ↓
from go2_safe_control.app import main
    ↓
app.main() 创建窗口
    ↓
root.mainloop() 持续处理按钮、键盘和画面
```

你自己在 PowerShell 中运行下面的命令，效果相同：

```powershell
cd "D:\你的项目路径\Go2 Controller"
.\.venv\Scripts\python.exe -m go2_safe_control
```

`python -m go2_safe_control` 的意思是：把 `go2_safe_control` 当作一个 Python 包启动，Python 会自动执行里面的 `__main__.py`。

## 3. PyCharm 为什么会报红

最常见的原因是 PyCharm 保存的解释器仍指向项目移动前的旧目录，例如：

```text
D:\旧项目路径\Go2 Controller\.venv\Scripts\python.exe
```

但现在项目已经移动到：

```text
D:\你的项目路径\Go2 Controller
```

当前真正可用的解释器是：

```text
D:\你的项目路径\Go2 Controller\.venv\Scripts\python.exe
```

解释器选错后，PyCharm 不知道去哪里找 `unitree_webrtc_connect`，于是 import 会显示红色。程序能在批处理里启动、PyCharm 却报红，通常就是两边使用了不同的 Python。

### 在 PyCharm 中修复

1. 用 PyCharm 打开包含 `go2_safe_control` 和 `requirements.txt` 的项目目录：

   ```text
   D:\你的项目路径\Go2 Controller
   ```

2. 打开 `File → Settings → Project → Python Interpreter`。
3. 点击 `Add Interpreter → Add Local Interpreter → Existing`。
4. 选择当前项目的：

   ```text
   .venv\Scripts\python.exe
   ```

5. 应用后等待 PyCharm 完成 `Indexing`。
6. 如果 `go2_safe_control` 仍然报红，右键项目根目录，选择 `Mark Directory as → Sources Root`。

不要选择系统 Python 3.14，也不要选择旧“通用分享版”的 `.venv`。

### 为什么不能直接运行每一个文件

`protocol.py`、`session.py` 等是包内模块，其中存在这样的相对导入：

```python
from .safety import Velocity
```

开头的点表示“从当前包中导入”。如果把 `session.py` 单独当脚本运行，它不知道自己属于哪个包，可能出现：

```text
ImportError: attempted relative import with no known parent package
```

这不是该文件损坏。正确做法是运行 `main.py`、运行模块 `go2_safe_control`，或运行测试。

## 4. 在 PyCharm 中怎样运行

### 运行界面

打开 `main.py`，右键编辑区，选择 `Run 'main'`。运行配置应满足：

```text
Script path: ...\机器狗安全运动控制器\main.py
Working directory: ...\机器狗安全运动控制器
Python interpreter: ...\.venv\Scripts\python.exe
```

### 运行全部离线测试

在 PyCharm Terminal 中运行：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

离线测试不会连接机器狗，也不会发送动作命令，适合学习和修改代码时使用。

## 5. 第一阶段：重新认识 Python 语法

先读 `safety.py`。它没有界面和网络，最容易理解。

### 变量和对象

```python
speed = 0.30
velocity = Velocity(0.30, 0.0, 0.0)
```

第一行把小数保存到变量；第二行创建一个 `Velocity` 对象。

### 函数

```python
def watchdog_velocity(desired, last_update, now, timeout):
    ...
```

函数把一段逻辑命名，参数是输入，`return` 是输出。

### 条件判断

```python
if now - last_update > timeout:
    return Velocity.zero()
```

意思是：如果最后一次速度更新时间太久，就返回零速度。

### 类

```python
class SafetyPolicy:
    ...
```

类是“数据和相关行为的组合模板”。`SafetyPolicy` 保存武装状态，同时负责计算允许发送的速度。

### dataclass

```python
@dataclass(frozen=True)
class Velocity:
    forward: float
    lateral: float
    yaw: float
```

`@dataclass` 自动生成初始化、比较和显示等常用代码；`frozen=True` 表示创建后不能原地修改，减少运动状态被意外改变的风险。

### 类型标注

```python
def active(self, *, now: float) -> set[str]:
```

它表示 `now` 期望是小数，返回值是字符串集合。类型标注主要帮助人和 PyCharm 理解代码，Python 运行时仍需要必要的输入校验。

## 6. 第二阶段：看懂模块之间的关系

推荐阅读顺序：

```text
safety.py
  ↓
protocol.py
  ↓
workflow.py
  ↓
session.py
  ↓
app.py
```

不要一开始从最大的 `app.py` 第一行看到最后一行。

### safety.py

学习目标：变量、函数、类、dataclass、集合、条件判断。

### protocol.py

学习目标：字典、常量、异常，以及“程序内部动作名如何变成 API 请求”。

### workflow.py

学习目标：列表、循环、JSON、数据校验、状态机和纯逻辑测试。

### session.py

学习目标：线程、`async/await`、锁、网络请求和回调。它是进阶模块，不需要第一次就全部看懂。

### app.py

学习目标：Tkinter 控件、事件绑定、界面状态以及模块协作。先从 `_connect()`、`_prepare_and_arm()`、`_emergency_stop()`、`_tick()` 四个方法读起。

## 7. 第三阶段：看懂一次“前进”

```text
用户点击 W 前进
→ app.py 记录方向
→ SafetyPolicy 检查连接、焦点和武装
→ 生成 Velocity
→ RobotSession.update_velocity()
→ 后台控制循环约每 0.1 秒读取速度
→ protocol.move_options() 生成 API 1008 请求
→ WebRTC data channel 发给 Go2
```

停止时路径相反：清空方向、目标速度归零并发送 `StopMove(1003)`。

## 8. 第四阶段：理解 async、线程和回调

- **主线程**：运行 Tkinter。按钮和界面更新都在这里完成。
- **后台线程**：运行 asyncio 和 WebRTC，避免网络等待卡住界面。
- **协程**：使用 `async def` 定义，可以在 `await` 时让事件循环处理其他任务。
- **回调**：把一个函数交给别的模块，等事件发生时再调用。
- **锁**：保证多个动作不会同时拼命向机器人发送互相冲突的命令。

初学阶段不要在机器人已连接或移动时使用断点暂停后台线程，因为暂停程序也可能暂停软件停止逻辑。

## 9. 注释应该怎样读

项目现在使用两种注释：

```python
# 单行注释：解释下一小段代码为什么这样做。
```

```python
def example():
    """文档字符串：解释函数负责什么、输入输出是什么。"""
```

注释不会解释每个括号和每次赋值，那会让代码更难读。注释主要解释模块职责、安全原因、线程边界和不直观的写法。

## 10. 建议的系统学习计划

### 第 1 周：Python 基础恢复

- 第 1 天：变量、数字、字符串、布尔值
- 第 2 天：`if`、`for`、`while`
- 第 3 天：函数、参数、返回值
- 第 4 天：列表、字典、集合、元组
- 第 5 天：类、对象、dataclass
- 第 6 天：异常、文件、JSON
- 第 7 天：阅读并修改 `safety.py` 的离线测试

### 第 2 周：读懂这个项目

- 依次阅读 `safety.py`、`protocol.py`、`workflow.py`
- 每看完一个函数，先用自己的话写一句“输入是什么、输出是什么”
- 运行对应单元测试
- 只改界面文字或测试数据，不连接真机

### 第 3 周：界面和网络基础

- 学习 Tkinter 控件和事件
- 理解主线程为什么不能被网络阻塞
- 学习 `async def`、`await`、事件循环
- 画出连接、移动和急停三条调用链

### 第 4 周：安全地做一个小功能

- 先写离线测试
- 再修改纯逻辑
- 运行全部测试
- 最后才进行有人持实体遥控器保护的真机验证

## 11. 你的第一个练习

先不要修改机器人动作。打开 `safety.py`，尝试回答：

1. `Velocity.zero()` 返回什么？
2. 为什么 `SafetyPolicy` 未武装时只能返回零速度？
3. `W` 和 `S` 同时按下，前后速度是多少？
4. `watchdog_velocity()` 解决了什么危险？

能不用运行代码回答前三题，再通过 `tests/test_safety_policy.py` 验证，就是第一阶段的学习成果。

## 12. 学习时的安全边界

- 学语法和调试时优先运行离线测试，不连接 Go2。
- 不要在已武装或运动中使用 PyCharm 断点。
- 不要随意删除 `StopMove`、看门狗、焦点停止或武装检查。
- 不确定一段代码的作用时，先搜索它在哪里被调用，再修改。
- 真机验证必须有人拿实体遥控器，并从最低风险动作开始。
