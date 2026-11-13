# Engineering Workstation, Hardware Provisioning, and Cloud Sandbox Infrastructure Policy

**Document ID:** POL-ENG-2026-004  
**Department:** Engineering  
**Security Clearance Level:** Internal  
**Effective Date:** January 1, 2026  
**Last Reviewed:** January 15, 2026  
**Target Audience:** Core Engineering, Infrastructure, QA, Machine Learning, Data Platform Staff  
**Policy Owner:** Head of IT Infrastructure & Engineering Operations  

---

## 1. Executive Purpose & Scope

Engineering excellence requires robust computing resources, reproducible local development setups, and sandboxed cloud environments. This policy mandates the hardware provisioning standards, procurement budgets, and cloud sandbox stipends for all technical contributors across our development centers in **Bengaluru**, **Hyderabad**, and **Pune**.

This document complements general corporate IT rules and supersedes standard office provisioning for all recognized engineering job families.

---

## 2. High-Performance Engineering Workstation Allowance

Engineering tasks involving local container compilation, vector indexing, neural network simulation, and high-concurrency microservice execution require enterprise-grade hardware.

### 2.1 Standard Hardware Budget
- **Workstation Allocation Ceiling:** Up to **₹1,50,000 (One Lakh Fifty Thousand Indian Rupees)** allocated per technical engineer upon joining or during hardware refresh cycles.
- **Approved Primary Configurations:**
  - **Option A (macOS Track):** Apple MacBook Pro (Apple Silicon M3 / M3 Pro chip, minimum 16 GB Unified RAM, 512 GB NVMe SSD).
  - **Option B (Linux / Windows Track):** Lenovo ThinkPad P-Series / T-Series Mobile Workstation or Dell Precision 3580 (Intel Core i7/i9 13th/14th Gen or AMD Ryzen 7 PRO, 32 GB DDR5 RAM, 1 TB PCIe 4.0 NVMe SSD, running Ubuntu LTS or Windows 11 Enterprise).
- **Custom High-Performance Exceptions:** Machine learning research engineers training local embedding models may request GPU-accelerated configurations up to ₹2,25,000 with VP of Engineering written endorsement.

### 2.2 Procurement and IT Delivery
- Hardware is procured through authorized enterprise corporate vendors (Apple India Enterprise Direct, Lenovo India Enterprise Portal) with delivery directly to the employee's residential address or Bengaluru/Pune/Hyderabad office facility.
- Equipment remains the legal property of Cortex Technologies India Pvt Ltd and is enrolled in corporate Mobile Device Management (MDM).

### 2.3 Hardware Refresh Cycle & Buyback
- **Refresh Interval:** Standard hardware refresh cycle is **thirty-six (36) months (3 years)** from the date of initial commissioning.
- **Employee Buyback Option:** Upon reaching the 36-month refresh milestone, employees may retain their deprecated laptop by purchasing it at its fair residual book value (typically 10% to 15% of original invoice value plus applicable GST).

---

## 3. Monthly Cloud Sandbox & Home Lab Stipend

In addition to central staging and development clusters hosted on AWS `ap-south-1` (Mumbai) and GCP `asia-south1` (Delhi), engineers are encouraged to experiment with emerging technologies, prototype architecture spikes, and maintain private home test labs.

### 3.1 Monthly Cloud Sandbox Stipend
- **Monthly Allocation:** **₹5,000 per month** (reimbursable against paid cloud provider or homelab invoices).
- **Approved Expenditures:**
  - Public cloud usage: Amazon Web Services (AWS), Google Cloud Platform (GCP), Microsoft Azure, Hetzner, or DigitalOcean cloud compute instances and managed Kubernetes clusters.
  - Model inference & API endpoints: Anthropic, OpenAI API, Groq, Hugging Face Pro subscriptions, Pinecone, or Datadog sandbox tiers.
  - Homelab hardware compute: Energy subsidies or hardware components (Raspberry Pi clusters, RAM expansions, home server NAS drives).
- **Expense Claim Submission:** Must be submitted monthly via the ERP portal under the `Engineering-Innovation-Stipend` ledger code, attaching the provider's machine-generated invoice or credit card statement in INR.

---

## 4. Endpoint Security & Operational Compliance

All engineering workstations, regardless of operating system, must maintain full compliance with Cortex Information Security Directives:

1. **Full-Disk Encryption:** FileVault 2 (macOS) or BitLocker (Windows) / LUKS (Linux) with 256-bit AES encryption mandatory.
2. **Security Agents:** CrowdStrike Falcon Endpoint Detection & Response (EDR) and Jamf / Microsoft Intune management agents must remain active 24/7.
3. **No Unapproved BYOD:** Code repositories, database connection strings, and production SSH keys must never be cloned or transferred to personal, non-corporate devices.
