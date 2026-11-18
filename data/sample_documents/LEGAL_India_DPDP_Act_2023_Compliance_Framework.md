# Legal & Compliance Framework: India Digital Personal Data Protection (DPDP) Act 2023

**Document ID:** LEGAL-COMP-DPDP-2026-01  
**Department:** HR / Legal & Compliance  
**Clearance Level:** Internal  
**Version:** 1.2  
**Owner:** Data Protection Officer (DPO) / Legal & Compliance Head  
**Last Reviewed:** 2026-10-15  
**Next Review:** 2027-04-15  

---

## 1. Purpose and Scope

This framework establishes Cortex Technologies Pvt. Ltd.'s compliance obligations and operational procedures under the **Digital Personal Data Protection (DPDP) Act, 2023** (No. 22 of 2023), which received Presidential assent on **August 11, 2023**, and is enforced by the **Data Protection Board of India (DPBI)**.

This framework applies to:
- All personal data of employees, contractors, and third parties (Data Principals) processed by Cortex Technologies
- All departments that collect, store, process, or transmit personal data
- All third-party processors (vendors, SaaS providers, cloud platforms) handling Cortex data under a data processing agreement

**What is Personal Data under DPDP 2023?**  
Any data about an identified or identifiable natural person (Data Principal) that can directly or indirectly identify them — including name, email, phone number, biometric data, financial data, location data, and inferred data.

---

## 2. Key Definitions

| Term | DPDP Act 2023 Definition |
|------|------------------------|
| **Data Principal** | The natural person to whom the personal data relates (employees, job applicants, users) |
| **Data Fiduciary** | The entity that determines the purpose and means of processing personal data — Cortex Technologies Pvt. Ltd. in this context |
| **Data Processor** | A third party that processes data on behalf of the Data Fiduciary (e.g., AWS, Darwinbox, Zoho) |
| **Consent Manager** | A registered entity that enables consent management and withdrawal on behalf of Data Principals |
| **Significant Data Fiduciary (SDF)** | A Data Fiduciary designated by the Central Government based on volume or sensitivity of data processed — higher obligations apply |
| **DPBI** | Data Protection Board of India — the regulatory authority for enforcement and adjudication |

---

## 3. Lawful Bases for Processing

Under the DPDP Act 2023, Cortex Technologies may process personal data only under the following lawful bases:

### 3.1 Consent (Section 6)

For non-employment personal data (e.g., user data, marketing, product usage analytics), **explicit, informed, and freely given consent** must be obtained before processing.

**Consent Requirements:**
- Written in **plain language** (not legalese)
- Provided in a manner that is as easy to **withdraw** as it is to give
- Purpose-specific — consent obtained for one purpose cannot be extended to another without fresh consent
- Accompanied by a **Privacy Notice** that specifies data categories, purposes, and retention periods
- Recorded with a **timestamp, IP address, and consent version** for audit trail

### 3.2 Legitimate Use (Section 7) — No Consent Required

Consent is not required (but transparency is mandatory) for:
- Performance of **employment contract** (payroll, benefits, performance management)
- Compliance with **legal obligations** (TDS, EPF, ESIC, MCA filings, CERT-In)
- **State functions** — processing for government benefit schemes
- **Medical emergencies** — where consent cannot be obtained
- **Sovereignty and security** — where national security interests override consent

---

## 4. Data Principal Rights

All Data Principals (employees, users, contractors) have the following rights under Section 11–14 of the DPDP Act 2023. Cortex Technologies must facilitate these rights within the prescribed timelines.

### 4.1 Right to Information (Section 11)

Data Principals may request what personal data is held about them and for what purpose. Response must be provided within **30 days** of a valid request.

### 4.2 Right of Access

Data Principals may request a copy of their personal data held by Cortex. Format: machine-readable (CSV/JSON) wherever technically feasible.

### 4.3 Right to Correction and Erasure (Section 13)

| Right | Description | Response SLA |
|-------|-------------|-------------|
| Right to Correct | Correct inaccurate or incomplete personal data | 15 business days |
| Right to Erase | Delete personal data when no longer necessary for the stated purpose | 30 days |

> **Important limitation on Erasure:** Cortex may retain personal data beyond a deletion request where retention is required by law (e.g., 8 years for financial records under CGST Act, 5 years for employment records under various labor laws). Such retention must be documented and communicated to the Data Principal.

### 4.4 Right to Withdraw Consent (Section 7)

Data Principals may withdraw consent at any time. Withdrawal does not affect lawfulness of processing based on consent prior to withdrawal.

**Operational Procedure:**
1. Withdrawal request submitted via email to `dpo@cortextechnologies.in` or through the Consent Manager portal
2. DPO logs request in the Data Request Register (DRR) with timestamp
3. Withdrawal acknowledged within **24 hours**
4. Processing halted and consent records updated within **7 business days**

### 4.5 Right to Nominate (Section 14)

Data Principals may nominate an individual to exercise rights on their behalf in the event of death or incapacity.

### 4.6 Right to Grievance Redressal (Section 13)

Data Principals may lodge grievances with the Cortex DPO regarding any processing concern. Grievance must be acknowledged within **24 hours** and resolved within **30 days**.

---

## 5. Data Fiduciary Obligations

### 5.1 Privacy Notice

Cortex Technologies must provide a **Privacy Notice** to Data Principals before or at the time of collecting personal data. The Privacy Notice must specify:
- Categories of personal data collected
- Purpose of processing
- Third parties with whom data is shared
- Retention period for each category
- How to exercise Data Principal rights
- Data Protection Officer contact details

### 5.2 Data Minimization

Cortex Technologies must collect only the **minimum personal data necessary** for the stated purpose. Data minimization reviews are conducted quarterly by the DPO across all departments.

### 5.3 Purpose Limitation

Personal data collected for one purpose (e.g., recruitment) may not be repurposed for another use (e.g., marketing) without fresh consent.

### 5.4 Storage Limitation and Retention Schedule

| Data Category | Retention Period | Legal Basis |
|--------------|-----------------|------------|
| Employee payroll records | 8 years from date of last transaction | CGST Act, Income Tax Act |
| Employment contracts and HR files | 7 years from date of separation | Factories Act, Shops & Establishments Act |
| Recruitment / candidate data (rejected candidates) | 6 months from rejection | DPDP Act 2023 (legitimate use ends) |
| User consent records (Consent Manager) | Duration of relationship + 3 years | Audit trail requirement |
| Access logs and security audit logs | 180 days | CERT-In Directions 2022 |
| CCTV footage (office premises) | 30 days | Physical security SOP |

### 5.5 Accuracy of Data

Cortex Technologies must take reasonable steps to ensure personal data is **accurate and up to date** at all times relevant to the purpose of processing.

---

## 6. Cross-Border Data Transfers

Under Section 16 of the DPDP Act 2023, the Central Government may restrict cross-border transfers to specified countries by notification. Until the DPDP Rules are notified with final country restrictions:

**Current Practice:**
- Personal data of Indian residents may be transferred to AWS Mumbai (ap-south-1) as the primary data residency region
- Transfers to US-East-1 (AWS Virginia) are permitted for specific AI model inference, subject to Standard Contractual Clauses (SCCs)
- Transfers to any country restricted by future Government notification will require immediate cessation and DPO escalation

---

## 7. Data Breach Response and DPBI Notification

### 7.1 Breach Classification

| Severity | Description | Example |
|---------|------------|---------|
| **P1 — Critical** | Unauthorized access/exfiltration of sensitive personal data affecting ≥100 Data Principals | Database breach exposing employee PAN/Aadhaar |
| **P2 — High** | Accidental disclosure to unauthorized parties, ransomware without confirmed exfiltration | Misconfigured S3 bucket |
| **P3 — Medium** | Minor unauthorized access, single individual affected, no sensitive data | Email sent to wrong internal recipient |

### 7.2 Mandatory DPBI Notification Timeline

Under Section 8(6) of the DPDP Act 2023 (read with anticipated Rules):

| Obligation | Timeline |
|-----------|---------|
| Internal incident logging | Within **1 hour** of detection |
| DPO notified | Within **2 hours** of detection |
| DPBI notification (mandatory for P1 and P2) | Within **72 hours** of detection |
| Affected Data Principals notified | Within **72 hours** (concurrent with DPBI notification) |
| Full incident report to DPBI | Within **14 days** of initial notification |

> **Important:** DPBI notification is **mandatory regardless of certainty** about the breach. If there is a reasonable belief that a breach may have occurred, notification must be initiated. Under-reporting or delayed reporting to DPBI may attract penalties up to ₹500 Crore.

---

## 8. Data Protection Officer (DPO)

Cortex Technologies has appointed a **Data Protection Officer (DPO)** to oversee DPDP Act 2023 compliance.

**DPO Responsibilities:**
- Serve as the primary point of contact for DPBI and Data Principal requests
- Monitor ongoing compliance across all departments
- Conduct quarterly internal audits and Data Protection Impact Assessments (DPIAs)
- Coordinate Data Processing Agreements (DPAs) with all third-party processors
- Review and approve new product features involving personal data processing

**DPO Contact:**
- Email: `dpo@cortextechnologies.in`
- Internal Slack: `#dpo-compliance`
- Grievance portal: Accessible via company intranet

---

## 9. Significant Data Fiduciary (SDF) Readiness

The Central Government may designate Cortex Technologies as a **Significant Data Fiduciary (SDF)** based on the volume or nature of data processed. SDF status triggers additional obligations:
- Appointment of a **Data Auditor** for annual independent audits
- Completion of a periodic **Data Protection Impact Assessment (DPIA)**
- Mandatory appointment of a resident DPO (Indian resident)
- Algorithmic transparency obligations for AI-driven decisions affecting Data Principals

The DPO team maintains a readiness checklist and conducts a gap assessment annually in advance of potential SDF designation.

---

## 10. Document Control

This framework is reviewed every 6 months and updated as DPDP Rules are notified by the Central Government. For questions, contact the DPO at `dpo@cortextechnologies.in`.

**Approvals:**
- Data Protection Officer: *Digitally signed — 2026-10-15*
- Legal & Compliance Head: *Digitally signed — 2026-10-15*
- Chief Executive Officer: *Reviewed — 2026-10-16*
