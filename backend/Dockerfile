# 使用官方 Playwright Python 镜像 (自带全套浏览器环境)
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# 设置工作目录
WORKDIR /app

# 复制项目的 requirements.txt
COPY backend/requirements.txt /app/

# 设置pip使用阿里云源，解决国内安装慢的问题
RUN pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/ \
    && pip install --no-cache-dir -r requirements.txt

# Install ffmpeg, libsndfile1 and other system deps via apt (using Ubuntu mirrors)
RUN sed -i 's|http://archive.ubuntu.com|https://mirrors.aliyun.com|g' /etc/apt/sources.list \
    && sed -i 's|http://security.ubuntu.com|https://mirrors.aliyun.com|g' /etc/apt/sources.list \
    && apt-get update \
    && apt-get install -y ffmpeg libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# 复制整个后端项目到容器
COPY backend /app/

# 设置环境变量
ENV PYTHONUNBUFFERED=1

# 设置默认的命令，启动 Uvicorn 服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]