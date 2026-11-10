# 人员识别模型

安装器会下载并校验固定的 YOLOX-Tiny 人员检测模型和 MediaPipe 站姿关键点模型。

## YOLOX-Tiny

- 来源：<https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx>
- 上游项目：<https://github.com/Megvii-BaseDetection/YOLOX>
- 上游许可证：Apache License 2.0
- SHA-256：`427CC366D34E27FF7A03E2899B5E3671425C262EA2291F88BB942BC1CC70B0F7`
- 输入尺寸：`416×416`
- 本项目只读取 COCO 类别 0（`person`）的结果。

## MediaPipe Pose Landmarker Lite

- 来源：<https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task>
- 上游项目：<https://github.com/google-ai-edge/mediapipe>
- 上游许可证：Apache License 2.0
- SHA-256：`59929E1D1EE95287735DDD833B19CF4AC46D29BC7AFDDBBF6753C459690D574A`
- 本项目用肩、髋、膝、踝关键点过滤坐姿，只保留明确站立人员。

模型损坏或缺失时，重新运行 `01_安装环境_双击.bat`；安装器会核对哈希并重新下载。
