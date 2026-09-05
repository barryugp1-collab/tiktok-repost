FROM mcr.microsoft.com/playwright/python:v1.44.0-jammy

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
RUN playwright install --with-deps chromium

COPY . .

EXPOSE 5000
CMD ["python", "app.py"]
