FROM python:3.11-slim

# Cài đặt các thư viện lõi hệ điều hành cần thiết cho audio (libsndfile/gcc)
RUN apt-get update && apt-get install -y \
    build-essential \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirement list và install trước để tối ưu hóa bộ nhớ đệm (cache)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy toàn bộ mã nguồn
COPY . .

# Mở port 8000
EXPOSE 8000

# Chạy server FastAPI
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
