# Parent Insights Component Input/Output Catalog

## Purpose

This document is the data companion to `parent_insights_leadership_architecture_v3.drawio`. It describes the input, processing, output, and representative sample data for every component in the end-to-end flow.

Samples are marked as follows:

- **Actual** — copied from the supplied raw dataset or manifest.
- **Implemented/derived** — defined or calculated by the existing Glue code and design documents.
- **Proposed contract** — recommended for a downstream component that is shown in the architecture but is not yet implemented in this repository.

Proposed examples use real profile/date/feature values where possible. Values such as anomaly scores, LLM decisions, and delivery IDs are illustrative because the repository does not yet contain their production outputs.

## Source material

| Source | Location | Status |
|---|---|---|
| Raw historical records | `phase1b-data-preparation/daily-history.jsonl` | Actual, 600 JSONL records |
| Uploaded raw batch | `raw-batches/run_id=20260830T055411Z/part-00000.jsonl.gz` | Actual compressed input |
| Raw manifest | `raw-batches/run_id=20260830T055411Z/manifest.json` | Actual |
| Record schema | `phase1b-data-preparation/daily-communication.schema.json` | Implemented contract |
| Glue Job 1 | `phase1b-data-preparation/daily_communication_raw_to_curated.py` | Implemented |
| Glue Job 2 | `phase1b-data-preparation/daily_communication_curated_to_features.py` | Implemented |
| Glue Job 1 design | `phase1b-data-preparation/glue-job-1-design.md` | Implemented design |
| Glue Job 2 design | `phase1b-data-preparation/glue-job-2-feature-engineering-design.md` | Implemented design |

## Actual dataset summary

```json
{
  "runId": "20260830T055411Z",
  "sourceFile": "daily-history.jsonl",
  "startDate": "2026-04-06",
  "endDate": "2026-06-04",
  "profileCount": 10,
  "recordCount": 600,
  "fileCount": 1,
  "status": "COMPLETE"
}
```

The implemented dataset contains 60 days per profile. The architecture uses “90-day history” as the production target. The same contracts and transformations apply when the input is extended to 90 completed days.

---

# Historical bootstrap

## H1. Amazon S3 — Historical raw data

**Purpose:** Store the immutable raw history and its run-control files.

**Input:** `daily-history.jsonl`, packaged by the existing preparation script.

**Actual S3 run structure:**

```text
s3://vsf-parent-insights-raw/daily-communication/history/
run_id=20260830T055411Z/
├── _SUCCESS
├── manifest.json
└── part-00000.jsonl.gz
```

**Actual record:**

```json
{
  "childProfileId": 9001,
  "aggDate": "2026-04-06",
  "timezone": "America/New_York",
  "communicationStatus": "ACTIVE",
  "callCount": 10,
  "textCount": 27,
  "nightCount": 1,
  "nightStartTime": "21:00",
  "nightEndTime": "06:00",
  "schoolCount": 1,
  "schoolStartTime": "08:00",
  "schoolEndTime": "15:00"
}
```

**Output:** Immutable raw objects and a manifest consumed by Glue Job 1.

**Control note:** The actual manifest declares 600 records and SHA-256 `cb71bb1484f5f1f4fde21cbcccf95407f769acd5baf55acd90a6cd97de4ec122` for the compressed part.

## H2. AWS Glue Job 1 — Validate and clean

**Purpose:** Convert a completed raw run into trusted curated data without modifying the raw input.

**Input:**

- `run_id=20260830T055411Z`
- `_SUCCESS`
- `manifest.json`
- Exact `part-*.jsonl.gz` objects listed in the manifest

**Input schema:** The 12 actual raw fields shown in H1. The unique business key is `childProfileId + aggDate`.

**Processing:**

1. Validate completion marker and manifest.
2. Verify part names, sizes, counts, and SHA-256 hashes.
3. Parse each JSONL record.
4. Validate required fields, types, date, IANA timezone, status, times, and nonnegative counts.
5. Enforce cross-field rules such as `nightCount <= callCount`.
6. Detect duplicate profile/date keys.
7. Route valid rows to curated output and invalid rows to quarantine.
8. Reconcile counts and write a validation summary.

**Implemented valid output:** Snappy Parquet at:

```text
s3://vsf-parent-insights-curated/daily-communication/
run_id=20260830T055411Z/data/
```

**Readable representation of a valid output row:**

```json
{
  "childProfileId": 9001,
  "aggDate": "2026-04-06",
  "timezone": "America/New_York",
  "communicationStatus": "ACTIVE",
  "callCount": 10,
  "textCount": 27,
  "nightCount": 1,
  "nightStartTime": "21:00",
  "nightEndTime": "06:00",
  "schoolCount": 1,
  "schoolStartTime": "08:00",
  "schoolEndTime": "15:00"
}
```

**Implemented quarantine output:** Snappy Parquet containing the recoverable contract fields plus:

```json
{
  "rawRecord": "{...original JSON line...}",
  "sourceFile": "s3://.../part-00000.jsonl.gz",
  "recordFingerprint": "<sha256-of-line>",
  "failureReasons": ["INVALID_TYPE:callCount"]
}
```

**Implemented validation-summary output:**

```text
s3://vsf-parent-insights-curated/daily-communication-validation/
run_id=20260830T055411Z/summary.json
```

The verified supplied run contains 600 curated rows and 0 quarantined rows.

## H3. AWS Glue Job 2 — Baseline and feature engineering

**Purpose:** Calculate leakage-free personalized historical features from each profile’s earlier calendar days.

**Input:** Glue Job 1 curated Parquet. Example actual row for the final day:

```json
{
  "childProfileId": 9001,
  "aggDate": "2026-06-04",
  "timezone": "America/New_York",
  "communicationStatus": "ACTIVE",
  "callCount": 13,
  "textCount": 22,
  "nightCount": 2,
  "nightStartTime": "21:00",
  "nightEndTime": "06:00",
  "schoolCount": 2,
  "schoolStartTime": "08:00",
  "schoolEndTime": "15:00"
}
```

**Processing:** For date `D`, calculate statistics using `D-7` through `D-1` and `D-30` through `D-1`. The current day is excluded to prevent leakage.

**Implemented/derived output:** Snappy Parquet at:

```text
s3://vsf-parent-insights-features/daily-communication/
run_id=20260830T055411Z/data/
```

**Implemented/derived feature sample:**

```json
{
  "childProfileId": 9001,
  "aggDate": "2026-06-04",
  "callCount": 13,
  "textCount": 22,
  "nightCount": 2,
  "schoolCount": 2,
  "historyDays7d": 7,
  "historyDays30d": 30,
  "featuresReady7d": true,
  "featuresReady30d": true,
  "callCountAvg7d": 8.5714,
  "callCountStd7d": 1.5908,
  "callCountDifference7d": 4.4286,
  "callCountZScore7d": 2.7839,
  "callCountAvg30d": 9.4000,
  "callCountStd30d": 1.9596,
  "callCountDifference30d": 3.6000,
  "callCountZScore30d": 1.8371,
  "textCountZScore7d": -0.5595,
  "textCountZScore30d": -0.8090,
  "nightCountZScore7d": 1.8708,
  "nightCountZScore30d": 1.7682,
  "schoolCountZScore7d": 0.6934,
  "schoolCountZScore30d": 0.6820
}
```

The full implemented output retains all 12 input fields and adds readiness, average, population-standard-deviation, difference, and Z-score fields for calls, texts, night calls, and school calls.

## H4. Amazon S3 — Versioned feature dataset

**Purpose:** Provide an immutable model-training input with reproducible lineage.

**Input:** Feature Parquet produced by Glue Job 2.

**Output:** The same feature rows stored under a run-specific prefix. A recommended dataset registration record is:

```json
{
  "runId": "20260830T055411Z",
  "inputUri": "s3://vsf-parent-insights-curated/daily-communication/run_id=20260830T055411Z/data/",
  "outputUri": "s3://vsf-parent-insights-features/daily-communication/run_id=20260830T055411Z/data/",
  "recordCount": 600,
  "ready7dCount": 530,
  "ready30dCount": 300,
  "readyBothCount": 300,
  "featureContractVersion": "1.0"
}
```

**Status:** Locations and counts are defined by the implemented feature job; `featureContractVersion` is a recommended explicit metadata field.

## H5. Amazon SageMaker — Isolation Forest training and approval

**Purpose:** Train an anomaly model from complete personalized feature rows and register an approved version.

**Input:** Rows where the required feature windows are ready. Recommended model columns include the Z-scores, differences, and selected current values from H3.

**Representative training vector derived from actual features:**

```json
{
  "callCountZScore7d": 2.7839,
  "callCountZScore30d": 1.8371,
  "textCountZScore7d": -0.5595,
  "textCountZScore30d": -0.8090,
  "nightCountZScore7d": 1.8708,
  "nightCountZScore30d": 1.7682,
  "schoolCountZScore7d": 0.6934,
  "schoolCountZScore30d": 0.6820
}
```

**Proposed model-registry output:**

```json
{
  "modelName": "daily-communication-isolation-forest",
  "modelVersion": "iforest-v1",
  "trainingRunId": "20260830T055411Z",
  "featureContractVersion": "1.0",
  "status": "APPROVED",
  "anomalyThreshold": 0.72,
  "artifactUri": "s3://<model-bucket>/daily-communication/iforest-v1/model.tar.gz"
}
```

**Status note:** The feature values are implemented/derived. The model version and threshold are proposed because no trained Isolation Forest output is present in the supplied repository.

---

# Daily operation

## D1. Amazon S3 — New daily summary

**Purpose:** Receive one completed daily aggregate per profile.

**Input/output contract:** The same 12-field raw contract used by historical ingestion.

**Actual representative record:** Use the `2026-06-04` profile `9001` record shown in H3 as the current-day example. In a production 90-day setup, the equivalent next record would be Day 91.

**Output:** A completed daily input partition that triggers Glue Job 3.

## D2. AWS Glue Job 3 — Daily processing

**Purpose:** Validate the new daily batch and coordinate leakage-free daily feature creation.

**Input:** New records using the H1 schema plus the prior approved baseline/feature state.

**Processing order:**

1. Validate and deduplicate the current day.
2. Calculate its features using only earlier days.
3. Submit the feature vector for SageMaker scoring.
4. After successful scoring, prepare the updated history/baseline state for the following day.

**Proposed control output:**

```json
{
  "jobName": "daily-communication-processing",
  "runId": "daily-20260604",
  "aggDate": "2026-06-04",
  "inputRecordCount": 10,
  "validRecordCount": 10,
  "quarantineRecordCount": 0,
  "featureContractVersion": "1.0",
  "status": "FEATURES_READY"
}
```

**Status note:** Glue Job 3 is architectural/proposed; it should reuse validation and feature code from implemented Jobs 1 and 2.

## D3. AWS Glue — Daily feature calculation

**Purpose:** Compare the current day with the profile’s earlier completed days.

**Input:** Valid daily row plus historical measurements. For the actual `2026-06-04` example, the 7-day call history is `8, 9, 6, 10, 7, 11, 9`.

**Output:** The actual derived H3 feature row. This is the exact shape submitted to model scoring after selecting the approved model columns.

## D4. Amazon SageMaker — Isolation Forest scoring

**Purpose:** Determine how unusual the daily feature combination is.

**Input:** Approved model version plus a daily feature vector.

**Proposed scoring output:**

```json
{
  "childProfileId": 9001,
  "aggDate": "2026-06-04",
  "modelVersion": "iforest-v1",
  "featureContractVersion": "1.0",
  "anomalyScore": 0.84,
  "threshold": 0.72,
  "isAnomaly": true
}
```

**Status note:** `0.84` is illustrative. The repository contains no actual SageMaker scoring result.

## D5. AWS Lambda — Insight candidate generation

**Purpose:** Convert a statistical anomaly into a structured, explainable candidate and reduce LLM volume.

**Input:** SageMaker score plus the actual contributing daily/baseline features.

**Proposed output grounded in the actual feature example:**

```json
{
  "candidateId": "cand-9001-20260604-communication-change",
  "childProfileId": 9001,
  "aggDate": "2026-06-04",
  "candidateType": "COMMUNICATION_PATTERN_CHANGE",
  "anomalyScore": 0.84,
  "evidence": [
    {"feature": "callCountZScore7d", "value": 2.7839},
    {"feature": "nightCountZScore7d", "value": 1.8708},
    {"feature": "callCount", "current": 13, "baseline7d": 8.5714}
  ],
  "qualificationStatus": "QUALIFIED"
}
```

**Output rule:** Below-threshold or duplicate signals do not proceed to Bedrock.

## D6. Amazon Bedrock — Batched LLM decision

**Purpose:** Evaluate only qualified candidates, grouped within the correct parent/privacy boundary.

**Input:** An array of candidate records from D5 plus approved prompt/policy context.

**Proposed structured output:**

```json
{
  "batchId": "llm-batch-20260604-001",
  "promptVersion": "parent-insight-decision-v1",
  "decisions": [
    {
      "candidateId": "cand-9001-20260604-communication-change",
      "recommendation": "NOTIFY",
      "priority": "MEDIUM",
      "reasonCode": "SUSTAINED_OR_MATERIAL_CHANGE",
      "summary": "Communication activity is notably above the recent personal pattern."
    }
  ]
}
```

**Control note:** Allowed recommendations are `NOTIFY`, `DEFER`, and `SUPPRESS`. Grouping and deduplication occur before the LLM.

## D7. Amazon DynamoDB — Decision and audit store

**Purpose:** Persist the original LLM recommendation, lineage, Trust Gate result, and delivery state.

**Input:** Bedrock decision from D6.

**Proposed initial record:**

```json
{
  "pk": "CHILD#9001",
  "sk": "INSIGHT#2026-06-04#cand-9001-20260604-communication-change",
  "candidateId": "cand-9001-20260604-communication-change",
  "llmRecommendation": "NOTIFY",
  "llmReasonCode": "SUSTAINED_OR_MATERIAL_CHANGE",
  "modelVersion": "iforest-v1",
  "featureContractVersion": "1.0",
  "promptVersion": "parent-insight-decision-v1",
  "finalStatus": "PENDING_TRUST_GATE"
}
```

**Output:** Durable record read by the Trust Gate and subsequently updated with final status.

## D8. AWS Lambda — Trust Gate

**Purpose:** Apply deterministic privacy, consent, preference, data-quality, duplicate, quiet-hour, freshness, and rate-limit policies.

**Input:** Decision/audit record plus parent configuration and recent-delivery state.

**Proposed result example:**

```json
{
  "candidateId": "cand-9001-20260604-communication-change",
  "llmRecommendation": "NOTIFY",
  "trustGateDecision": "DEFER",
  "reasonCode": "QUIET_HOURS",
  "scheduledAfter": "2026-06-05T07:00:00-04:00",
  "policyVersion": "trust-gate-v1"
}
```

**Output:** `DELIVER`, `DEFER`, or `BLOCK`, written back to D7. Only `DELIVER` proceeds immediately to EKS.

## D9. Amazon EKS — Notification Platform

**Purpose:** Accept an approved delivery request and execute the notification workflow.

**Input:** Trust-Gate-approved insight. Example when the deferred item becomes eligible:

```json
{
  "notificationRequestId": "notify-9001-20260605-001",
  "candidateId": "cand-9001-20260604-communication-change",
  "parentProfileId": "parent-for-child-9001",
  "channel": "PUSH",
  "title": "A communication pattern changed",
  "body": "Open the app to review a recent communication insight.",
  "deepLink": "/insights/cand-9001-20260604-communication-change",
  "idempotencyKey": "cand-9001-20260604-communication-change#PUSH"
}
```

**Proposed output:**

```json
{
  "notificationRequestId": "notify-9001-20260605-001",
  "deliveryStatus": "ACCEPTED",
  "attempt": 1,
  "acceptedAt": "2026-06-05T07:00:02-04:00"
}
```

**Control note:** Push content remains privacy-safe; detailed evidence stays behind authenticated application access.

## D10. Parent experience — Authenticated application

**Purpose:** Display the insight and collect outcome events.

**Input:** Deep link plus authenticated retrieval of the final approved insight record.

**Proposed application response:**

```json
{
  "insightId": "cand-9001-20260604-communication-change",
  "insightType": "COMMUNICATION_PATTERN_CHANGE",
  "displayDate": "2026-06-05",
  "summary": "Communication activity was higher than the recent personal pattern.",
  "evidence": [
    "13 calls compared with a 7-day average of 8.57",
    "Night-call activity was 1.87 standard deviations above the 7-day pattern"
  ]
}
```

**Proposed output event:**

```json
{
  "insightId": "cand-9001-20260604-communication-change",
  "eventType": "OPENED",
  "eventAt": "2026-06-05T07:14:31-04:00"
}
```

---

# Monitoring and governance

## G1. Amazon DynamoDB — Parent outcomes

**Purpose:** Store delivered/opened/dismissed/useful/ignored outcome events for insight-quality evaluation.

**Input:** Application and delivery events from D9 and D10.

**Proposed stored event:**

```json
{
  "pk": "INSIGHT#cand-9001-20260604-communication-change",
  "sk": "OUTCOME#2026-06-05T07:14:31-04:00#OPENED",
  "eventType": "OPENED",
  "notificationRequestId": "notify-9001-20260605-001",
  "eventAt": "2026-06-05T07:14:31-04:00"
}
```

**Output:** Outcome stream or queryable records consumed by monitoring. Parent outcomes do not directly modify the child’s behavioral history.

## G2. Amazon CloudWatch — Monitoring

**Purpose:** Monitor pipeline health, model/decision volume, cost, Trust Gate outcomes, and delivery quality.

**Inputs:** Metrics/logs from Glue, SageMaker, Lambda, Bedrock, DynamoDB, and the EKS Notification Platform.

**Proposed metrics:**

```json
{
  "namespace": "ParentInsights",
  "dimensions": {"Environment": "prod"},
  "metrics": {
    "RawRecords": 600,
    "CuratedRecords": 600,
    "QuarantinedRecords": 0,
    "QualifiedCandidates": 12,
    "LLMNotifyRecommendations": 5,
    "TrustGateBlocked": 1,
    "NotificationsDelivered": 4
  }
}
```

**Status note:** The raw/curated/quarantine counts reflect the supplied verified run. Downstream counts are illustrative until those components produce real telemetry.

## G3. SageMaker Model Registry — Model governance

**Purpose:** Version, compare, approve, and promote the Isolation Forest model.

**Input:** Candidate model artifact, evaluation results, feature version, threshold, and approval evidence.

**Proposed registry record:**

```json
{
  "modelPackageGroup": "daily-communication-isolation-forest",
  "modelVersion": "iforest-v2",
  "featureContractVersion": "1.0",
  "evaluationDatasetRunId": "20260830T055411Z",
  "comparison": {
    "champion": "iforest-v1",
    "challenger": "iforest-v2",
    "expectedAnomalyRate": 0.02
  },
  "approvalStatus": "PENDING_MANUAL_APPROVAL"
}
```

**Output:** Approved model version consumed by H5/D4. Prompt, Trust Gate policy, and candidate-rule versions should be governed in their own versioned configuration repository even though they are reviewed through the same operating process.

---

# End-to-end lineage example

```text
Actual raw record
childProfileId=9001, aggDate=2026-06-04, callCount=13
    ↓ Glue Job 1
Validated curated record with the same 12 contract fields
    ↓ Glue Job 2 / daily feature calculation
callCountZScore7d=2.7839, nightCountZScore7d=1.8708
    ↓ SageMaker scoring
Proposed anomalyScore=0.84, threshold=0.72
    ↓ Insight Candidate Lambda
COMMUNICATION_PATTERN_CHANGE with traceable feature evidence
    ↓ Bedrock
Proposed recommendation=NOTIFY
    ↓ DynamoDB
Persist recommendation, model version, feature version and prompt version
    ↓ Trust Gate
Proposed decision=DEFER because of quiet hours
    ↓ EKS Notification Platform when eligible
Privacy-safe push with authenticated deep link
    ↓ Parent application
OPENED outcome stored for monitoring
```

# Contract principles

1. Preserve the immutable raw record and run manifest.
2. Quarantine invalid data with complete reason codes; do not silently delete it.
3. Exclude the current day from the baseline used to evaluate that day.
4. Carry `runId`, model version, feature version, prompt version, and policy version through the lineage.
5. Send only qualified candidates to Bedrock.
6. Persist the original LLM recommendation before applying the Trust Gate.
7. Keep the Trust Gate deterministic and before notification delivery.
8. Update the audit record with Trust Gate and delivery outcomes.
9. Keep sensitive insight detail out of push-notification payloads.
10. Do not use parent engagement outcomes as child behavioral-history records.
