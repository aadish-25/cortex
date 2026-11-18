# Confidential SOW: Project Apex — Tier-1 Banking Client Integration Architecture

**Document ID:** ENG-CONF-SOW-APEX-2026-01  
**Department:** Engineering  
**Clearance Level:** Confidential  
**Version:** 0.9 (Pre-Execution Draft)  
**Classification:** CONFIDENTIAL — Need-to-Know Only  
**Owner:** VP Engineering / Head of Client Delivery  
**Last Updated:** 2026-11-01  
**Expected Execution Date:** 2026-12-01  

---

> ⚠️ **CONFIDENTIAL DOCUMENT** — This Statement of Work contains commercially sensitive client information. Distribution is restricted to Engineering leads, Security team, and Finance team on a strict need-to-know basis. Unauthorized distribution constitutes a breach of client NDA (executed 2026-08-15) and may result in disciplinary action and legal proceedings.

---

## 1. Engagement Overview

### 1.1 Project Code Name: Apex

Cortex Technologies Pvt. Ltd. ("Service Provider") is engaged by **[TIER-1 BANKING CLIENT]** ("Client") — a leading private sector bank operating across 23 states with a 2026 digital transformation initiative — to integrate the Cortex Enterprise RAG Platform as the AI-powered Knowledge Intelligence Layer for the Client's internal compliance, credit policy, and operations teams.

> *Client identity is classified per NDA. Internal references use "Project Apex" or "HDFC-analog Client."*

### 1.2 Engagement Value

| Component | Estimated Value (INR) |
|-----------|----------------------|
| Year 1 Platform License + Implementation | ₹4,20,00,000 (₹4.2 Crore) |
| Year 2–3 Maintenance & SLA (annual) | ₹85,00,000/year |
| Professional Services (Year 1) | ₹1,20,00,000 |
| **Total 3-Year Contract Value** | **₹6,90,00,000 (₹6.9 Crore)** |

### 1.3 Project Timeline

| Milestone | Target Date | Owner | Status |
|-----------|------------|-------|--------|
| M0 — NDA and MSA executed | 2026-08-15 | Legal | ✅ Done |
| M1 — Architecture Design Workshop | 2026-09-10 | Engineering | ✅ Done |
| M2 — VPC Peering and Network Setup | 2026-11-15 | Engineering / Cloud Ops | 🔄 In Progress |
| M3 — Kafka Integration (Dev) | 2026-12-01 | Engineering | ⏳ Upcoming |
| M4 — UAT (User Acceptance Testing) | 2027-01-15 | Engineering + Client | ⏳ Upcoming |
| M5 — Security Audit & Pen Test | 2027-02-01 | Security | ⏳ Upcoming |
| M6 — Go-Live (Production) | 2027-03-01 | Engineering / Client | ⏳ Upcoming |
| M7 — Hypercare Period (90 days) | 2027-03-01–05-31 | Engineering SRE | ⏳ Upcoming |

---

## 2. Technical Architecture

### 2.1 Network and Infrastructure

**Cloud Deployment Model: AWS Mumbai Region (ap-south-1) — Dedicated VPC**

| Component | Specification |
|-----------|--------------|
| Primary Region | AWS ap-south-1 (Mumbai) |
| Disaster Recovery Region | AWS ap-south-2 (Hyderabad) |
| Network Connectivity | AWS VPC Peering — Cortex VPC ↔ Client Banking VPC (direct, no public internet exposure) |
| IP Whitelisting | Static IP ranges pre-approved by Client Network Security team |
| DNS Resolution | Private Route 53 hosted zone — `apex.cortex.internal` |
| TLS Version | TLS 1.3 mandatory; TLS 1.2 as backward-compatible fallback |
| Mutual TLS (mTLS) | mTLS required for all service-to-service API calls within Client perimeter |

**VPC Architecture:**
```
Client Banking Network (10.20.0.0/16)
         │
    [VPC Peering - PCX-xxxxxxxx]
         │
Cortex Apex VPC (10.30.0.0/16)
  ├── Public Subnet (NAT Gateway, Load Balancer)
  ├── Private Subnet - App Tier (Cortex API, Worker)
  ├── Private Subnet - Data Tier (RDS PostgreSQL, ElastiCache Redis)
  └── Private Subnet - Vector Tier (Qdrant on EC2 - r6g.4xlarge)
```

### 2.2 Event Streaming: Apache Kafka Integration

The Client requires real-time policy and compliance document update propagation through **Apache Kafka** (Client-managed MSK on AWS).

| Kafka Parameter | Specification |
|----------------|--------------|
| Kafka Version | 3.6.x (AWS MSK) |
| Peak Throughput | **15,000 messages/second (TPS)** |
| Partition Strategy | Topic: `cortex.document.events` — 24 partitions, 3 replicas |
| Consumer Group | `cortex-ingestion-consumer-group` |
| Message Schema | Avro with Schema Registry (Confluent compatible) |
| Security | SASL/SCRAM-SHA-512 + TLS 1.3 |
| Message Retention | 7 days (168 hours) |
| Dead Letter Queue (DLQ) | `cortex.document.events.dlq` — monitored with PagerDuty alert on >100 DLQ messages |

**Kafka Consumer Architecture in Cortex:**
- `cortex-kafka-worker` — dedicated Kubernetes Deployment, 3–10 replicas (HPA on consumer lag > 5,000 messages)
- Consumer lag SLO: Maximum **60 seconds** lag at steady state; alert at 120 seconds
- Failed messages after 3 retries are routed to DLQ with structured error envelope

### 2.3 Service Level Agreements (SLAs)

| SLA Metric | Target | Measurement Window | Penalty Clause |
|-----------|--------|-------------------|---------------|
| **Platform Uptime** | **99.99%** (≤ 52.6 min/year downtime) | Rolling 365 days | 5% monthly fee credit per 0.01% below target |
| **API p95 Latency** | **< 120ms** (excluding LLM inference time) | Rolling 30-day percentile | Measured at Load Balancer; LLM inference excluded |
| **API p99 Latency** | **< 300ms** | Rolling 30-day percentile | Alert threshold; contract review if breached >5 days/month |
| **Kafka Consumer Lag** | < 60s at steady state; < 300s during ingestion bursts | Continuous | PagerDuty alert; no commercial penalty |
| **Incident Response SLA** | SEV-1: 15 min; SEV-2: 1 hour | Per incident | Measured from client notification to Cortex acknowledgment |
| **RPO (Recovery Point Objective)** | **< 1 minute** | Per DR event | RDS Multi-AZ + Kafka offset-based replay |
| **RTO (Recovery Time Objective)** | **< 15 minutes** | Per DR event | Automated failover to Hyderabad DR via Route 53 health check |

### 2.4 Security Architecture

| Control | Implementation |
|--------|---------------|
| Authentication | JWT (RS256) + mTLS for internal service-to-service |
| Authorization | 3D RBAC (Role × Department × Clearance Level) — Cortex native |
| Data Encryption at Rest | AES-256 (AWS EBS encryption, RDS encryption) |
| Data Encryption in Transit | TLS 1.3 everywhere; TLS 1.2 fallback for legacy systems only |
| Secret Management | AWS Secrets Manager with 24-hour rotation for database credentials |
| WAF | AWS WAF with OWASP Top 10 rule set on Application Load Balancer |
| DDoS Protection | AWS Shield Standard |
| Vulnerability Scanning | Trivy container scan in CI/CD; Snyk dependency scan |
| Penetration Testing | Third-party pen test (Client-approved firm) prior to M6 Go-Live |
| Audit Logging | All API calls logged to CloudWatch + S3 (180-day retention per CERT-In Directions 2022) |

---

## 3. RACI Matrix — Project Apex

| Deliverable | Engineering | Security | Finance | Client | Legal |
|------------|-------------|---------|---------|--------|-------|
| Platform architecture and deployment | **R** / A | C | I | I | — |
| Kafka consumer implementation | **R** / A | C | — | I | — |
| VPC peering and network configuration | **R** | A | — | C | — |
| Security audit and pen test coordination | C | **R** / A | — | A | — |
| Milestone billing and invoicing | I | — | **R** / A | A | C |
| NDA and MSA compliance | I | I | I | C | **R** / A |
| UAT test case execution (Client side) | C | — | — | **R** / A | — |
| Go-Live sign-off | A | A | I | **R** | — |

*R = Responsible, A = Accountable, C = Consulted, I = Informed*

---

## 4. Data Governance and Compliance

### 4.1 Data Sovereignty

All Client data processed under Project Apex must remain within **India geography (AWS ap-south-1 and ap-south-2)**. No Client data may be transferred outside India without prior written approval from the Client's Chief Data Officer and Cortex's DPO.

### 4.2 Data Classification

| Data Category | Classification | Handling |
|--------------|---------------|---------|
| Client employee queries and conversations | Confidential | Encrypted at rest, access logged, 90-day retention |
| Compliance and policy documents | Confidential | Encrypted, access controlled by department/clearance |
| Kafka event metadata | Internal | Retained 7 days per Kafka policy |
| Audit logs (API and system) | Confidential | 180-day retention, immutable |

### 4.3 Regulatory Context

The Client is regulated by the **Reserve Bank of India (RBI)** and must comply with:
- **RBI IT Risk and Cyber Security Framework (2023)**
- **CERT-In Directions 2022** (6-hour incident reporting)
- **DPDP Act 2023** (once rules are notified)
- **PCI DSS** (for any payment data adjacency)

Cortex Technologies will provide supporting compliance artifacts (SOC 2 Type II report, ISO 27001 certificate, pen test report) to the Client's IT Risk team as part of vendor due diligence.

---

## 5. Commercials and Milestone Billing

| Milestone | Billing Trigger | Invoice Amount |
|-----------|----------------|---------------|
| M1 — Architecture Workshop Completion | On delivery of Architecture Design Document | ₹30,00,000 |
| M3 — Kafka Integration (Dev) | On acceptance of Kafka integration proof of concept | ₹60,00,000 |
| M4 — UAT Completion | On Client sign-off of UAT completion certificate | ₹1,20,00,000 |
| M6 — Go-Live | On production go-live sign-off | ₹2,10,00,000 |
| M7 — Hypercare Completion | On completion of 90-day hypercare with SLA compliance | ₹1,20,00,000 |
| **Year 2 License (annual)** | Recurring — 2027-12-01 | **₹85,00,000** |

> All invoices are subject to 18% GST. Payment terms: Net 30 from invoice date. Late payment attracts 1.5% per month compounding interest per contract clause 12.4.

---

## 6. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| VPC peering approval delays from Client network team | Medium | High | Escalation path to Client CTO agreed in M1 workshop |
| Kafka throughput spikes beyond 15,000 TPS | Low | High | Auto-scaling consumer group tested to 25,000 TPS in load tests |
| Client regulatory change requiring data sovereignty expansion | Low | Medium | Architecture designed for multi-region from day one |
| Key engineering staff attrition | Low | High | Knowledge transfer documentation mandatory for each milestone |
| Pen test findings requiring significant re-architecture | Medium | High | Security review conducted at M3 to catch issues early |

---

## 7. Document Control

This SOW is a **living pre-execution draft**. Changes require sign-off from VP Engineering, Legal Head, and CFO. Final executed version replaces this draft.

**Restricted Distribution:** VP Engineering, Head of Client Delivery, Security Lead, CFO, Legal Head, CEO.

**Approvals:**
- VP Engineering: *Draft reviewed — 2026-11-01*
- Legal Head: *Draft reviewed — 2026-11-01*
- CFO: *Commercial terms reviewed — 2026-11-01*
