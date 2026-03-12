# 使用 Python 3.10 精简版镜像
FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 复制项目的 requirements.txt
COPY backend/requirements.txt /app/

# 配置 pip 使用阿里云镜像并安装依赖
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --no-cache-dir -r requirements.txt

# Install ffmpeg (includes ffprobe) and libsndfile1 (required by faster-whisper / CTranslate2)
RUN apt-get update && apt-get install -y ffmpeg libsndfile1 && rm -rf /var/lib/apt/lists/*

# 复制整个后端项目到容器
COPY backend /app/

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 设置默认的命令，启动 Uvicorn 服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]