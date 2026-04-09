#!/bin/bash
# Lấy dường dẫn thư mục hiện tại của file chạy
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

echo "Đang khởi động môi trường ảo và chạy Server API âm thanh..."
# Kích hoạt môi trường và chạy server
source venv/bin/activate
uvicorn main:app --reload --port 8000
