"""Lightweight FastAPI server for receiving Copyleaks webhook callbacks.

Runs as a separate Docker container (api_server) on port 8502.
Only purpose: receive POST from Copyleaks and save results to DB.
"""
from fastapi import FastAPI
from promptops_app.api.plagiarism_api import router

app = FastAPI(title="Content AI — Plagiarism Webhook Server")
app.include_router(router)
