# Audio TTS Project

Dự án này là một hệ thống Text-to-Speech (TTS) được thiết kế với hai nhiệm vụ chính:

## 1. API Tạo Audio từ TTS
Cung cấp các API (dựa trên FastAPI) cho phép người dùng gọi và tự động tạo ra file audio từ văn bản. Thành phần này đã được tích hợp engine Vieneu TTS, hỗ trợ:
- Chuyển đổi văn bản thành giọng nói tiếng Việt.
- Voice cloning (sao chép giọng nói) từ file audio mẫu.
- Tuỳ chỉnh các hiệu ứng âm thanh như pitch shift, robotic, telephone,...

## 2. Worker Tự Động (Automation Worker)
*Tính năng đang được phát triển*

Một background worker (luồng chạy ngầm) với nhiệm vụ:
- Tự động kiểm tra và quét trên Database.
- Phát hiện các đoạn văn bản (text) đang bị thiếu file audio.
- Tự động gọi quá trình tạo audio (sử dụng TTS) và lưu trữ lại mà không cần sự can thiệp thủ công.
