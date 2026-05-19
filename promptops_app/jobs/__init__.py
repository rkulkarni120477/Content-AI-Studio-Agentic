# promptops_app/jobs — lightweight background job system (thread-based).
# Drop-in Celery migration path: replace job_runner.submit() with
# a shared_task decorator and run_generation_job(job_id) as a Celery task.
