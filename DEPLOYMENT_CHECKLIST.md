# Agent Builder Phase 4 Deployment Checklist

**Status**: Ready for Production  
**Date**: October 7, 2026  
**Release Version**: 1.0

---

## Pre-Deployment Verification

### Code Quality
- [ ] All tests passing (`pytest tests/`)
  - [ ] Unit tests pass
  - [ ] Integration tests pass
  - [ ] Performance tests within acceptable range
  - [ ] Security tests passing
  - [ ] E2E tests passing

- [ ] Code coverage > 90% for critical paths
  - [ ] Run: `coverage run -m pytest tests/ && coverage report`
  - [ ] Check critical services: `coverage report promptops_app/services/agent*.py`

- [ ] No type errors
  - [ ] Run: `mypy promptops_app/services/agent*.py`
  - [ ] All function signatures typed

- [ ] No linting issues
  - [ ] Run: `flake8 promptops_app/services/agent*.py`
  - [ ] Run: `black --check promptops_app/services/`

- [ ] No security vulnerabilities
  - [ ] Run: `bandit -r promptops_app/services/`
  - [ ] Run: `safety check`
  - [ ] No hardcoded secrets

- [ ] Code review approved
  - [ ] Peer review completed
  - [ ] Security review completed
  - [ ] Performance review completed

### Database Schema
- [ ] Schema migration created
  - [ ] Migration file: `migrations/versions/xxx_agent_builder.py`
  - [ ] Up migration verified
  - [ ] Down migration verified (if applicable)

- [ ] Schema compatibility verified
  - [ ] Tested on fresh database
  - [ ] Tested on existing database
  - [ ] Foreign key constraints enabled
  - [ ] Indexes created for performance

- [ ] Backup tested
  - [ ] Database backup procedure documented
  - [ ] Restore procedure tested
  - [ ] Backup storage location configured

### Configuration & Secrets
- [ ] Environment variables documented
  - [ ] `.env.example` created with all required vars
  - [ ] All secrets use environment variables (not hardcoded)
  - [ ] Configuration validated on startup

- [ ] Configuration for different environments
  - [ ] Development configuration
  - [ ] Staging configuration
  - [ ] Production configuration

- [ ] Secrets management
  - [ ] API keys in secrets manager (not in code)
  - [ ] Database credentials secure
  - [ ] JWT secret configured
  - [ ] Rotation procedures documented

### Documentation
- [ ] API documentation
  - [ ] All endpoints documented
  - [ ] Request/response schemas documented
  - [ ] Error codes documented
  - [ ] Rate limits documented

- [ ] Architecture documentation
  - [ ] System design diagram
  - [ ] Data flow diagrams
  - [ ] Database schema diagram
  - [ ] Component interaction diagram

- [ ] Operational documentation
  - [ ] Deployment procedures documented
  - [ ] Rollback procedures documented
  - [ ] Monitoring setup documented
  - [ ] Troubleshooting guide created

- [ ] Developer documentation
  - [ ] Installation guide
  - [ ] Local development setup
  - [ ] Testing procedures
  - [ ] Debugging guide

---

## Environment Setup

### Infrastructure
- [ ] Server/VM provisioned
  - [ ] CPU: 2+ cores minimum
  - [ ] RAM: 4GB minimum (8GB recommended)
  - [ ] Disk: 50GB+ for SQLite growth
  - [ ] Network: 10Mbps+ recommended

- [ ] Database setup
  - [ ] SQLite 3.40+ installed and verified
  - [ ] WAL mode enabled
  - [ ] Busy timeout configured (30s)
  - [ ] Foreign keys enforced
  - [ ] Backup location configured

- [ ] Python environment
  - [ ] Python 3.11+ installed
  - [ ] Virtual environment created
  - [ ] Dependencies installed: `pip install -r requirements.txt`
  - [ ] Verified no conflicts

- [ ] Application directory structure
  - [ ] All code directories in place
  - [ ] Logs directory writable
  - [ ] Database directory writable
  - [ ] Backup directory accessible

### Configuration Files
- [ ] Environment variables set
  ```bash
  DATABASE_URL=sqlite:///content_ai.db
  AGENT_BUILDER_ENABLED=true
  AGENT_EXECUTION_TIMEOUT=300
  AGENT_CONCURRENT_RUNS=2
  OPENAI_API_KEY=sk-...
  LOG_LEVEL=INFO
  ```

- [ ] Database configuration
  ```sql
  PRAGMA journal_mode=WAL;
  PRAGMA busy_timeout=30000;
  PRAGMA foreign_keys=ON;
  ```

- [ ] Logging configuration
  - [ ] Log directory writable
  - [ ] Log rotation configured (if applicable)
  - [ ] Log level set appropriately
  - [ ] Structured logging enabled

### API Keys & Credentials
- [ ] OpenAI API key obtained and validated
  - [ ] Key has appropriate permissions
  - [ ] Rate limits reviewed
  - [ ] Organization ID configured (if applicable)

- [ ] AWS Bedrock credentials (if used)
  - [ ] IAM role configured
  - [ ] Model access verified
  - [ ] Region configured

- [ ] JWT secret configured
  - [ ] Strong random secret generated
  - [ ] Secret securely stored
  - [ ] Token expiration configured

---

## Database & Schema Migration

### Migration Execution
- [ ] Pre-migration backup
  - [ ] Run: `cp content_ai.db content_ai.db.pre-deployment`
  - [ ] Verify backup integrity

- [ ] Run migrations
  - [ ] Run: `alembic upgrade head`
  - [ ] No errors in migration output
  - [ ] Verify schema created correctly

- [ ] Verify schema
  - [ ] Check tables exist: `sqlite3 content_ai.db ".tables"`
  - [ ] Check agent tables:
    ```sql
    SELECT name FROM sqlite_master WHERE type='table' 
    AND name LIKE 'agent%' OR name LIKE 'workflow%';
    ```
  - [ ] Check indexes: `sqlite3 content_ai.db ".indices"`
  - [ ] Foreign keys enabled: `PRAGMA foreign_keys;`

- [ ] Data verification (if upgrading)
  - [ ] Record counts match pre-migration
  - [ ] Referential integrity maintained
  - [ ] No data loss detected
  - [ ] Spot-check key records

- [ ] Performance testing post-migration
  - [ ] Query performance acceptable
  - [ ] No lock contention
  - [ ] WAL mode working correctly

### Testing
- [ ] Unit tests pass on production schema
  - [ ] Run: `pytest tests/test_agents*.py`
  - [ ] All tests green
  - [ ] No deprecation warnings

- [ ] Integration tests pass
  - [ ] Run: `pytest tests/test_agents_e2e.py`
  - [ ] Full workflows execute successfully
  - [ ] Isolation verified

---

## Application Deployment

### Service Startup
- [ ] Application starts without errors
  ```bash
  uvicorn app.main:app --port 8000
  ```
  - [ ] No import errors
  - [ ] Database connection successful
  - [ ] All services initialized
  - [ ] Ready to accept requests

- [ ] Health check endpoints working
  - [ ] `GET /api/v1/health` returns 200
  - [ ] `GET /api/v1/health/db` returns 200
  - [ ] `GET /api/v1/health/llm` returns 200

- [ ] Service process management
  - [ ] systemd service created (if applicable)
  - [ ] Service auto-starts on reboot
  - [ ] Process monitoring configured

### Load Balancing (if applicable)
- [ ] Load balancer configured
  - [ ] Health check path configured
  - [ ] Session affinity configured (if needed)
  - [ ] Timeout values set appropriately
  - [ ] SSL/TLS certificates configured

- [ ] Multiple instances (if applicable)
  - [ ] All instances healthy
  - [ ] Database connection pooling configured
  - [ ] Sticky sessions (if needed)

---

## Monitoring & Observability Setup

### Logging Configuration
- [ ] Application logging
  - [ ] Log level set to INFO
  - [ ] Logs written to file and stdout
  - [ ] Structured logging enabled
  - [ ] Sensitive data not logged

- [ ] Log aggregation (if applicable)
  - [ ] CloudWatch/DataDog/Splunk configured
  - [ ] Log parsing rules configured
  - [ ] Retention policy set

- [ ] Log monitoring
  - [ ] Error alerts configured
  - [ ] Critical errors trigger notifications
  - [ ] Team notified of issues

### Metrics Collection
- [ ] Monitoring service initialized
  - [ ] Performance metrics collected
  - [ ] Error rates tracked
  - [ ] Budget tracking enabled

- [ ] Metrics export (if applicable)
  - [ ] Prometheus metrics endpoint active
  - [ ] DataDog agent configured
  - [ ] Custom metrics defined

- [ ] Alerting
  - [ ] High error rate alert configured
  - [ ] Database lock contention alert
  - [ ] Budget limit alert
  - [ ] Performance degradation alert

### Dashboards
- [ ] Agent execution dashboard
  - [ ] Run count by status
  - [ ] Average execution time
  - [ ] Error rate
  - [ ] Success rate

- [ ] Resource dashboard
  - [ ] CPU usage
  - [ ] Memory usage
  - [ ] Database size
  - [ ] Disk I/O

- [ ] Business metrics dashboard
  - [ ] Total tokens used
  - [ ] Total cost
  - [ ] Budget utilization
  - [ ] Cost per tenant

---

## Security Verification

### Authentication & Authorization
- [ ] JWT authentication working
  - [ ] Valid token accepted
  - [ ] Invalid token rejected
  - [ ] Expired token rejected
  - [ ] Token refresh working (if implemented)

- [ ] RBAC enforced
  - [ ] Admin can create agents
  - [ ] Non-admin cannot create agents
  - [ ] Author can run agents
  - [ ] Reviewer cannot activate agents

- [ ] Cross-tenant isolation verified
  - [ ] Tenant A cannot access Tenant B's agents
  - [ ] Tenant A cannot access Tenant B's runs
  - [ ] Budget isolated by tenant
  - [ ] Workflows isolated by tenant

### Input Validation
- [ ] No SQL injection possible
  - [ ] All queries use ORM parameterization
  - [ ] No raw SQL with string interpolation
  - [ ] Special characters handled safely

- [ ] API input validation
  - [ ] Invalid JSON rejected
  - [ ] Missing required fields rejected
  - [ ] Type validation enforced
  - [ ] Size limits enforced

### Data Protection
- [ ] Sensitive data not logged
  - [ ] API keys not logged
  - [ ] Passwords not logged
  - [ ] Personal data handling compliant

- [ ] Database encryption (if applicable)
  - [ ] Encryption at rest configured
  - [ ] Encryption in transit enabled
  - [ ] Key management in place

- [ ] Backup encryption
  - [ ] Backups encrypted
  - [ ] Encryption keys securely stored
  - [ ] Restore tested with encrypted backup

---

## Performance Validation

### Load Testing
- [ ] Concurrent execution validated
  - [ ] 10 concurrent runs: ✓
  - [ ] 50 concurrent runs: ✓
  - [ ] 100 concurrent runs: ✓ (with reasonable latency)

- [ ] Performance metrics acceptable
  - [ ] Average run setup time: < 500ms
  - [ ] P95 run time: < 60 seconds
  - [ ] Database query time: < 100ms
  - [ ] No memory leaks detected over 1 hour

### Stress Testing
- [ ] System under sustained load
  - [ ] 24-hour run test completed
  - [ ] No memory leaks
  - [ ] No database corruption
  - [ ] Error rate stable

- [ ] Recovery from errors
  - [ ] Timeouts handled gracefully
  - [ ] Failed runs don't corrupt state
  - [ ] Checkpoints restore correctly

---

## Smoke Testing

### Basic Functionality
- [ ] Create agent
  ```bash
  curl -X POST http://localhost:8000/api/v1/agents \
    -H "Authorization: Bearer $TOKEN" \
    -d '{"name": "Test Agent", ...}'
  ```

- [ ] List agents
  ```bash
  curl http://localhost:8000/api/v1/agents \
    -H "Authorization: Bearer $TOKEN"
  ```

- [ ] Activate agent
  ```bash
  curl -X POST http://localhost:8000/api/v1/agents/{id}/activate \
    -H "Authorization: Bearer $TOKEN"
  ```

- [ ] Run agent
  ```bash
  curl -X POST http://localhost:8000/api/v1/agents/{id}/run \
    -H "Authorization: Bearer $TOKEN" \
    -d '{"input_content": "test"}'
  ```

- [ ] Check run status
  ```bash
  curl http://localhost:8000/api/v1/runs/{run_id} \
    -H "Authorization: Bearer $TOKEN"
  ```

- [ ] Create workflow
  ```bash
  curl -X POST http://localhost:8000/api/v1/workflows \
    -H "Authorization: Bearer $TOKEN" \
    -d '{...}'
  ```

### End-to-End Workflow
- [ ] Full workflow execution
  1. Create 3 agents
  2. Create workflow
  3. Execute workflow
  4. Monitor completion
  5. Verify results
  6. Check cost tracking

---

## Rollback Plan

### Automatic Rollback Triggers
- [ ] Documented conditions for automatic rollback
  - [ ] Database migration failure
  - [ ] Critical API error (>50% error rate)
  - [ ] Data corruption detected
  - [ ] Security vulnerability found

### Manual Rollback Procedure
- [ ] Stop application
  ```bash
  systemctl stop content-ai-studio
  ```

- [ ] Restore database from backup
  ```bash
  cp content_ai.db.pre-deployment content_ai.db
  ```

- [ ] Rollback migrations (if needed)
  ```bash
  alembic downgrade -1
  ```

- [ ] Restart application
  ```bash
  systemctl start content-ai-studio
  ```

- [ ] Verify rollback
  - [ ] Health checks passing
  - [ ] Previous functionality restored
  - [ ] No error logs

- [ ] Notify stakeholders
  - [ ] Incident report filed
  - [ ] Rollback reason documented
  - [ ] Post-mortem scheduled

---

## Post-Deployment

### Validation
- [ ] All health checks passing
- [ ] Smoke tests all pass
- [ ] Monitoring data flowing
- [ ] Logs being collected
- [ ] Backups running
- [ ] Users can access system

### Team Communication
- [ ] Deployment announced
- [ ] Release notes distributed
- [ ] Support team briefed
- [ ] Known issues documented
- [ ] Support procedures updated

### Monitoring Period (24-48 hours)
- [ ] Daily review of metrics
  - [ ] Error rates normal
  - [ ] Performance normal
  - [ ] Resource usage normal
  - [ ] No security issues

- [ ] User feedback monitored
  - [ ] Issues reported and tracked
  - [ ] User questions answered
  - [ ] Feedback on new features

- [ ] Incident response ready
  - [ ] On-call engineer assigned
  - [ ] Escalation path clear
  - [ ] Rollback ready if needed

---

## Final Sign-Off

### Deployment Owner
- **Name**: ___________________
- **Date**: ___________________
- **Approval**: ☐ Approved ☐ Conditional ☐ Rejected

### Product Manager
- **Name**: ___________________
- **Date**: ___________________
- **Approval**: ☐ Approved ☐ Conditional ☐ Rejected

### Engineering Lead
- **Name**: ___________________
- **Date**: ___________________
- **Approval**: ☐ Approved ☐ Conditional ☐ Rejected

### Operations/DevOps
- **Name**: ___________________
- **Date**: ___________________
- **Approval**: ☐ Approved ☐ Conditional ☐ Rejected

---

## Notes & Issues

### Outstanding Issues
```
1. [Issue]: Description
   [Status]: Open/Resolved
   [Workaround]: ...
   
2. [Issue]: Description
   [Status]: Open/Resolved
   [Workaround]: ...
```

### Known Limitations
- SQLite concurrency limited to ~5 concurrent writes
- Single-host deployment only (no distributed setup)
- No built-in replication (backup-based recovery)
- Manual checkpoint cleanup needed for long-lived systems

### Future Improvements
- [ ] PostgreSQL support for higher concurrency
- [ ] Distributed checkpoint storage
- [ ] Advanced monitoring dashboards
- [ ] Auto-scaling capabilities (with PostgreSQL)

---

## References

- [Agent Builder Production Guide](./docs/AGENT_BUILDER_PRODUCTION_GUIDE.md)
- [Architecture Design](./docs/CONTEXT_FLOW_AND_ARCHITECTURE.md)
- [Performance Metrics](./docs/PERFORMANCE_CHARACTERISTICS.md)
- [Phase 3 Report](./PHASE_3_IMPLEMENTATION_REPORT.md)

---

**Deployment Date**: ___________  
**Deployed By**: ___________  
**Version**: 1.0
