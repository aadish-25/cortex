# Engineering Infrastructure Security Directive: Production Secret Vault, Zero-Trust Access, and Incident Notification

**Document Reference:** SEC-ENG-CONF-2026-012  
**Classification:** CONFIDENTIAL // STRICT ACCESS CONTROLS  
**Department:** Engineering (Infrastructure & Information Security)  
**Security Clearance Level:** Confidential  
**Effective Date:** January 15, 2026  
**Audience:** Site Reliability Engineers (SREs), Database Administrators (DBAs), Platform SecOps, CISO  
**Approved By:** Chief Information Security Officer (CISO) & Head of Engineering  

---

## 1. Scope and Objective

This confidential standard governs cryptographic posture, credential lifecycle automation, bastion jump host connectivity, and statutory cybersecurity compliance across all production infrastructure hosting Cortex workloads.

Primary production clusters operate in the **AWS Mumbai Region (`ap-south-1`)** with disaster recovery failover in **GCP Delhi Region (`asia-south1`)**. All systems are subject to continuous telemetry auditing, automated configuration drift detection, and rigorous Indian cybersecurity statutory oversight.

---

## 2. Secrets Management with HashiCorp Vault Enterprise

Static credentials, unencrypted secrets in repositories, and cleartext environment variables are strictly forbidden. All secrets must be dynamically provisioned, rotated, and audited through **HashiCorp Vault Enterprise**.

### 2.1 Ephemeral Dynamic Database Credentials
- Application microservices in Kubernetes (EKS / GKE) must request database credentials using the Vault Database Secrets Engine.
- **Lease Time-To-Live (TTL):** Standard TTL is set to **24 hours**, renewable up to an absolute maximum hard limit of seven (7) calendar days.
- **Automatic Revocation:** Vault immediately executes SQL revocation scripts (`REVOKE`, `DROP USER`) upon lease expiration or Kubernetes pod termination.
- **Workload Identity:** Services authenticate using Kubernetes Service Account Tokens mapped to Vault AppRole identities with strict, least-privilege RBAC policies.

### 2.2 Key Hierarchy & Encryption Standard
- Data at rest across all PostgreSQL databases, Qdrant vector collections, and Redis clusters is encrypted with **AES-256** using Customer-Managed Keys (CMK) secured in cloud Hardware Security Modules (Cloud HSM).
- Master Key Encryption Keys (KEK) undergo automated annual key rotation orchestrated via KMS.

---

## 3. Database Password Rotation Protocol: Mandatory 60-Day Cadence

### 3.1 60-Day Rotation Schedule
All static administrative database accounts (e.g., `cortex_dba`, `pg_admin`, `qdrant_admin`) that cannot utilize dynamic ephemeral Vault leases must undergo automated credential rotation every **sixty (60) calendar days**.

### 3.2 Automated Zero-Downtime Shadow User Swapping
Rotation is executed via automated CI/CD pipeline triggers during designated maintenance windows (Sundays 02:00 – 03:00 IST):
1. **Shadow Creation:** The rotation orchestrator creates an ephemeral shadow account (`cortex_dba_v2`) with identical schema permissions.
2. **Credential Injection:** The new cryptographically generated 32-character secret (with high entropy) is committed to Vault path `secret/production/database/admin`.
3. **Application Pool Handshake:** Connection pools across microservices drain connections and re-authenticate against the new credential.
4. **Deprecation:** The previous account is disabled, placed into audit mode for 48 hours, and subsequently purged.

---

## 4. Indian Statutory Cybersecurity Compliance: CERT-In 6-Hour Incident Notification

Pursuant to the **Indian Computer Emergency Response Team (CERT-In) Cyber Security Directions** issued under sub-section (6) of Section 70B of the Information Technology Act, 2000 (Directions No. 20(3)/2022-CERT-In):

### 4.1 Mandatory 6-Hour Reporting SLA
The Company is legally obligated to report any confirmed or suspected cybersecurity incident to CERT-In within **six (6) hours** of noticing or being brought to notice of the incident:
- Incident categories include: Unauthorized access to database clusters, ransomware or data encryption attacks, identity compromise on HashiCorp Vault, SQL injection exploitation, denial of service (DoS/DDoS), or compromised credentials leaked publicly.
- **Reporting Channel:** Incidents must be logged immediately through the official CERT-In Incident Reporting Portal (`incident@cert-in.org.in`) using Form CERT-In-IR-1.

### 4.2 Mandatory 180-Day System Log Retention
All production server logs, SSH bastion connection records, DNS queries, and database query audit trails must be securely retained in immutable cloud object storage (WORM-compliant S3 Glacier with Object Lock) within Indian territorial boundaries for a minimum period of **one hundred and eighty (180) consecutive calendar days**.

---

## 5. Bastion Host Access via Approved Bengaluru Static IPs

### 5.1 Perimeter Isolation
Production database subnets and internal Kubernetes control planes reside in private VPCs with zero direct public internet ingress routes.

### 5.2 Approved Bengaluru Static IP Gating
All administrative SSH and maintenance access is strictly channeled through hardened **SSH Bastion Jump Hosts**:
- Ingress to the Bastion gateway is restricted via security groups exclusively to the whitelisted **Corporate Static IP pool** of the **Bengaluru Headquarters (Bellandur Tech Campus)**:
  - Primary Corporate CIDR: `103.145.72.0/24` (Bengaluru Primary Leased Line)
  - Secondary Disaster Recovery CIDR: `103.145.73.0/24` (Tata Communications Backup Link)
- **Zero-Trust Access Requirements:**
  - Mandatory SSO authentication via corporate IdP with FIDO2 hardware token WebAuthn MFA.
  - Ephemeral SSH client certificates signed by Vault SSH Secrets Engine with a maximum validity of **four (4) hours**.
  - Complete keystroke-level session auditing with real-time streaming to the SIEM.
