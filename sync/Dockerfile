FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY sync_functions.py ./
ENTRYPOINT ["python", "/app/sync_functions.py"]
