# 1. 基础镜像：使用官方 Python 3.12 精简版，极大地减小打包体积
FROM python:3.12-slim

# 2. 工作目录：在容器内创建一个 /app 文件夹，并作为后续操作的默认路径
WORKDIR /app

# 3. 复制依赖清单：先单独把 requirements.txt 拷进去
COPY requirements.txt .

# 4. 安装依赖：使用清华源加速下载，并且不缓存安装包以节省空间
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 5. 复制全部代码：把本地当前目录下的所有文件，复制到容器的 /app 目录下
COPY . .

# 将原来的 EXPOSE 8000 改为：
EXPOSE 80

# 将原来的 CMD 启动命令，端口改为 80：
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "80"]