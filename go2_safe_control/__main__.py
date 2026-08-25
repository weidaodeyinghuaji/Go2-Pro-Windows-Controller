"""支持 ``python -m go2_safe_control`` 的包入口。

Python 使用 ``-m 包名`` 启动时，会自动寻找并执行包内的 ``__main__.py``。
"""

# 点号表示从当前包导入；这里最终仍然进入 app.py 的 main()。
from .app import main


if __name__ == "__main__":
    # 只有执行 python -m go2_safe_control 时，本文件的 __name__ 才是 __main__。
    main()
