# Security Governance Manual: SOC 2 Type II, ISO 27001, and Quarterly Access Review Procedures

**Document ID:** SEC-GOV-AUDIT-2026-03  
**Department:** Engineering / Information Security  
**Clearance Level:** Confidential  
**Version:** 2.1  
**Owner:** Chief Information Security Officer (CISO) / Head of InfoSec  
**Last Reviewed:** 2026-10-01  
**Next Review:** 2027-04-01  

---

> ⚠️ **CONFIDENTIAL** — This document contains details of Cortex's security controls and audit posture. Distribution restricted to Engineering leadership, Security team, and authorized auditors.

---

## 1. Purpose and Scope

This manual defines Cortex Technologies' operational procedures and control framework for maintaining **SOC 2 Type II** and **ISO/IEC 27001:2022** compliance. It governs quarterly Identity and Access Management (IAM) recertification, credential rotation schedules, MFA enforcement, disaster recovery testing cadence, and incident response SLAs.

All Engineering, Security, DevOps, and IT team members must adhere to the controls in this manual. Non-compliance with any mandatory control may result in disciplinary action and will be flagged as a control deficiency in audit findings.

---

## 2. Compliance Frameworks

### 2.1 SOC 2 Type II

Cortex Technologies maintains a **SOC 2 Type II** audit report (annual, issued by an AICPA-accredited CPA firm) covering the Trust Services Criteria (TSC):

| TSC Category | Coverage |
|-------------|---------|
| **Security (CC)** | Access controls, monitoring, incident response, change management |
| **Availability (A)** | Uptime commitments, DR procedures, capacity management |
| **Confidentiality (C)** | Data classification, encryption, NDA enforcement |
| **Processing Integrity (PI)** | Data processing accuracy, completeness controls |

**Annual SOC 2 Audit Timeline:**
- **Q1 (Jan–Mar):** Audit observation period begins
- **Q4 (Oct–Dec):** Audit fieldwork and evidence collection
- **Q4 Year-End:** SOC 2 Type II report issued (covering Jan–Dec observation period)
- **Report sharing:** Shared under NDA with clients and enterprise partners upon request

### 2.2 ISO/IEC 27001:2022

Cortex Technologies maintains **ISO 27001:2022 certification** (annual surveillance audits, triennial re-certification audits) covering the **Information Security Management System (ISMS)** for:

- Scope: Cortex Enterprise Platform — cloud infrastructure, application, data, and personnel
- Certifying body: Authorized ISO accreditation body
- Statement of Applicability (SoA): Covers all Annex A controls (93 controls as per ISO 27001:2022 Annex A)
- Mandatory controls for internal compliance monitoring: See Section 4

---

## 3. Identity and Access Management (IAM) — Quarterly Recertification

### 3.1 Recertification Cadence

All access rights to production systems, internal tools, and sensitive data must be **formally recertified every 90 days (quarterly)**.

**Recertification Schedule:**
| Quarter | Period | Manager Review Deadline | HR/CISO Sign-Off |
|---------|--------|------------------------|-----------------|
| Q1 Recert | Jan 1 – Mar 31 access snapshot | **March 15** | **March 31** |
| Q2 Recert | Apr 1 – Jun 30 access snapshot | **June 15** | **June 30** |
| Q3 Recert | Jul 1 – Sep 30 access snapshot | **September 15** | **September 30** |
| Q4 Recert | Oct 1 – Dec 31 access snapshot | **December 15** | **December 31** |

### 3.2 Recertification Process

1. **IAM Snapshot:** InfoSec exports a full access roster from AWS IAM, GitHub, Qdrant (admin roles), PostgreSQL (database roles), and Darwinbox (HRMS) at the start of each recertification window
2. **Manager Review:** Each manager reviews direct reports' access and certifies that all access remains **necessary, appropriate, and least-privilege**
3. **Orphaned Accounts:** Accounts with no login activity in the past 90 days are **automatically flagged** and must be explicitly justified or deprovisioned
4. **Terminated Employee Accounts:** Verification that all access for employees who separated in the quarter was revoked within **24 hours** of separation
5. **Privileged Access Review:** AWS root account usage, IAM admin roles, and production database access receive **heightened scrutiny** — CISO approval required for each retained privileged access
6. **CISO Sign-Off:** CISO signs off on completed recertification evidence in Cortex's GRC tool
7. **Audit Evidence:** Evidence package (access roster, manager sign-off emails, deprovisioning logs) archived for SOC 2 and ISO 27001 audit use

### 3.3 Access Provisioning and Deprovisioning

| Event | Maximum Response Time |
|-------|----------------------|
| New hire access provisioning (after HR approval) | 1 business day |
| Employee separation — access revocation | **24 hours** (immediate for involuntary terminations) |
| Role change — excess access removal (least-privilege) | 5 business days |
| Contractor access expiry enforcement | Automatic on contract end date (enforced via Terraform/SCIM) |

---

## 4. Credential Rotation and Authentication Standards

### 4.1 Credential Rotation Schedule

| Credential Type | Maximum Validity | Rotation Enforcement |
|----------------|-----------------|---------------------|
| Database passwords (PostgreSQL, Redis) | **60 days** | Automated via AWS Secrets Manager with shadow credential swap |
| SSH private keys (bastion/server access) | **90 days** | Enforced by SSH key expiry policy in GitHub and AWS Key Pairs |
| AWS IAM access keys (programmatic) | **90 days** | AWS Config rule `access-keys-rotated` alerts on violations |
| API keys and service tokens | **365 days** | Tracked in Vault; expiry alerts 30 days before rotation due |
| Employee account passwords | **90 days** | Enforced via Okta password policy |
| JWT signing keys (RSA-2048) | **180 days** | Automated key rollover with 24-hour dual-verification window |

### 4.2 Multi-Factor Authentication (MFA) Standards

**All** access to Cortex production systems, cloud management consoles, and internal tools requires MFA. The minimum accepted MFA methods are:

| MFA Method | Allowed for Production Access | Notes |
|-----------|------------------------------|-------|
| **FIDO2 / WebAuthn (Hardware Security Key)** | ✅ Preferred | YubiKey 5 Series, Apple Touch ID with platform authenticator |
| **Authenticator App (TOTP)** | ✅ Permitted | Google Authenticator, Authy, Microsoft Authenticator |
| **Push Notification (Okta Verify)** | ✅ Permitted | Okta Verify with number matching required |
| SMS OTP | ❌ **NOT permitted** | SIM-swap attack vulnerability |
| Email OTP | ❌ **NOT permitted** | Email account compromise risk |

**Privileged/Admin Access:** FIDO2 hardware security key is **mandatory** for:
- AWS root account access (physical YubiKey stored in fireproof safe, 2-person rule)
- Production database direct access
- Qdrant collection admin operations
- GitHub organization owner role

### 4.3 Password Complexity Policy

| Requirement | Standard |
|------------|---------|
| Minimum length | **14 characters** |
| Character classes | Uppercase, lowercase, number, and special character |
| Common password check | Blocklist of top 100,000 common passwords (HaveIBeenPwned integration) |
| Password reuse | No reuse of last **12 passwords** |
| Shared/generic passwords | **Strictly prohibited** — every identity must have a unique credential |

---

## 5. Incident Response and Severity Matrix

### 5.1 Incident Severity Classification

| Severity | Criteria | Examples |
|---------|---------|---------|
| **SEV-1 — Critical** | > 20% of active users impacted, complete service unavailability, confirmed data breach | Full platform outage, ransomware, production data exfiltration |
| **SEV-2 — High** | Degraded service affecting 5–20% of users, significant performance impact, unconfirmed potential breach | Qdrant timeout affecting partial queries, suspected unauthorized access |
| **SEV-3 — Medium** | Minor degradation, single non-critical service failure, isolated data access anomaly | Non-production environment breach, single user affected, CI/CD pipeline failure |
| **SEV-4 — Low** | Informational security events, failed login alerts below threshold, minor vulnerability discovered | Vulnerability scanner finding (CVSS < 7.0), test environment misconfiguration |

### 5.2 Incident Response SLAs

| Severity | Detection-to-Acknowledgment | First Response (Customer/DPBI if applicable) | Resolution Target |
|---------|---------------------------|---------------------------------------------|------------------|
| SEV-1 | **15 minutes** | 1 hour (customer notification); 72 hours (DPBI if personal data) | 4 hours |
| SEV-2 | **1 hour** | 4 hours (customer notification if affected) | 24 hours |
| SEV-3 | **4 hours** | Next business day | 72 hours |
| SEV-4 | **24 hours** | No external notification required | 7 business days |

---

## 6. Disaster Recovery (DR) Testing

### 6.1 DR Drill Cadence

Disaster recovery drills are mandatory to validate RPO/RTO commitments:

| DR Test Type | Frequency | Scope |
|-------------|-----------|-------|
| **Database failover drill (RDS Multi-AZ)** | Quarterly | Trigger automated RDS failover; verify application reconnection within RTO |
| **Full platform DR drill (AWS ap-south-1 → ap-south-2)** | Semi-annually (every 6 months) | Failover entire Cortex platform to Hyderabad DR region; validate 15-min RTO |
| **Backup restoration test** | Monthly | Restore PostgreSQL from latest S3 snapshot; verify data integrity against checksum |
| **Kafka replay test** | Quarterly | Replay Kafka partition offsets from known checkpoint; verify 1-minute RPO |

### 6.2 RPO/RTO Targets

| Metric | Target | Verification Method |
|--------|--------|-------------------|
| **RPO (Recovery Point Objective)** | **< 1 minute** | RDS automated backups every 5 min + Kafka offset-based replay for event stream |
| **RTO (Recovery Time Objective)** | **< 15 minutes** | Automated Route 53 health check failover to Hyderabad + ECS Fargate auto-start |

---

## 7. Vulnerability Management

### 7.1 Vulnerability Scanning

| Scan Type | Tool | Frequency |
|-----------|------|-----------|
| Container image scan | Trivy + Docker Scout | Every CI/CD build |
| Dependency vulnerability scan | Snyk (Python + Node.js) | Every pull request + daily scheduled scan |
| Infrastructure misconfiguration | AWS Security Hub + Prowler | Daily automated scan |
| Web application scan | OWASP ZAP (automated) | Weekly scheduled scan on staging |
| Penetration test (external) | Third-party (certified CREST/OSCP) | Annually (or before major client go-lives) |

### 7.2 Vulnerability SLA

| CVSS Severity | Patch/Mitigation Deadline |
|--------------|--------------------------|
| Critical (9.0–10.0) | **48 hours** from confirmed disclosure |
| High (7.0–8.9) | **7 days** |
| Medium (4.0–6.9) | **30 days** |
| Low (0.1–3.9) | **90 days** |

---

## 8. Document Control

Reviewed semi-annually by the CISO. Changes require approval from CISO + CEO.

**Approvals:**
- CISO: *Digitally signed — 2026-10-01*
- CTO: *Reviewed — 2026-10-02*
- CEO: *Acknowledged — 2026-10-02*
