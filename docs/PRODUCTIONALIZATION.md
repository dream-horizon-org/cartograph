# Cartograph — Productionalization Touchpoints (Dream11 Onboarding)

The following are the touch points involving productionalization Cartograph to onboard Dream11:

---

## Onboarding

- No multi-tenancy: Only catering to a single org
- Control plane in Horizon: No security/on-prem deployments in phase-1
- Human Interface: No chat interface with orchestrator, devs from Cartograph will speak with clients and get the credentials

---

## Fault Tolerance

- AWS disk storage as inherent backup vs S3 storage
- Refining edge cases involved in backing up cwd, session files, installed binaries, used ports etc

---

## Distributed (deprioritized)

- We'll run cartograph on single instance for now, we'll get bigger instance if necessary

---

## Unresolved Variables Resolution

- When agents come across unknown variables they put it up in an un-resovled table, need to have a mechanism to resolve them

---

## Update Flow

- Only considering github plane's update flow for now
- Can leverage github' webhooks

---

## Cartograph Familiarity/Sanity

- All 4 of us needs to get ourselves familiarised with cartograph flows and architecture and run atleast a couple of test runs so that we can sanity check the current architecture for any major flaws or obvious improvements

---

## Cartograph Maturity

### Extensive Testing

- Need additional Bandwidth for testing
- 2 Dedicated dev bandwidth is required
- The devs assigned for testing would:
  - Communicate with few teams in D11 to identify silo'd service sets to run our Cartograph analysis on
  - Clarify with those teams if the test results look fine or anything is missing in them (since those teams would have better understanding of the flows of their service)
  - Run tests on entire D11 services (currently active)

---

## PR2Dev Maturity

- D11 active repos list

---

## Cartograph DB as AWS managed Aurora Postgres (along with pg vector extension)

- Can quickly evaluate if this is possible via Asgard's postgres component if not we'd need to instantiate this manually

---

## Agent Manager Compaction and Heartbeat flows

---

## Observability

- Alerts and recovery flows in case any particular piece of cartograph goes down

---

## Deployment Strategy

- Monolithic Deployment vs micro service deployment
- Local Embedding vs Embedding API Key
- Horizon Account to D11 account VPC peering
- Whitelisting Bedrock api access on the instance

---

## Cartograph Graph integration on Asgard UI

---

## Expected scale on Cartograph and PR2DEV and corresponding infra numbers

- Multi-threaded/asynchronousness of cartograph systems evaluation and basic load test (1000-2000rps) (and fixes in case any issues identified)

---

## Callouts

- Expected High Cost
- Restricting ourselves to only 2 planes (Github + telemetry)
- Doing away with V2 evaluation
