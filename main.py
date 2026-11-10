"""图形控制器的脚本入口。

直接运行本文件时，Python 会导入真正的界面入口 ``go2_safe_control.app.main``。
把入口保持得很短，可以避免启动文件混入业务逻辑。
"""

import sys
import traceback
from pathlib import Path

from go2_safe_control.app import main


def _check_person_runtime(report_path: Path, image_path: Path | None = None) -> int:
    """在冻结版用真人测试帧跑完 YOLO、站姿过滤，不能只验证模型加载。"""

    try:
        from PIL import Image
        from go2_safe_control.person_detection import StandingPersonDetector, default_model_path
        from go2_safe_control.session import VideoFrameData, video_processing_size

        fixture = image_path or Path(__file__).resolve().parent / "tests" / "fixtures" / "standing_person_cc0.jpg"
        with Image.open(fixture) as source:
            rgb = source.convert("RGB")
        width, height = video_processing_size(*rgb.size)
        if (width, height) != rgb.size:
            rgb = rgb.resize((width, height), Image.Resampling.BILINEAR)
        frame = VideoFrameData(width, height, rgb.tobytes())

        detector = StandingPersonDetector(default_model_path())
        try:
            boxes = detector.detect(frame)
        finally:
            detector.close()
        if not boxes:
            raise RuntimeError("模型能加载，但测试帧中的站立人员未被识别")
        report_path.write_text(f"OK standing={len(boxes)} size={width}x{height}\n", encoding="utf-8")
        return 0
    except Exception:
        report_path.write_text(traceback.format_exc(), encoding="utf-8")
        return 1


if __name__ == "__main__":
    # 只有“直接运行 main.py”时才执行；被别的文件 import 时不会自动弹窗。
    if len(sys.argv) == 3 and sys.argv[1] == "--check-person-runtime":
        raise SystemExit(_check_person_runtime(Path(sys.argv[2])))
    main()
