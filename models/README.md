# 人员识别模型

安装器会把固定版本的 `YOLOX-Tiny` ONNX 模型下载为 `yolox_tiny.onnx`。

- 来源：<https://github.com/Megvii-BaseDetection/YOLOX/releases/download/0.1.1rc0/yolox_tiny.onnx>
- 上游项目：<https://github.com/Megvii-BaseDetection/YOLOX>
- 上游许可证：Apache License 2.0
- SHA-256：`427CC366D34E27FF7A03E2899B5E3671425C262EA2291F88BB942BC1CC70B0F7`
- 输入尺寸：`416×416`
- 本项目只读取 COCO 类别 0（`person`）的结果。

模型损坏或缺失时，重新运行 `01_安装环境_双击.bat`；安装器会核对哈希并重新下载。
