"""图形控制器的脚本入口。

直接运行本文件时，Python 会导入真正的界面入口 ``go2_safe_control.app.main``。
把入口保持得很短，可以避免启动文件混入业务逻辑。
"""

from go2_safe_control.app import main


if __name__ == "__main__":
    # 只有“直接运行 main.py”时才执行；被别的文件 import 时不会自动弹窗。
    main()
