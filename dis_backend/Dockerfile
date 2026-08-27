FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# antiword is the only path this image has to pre-2007 binary .doc files:
# python-docx handles the OOXML .docx container only and raises on OLE2, so
# extract_docx falls back to _extract_legacy_doc, which shells out to antiword.
# Without the binary that fallback logs "antiword is not installed" and returns
# EMPTY TEXT, and the pipeline indexes the document as a success with nothing in
# it. That is not hypothetical: 110 of AIM's 121 .doc files — final cumulative
# exams, landing-gear and ice & rain project keys — were ingested as empty and
# reported "completed" for a month. Verified 2026-08-27 that antiword recovers
# 12/12 of a random sample of those files (1.9k-26.8k chars each).
RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    libmagic1 \
    antiword \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]