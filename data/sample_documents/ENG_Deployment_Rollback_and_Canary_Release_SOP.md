# Engineering SOP: Deployment, Canary Release, and Automated Rollback Procedures

**Document ID:** ENG-SOP-DEPLOY-2026-04  
**Department:** Engineering  
**Clearance Level:** Internal  
**Version:** 3.1  
**Owner:** VP Engineering / Site Reliability Engineering (SRE) Team  
**Last Reviewed:** 2026-10-01  
**Next Review:** 2027-04-01  

---

## 1. Purpose and Scope

This Standard Operating Procedure (SOP) defines the process for safely deploying new software versions to production, executing canary releases, and triggering automated or manual rollbacks in the event of degradation or failure.

This SOP applies to all services operated by the Engineering department on the Cortex Platform — including core API services, ingestion workers, background sweeper jobs, and AI inference endpoints. Compliance with this SOP is **mandatory** for all production releases.

---

## 2. Definitions

| Term | Definition |
|------|-----------|
| **Canary Release** | A phased traffic-shifting deployment strategy that exposes a new version to a gradually increasing percentage of live traffic before full rollout. |
| **Rollback** | The act of reverting a production deployment to a previously known-good revision. |
| **SEV-1 Incident** | A production failure affecting >20% of active users or causing complete service unavailability. Triggers immediate rollback protocol. |
| **Helm Revision** | Kubernetes Helm chart deployment snapshot identified by an integer revision number. |
| **Expand/Contract** | A database migration pattern that makes schema changes backward-compatible by expanding schema before contract. |
| **SLO** | Service Level Objective — an internal reliability target (e.g., 99.9% uptime over 30-day rolling window). |

---

## 3. Pre-Deployment Checklist

Before initiating any production deployment, the Release Engineer must complete the following checklist:

- [ ] Pull request reviewed and approved by **at least 2 senior engineers**
- [ ] All CI pipeline checks passing: unit tests, integration tests, type checks (`mypy --strict`), linter (`ruff`)
- [ ] Docker image tagged with immutable SHA digest (e.g., `cortex-api:sha-a3b7c291`)
- [ ] Database migration scripts reviewed for rollback safety (Expand/Contract pattern verified)
- [ ] Helm chart values diff reviewed — no unintended resource limit changes
- [ ] On-call engineer notified and acknowledged
- [ ] Deployment incident channel (`#prod-deployments`) pinged with ETA and rollback plan
- [ ] Qdrant and PostgreSQL backup snapshot confirmed within last 4 hours
- [ ] Feature flags reviewed — partial rollouts enabled where appropriate

---

## 4. Canary Release Pipeline

### 4.1 Stage Definitions

Production traffic is shifted in four progressive stages. Each stage is validated against **automated SLO gates** before proceeding to the next.

| Stage | Traffic Split | Duration | Go/No-Go Trigger |
|-------|--------------|----------|-----------------|
| **Stage 1 — Canary** | 5% → new, 95% → stable | 10 minutes | Error rate <0.1%, p99 latency <400ms |
| **Stage 2 — Early Majority** | 25% → new, 75% → stable | 20 minutes | Error rate <0.3%, p99 latency <600ms |
| **Stage 3 — Majority** | 50% → new, 50% → stable | 30 minutes | Error rate <0.5%, p99 latency <700ms |
| **Stage 4 — Full Rollout** | 100% → new | Permanent | Error rate <0.5%, p99 latency <800ms sustained |

### 4.2 Traffic Management

Traffic splitting is managed via **Istio VirtualService** configuration applied by the SRE automation pipeline:

```yaml
# Example: Stage 1 Canary Split
apiVersion: networking.istio.io/v1alpha3
kind: VirtualService
metadata:
  name: cortex-api
spec:
  http:
  - route:
    - destination:
        host: cortex-api-stable
        port:
          number: 8000
      weight: 95
    - destination:
        host: cortex-api-canary
        port:
          number: 8000
      weight: 5
```

### 4.3 SLO Gate Automation

Automated SLO gating is performed by the **Cortex SRE Pipeline (GitLab CI)** using Prometheus metrics:

```bash
# Check error rate over last 2 minutes
rate(http_requests_total{status=~"5.*",service="cortex-api"}[2m]) > 0.005

# Check p99 latency
histogram_quantile(0.99, rate(http_request_duration_seconds_bucket[2m])) > 0.8
```

If either condition evaluates to `true` for **two consecutive Prometheus scrape cycles (2 minutes)**, the pipeline automatically:
1. Halts traffic shift progression
2. Triggers `AUTOMATED_ROLLBACK` alert in PagerDuty
3. Initiates Helm rollback to the last known-good revision

---

## 5. Automated Rollback Triggers

The following conditions trigger an **immediate automated rollback**:

### 5.1 Primary Triggers

| Trigger Condition | Threshold | Window | Severity |
|------------------|-----------|--------|----------|
| HTTP 5xx error rate | > 0.5% (50 errors/10,000 requests) | 2 consecutive minutes | CRITICAL |
| p99 API latency | > 800ms | 2 consecutive minutes | CRITICAL |
| p95 API latency | > 500ms | 5 consecutive minutes | HIGH |
| AI inference timeout rate | > 2% | 3 consecutive minutes | HIGH |
| Database connection pool exhaustion | > 90% utilization | 1 minute | CRITICAL |
| Qdrant health check failure | 2 consecutive failures | 60-second probe interval | CRITICAL |

### 5.2 Manual Rollback Triggers

A manual rollback may be initiated by the on-call engineer or any senior engineer when:
- A critical bug is discovered in production that evaded automated detection
- User-reported P0/P1 issues spike in the support queue
- The on-call engineer's gut judgment warrants rollback (no approval required during active incident)

---

## 6. Rollback Execution Procedure

### 6.1 Helm Rollback Command

```bash
# Step 1: Identify the last known-good Helm revision
helm history cortex-prod -n production

# Step 2: Execute rollback to specific revision
helm rollback cortex-prod <revision_number> -n production --wait --timeout 5m

# Example — rollback to revision 47
helm rollback cortex-prod 47 -n production --wait --timeout 5m

# Step 3: Verify rollback status
helm status cortex-prod -n production
kubectl rollout status deployment/cortex-api -n production
```

### 6.2 Rollback Completion Criteria

The rollback is considered **complete and successful** when ALL of the following are confirmed:

- `helm status cortex-prod` reports `STATUS: deployed`
- `kubectl rollout status` reports `successfully rolled out`
- Prometheus: HTTP 5xx error rate drops below 0.1% for 3 consecutive minutes
- Prometheus: p99 latency drops below 400ms for 3 consecutive minutes
- PagerDuty incident transitions to `Acknowledged → Resolved`
- On-call engineer posts confirmation message in `#prod-incidents` channel

### 6.3 Traffic Restoration After Rollback

After a successful rollback, all Istio weights are reset to 100% stable:

```bash
kubectl apply -f infra/k8s/istio/virtualservice-stable-100.yaml -n production
```

---

## 7. Database Migration Rollback: Expand/Contract Pattern

All database schema migrations **must** follow the Expand/Contract pattern to ensure backward compatibility during canary deployments where both old and new application code may be simultaneously live.

### 7.1 Migration Phases

**Phase 1 — Expand (Safe to deploy):**
Add new columns as nullable, add new tables. Never remove or rename columns. The old application code continues to function normally.

```sql
-- EXPAND: Add new column as nullable
ALTER TABLE documents ADD COLUMN embedding_version INTEGER DEFAULT NULL;
```

**Phase 2 — Backfill (Safe to deploy separately):**
Backfill data for the new column in a background job while both old and new code are live.

```sql
-- BACKFILL: Set default for existing rows
UPDATE documents SET embedding_version = 1 WHERE embedding_version IS NULL;
```

**Phase 3 — Contract (Only after full rollout confirmed):**
Remove deprecated columns, add NOT NULL constraints, drop old indexes. This phase is executed **only after** the new code is 100% rolled out and verified stable for at least 24 hours.

```sql
-- CONTRACT: Safe to run only after full 100% rollout + 24h stability
ALTER TABLE documents ALTER COLUMN embedding_version SET NOT NULL;
-- DROP old deprecated column only here, never in Phase 1
```

---

## 8. Post-Incident Requirements

### 8.1 Root Cause Analysis (RCA)

All automated and manual rollbacks trigger mandatory RCA documentation:

| Requirement | Deadline |
|------------|----------|
| Initial incident summary in Confluence | Within **2 hours** of incident resolution |
| Full RCA document submitted | Within **24 hours** of rollback |
| Post-mortem meeting scheduled and completed | Within **3 business days** |
| Action items tracked in Jira (minimum 1 preventive action) | Within **5 business days** |

### 8.2 RCA Template Sections

The RCA must include:
1. **Incident Timeline** — Exact timestamps (IST) for detection, alerting, response, rollback, and resolution
2. **Root Cause** — Specific code change, configuration drift, or infrastructure issue responsible
3. **Impact Assessment** — Users affected, services degraded, data integrity status
4. **Contributing Factors** — Process gaps or automation failures that allowed this to occur
5. **5-Whys Analysis** — Iterative causal chain analysis (minimum 5 levels)
6. **Action Items** — Preventive measures with assignee and due date

---

## 9. Contacts and Escalation

| Role | Contact | PagerDuty Schedule |
|------|---------|-------------------|
| Primary On-Call SRE | PagerDuty auto-paged | `cortex-sre-primary` |
| Engineering Manager | Slack `@eng-manager` | Manual escalation |
| VP Engineering | Slack `@vp-eng` | SEV-1 only, auto-escalate after 15 min |
| CTO | Phone (registered in PagerDuty) | SEV-1 only, manual escalation |

---

## 10. Document Control

This document is reviewed semi-annually (every 6 months) by the VP Engineering and SRE Lead. Any deviations from this SOP must be approved in writing by the Engineering Manager and logged in the Cortex Governance Audit system.

**Approval Sign-offs:**
- VP Engineering: *Digitally signed — 2026-10-01*
- SRE Lead: *Digitally signed — 2026-10-01*
- CTO (reviewed): *Digitally signed — 2026-10-02*
