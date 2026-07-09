# DIS Source Library - S3 Only Final

This build stores all DIS Source Library artifacts in AWS S3 only. It does not use `local_s3`.

## Required environment

```powershell
$env:DIS_SERVICE_TOKEN="dev-dis-token"
$env:DIS_S3_BUCKET="content-ai-studio"
$env:DIS_S3_BASE_PREFIX="DIS"
$env:AWS_REGION="us-east-1"
```

Use separate buckets if needed:

```powershell
$env:DIS_RAW_BUCKET="content-ai-studio-raw"
$env:DIS_PROCESSED_BUCKET="content-ai-studio-processed"
```

`DIS_S3_BUCKET` is the simple option and sets both raw and processed bucket.

## S3 keys

For client `cengage` and job `<job_id>`:

```text
raw/cengage_ns/cengage/development/<job_id>/<filename>
processed/cengage_ns/cengage/development/<job_id>/studio_payload/payload.json
processed/cengage_ns/cengage/development/<job_id>/source_content/content.json
processed/cengage_ns/cengage/development/source_index/source_list.json
```

If `DIS_S3_BASE_PREFIX=DIS`, the keys are stored under `DIS/...`.

## Source Library behavior

- List endpoint reads `source_index/source_list.json` from S3.
- View endpoint reads `source_content/content.json` from S3.
- List response is compact and does not expose heavy metadata.
- View response shows clean readable content only.
- Style, CDD, and Blueprint files are stored as one full-document unit.
- Course-generation material can use semantic chunks.

## Run

```powershell
cd dis_backend
.\.venv\Scripts\Activate.ps1
$env:DIS_SERVICE_TOKEN="dev-dis-token"
$env:DIS_S3_BUCKET="content-ai-studio"
$env:DIS_S3_BASE_PREFIX="DIS"
$env:AWS_REGION="us-east-1"
uvicorn main:app --host 0.0.0.0 --port 8010 --reload
```
